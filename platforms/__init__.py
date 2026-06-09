"""
platforms/
──────────
Platform adapter package for Repo Migrator.

Supported platforms
───────────────────
  github  →  platforms.github_adapter.GitHubAdapter
  gitlab  →  platforms.gitlab_adapter.GitLabAdapter

Each adapter implements the PlatformAdapter interface defined in base.py.
"""

from platforms.base import PlatformAdapter  # noqa: F401 (re-export for convenience)
