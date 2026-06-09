"""
platforms/gitlab_adapter.py
────────────────────────────
GitLab implementation of PlatformAdapter.

Uses the python-gitlab SDK (gitlab.Gitlab) for all API calls.
Falls back to git-level helpers from utils.py for clone / LOC via clone.

Normalisation notes
───────────────────
- GitLab MR state "opened" is mapped to "open" at the adapter boundary.
- Additions/deletions require a separate /changes API call per MR (100ms delay).
- Clone URL format: https://oauth2:TOKEN@gitlab.com/namespace/project.git
- Supports self-hosted GitLab via GITLAB_BASE_URL config value.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any

from platforms.base import PlatformAdapter

logger = logging.getLogger(__name__)

# GitLab MR state → normalised state
_STATE_MAP: dict[str, str] = {
    "opened": "open",
    "closed": "closed",
    "merged": "merged",
    "locked": "closed",
}


class GitLabAdapter(PlatformAdapter):
    """
    Full GitLab implementation.

    Parameters
    ----------
    cfg : RunConfig instance assembled by the GUI dialogs.
          Uses: source_token, source_repo, source_base_url,
                dest_token, dest_owner, dest_repo, dest_base_url, dest_private.
    """

    def __init__(self, cfg) -> None:
        self._cfg = cfg
        self._gl_src: Any = None    # python-gitlab client for source
        self._gl_dst: Any = None    # python-gitlab client for destination
        self._src_project: Any = None  # cached source project object
        self._src_project_id: int | None = None

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_source_client(self):
        """Lazy-init the python-gitlab client for the source instance."""
        if self._gl_src is None:
            import gitlab
            self._gl_src = gitlab.Gitlab(
                self._cfg.source_base_url,
                private_token=self._cfg.source_token,
            )
        return self._gl_src

    def _get_dest_client(self):
        """Lazy-init the python-gitlab client for the destination instance."""
        if self._gl_dst is None:
            import gitlab
            self._gl_dst = gitlab.Gitlab(
                self._cfg.dest_base_url,
                private_token=self._cfg.dest_token,
            )
        return self._gl_dst

    def _resolve_source_project(self):
        """
        Resolve source repo name to a python-gitlab Project object (once).
        GitLab APIs prefer numeric project IDs.  The SDK accepts
        'namespace%2Fproject' (URL-encoded) or numeric ID — we let it handle it.
        """
        if self._src_project is None:
            gl = self._get_source_client()
            # The SDK accepts "namespace/project" directly — it URL-encodes internally.
            try:
                self._src_project = gl.projects.get(self._cfg.source_repo)
                self._src_project_id = self._src_project.id
                logger.info(
                    "Resolved source project '%s' → ID %d",
                    self._cfg.source_repo,
                    self._src_project_id,
                )
            except Exception as exc:
                logger.error(
                    "Could not resolve GitLab project '%s': %s",
                    self._cfg.source_repo, exc,
                )
                sys.exit(1)
        return self._src_project

    def _normalise_mr(self, mr_data: dict, changes: dict | None = None) -> dict:
        """
        Map a raw GitLab MR dict (from REST API) to the shared PR_SCHEMA.

        Parameters
        ----------
        mr_data : Raw MR dict from the GitLab API.
        changes : Optional dict from the /changes endpoint
                  (contains 'changes' list with diff stats).
        """
        raw_state = mr_data.get("state", "opened")
        normalised_state = _STATE_MAP.get(raw_state, raw_state)
        is_merged = normalised_state == "merged"

        labels   = mr_data.get("labels", [])
        assignees = [
            a.get("username", "") for a in (mr_data.get("assignees") or [])
        ]

        # Additions / deletions: only available via /changes
        additions    = 0
        deletions    = 0
        changed_files = 0
        if changes:
            diffs = changes.get("changes") or []
            changed_files = len(diffs)
            for diff in diffs:
                diff_text = diff.get("diff", "")
                for line in diff_text.splitlines():
                    if line.startswith("+") and not line.startswith("+++"):
                        additions += 1
                    elif line.startswith("-") and not line.startswith("---"):
                        deletions += 1

        author = ""
        if mr_data.get("author"):
            author = mr_data["author"].get("username", "")

        return {
            "pr_number":             mr_data.get("iid", 0),
            "title":                 mr_data.get("title", ""),
            "state":                 normalised_state,
            "is_merged":             is_merged,
            "author":                author,
            "created_at":            mr_data.get("created_at", ""),
            "updated_at":            mr_data.get("updated_at", ""),
            "closed_at":             mr_data.get("closed_at") or "",
            "merged_at":             mr_data.get("merged_at") or "",
            "base_branch":           mr_data.get("target_branch", ""),
            "head_branch":           mr_data.get("source_branch", ""),
            "commits_count":         mr_data.get("commits_count") or 0,
            "additions":             additions,
            "deletions":             deletions,
            "changed_files":         changed_files,
            "body":                  mr_data.get("description") or "",
            "labels":                json.dumps(labels),
            "assignees":             json.dumps(assignees),
            "review_comments_count": 0,   # GitLab discussion notes — not directly mapped
            "comments_count":        mr_data.get("user_notes_count") or 0,
            "merge_commit_sha":      mr_data.get("merge_commit_sha") or "",
        }

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def platform_name(self) -> str:
        return "GitLab"

    @property
    def platform_emoji(self) -> str:
        return "🦊"

    # ── Token validation ──────────────────────────────────────────────────────
    # Tokens are validated inline by the GUI credential dialogs before RunConfig
    # is assembled.  validate_tokens() is inherited as a no-op from PlatformAdapter.

    # ── Repository discovery ──────────────────────────────────────────────────

    def list_accessible_repos(self) -> list[str]:
        """Return all projects the source token is a member of."""
        gl = self._get_source_client()
        projects = gl.projects.list(membership=True, all=True)
        return [p.path_with_namespace for p in projects]

    # ── PR / MR collection ────────────────────────────────────────────────────

    def fetch_pull_requests(self) -> list[dict[str, Any]]:
        """
        Fetch all Merge Requests from the source project.

        Each MR requires a second API call to /changes for diff stats
        (additions, deletions, changed_files).  A 100 ms delay is inserted
        between calls to stay polite.
        """
        project = self._resolve_source_project()
        logger.info(
            "Fetching merge requests from GitLab project '%s' (ID %d) …",
            self._cfg.source_repo, project.id,
        )

        # Paginated fetch — python-gitlab handles pagination automatically
        mrs = project.mergerequests.list(
            state="all",
            order_by="created_at",
            sort="asc",
            all=True,
        )
        logger.info("Found %d merge requests total.", len(mrs))

        pr_list: list[dict] = []
        for idx, mr in enumerate(mrs, start=1):
            if idx % 50 == 0:
                logger.info("  … normalised %d MRs so far", idx)

            mr_data = mr.asdict() if hasattr(mr, "asdict") else mr._attrs  # type: ignore[attr-defined]

            # Fetch diff stats (separate API call per MR)
            changes = None
            try:
                mr_with_changes = project.mergerequests.get(mr.iid)
                changes = {"changes": mr_with_changes.changes().get("changes", [])}
            except Exception as exc:
                logger.debug("Could not fetch changes for MR !%s: %s", mr.iid, exc)

            pr_list.append(self._normalise_mr(mr_data, changes))
            time.sleep(0.1)  # polite API usage

        logger.info("Collected and normalised %d merge requests.", len(pr_list))
        return pr_list

    # ── LOC calculation ───────────────────────────────────────────────────────

    def calculate_loc(self, method: str) -> dict[str, Any]:
        """
        Calculate LOC.

        "api"   → GET /projects/{id}/languages (byte counts — same caveat as GitHub).
        "clone" → reuse _loc_via_clone() from task1_collector (git-agnostic).
        """
        if method == "clone":
            import task1_collector
            clone_url = self._build_clone_url(self._cfg.source_repo, source=True)
            return task1_collector._loc_via_clone(clone_url)
        else:
            return self._loc_via_api()

    def _loc_via_api(self) -> dict[str, Any]:
        """GET /api/v4/projects/{id}/languages — returns byte counts per language."""
        project = self._resolve_source_project()
        logger.info("Calculating LOC via GitLab API (byte approximation) …")
        try:
            lang_data: dict = project.languages()
        except Exception as exc:
            logger.error("Failed to fetch language data from GitLab: %s", exc)
            return {"method": "gitlab_api_bytes", "error": str(exc), "languages": []}

        # GitLab returns percentages, not raw bytes — we store percentages as-is
        total = sum(lang_data.values())
        languages = [
            {"language": lang, "percent": round(pct, 2)}
            for lang, pct in sorted(lang_data.items(), key=lambda x: -x[1])
        ]
        logger.info("GitLab language breakdown: %s", lang_data)
        return {
            "method":    "gitlab_api_percent",
            "note":      (
                "GitLab API returns percentage of code per language, not byte counts. "
                "Use 'clone' method with cloc for true line counts."
            ),
            "total_languages": len(languages),
            "languages": languages,
        }

    # ── Clone URL builder ─────────────────────────────────────────────────────

    def _build_clone_url(self, repo_path: str, source: bool = True) -> str:
        """
        Build a token-authenticated HTTPS clone URL for GitLab.

        Format: https://oauth2:TOKEN@gitlab.com/namespace/project.git
        This works for both gitlab.com and self-hosted instances.
        """
        token = self._cfg.source_token if source else self._cfg.dest_token
        base  = self._cfg.source_base_url if source else self._cfg.dest_base_url
        # Strip scheme so we can re-inject credentials
        if base.startswith("https://"):
            host = base[len("https://"):]
        elif base.startswith("http://"):
            host = base[len("http://"):]
        else:
            host = base

        return f"https://oauth2:{token}@{host}/{repo_path}.git"

    # ── Repository cloning ────────────────────────────────────────────────────

    def clone_repo(self, dest_path: str) -> None:
        """Mirror-clone source GitLab project to dest_path."""
        from utils import run_subprocess
        clone_url = self._build_clone_url(self._cfg.source_repo, source=True)
        logger.info("Mirror-cloning GitLab project '%s' …", self._cfg.source_repo)
        run_subprocess(["git", "clone", "--mirror", clone_url, dest_path])
        logger.info("Mirror clone complete.")

    # ── Full migration ────────────────────────────────────────────────────────

    def migrate_repo(self, pr_list: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Full GitLab-to-GitLab migration:
          1. Create destination project.
          2. Mirror-clone source → push to destination.
          3. Migrate MRs as Issues on destination (MR creation requires
             branches to exist on dest, which they will after the push).
          4. Write migration_report.json.

        Returns migration stats dict.
        """
        import json
        import subprocess
        from datetime import datetime, timezone
        from utils import ensure_dir, remove_dir, run_subprocess

        # Step 1 — create destination project
        dest_project = self._create_dest_project()

        # Step 2 — mirror clone + push
        mirror_dir = os.path.join(os.path.dirname(__file__), "..", "mirror_repo.git")
        mirror_dir = os.path.abspath(mirror_dir)
        remove_dir(mirror_dir)
        branches: list[str] = []
        mirror_ok = False

        try:
            self.clone_repo(mirror_dir)

            # Collect branch names from the mirror
            result = run_subprocess(
                ["git", "branch", "-a"], cwd=mirror_dir, check=False
            )
            branches = [
                b.strip().lstrip("* ").replace("refs/heads/", "")
                for b in result.stdout.splitlines()
                if b.strip() and not b.strip().startswith("HEAD")
            ]
            logger.info("Branches to push: %s", branches)

            # Push mirror to destination
            dest_clone_url = self._build_clone_url(
                f"{self._cfg.dest_owner}/{self._cfg.dest_repo}", source=False
            )
            check_remote = run_subprocess(
                ["git", "remote"], cwd=mirror_dir, check=False
            )
            if "origin" in check_remote.stdout.splitlines():
                run_subprocess(
                    ["git", "remote", "set-url", "origin", dest_clone_url],
                    cwd=mirror_dir,
                )
            else:
                run_subprocess(
                    ["git", "remote", "add", "origin", dest_clone_url],
                    cwd=mirror_dir,
                )

            logger.info("Pushing mirror to destination GitLab project …")
            push_result = run_subprocess(
                ["git", "push", "--mirror", "--force"],
                cwd=mirror_dir,
                check=False,
            )
            if push_result.returncode != 0:
                raise subprocess.CalledProcessError(
                    push_result.returncode,
                    "git push --mirror",
                    push_result.stdout,
                    push_result.stderr,
                )
            logger.info("Mirror push complete ✓")
            mirror_ok = True

        except subprocess.CalledProcessError as exc:
            logger.error(
                "Mirror push failed:\n  STDOUT: %s\n  STDERR: %s",
                (exc.stdout or "")[:800],
                (exc.stderr or "")[:800],
            )
        finally:
            remove_dir(mirror_dir)
            logger.info("Cleaned up mirror directory.")

        # Step 3 — migrate MRs as Issues on destination
        stats: dict[str, Any] = {
            "prs_total":      len(pr_list),
            "prs_as_real_pr": 0,
            "prs_as_issue":   0,
            "prs_failed":     0,
        }
        if pr_list and dest_project:
            stats = self._migrate_mrs(pr_list, dest_project, branches)

        # Step 4 — write migration report
        import config as _config
        ensure_dir(_config.OUTPUT_DIR)
        report = {
            "platform":           "gitlab",
            "source_repo":        self._cfg.source_repo,
            "dest_repo":          f"{self._cfg.dest_owner}/{self._cfg.dest_repo}",
            "migrated_at":        datetime.now(timezone.utc).isoformat(),
            "commit_history":     "success" if mirror_ok else "failed",
            "branches_mirrored":  branches,
            "prs_total":          stats["prs_total"],
            "prs_as_real_pr":     stats["prs_as_real_pr"],
            "prs_as_issue":       stats["prs_as_issue"],
            "prs_failed":         stats["prs_failed"],
        }
        report_path = os.path.join(_config.OUTPUT_DIR, "migration_report.json")
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        logger.info("Migration report written → %s", report_path)

        logger.info("═" * 60)
        logger.info("GITLAB MIGRATION COMPLETE")
        logger.info("  Mirror push  : %s", "✓" if mirror_ok else "✗")
        logger.info("  Branches     : %s", branches)
        logger.info(
            "  MR migration : %d issues, %d real MRs, %d failed",
            stats["prs_as_issue"], stats["prs_as_real_pr"], stats["prs_failed"],
        )
        logger.info("  Report       : %s", report_path)
        logger.info("═" * 60)

        return stats

    # ── Destination project creation ──────────────────────────────────────────

    def _create_dest_project(self):
        """Create (or locate) the destination GitLab project."""
        gl_dst = self._get_dest_client()
        dest_path = f"{self._cfg.dest_owner}/{self._cfg.dest_repo}"

        # Check if it already exists
        try:
            project = gl_dst.projects.get(dest_path)
            logger.warning(
                "Destination project '%s' already exists. "
                "Mirror push will force-overwrite any existing commits.",
                dest_path,
            )
            return project
        except Exception:
            pass  # Not found — create it

        logger.info("Creating destination GitLab project: %s …", dest_path)

        # Resolve namespace ID for the destination owner
        namespace_id = self._resolve_namespace_id(gl_dst, self._cfg.dest_owner)

        visibility = "private" if self._cfg.dest_private else "public"
        project_data = {
            "name":        self._cfg.dest_repo,
            "path":        self._cfg.dest_repo,
            "visibility":  visibility,
            "description": f"Migrated from {self._cfg.source_repo}",
            "initialize_with_readme": False,
        }
        if namespace_id is not None:
            project_data["namespace_id"] = namespace_id

        try:
            project = gl_dst.projects.create(project_data)
            logger.info(
                "Destination project created: %s (ID %d)", project.path_with_namespace, project.id
            )
            return project
        except Exception as exc:
            logger.error("Failed to create destination GitLab project: %s", exc)
            return None

    def _resolve_namespace_id(self, gl, namespace: str) -> int | None:
        """Resolve a group/user namespace name to its numeric ID."""
        try:
            namespaces = gl.namespaces.list(search=namespace)
            for ns in namespaces:
                if ns.path == namespace or ns.full_path == namespace:
                    return ns.id
        except Exception as exc:
            logger.debug("Namespace resolution failed: %s", exc)
        return None

    # ── MR migration (Issues on destination) ─────────────────────────────────

    def _migrate_mrs(
        self,
        pr_list: list[dict],
        dest_project,
        dest_branches: list[str],
    ) -> dict[str, Any]:
        """
        Iterate over all normalised MR dicts and create Issues on the
        destination project.  Attempts a real MR for open MRs when both
        source and target branches exist on destination.
        """
        self._ensure_dest_labels(dest_project)

        stats: dict[str, Any] = {
            "prs_total":      len(pr_list),
            "prs_as_real_pr": 0,
            "prs_as_issue":   0,
            "prs_failed":     0,
        }

        sorted_mrs = sorted(pr_list, key=lambda p: p["pr_number"])

        for pr in sorted_mrs:
            mr_num = pr["pr_number"]
            state  = pr.get("state", "?")
            logger.info("Migrating MR !%d (%s) …", mr_num, state)

            try:
                if pr.get("is_merged") or pr.get("state") == "closed":
                    issue = self._create_dest_issue(dest_project, pr)
                    if issue:
                        # Close the issue
                        issue.state_event = "close"
                        issue.save()
                        stats["prs_as_issue"] += 1
                        logger.info("  → Created closed Issue #%d", issue.iid)
                    else:
                        stats["prs_failed"] += 1

                elif pr.get("state") == "open":
                    head  = pr.get("head_branch", "")
                    base  = pr.get("base_branch", "")
                    # Attempt real MR if both branches exist on destination
                    if head in dest_branches and base in dest_branches:
                        mr = self._try_create_real_mr(dest_project, pr)
                        if mr:
                            stats["prs_as_real_pr"] += 1
                            logger.info("  → Created real MR !%d", mr.iid)
                        else:
                            issue = self._create_dest_issue(dest_project, pr)
                            if issue:
                                stats["prs_as_issue"] += 1
                                logger.info("  → Fell back to Issue #%d", issue.iid)
                            else:
                                stats["prs_failed"] += 1
                    else:
                        issue = self._create_dest_issue(dest_project, pr)
                        if issue:
                            stats["prs_as_issue"] += 1
                            logger.info(
                                "  → Branch '%s' not in dest; created Issue #%d",
                                head, issue.iid,
                            )
                        else:
                            stats["prs_failed"] += 1

                else:
                    # Fallback for any unexpected state
                    issue = self._create_dest_issue(dest_project, pr)
                    if issue:
                        stats["prs_as_issue"] += 1
                    else:
                        stats["prs_failed"] += 1

            except Exception as exc:
                logger.error("Unexpected error migrating MR !%d: %s", mr_num, exc)
                stats["prs_failed"] += 1

            time.sleep(0.5)  # polite API usage

        return stats

    def _build_issue_body(self, pr: dict) -> str:
        """Format a rich issue body with original MR description + metadata table."""
        original_body = pr.get("body", "") or "_No description provided._"
        state_str = "merged" if pr.get("is_merged") else pr.get("state", "unknown")

        table = (
            "| Field | Value |\n"
            "|---|---|\n"
            f"| Original MR | !{pr['pr_number']} |\n"
            f"| Author | @{pr.get('author', 'unknown')} |\n"
            f"| State | {state_str} |\n"
            f"| Created At | {pr.get('created_at', '')[:10]} |\n"
            f"| Closed At | {pr.get('closed_at', '')[:10] or '—'} |\n"
            f"| Merged At | {pr.get('merged_at', '')[:10] or '—'} |\n"
            f"| Target Branch | `{pr.get('base_branch', '')}` |\n"
            f"| Source Branch | `{pr.get('head_branch', '')}` |\n"
            f"| Commits | {pr.get('commits_count', 0)} |\n"
            f"| +additions | {pr.get('additions', 0)} |\n"
            f"| -deletions | {pr.get('deletions', 0)} |\n"
            f"| Changed Files | {pr.get('changed_files', 0)} |\n"
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

    def _create_dest_issue(self, dest_project, pr: dict):
        """Create a GitLab Issue on the destination project."""
        title = f"[Migrated MR !{pr['pr_number']}] {pr.get('title', '(no title)')}"
        body  = self._build_issue_body(pr)

        state_label = "merged" if pr.get("is_merged") else (
            "closed-unmerged" if pr.get("state") == "closed" else "open-mr"
        )

        for attempt in range(1, 4):
            try:
                issue = dest_project.issues.create({
                    "title":       title,
                    "description": body,
                    "labels":      ["migrated-mr", state_label],
                })
                return issue
            except Exception as exc:
                import gitlab
                if isinstance(exc, gitlab.exceptions.GitlabHttpError) and exc.response_code in (401, 403):
                    logger.error(
                        "MR !%d → Issue creation failed permanently with %d Forbidden/Unauthorized. "
                        "Please check if your token has 'api' scope and correct permissions.",
                        pr["pr_number"], exc.response_code
                    )
                    return None
                delay = 2 ** attempt
                logger.warning(
                    "MR !%d → Issue creation failed (attempt %d): %s. Retrying in %ds …",
                    pr["pr_number"], attempt, exc, delay,
                )
                time.sleep(delay)
        return None

    def _try_create_real_mr(self, dest_project, pr: dict):
        """Attempt to create a real GitLab MR on the destination project."""
        try:
            mr = dest_project.mergerequests.create({
                "title":         f"[Migrated MR !{pr['pr_number']}] {pr.get('title', '')}",
                "description":   self._build_issue_body(pr),
                "source_branch": pr.get("head_branch", ""),
                "target_branch": pr.get("base_branch", "main"),
            })
            return mr
        except Exception as exc:
            logger.debug(
                "Real MR creation failed for original MR !%d: %s",
                pr["pr_number"], exc,
            )
            return None

    def _ensure_dest_labels(self, dest_project) -> None:
        """Create migration labels on the destination project if they don't exist."""
        labels_to_create = [
            {"name": "migrated-mr",      "color": "#0075ca", "description": "Originally a GitLab MR"},
            {"name": "merged",           "color": "#6f42c1", "description": "Migrated merged MR"},
            {"name": "closed-unmerged",  "color": "#e4e669", "description": "Migrated closed (unmerged) MR"},
            {"name": "open-mr",          "color": "#2cbe4e", "description": "Migrated open MR"},
        ]
        for lbl in labels_to_create:
            try:
                dest_project.labels.create(lbl)
                logger.info("Created label: %s", lbl["name"])
            except Exception:
                logger.debug("Label already exists or could not be created: %s", lbl["name"])
