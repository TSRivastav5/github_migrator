"""
task1_collector.py
──────────────────
Phase 2: Collect all Pull Request metadata and Lines-of-Code information from
the source repository, then export everything to CSV and JSON files.

Usage (called from main.py — do not run directly):
    from task1_collector import run_task1
    run_task1(loc_method="api")   # or "clone"
"""

from __future__ import annotations

import csv
import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from typing import Any


from github import Github, GithubException

import config
from utils import (
    command_exists,
    ensure_dir,
    remove_dir,
    retry_github,
    run_subprocess,
    wait_for_rate_limit,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# 2A — Pull Request Collection
# ══════════════════════════════════════════════════════════════════════════════

def _safe_str(val: Any) -> str:
    """Convert a value to string, returning '' for None."""
    return str(val) if val is not None else ""


def _dt_str(dt) -> str:
    """Format a datetime object as an ISO-8601 string (UTC)."""
    if dt is None:
        return ""
    if hasattr(dt, "isoformat"):
        return dt.isoformat()
    return str(dt)


def collect_pull_requests(g: Github, repo) -> list[dict]:
    """
    Fetch all Pull Requests (open, closed, merged) from *repo* and return
    them as a list of dictionaries.

    Pagination is handled automatically; rate-limit exceptions are retried.
    """
    logger.info("Fetching pull requests from: %s …", config.SOURCE_REPO)
    pulls = repo.get_pulls(state="all", sort="created", direction="asc")

    # Materialise the paginated list — each page access may trigger a network call
    pr_list: list[dict] = []
    page_num = 0

    for pr in pulls:
        page_num += 1
        if page_num % 50 == 0:
            logger.info("  … collected %d PRs so far", page_num)

        try:
            labels   = [lb.name for lb in pr.labels]
            assignees = [a.login for a in pr.assignees]

            pr_dict = {
                "pr_number":            pr.number,
                "title":                pr.title,
                "state":                pr.state,          # open / closed
                "is_merged":            pr.merged,
                "author":               pr.user.login if pr.user else "",
                "created_at":           _dt_str(pr.created_at),
                "updated_at":           _dt_str(pr.updated_at),
                "closed_at":            _dt_str(pr.closed_at),
                "merged_at":            _dt_str(pr.merged_at),
                "base_branch":          pr.base.ref,
                "head_branch":          pr.head.ref,
                "commits_count":        pr.commits,
                "additions":            pr.additions,
                "deletions":            pr.deletions,
                "changed_files":        pr.changed_files,
                "body":                 pr.body or "",
                "labels":               json.dumps(labels),
                "assignees":            json.dumps(assignees),
                "review_comments_count": pr.review_comments,
                "comments_count":       pr.comments,
                "merge_commit_sha":     _safe_str(pr.merge_commit_sha),
            }
            pr_list.append(pr_dict)

        except GithubException as exc:
            logger.warning("Could not fetch full data for PR #%s: %s", pr.number, exc)
            time.sleep(0.5)
        except Exception as exc:  # pragma: no cover
            logger.warning("Unexpected error on PR #%s: %s", pr.number, exc)

        # Be polite to the API
        time.sleep(0.1)

    logger.info("Collected %d pull requests total.", len(pr_list))
    return pr_list


# ══════════════════════════════════════════════════════════════════════════════
# 2B — LOC Collection
# ══════════════════════════════════════════════════════════════════════════════

def _loc_via_api(repo) -> dict:
    """
    Use GitHub's language API to estimate code size (byte-based, NOT line-count).

    ⚠️  GitHub returns *byte counts*, not line counts.  This is documented in
        the output files and migration report.
    """
    logger.info("Calculating LOC via GitHub API (byte approximation) …")
    languages: dict[str, int] = repo.get_languages()
    total = sum(languages.values())
    result = {
        "method":  "github_api_bytes",
        "note":    (
            "GitHub API returns byte counts per language, NOT line counts. "
            "Use 'clone' method with cloc for true line counts."
        ),
        "total_bytes": total,
        "languages": [
            {"language": lang, "bytes": count}
            for lang, count in sorted(languages.items(), key=lambda x: -x[1])
        ],
    }
    logger.info("Language breakdown: %s", languages)
    logger.info("Total bytes across all languages: %d", total)
    return result


def _loc_via_clone(source_repo_url: str) -> dict:
    """
    Clone the repo locally (shallow) and run `cloc` to get true line counts.
    Falls back to `wc -l` via git ls-files if cloc is not installed.
    Cleans up the temp directory unconditionally.
    """
    temp_dir = os.path.join(os.path.dirname(__file__), "temp_loc_repo")
    remove_dir(temp_dir)  # clean any previous run

    logger.info("Cloning repository (depth=1) for LOC analysis …")
    try:
        run_subprocess(["git", "clone", "--depth=1", source_repo_url, temp_dir])
    except subprocess.CalledProcessError as exc:
        logger.error("Failed to clone repo for LOC: %s", exc.stderr[:500])
        remove_dir(temp_dir)
        return {"method": "clone_failed", "error": str(exc)}

    try:
        if command_exists("cloc"):
            return _run_cloc(temp_dir)
        else:
            logger.warning(
                "cloc not found on PATH. Falling back to git ls-files | wc -l. "
                "Install cloc for accurate per-language counts: "
                "  macOS:  brew install cloc\n"
                "  Linux:  sudo apt install cloc"
            )
            return _run_wc_fallback(temp_dir)
    finally:
        remove_dir(temp_dir)


def _run_cloc(repo_dir: str) -> dict:
    """Run cloc and parse its JSON output."""
    logger.info("Running cloc …")
    result = run_subprocess(
        ["cloc", repo_dir, "--json", "--quiet"],
        check=False,
    )
    if result.returncode != 0 and not result.stdout.strip():
        logger.warning("cloc returned non-zero exit code %d.", result.returncode)
        return {"method": "cloc", "error": "cloc failed", "raw_stderr": result.stderr[:500]}

    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        logger.error("Could not parse cloc JSON output: %s", exc)
        return {"method": "cloc", "error": "json_parse_failed", "raw_stdout": result.stdout[:1000]}

    languages = []
    total_code = 0
    for lang, data in raw.items():
        if lang in ("header", "SUM"):
            continue
        languages.append({
            "language": lang,
            "files":    data.get("nFiles", 0),
            "blank":    data.get("blank",  0),
            "comment":  data.get("comment", 0),
            "code":     data.get("code",   0),
        })
        total_code += data.get("code", 0)

    summary = raw.get("SUM", {})
    return {
        "method":     "cloc",
        "total_loc":  total_code,
        "total_files": summary.get("nFiles", 0),
        "total_blank": summary.get("blank", 0),
        "total_comment": summary.get("comment", 0),
        "languages":  sorted(languages, key=lambda x: -x["code"]),
    }


def _run_wc_fallback(repo_dir: str) -> dict:
    """Crude LOC count using git ls-files | xargs wc -l."""
    logger.info("Running git ls-files | xargs wc -l fallback …")
    try:
        ls_result = run_subprocess(["git", "ls-files"], cwd=repo_dir)
        files = [f for f in ls_result.stdout.splitlines() if f.strip()]
        if not files:
            return {"method": "wc_fallback", "total_loc": 0, "note": "No files found."}

        # wc -l can fail on binary files; we suppress errors and sum up totals
        total_lines = 0
        for filepath in files:
            full = os.path.join(repo_dir, filepath)
            try:
                with open(full, "rb") as fh:
                    total_lines += sum(1 for _ in fh)
            except Exception:
                pass

        return {
            "method":    "wc_fallback",
            "note":      "Line count via wc -l on all tracked files (no language breakdown).",
            "total_loc": total_lines,
        }
    except subprocess.CalledProcessError as exc:
        return {"method": "wc_fallback", "error": str(exc)}


# ══════════════════════════════════════════════════════════════════════════════
# 2C — Output Export
# ══════════════════════════════════════════════════════════════════════════════

PR_CSV_FIELDS = [
    "pr_number", "title", "state", "is_merged", "author",
    "created_at", "updated_at", "closed_at", "merged_at",
    "base_branch", "head_branch", "commits_count", "additions",
    "deletions", "changed_files", "review_comments_count",
    "comments_count", "merge_commit_sha", "labels", "assignees", "body",
]


def export_pr_data(pr_list: list[dict], output_dir: str) -> None:
    """Write pr_data.csv and pr_data.json."""
    ensure_dir(output_dir)

    # CSV
    csv_path = os.path.join(output_dir, "pr_data.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=PR_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(pr_list)
    logger.info("Wrote PR CSV  → %s", csv_path)

    # JSON
    json_path = os.path.join(output_dir, "pr_data.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(pr_list, fh, indent=2, default=str)
    logger.info("Wrote PR JSON → %s", json_path)


def export_loc_data(loc_data: dict, output_dir: str) -> None:
    """Write loc_data.csv and loc_data.json."""
    ensure_dir(output_dir)

    # JSON (always complete)
    json_path = os.path.join(output_dir, "loc_data.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(loc_data, fh, indent=2)
    logger.info("Wrote LOC JSON → %s", json_path)

    # CSV — language-level rows (may be empty for api/fallback methods)
    csv_path = os.path.join(output_dir, "loc_data.csv")
    languages: list[dict] = loc_data.get("languages", [])

    if languages:
        # cloc output has: language, files, blank, comment, code
        # api output has:  language, bytes
        fieldnames = list(languages[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(languages)
    else:
        # Write a minimal single-row CSV so the file always exists
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["method", "note", "total"])
            writer.writerow([
                loc_data.get("method", ""),
                loc_data.get("note", loc_data.get("error", "")),
                loc_data.get("total_loc", loc_data.get("total_bytes", "")),
            ])

    logger.info("Wrote LOC CSV  → %s", csv_path)


# ══════════════════════════════════════════════════════════════════════════════
# Public entry point
# ══════════════════════════════════════════════════════════════════════════════

def run_task1(loc_method: str = "api") -> tuple[list[dict], dict]:
    """
    Execute Task 1: collect PR data + LOC data, export to output/.

    Parameters
    ----------
    loc_method : "api"   → use GitHub language API (byte counts)
                 "clone" → clone locally and run cloc (true line counts)

    Returns
    -------
    (pr_list, loc_data) — raw data structures for reuse by Task 2.
    """
    logger.info("═" * 60)
    logger.info("TASK 1 — PR & LOC Collection  |  method=%s", loc_method)
    logger.info("═" * 60)

    g    = Github(config.SOURCE_TOKEN, per_page=100)
    repo = g.get_repo(config.SOURCE_REPO)

    # ── Pull Requests ──────────────────────────────────────────────────────────
    pr_list = collect_pull_requests(g, repo)
    export_pr_data(pr_list, config.OUTPUT_DIR)

    # ── LOC ───────────────────────────────────────────────────────────────────
    if loc_method == "clone":
        source_url = (
            f"https://{config.SOURCE_TOKEN}@github.com/{config.SOURCE_REPO}.git"
        )
        loc_data = _loc_via_clone(source_url)
    else:
        loc_data = _loc_via_api(repo)

    export_loc_data(loc_data, config.OUTPUT_DIR)

    logger.info("Task 1 complete. Output saved to: %s", config.OUTPUT_DIR)
    logger.info("═" * 60)
    return pr_list, loc_data
