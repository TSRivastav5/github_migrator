"""
utils.py
────────
Shared helpers used across task1_collector.py and task2_migrator.py.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from typing import Any, Callable, TypeVar

import requests
from github import GithubException, RateLimitExceededException

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


# ── Retry / rate-limit helpers ─────────────────────────────────────────────────

def wait_for_rate_limit(g) -> None:
    """
    Block until the PyGithub client's primary rate limit resets.
    Prints a countdown so the user knows the tool isn't stuck.
    """
    try:
        rate = g.get_rate_limit().core
        reset_ts = rate.reset.timestamp()
        wait_secs = max(0, reset_ts - time.time()) + 5  # +5 s buffer
        logger.warning(
            "Rate limit reached (remaining=%d). Sleeping %.0f seconds until reset …",
            rate.remaining,
            wait_secs,
        )
        time.sleep(wait_secs)
    except Exception as exc:  # pragma: no cover
        logger.warning("Could not read rate limit info: %s. Sleeping 60 s.", exc)
        time.sleep(60)


def retry_github(func: F, *args, g=None, retries: int = 3, label: str = "", **kwargs) -> Any:
    """
    Call *func* with *args/**kwargs*, retrying up to *retries* times.

    Handles:
      - RateLimitExceededException → wait for reset then retry
      - GithubException (5xx) → exponential back-off
      - requests.exceptions.ConnectionError → exponential back-off
    """
    for attempt in range(1, retries + 2):
        try:
            return func(*args, **kwargs)
        except RateLimitExceededException:
            if g is None:
                raise
            wait_for_rate_limit(g)
        except GithubException as exc:
            if exc.status >= 500:
                delay = 2 ** attempt
                logger.warning(
                    "[%s] GitHub server error (%d) on attempt %d/%d. Retrying in %ds …",
                    label, exc.status, attempt, retries, delay,
                )
                time.sleep(delay)
            else:
                raise
        except requests.exceptions.ConnectionError as exc:
            delay = 2 ** attempt
            logger.warning(
                "[%s] Network error on attempt %d/%d: %s. Retrying in %ds …",
                label, attempt, retries, exc, delay,
            )
            time.sleep(delay)
        if attempt > retries:
            raise RuntimeError(f"[{label}] Failed after {retries} retries.")
    return None  # unreachable


# ── Subprocess helpers ─────────────────────────────────────────────────────────

def run_subprocess(cmd: list[str], cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """
    Run a shell command safely, streaming its stderr to the logger.

    Parameters
    ----------
    cmd   : Command + arguments as a list (never pass shell=True with user data).
    cwd   : Working directory for the subprocess.
    check : If True, raise CalledProcessError on non-zero exit code.
    """
    logger.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        logger.debug("STDOUT: %s", result.stdout[:2000])
        logger.debug("STDERR: %s", result.stderr[:2000])
        if check:
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )
    return result


def command_exists(name: str) -> bool:
    """Return True if *name* is available on PATH."""
    return shutil.which(name) is not None


# ── Filesystem helpers ─────────────────────────────────────────────────────────

def ensure_dir(path: str) -> None:
    """Create directory (and parents) if it doesn't already exist."""
    os.makedirs(path, exist_ok=True)


def remove_dir(path: str) -> None:
    """Recursively delete a directory tree, ignoring errors."""
    try:
        shutil.rmtree(path, ignore_errors=True)
        logger.debug("Removed directory: %s", path)
    except Exception as exc:  # pragma: no cover
        logger.warning("Could not remove %s: %s", path, exc)


# ── Runtime credential validation ─────────────────────────────────────────────

def validate_token(
    platform: str,
    token: str,
    base_url: str = "https://github.com",
) -> tuple[bool, str | None]:
    """
    Verify an API token against the platform's user-identity endpoint.

    Parameters
    ----------
    platform : "github" | "gitlab"
    token    : The Personal Access Token to verify.
    base_url : Base URL of the instance.  Only meaningful for GitLab
               (defaults to https://github.com for GitHub, which is
               always used regardless of the value passed).

    Returns
    -------
    (True, username)  on success
    (False, None)     on failure (invalid token, network error, etc.)
    """
    try:
        if platform == "github":
            resp = requests.get(
                "https://api.github.com/user",
                headers={
                    "Authorization":        f"token {token}",
                    "Accept":               "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=8,
            )
            if resp.status_code == 200:
                return True, resp.json().get("login")
            return False, None

        else:  # gitlab
            url = f"{base_url.rstrip('/')}/api/v4/user"
            resp = requests.get(
                url,
                headers={"PRIVATE-TOKEN": token},
                timeout=8,
            )
            if resp.status_code == 200:
                return True, resp.json().get("username")
            return False, None

    except Exception as exc:
        logger.debug("validate_token error (%s): %s", platform, exc)
        return False, None
