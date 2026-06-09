"""
config.py
─────────
Defines RunConfig — the single runtime configuration object built entirely
from GUI dialog inputs.  No .env file is read.  Credentials live only in
memory for the duration of the run; nothing is written to disk except the
output CSV/JSON files in OUTPUT_DIR.

Quick reference
───────────────
    from config import RunConfig, OUTPUT_DIR

    cfg = RunConfig(
        platform        = "github",
        source_token    = "ghp_...",
        source_repo     = "owner/repo",
        source_base_url = "https://github.com",
        dest_token      = "ghp_...",
        dest_owner      = "dest-org",
        dest_repo       = "new-repo",
        dest_base_url   = "https://github.com",
        dest_private    = True,
    )
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# ── Shared constants ───────────────────────────────────────────────────────────

# Output directory for CSV / JSON files (not a credential — always this path).
OUTPUT_DIR: str = os.path.join(os.path.dirname(__file__), "output")


# ── Mutable module-level shims ─────────────────────────────────────────────────
# task1_collector.py and task2_migrator.py reference these names for the GitHub
# path.  GitHubAdapter.migrate_repo() populates them from RunConfig before
# delegating to those modules.  Do NOT read these directly — use RunConfig.
SOURCE_TOKEN:  str  = ""
SOURCE_REPO:   str  = ""
DEST_TOKEN:    str  = ""
DEST_OWNER:    str  = ""
DEST_REPO:     str  = ""
DEST_PRIVATE:  bool = True


# ══════════════════════════════════════════════════════════════════════════════
# RunConfig dataclass
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class RunConfig:
    """
    All configuration collected at runtime through GUI dialogs.
    Passed directly to platform adapters.

    Attribute naming is platform-agnostic so adapters can share one interface:
      source_*  — describes the repository being migrated FROM
      dest_*    — describes the repository being migrated TO
    """

    platform:        str    # "github" | "gitlab"

    # Source
    source_token:    str
    source_repo:     str    # "owner/repo" (GitHub) | "namespace/project" (GitLab)
    source_base_url: str    # "https://github.com" | custom GitLab base URL

    # Destination
    dest_token:      str
    dest_owner:      str    # GitHub username/org  | GitLab namespace
    dest_repo:       str    # new repository / project name
    dest_base_url:   str    # destination instance URL
    dest_private:    bool   # make the destination private?

    # Derived — not set by dialogs
    output_dir: str = field(default_factory=lambda: OUTPUT_DIR)
