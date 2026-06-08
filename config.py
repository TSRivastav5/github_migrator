"""
config.py
─────────
Loads all credentials and repository configuration from environment variables
(via a .env file).  Token validity is checked at import time so failures
surface early, before any heavy work begins.
"""

import os
import sys
import logging

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Raw values from environment ────────────────────────────────────────────────

SOURCE_TOKEN: str = os.getenv("SOURCE_TOKEN", "")
DEST_TOKEN: str   = os.getenv("DEST_TOKEN", "")

# Format: "owner/repo"  e.g. "acme-corp/backend-api"
SOURCE_REPO: str  = os.getenv("SOURCE_REPO", "")
DEST_REPO: str    = os.getenv("DEST_REPO", "")
DEST_OWNER: str   = os.getenv("DEST_OWNER", "")

# Whether to make the destination repo private (default: True)
DEST_PRIVATE: bool = os.getenv("DEST_PRIVATE", "true").lower() == "true"

# Output directory for CSV / JSON files
OUTPUT_DIR: str = os.path.join(os.path.dirname(__file__), "output")


# ── Validation helpers ─────────────────────────────────────────────────────────

def _require(value: str, name: str) -> str:
    """Assert a config value is non-empty, print a helpful error otherwise."""
    if not value or not value.strip():
        logger.error(
            "Missing required config: %s — please set it in your .env file.", name
        )
        sys.exit(1)
    return value.strip()


def _validate_token(token: str, label: str) -> dict:
    """
    Hit GET /user with the given token to confirm it is valid and retrieve the
    authenticated user's login name.

    Returns the parsed JSON response on success.
    Exits with a clear message on 401 / 403 / any other error.
    """
    import requests  # local import so config stays importable even without requests

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        resp = requests.get("https://api.github.com/user", headers=headers, timeout=15)
    except requests.exceptions.ConnectionError as exc:
        logger.error("[%s] Network error while validating token: %s", label, exc)
        sys.exit(1)

    if resp.status_code == 200:
        data = resp.json()
        logger.info("[%s] Token valid — authenticated as: %s", label, data.get("login"))
        return data
    elif resp.status_code == 401:
        logger.error(
            "[%s] Token is INVALID or expired (HTTP 401). "
            "Check your .env file and regenerate the token if needed.",
            label,
        )
        sys.exit(1)
    elif resp.status_code == 403:
        logger.error(
            "[%s] Token lacks required scopes (HTTP 403). "
            "Source token needs: repo, read:org  |  Dest token needs: repo, workflow.",
            label,
        )
        sys.exit(1)
    else:
        logger.error(
            "[%s] Unexpected response while validating token: HTTP %d — %s",
            label,
            resp.status_code,
            resp.text[:200],
        )
        sys.exit(1)


def validate_all() -> None:
    """
    Validate every required config value and both GitHub tokens.
    Call this once at startup (done automatically in main.py).
    """
    _require(SOURCE_TOKEN, "SOURCE_TOKEN")
    _require(DEST_TOKEN,   "DEST_TOKEN")
    _require(SOURCE_REPO,  "SOURCE_REPO")
    _require(DEST_REPO,    "DEST_REPO")
    _require(DEST_OWNER,   "DEST_OWNER")

    if "/" not in SOURCE_REPO:
        logger.error("SOURCE_REPO must be in 'owner/repo' format, got: %s", SOURCE_REPO)
        sys.exit(1)

    logger.info("Validating SOURCE_TOKEN …")
    _validate_token(SOURCE_TOKEN, "SOURCE_TOKEN")

    logger.info("Validating DEST_TOKEN …")
    _validate_token(DEST_TOKEN,   "DEST_TOKEN")

    logger.info("All configuration validated successfully.")
