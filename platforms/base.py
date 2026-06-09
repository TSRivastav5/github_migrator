"""
platforms/base.py
─────────────────
Abstract base class (interface contract) for all platform adapters.

Both GitHubAdapter and GitLabAdapter must implement every abstract method
defined here.  All methods work with the normalised PR_SCHEMA dict so that
all downstream logic (CSV writing, report generation, issue migration) works
identically regardless of the underlying platform.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


# ══════════════════════════════════════════════════════════════════════════════
# Normalised PR/MR data schema
# ══════════════════════════════════════════════════════════════════════════════

# Both GitHub PRs and GitLab MRs are normalised to this field set.
# All adapters MUST return dicts whose keys are a superset of this schema.
#
# Field names match the keys used by task1_collector.collect_pull_requests()
# so existing CSV/JSON export functions require zero changes.
#
# Key           Type            Notes
# ──────────── ─────────────── ─────────────────────────────────────────────
# pr_number    int             PR #42 or MR !42 (iid on GitLab)
# title        str
# state        str             "open" | "closed" | "merged"
# is_merged    bool
# author       str             login/username
# created_at   str             ISO-8601
# updated_at   str             ISO-8601
# closed_at    str             ISO-8601 or ""
# merged_at    str             ISO-8601 or ""
# base_branch  str             target_branch in GitLab
# head_branch  str             source_branch in GitLab
# commits_count int
# additions    int
# deletions    int
# changed_files int
# body         str             PR / MR description
# labels       str             JSON-encoded list of label names
# assignees    str             JSON-encoded list of login/username strings
# review_comments_count int
# comments_count int
# merge_commit_sha str         "" when not merged

PR_SCHEMA: dict[str, type] = {
    "pr_number":             int,
    "title":                 str,
    "state":                 str,
    "is_merged":             bool,
    "author":                str,
    "created_at":            str,
    "updated_at":            str,
    "closed_at":             str,
    "merged_at":             str,
    "base_branch":           str,
    "head_branch":           str,
    "commits_count":         int,
    "additions":             int,
    "deletions":             int,
    "changed_files":         int,
    "body":                  str,
    "labels":                str,   # JSON-encoded list
    "assignees":             str,   # JSON-encoded list
    "review_comments_count": int,
    "comments_count":        int,
    "merge_commit_sha":      str,
}


# ══════════════════════════════════════════════════════════════════════════════
# Abstract platform adapter
# ══════════════════════════════════════════════════════════════════════════════

class PlatformAdapter(ABC):
    """
    Interface contract every platform adapter must satisfy.

    Adapters are constructed with a platform-specific config object whose
    attributes follow the common naming convention:
      .source_token    str   API token for the source platform
      .source_repo     str   "owner/repo" or "namespace/project"
      .dest_token      str   API token for the destination
      .dest_owner      str   destination owner / namespace
      .dest_repo       str   destination repo / project name
      .private         bool  make destination private?
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Human-readable platform name, e.g. "GitHub" or "GitLab"."""

    @property
    @abstractmethod
    def platform_emoji(self) -> str:
        """Emoji for dialog display, e.g. "🐙" or "🦊"."""

    # ── Token / connectivity ──────────────────────────────────────────────────

    def validate_tokens(self) -> None:
        """
        Token validation is handled inline by the GUI credential dialogs
        (Dialog 1 & 2) before RunConfig is assembled.  Adapters may override
        this if programmatic validation is needed outside the GUI flow.
        """
        # No-op by default — GUI dialogs perform validation before adapter creation.

    # ── Repository discovery ──────────────────────────────────────────────────

    @abstractmethod
    def list_accessible_repos(self) -> list[str]:
        """
        Return a list of repo identifiers accessible by the source token.
        Format: ["owner/repo", ...]  (or "namespace/project" for GitLab).
        """

    # ── PR / MR collection ────────────────────────────────────────────────────

    @abstractmethod
    def fetch_pull_requests(self) -> list[dict[str, Any]]:
        """
        Fetch all PRs / MRs for the configured source repo.

        Returns a list of normalised dicts conforming to PR_SCHEMA.
        All downstream CSV/JSON export functions rely on these field names.
        """

    # ── LOC calculation ───────────────────────────────────────────────────────

    @abstractmethod
    def calculate_loc(self, method: str) -> dict[str, Any]:
        """
        Calculate lines-of-code for the configured source repo.

        Parameters
        ----------
        method : "api"   → platform language API (byte counts)
                 "clone" → shallow clone + cloc (true line counts)

        Returns
        -------
        dict conforming to the LOC data shape used by export_loc_data().
        """

    # ── Repository migration ──────────────────────────────────────────────────

    @abstractmethod
    def clone_repo(self, dest_path: str) -> None:
        """
        Mirror-clone the source repo to *dest_path*.

        Must use a token-authenticated URL so no interactive credentials are
        required.  The clone should include all branches, tags, and full history
        (i.e. equivalent to `git clone --mirror`).
        """

    @abstractmethod
    def migrate_repo(self, pr_list: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Full repository migration: create dest repo, push mirror, migrate PRs.

        Parameters
        ----------
        pr_list : Normalised PR dicts from fetch_pull_requests().
                  Pass [] to skip PR migration.

        Returns
        -------
        Migration stats dict with at minimum these keys:
          prs_total, prs_as_real_pr, prs_as_issue, prs_failed
        """
