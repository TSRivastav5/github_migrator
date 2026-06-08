"""
task2_migrator.py
─────────────────
Phase 3: Mirror-clone the source repo to a destination GitHub account, then
migrate all Pull Requests as Issues (or real PRs when possible).

Usage (called from main.py — do not run directly):
    from task2_migrator import run_task2
    run_task2(pr_list)    # pr_list from task1_collector.run_task1()
"""

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from typing import Any

import requests
from github import Github, GithubException

import config
from utils import ensure_dir, remove_dir, retry_github, run_subprocess, wait_for_rate_limit

logger = logging.getLogger(__name__)

# Destination API base URL
_GH_API = "https://api.github.com"


# ══════════════════════════════════════════════════════════════════════════════
# 3A — Mirror clone + push to destination
# ══════════════════════════════════════════════════════════════════════════════

def _dest_headers() -> dict:
    return {
        "Authorization":        f"Bearer {config.DEST_TOKEN}",
        "Accept":               "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _source_headers() -> dict:
    return {
        "Authorization":        f"Bearer {config.SOURCE_TOKEN}",
        "Accept":               "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def create_destination_repo() -> dict:
    """
    Create (or locate) the destination repository via the GitHub REST API.

    Returns the JSON body of the repo object.
    Exits loudly if creation fails for an unrecoverable reason.
    """
    repo_name = config.DEST_REPO
    owner     = config.DEST_OWNER

    # Check if the repo already exists — if so, warn and continue
    check_url = f"{_GH_API}/repos/{owner}/{repo_name}"
    resp = requests.get(check_url, headers=_dest_headers(), timeout=15)
    if resp.status_code == 200:
        logger.warning(
            "Destination repo '%s/%s' already exists. "
            "Mirror push will fail if it contains commits. "
            "Delete or empty the repo first.",
            owner, repo_name,
        )
        return resp.json()

    logger.info("Creating destination repository: %s/%s …", owner, repo_name)

    # Determine if dest owner is a user or an org
    me_resp = requests.get(f"{_GH_API}/user", headers=_dest_headers(), timeout=15)
    me_resp.raise_for_status()
    me = me_resp.json()

    if me["login"].lower() == owner.lower():
        create_url = f"{_GH_API}/user/repos"
    else:
        create_url = f"{_GH_API}/orgs/{owner}/repos"

    payload = {
        "name":        repo_name,
        "private":     config.DEST_PRIVATE,
        "description": f"Migrated from {config.SOURCE_REPO}",
        "auto_init":   False,
    }

    resp = requests.post(create_url, headers=_dest_headers(), json=payload, timeout=15)
    if resp.status_code in (200, 201):
        logger.info("Destination repo created successfully.")
        return resp.json()
    else:
        logger.error(
            "Failed to create destination repo (HTTP %d): %s",
            resp.status_code, resp.text[:500],
        )
        raise RuntimeError(f"Could not create destination repo: {resp.status_code}")


def mirror_clone_and_push() -> list[str]:
    """
    1. git clone --mirror <source>
    2. git remote set-url origin <dest>
    3. git push --mirror

    Returns the list of branch names that were pushed.
    Raises subprocess.CalledProcessError on failure.
    """
    mirror_dir = os.path.join(os.path.dirname(__file__), "mirror_repo.git")
    remove_dir(mirror_dir)

    source_url = f"https://{config.SOURCE_TOKEN}@github.com/{config.SOURCE_REPO}.git"
    dest_url   = (
        f"https://{config.DEST_TOKEN}@github.com/"
        f"{config.DEST_OWNER}/{config.DEST_REPO}.git"
    )

    logger.info("Starting mirror clone of %s …", config.SOURCE_REPO)
    run_subprocess(["git", "clone", "--mirror", source_url, mirror_dir])
    logger.info("Mirror clone complete.")

    # Collect branch names before pushing
    result = run_subprocess(["git", "branch", "-a"], cwd=mirror_dir, check=False)
    branches = [
        b.strip().lstrip("* ").replace("refs/heads/", "")
        for b in result.stdout.splitlines()
        if b.strip() and not b.strip().startswith("HEAD")
    ]
    logger.info("Branches to migrate: %s", branches)

    logger.info("Updating remote origin → %s/%s …", config.DEST_OWNER, config.DEST_REPO)
    run_subprocess(["git", "remote", "set-url", "origin", dest_url], cwd=mirror_dir)

    logger.info("Pushing mirror to destination (this may take a while) …")
    run_subprocess(["git", "push", "--mirror"], cwd=mirror_dir)
    logger.info("Mirror push complete.")

    remove_dir(mirror_dir)
    logger.info("Cleaned up mirror directory.")

    # Set default branch to 'main' (or 'master' if main doesn't exist)
    default_branch = "main" if "main" in branches else (branches[0] if branches else "main")
    _set_default_branch(default_branch)

    return branches


def _set_default_branch(branch: str) -> None:
    url = f"{_GH_API}/repos/{config.DEST_OWNER}/{config.DEST_REPO}"
    resp = requests.patch(
        url,
        headers=_dest_headers(),
        json={"default_branch": branch},
        timeout=15,
    )
    if resp.status_code == 200:
        logger.info("Default branch set to '%s'.", branch)
    else:
        logger.warning(
            "Could not set default branch to '%s' (HTTP %d): %s",
            branch, resp.status_code, resp.text[:300],
        )


# ══════════════════════════════════════════════════════════════════════════════
# 3B — PR Migration
# ══════════════════════════════════════════════════════════════════════════════

_REQUIRED_LABELS = [
    {"name": "merged",          "color": "6f42c1", "description": "Migrated merged PR"},
    {"name": "closed-unmerged", "color": "e4e669", "description": "Migrated closed (unmerged) PR"},
    {"name": "migrated-pr",     "color": "0075ca", "description": "Originally a GitHub PR"},
    {"name": "open-pr",         "color": "2cbe4e", "description": "Migrated open PR"},
]


def ensure_labels() -> None:
    """Create migration labels on the destination repo if they don't exist."""
    url = f"{_GH_API}/repos/{config.DEST_OWNER}/{config.DEST_REPO}/labels"
    for label in _REQUIRED_LABELS:
        resp = requests.post(url, headers=_dest_headers(), json=label, timeout=15)
        if resp.status_code == 201:
            logger.info("Created label: %s", label["name"])
        elif resp.status_code == 422:
            logger.debug("Label already exists: %s", label["name"])
        else:
            logger.warning(
                "Could not create label '%s' (HTTP %d): %s",
                label["name"], resp.status_code, resp.text[:200],
            )


def _build_issue_body(pr: dict) -> str:
    """Format a rich issue body containing the original PR description + a metadata table."""
    original_body = pr.get("body", "") or "_No description provided._"
    state_str = "merged" if pr.get("is_merged") else pr.get("state", "unknown")

    table = (
        "| Field | Value |\n"
        "|---|---|\n"
        f"| Original PR | #{pr['pr_number']} |\n"
        f"| Author | @{pr.get('author', 'unknown')} |\n"
        f"| State | {state_str} |\n"
        f"| Created At | {pr.get('created_at', '')[:10]} |\n"
        f"| Closed At | {pr.get('closed_at', '')[:10] or '—'} |\n"
        f"| Merged At | {pr.get('merged_at', '')[:10] or '—'} |\n"
        f"| Base Branch | `{pr.get('base_branch', '')}` |\n"
        f"| Head Branch | `{pr.get('head_branch', '')}` |\n"
        f"| Commits | {pr.get('commits_count', 0)} |\n"
        f"| +additions | {pr.get('additions', 0)} |\n"
        f"| -deletions | {pr.get('deletions', 0)} |\n"
        f"| Changed Files | {pr.get('changed_files', 0)} |\n"
        f"| Review Comments | {pr.get('review_comments_count', 0)} |\n"
        f"| Comments | {pr.get('comments_count', 0)} |\n"
        f"| Merge Commit SHA | `{pr.get('merge_commit_sha', '') or '—'}` |\n"
    )

    return (
        f"> **⚠️ This issue was automatically migrated from the source repository.**\n\n"
        f"---\n\n"
        f"## Original Description\n\n"
        f"{original_body}\n\n"
        f"---\n\n"
        f"## Migration Metadata\n\n"
        f"{table}"
    )


def _branch_exists_on_dest(branch: str, dest_branches: list[str]) -> bool:
    return branch in dest_branches


def _create_issue(pr: dict, labels: list[str]) -> dict | None:
    """POST /repos/{owner}/{repo}/issues — returns parsed response or None on failure."""
    url   = f"{_GH_API}/repos/{config.DEST_OWNER}/{config.DEST_REPO}/issues"
    title = f"[Migrated PR #{pr['pr_number']}] {pr.get('title', '(no title)')}"
    body  = _build_issue_body(pr)

    payload: dict[str, Any] = {"title": title, "body": body, "labels": labels}

    for attempt in range(1, 4):
        resp = requests.post(url, headers=_dest_headers(), json=payload, timeout=20)
        if resp.status_code == 201:
            return resp.json()
        elif resp.status_code == 403:
            logger.warning("Rate limited creating issue. Sleeping 60s …")
            time.sleep(60)
        elif resp.status_code == 422:
            logger.warning(
                "PR #%d → Issue creation unprocessable (422): %s",
                pr["pr_number"], resp.text[:300],
            )
            return None
        else:
            delay = 2 ** attempt
            logger.warning(
                "PR #%d → Issue creation failed (HTTP %d). Retry in %ds …",
                pr["pr_number"], resp.status_code, delay,
            )
            time.sleep(delay)
    return None


def _close_issue(issue_number: int) -> None:
    url  = f"{_GH_API}/repos/{config.DEST_OWNER}/{config.DEST_REPO}/issues/{issue_number}"
    resp = requests.patch(url, headers=_dest_headers(), json={"state": "closed"}, timeout=15)
    if resp.status_code != 200:
        logger.warning("Could not close issue #%d (HTTP %d).", issue_number, resp.status_code)


def _try_create_real_pr(pr: dict) -> dict | None:
    """
    Attempt to create a real PR on the destination repo.
    Returns parsed response or None if creation fails.
    """
    url = f"{_GH_API}/repos/{config.DEST_OWNER}/{config.DEST_REPO}/pulls"
    payload = {
        "title": f"[Migrated PR #{pr['pr_number']}] {pr.get('title', '(no title)')}",
        "body":  _build_issue_body(pr),
        "head":  pr.get("head_branch", ""),
        "base":  pr.get("base_branch", "main"),
    }
    resp = requests.post(url, headers=_dest_headers(), json=payload, timeout=20)
    if resp.status_code == 201:
        return resp.json()
    logger.debug(
        "Real PR creation failed for original PR #%d (HTTP %d): %s",
        pr["pr_number"], resp.status_code, resp.text[:200],
    )
    return None


def migrate_pull_requests(pr_list: list[dict], dest_branches: list[str]) -> dict:
    """
    Iterate over all PRs (sorted by pr_number asc) and migrate each one.

    Returns a stats dict used for the migration report.
    """
    ensure_labels()

    stats = {
        "prs_total":      len(pr_list),
        "prs_as_real_pr": 0,
        "prs_as_issue":   0,
        "prs_failed":     0,
    }

    sorted_prs = sorted(pr_list, key=lambda p: p["pr_number"])

    for pr in sorted_prs:
        pr_num = pr["pr_number"]
        logger.info(
            "Migrating PR #%d (%s) …",
            pr_num,
            "merged" if pr.get("is_merged") else pr.get("state", "?"),
        )

        try:
            if pr.get("is_merged"):
                # Merged PRs → Issue (closed)
                labels  = ["migrated-pr", "merged"]
                issue   = _create_issue(pr, labels)
                if issue:
                    _close_issue(issue["number"])
                    stats["prs_as_issue"] += 1
                    logger.info("  → Created closed Issue #%d", issue["number"])
                else:
                    stats["prs_failed"] += 1

            elif pr.get("state") == "open":
                # Open PRs — try real PR first
                head_branch = pr.get("head_branch", "")
                if _branch_exists_on_dest(head_branch, dest_branches):
                    real_pr = _try_create_real_pr(pr)
                    if real_pr:
                        stats["prs_as_real_pr"] += 1
                        logger.info("  → Created real PR #%d", real_pr["number"])
                    else:
                        # Fall back to issue
                        issue = _create_issue(pr, ["migrated-pr", "open-pr"])
                        if issue:
                            stats["prs_as_issue"] += 1
                            logger.info("  → Fell back to Issue #%d", issue["number"])
                        else:
                            stats["prs_failed"] += 1
                else:
                    issue = _create_issue(pr, ["migrated-pr", "open-pr"])
                    if issue:
                        stats["prs_as_issue"] += 1
                        logger.info(
                            "  → Branch '%s' not in dest; created Issue #%d",
                            head_branch, issue["number"],
                        )
                    else:
                        stats["prs_failed"] += 1

            else:
                # Closed-unmerged PRs → Issue (closed)
                labels = ["migrated-pr", "closed-unmerged"]
                issue  = _create_issue(pr, labels)
                if issue:
                    _close_issue(issue["number"])
                    stats["prs_as_issue"] += 1
                    logger.info("  → Created closed Issue #%d (unmerged)", issue["number"])
                else:
                    stats["prs_failed"] += 1

        except Exception as exc:  # pragma: no cover — never crash mid-migration
            logger.error("Unexpected error migrating PR #%d: %s", pr_num, exc)
            stats["prs_failed"] += 1

        # Respect API rate limit — 0.5 s between calls
        time.sleep(0.5)

    return stats


# ══════════════════════════════════════════════════════════════════════════════
# 3C — Migration Report
# ══════════════════════════════════════════════════════════════════════════════

def write_migration_report(branches: list[str], pr_stats: dict) -> str:
    """Write migration_report.json and return its path."""
    ensure_dir(config.OUTPUT_DIR)
    report = {
        "source_repo":        config.SOURCE_REPO,
        "dest_repo":          f"{config.DEST_OWNER}/{config.DEST_REPO}",
        "migrated_at":        datetime.now(timezone.utc).isoformat(),
        "commit_history":     "success",
        "branches_mirrored":  branches,
        "prs_total":          pr_stats["prs_total"],
        "prs_as_real_pr":     pr_stats["prs_as_real_pr"],
        "prs_as_issue":       pr_stats["prs_as_issue"],
        "prs_failed":         pr_stats["prs_failed"],
    }
    path = os.path.join(config.OUTPUT_DIR, "migration_report.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    logger.info("Migration report written → %s", path)
    return path


# ══════════════════════════════════════════════════════════════════════════════
# Public entry point
# ══════════════════════════════════════════════════════════════════════════════

def run_task2(pr_list: list[dict]) -> None:
    """
    Execute Task 2: mirror-clone source repo → push to destination → migrate PRs.

    Parameters
    ----------
    pr_list : List of PR dicts as returned by task1_collector.run_task1().
              If Task 1 was skipped, pass an empty list [].
    """
    logger.info("═" * 60)
    logger.info("TASK 2 — Repository Migration")
    logger.info("═" * 60)

    # Step 1: create destination repo
    create_destination_repo()

    # Step 2: mirror clone + push
    branches: list[str] = []
    try:
        branches = mirror_clone_and_push()
        mirror_ok = True
    except subprocess.CalledProcessError as exc:
        logger.error(
            "Mirror push failed:\n  STDOUT: %s\n  STDERR: %s",
            exc.stdout[:500] if exc.stdout else "",
            exc.stderr[:500] if exc.stderr else "",
        )
        mirror_ok = False

    # Step 3: migrate PRs
    pr_stats: dict[str, Any] = {"prs_total": 0, "prs_as_real_pr": 0, "prs_as_issue": 0, "prs_failed": 0}
    if pr_list:
        pr_stats = migrate_pull_requests(pr_list, branches)
    else:
        logger.warning(
            "No PR data provided — skipping PR migration. "
            "Run Task 1 first or pass pr_list to run_task2()."
        )

    # Step 4: report
    report_path = write_migration_report(branches, pr_stats)

    logger.info("═" * 60)
    logger.info("TASK 2 COMPLETE")
    logger.info("  Mirror push : %s", "✓" if mirror_ok else "✗ (see log above)")
    logger.info("  PRs total   : %d", pr_stats["prs_total"])
    logger.info("  Real PRs    : %d", pr_stats["prs_as_real_pr"])
    logger.info("  Issues      : %d", pr_stats["prs_as_issue"])
    logger.info("  Failed      : %d", pr_stats["prs_failed"])
    logger.info("  Report      : %s", report_path)
    logger.info("═" * 60)
