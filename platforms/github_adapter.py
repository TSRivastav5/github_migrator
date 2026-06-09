"""
platforms/github_adapter.py
────────────────────────────
GitHub implementation of PlatformAdapter.

Intentionally a *thin wrapper* around the existing task1_collector and
task2_migrator modules — almost no new logic lives here.  All heavy lifting
remains in those files so the GitHub flow is byte-for-byte identical to the
pre-adapter version.

Accepts a RunConfig instance built from the GUI dialogs.  Before delegating
to task2_migrator (which references config module-level vars), it injects
the RunConfig values into that module so its internal functions pick them up.
"""

from __future__ import annotations

import logging
from typing import Any

from platforms.base import PlatformAdapter

logger = logging.getLogger(__name__)


class GitHubAdapter(PlatformAdapter):
    """
    Delegates to existing task1_collector / task2_migrator modules.

    Parameters
    ----------
    cfg : RunConfig instance assembled by the GUI dialogs.
    """

    def __init__(self, cfg) -> None:
        self._cfg = cfg

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def platform_name(self) -> str:
        return "GitHub"

    @property
    def platform_emoji(self) -> str:
        return "🐙"

    # ── Config injection ──────────────────────────────────────────────────────

    def _inject_config(self) -> None:
        """
        Populate the config module's mutable shim vars from RunConfig.

        task2_migrator's internal functions (create_destination_repo,
        mirror_clone_and_push, write_migration_report, …) reference
        config.SOURCE_TOKEN / config.DEST_TOKEN etc. directly.  Injecting
        here keeps those files unchanged while supporting the dialog-based flow.
        """
        import config as _config
        _config.SOURCE_TOKEN = self._cfg.source_token
        _config.SOURCE_REPO  = self._cfg.source_repo
        _config.DEST_TOKEN   = self._cfg.dest_token
        _config.DEST_OWNER   = self._cfg.dest_owner
        _config.DEST_REPO    = self._cfg.dest_repo
        _config.DEST_PRIVATE = self._cfg.dest_private

    # ── Repository discovery ──────────────────────────────────────────────────

    def list_accessible_repos(self) -> list[str]:
        from github import Github
        g = Github(self._cfg.source_token, per_page=100)
        return [repo.full_name for repo in g.get_user().get_repos()]

    # ── PR collection ─────────────────────────────────────────────────────────

    def fetch_pull_requests(self) -> list[dict[str, Any]]:
        """
        Delegate to task1_collector.collect_pull_requests().
        The GitHub PR dict shape already matches PR_SCHEMA exactly.
        """
        from github import Github
        import task1_collector

        g    = Github(self._cfg.source_token, per_page=100)
        repo = g.get_repo(self._cfg.source_repo)
        return task1_collector.collect_pull_requests(g, repo)

    # ── LOC calculation ───────────────────────────────────────────────────────

    def calculate_loc(self, method: str) -> dict[str, Any]:
        import task1_collector
        from github import Github

        if method == "clone":
            source_url = (
                f"https://{self._cfg.source_token}@github.com"
                f"/{self._cfg.source_repo}.git"
            )
            return task1_collector._loc_via_clone(source_url)
        else:
            g    = Github(self._cfg.source_token, per_page=100)
            repo = g.get_repo(self._cfg.source_repo)
            return task1_collector._loc_via_api(repo)

    # ── Repository cloning ────────────────────────────────────────────────────

    def clone_repo(self, dest_path: str) -> None:
        from utils import run_subprocess
        source_url = (
            f"https://{self._cfg.source_token}@github.com"
            f"/{self._cfg.source_repo}.git"
        )
        run_subprocess(["git", "clone", "--mirror", source_url, dest_path])

    # ── Full migration ────────────────────────────────────────────────────────

    def migrate_repo(self, pr_list: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Inject RunConfig into config module vars, then delegate entirely to
        task2_migrator.run_task2() (which handles create repo, mirror push,
        PR migration, and report writing).
        """
        self._inject_config()

        import task2_migrator
        task2_migrator.run_task2(pr_list)
        return {"delegated_to": "task2_migrator.run_task2"}
