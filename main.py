"""
main.py
───────
Entry point for the Repo Migrator tool.

All user input is collected through the terminal — no GUI dialogs.

Run flow
────────
  Step 0  — Platform selection     (GitHub | GitLab)
  Step 1  — Source credentials     (token + repo + optional base URL)
  Step 2  — Destination details    (token + owner + repo + visibility)
  Step 3  — LOC method             (API | Clone Locally)
  Step 4  — Task selection         (Collect | Migrate | Both)
"""

from __future__ import annotations

import getpass
import json
import logging
import os
import sys
from urllib.parse import urlparse

from config import RunConfig, OUTPUT_DIR
from utils import command_exists, ensure_dir, validate_token


# ── Logging setup ──────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    ensure_dir(os.path.dirname(os.path.abspath(__file__)))
    log_path = os.path.join(os.path.dirname(__file__), "migrator.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Terminal colour helpers  (ANSI — works on macOS / Linux zsh/bash)
# ══════════════════════════════════════════════════════════════════════════════

_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_RESET  = "\033[0m"
_GREEN  = "\033[92m"
_RED    = "\033[91m"
_YELLOW = "\033[93m"
_CYAN   = "\033[96m"
_PURPLE = "\033[95m"
_ORANGE = "\033[33m"


def _h1(text: str) -> None:
    """Print a top-level banner."""
    bar = "━" * 52
    print(f"\n{_BOLD}{_PURPLE}{bar}{_RESET}")
    print(f"{_BOLD}{_PURPLE}  {text}{_RESET}")
    print(f"{_BOLD}{_PURPLE}{bar}{_RESET}\n")


def _h2(text: str) -> None:
    """Print a section sub-header."""
    print(f"\n{_BOLD}{_CYAN}  ─── {text} {'─' * max(0, 44 - len(text))}{_RESET}")


def _ok(text: str) -> None:
    print(f"  {_GREEN}✅  {text}{_RESET}")


def _err(text: str) -> None:
    print(f"  {_RED}❌  {text}{_RESET}")


def _warn(text: str) -> None:
    print(f"  {_YELLOW}⚠️   {text}{_RESET}")


def _info(text: str) -> None:
    print(f"  {_DIM}{text}{_RESET}")


# ══════════════════════════════════════════════════════════════════════════════
# Input helpers
# ══════════════════════════════════════════════════════════════════════════════

def _ask(prompt: str, default: str = "") -> str:
    """Prompt for visible text input. Repeats until non-empty."""
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            val = input(f"  {_BOLD}{prompt}{suffix}: {_RESET}").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            logger.info("Interrupted — exiting.")
            sys.exit(0)
        if val:
            return val
        if default:
            return default
        _err("This field is required.")


def _ask_secret(prompt: str) -> str:
    """Prompt for hidden token input (like a password). Repeats until non-empty."""
    while True:
        try:
            val = getpass.getpass(f"  {_BOLD}{prompt}: {_RESET}").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            logger.info("Interrupted — exiting.")
            sys.exit(0)
        if val:
            return val
        _err("Token cannot be empty.")


def _pick(options: list[str], prompt: str = "Enter choice") -> str:
    """
    Display a numbered list and return the chosen option text.
    Accepts the number (1, 2 …) or an unambiguous prefix of the option text.
    """
    for idx, opt in enumerate(options, start=1):
        print(f"    {_BOLD}[{idx}]{_RESET}  {opt}")
    print()
    while True:
        try:
            raw = input(f"  {_BOLD}{prompt} [1–{len(options)}]: {_RESET}").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            logger.info("Interrupted — exiting.")
            sys.exit(0)
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(options):
                return options[idx - 1]
        _err(f"Please enter a number between 1 and {len(options)}.")


def _confirm_bool(prompt: str, default: bool = True) -> bool:
    """Yes/No prompt. Returns bool."""
    hint = "Y/n" if default else "y/N"
    while True:
        try:
            raw = input(f"  {_BOLD}{prompt} [{hint}]: {_RESET}").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print()
            sys.exit(0)
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        _err("Please enter y or n.")


def _parse_url_input(val: str, platform: str, default_base_url: str = "https://gitlab.com") -> tuple[str, str]:
    """
    Parses a user input (which could be a full URL, relative path, or contain a URL with prefix labels).
    Returns (base_url, path).
    """
    val = val.strip()
    
    # Extract the URL if the user accidentally pasted a label/prefix (e.g. "Source URL: https://...")
    url_idx = -1
    for prefix in ("https://", "http://"):
        idx = val.lower().find(prefix)
        if idx != -1:
            url_idx = idx
            break

    if url_idx != -1:
        # Get the URL starting from the http(s):// prefix until the next whitespace
        url_part = val[url_idx:].split()[0]
        try:
            parsed = urlparse(url_part)
            scheme = parsed.scheme or "https"
            netloc = parsed.netloc
            
            # Extract path
            path = parsed.path.strip("/")
            if path.endswith(".git"):
                path = path[:-4]
                
            # If it is github.com, base_url is always https://github.com
            if platform == "github" or netloc == "github.com":
                return "https://github.com", path
                
            # For GitLab, let's determine the base_url
            if netloc == "gitlab.com":
                return "https://gitlab.com", path
                
            # Self-hosted GitLab might have a subpath (e.g. company.com/gitlab)
            path_parts = [p for p in path.split("/") if p]
            if len(path_parts) > 1 and path_parts[0] in ("gitlab", "git"):
                base_url = f"{scheme}://{netloc}/{path_parts[0]}"
                rel_path = "/".join(path_parts[1:])
                return base_url, rel_path
            else:
                base_url = f"{scheme}://{netloc}"
                return base_url, path
        except Exception:
            pass
            
    # Relative path (strip typical prefix labels like 'Source Repository:', 'source:', etc.)
    clean_val = val
    if ":" in clean_val:
        prefix_part, remainder_part = clean_val.split(":", 1)
        if "/" not in prefix_part:
            clean_val = remainder_part.strip()
            
    default_base = "https://github.com" if platform == "github" else default_base_url
    return default_base, clean_val


# ══════════════════════════════════════════════════════════════════════════════
# Step 0 — Platform selection
# ══════════════════════════════════════════════════════════════════════════════

def _ask_platform() -> str:
    """Returns 'github' or 'gitlab'."""
    _h2("Platform")
    choice = _pick(["🐙  GitHub", "🦊  GitLab"], prompt="Which platform")
    return "gitlab" if "GitLab" in choice else "github"


# ══════════════════════════════════════════════════════════════════════════════
# Step 1 — Source credentials
# ══════════════════════════════════════════════════════════════════════════════

def _ask_source(platform: str) -> dict:
    """
    Collect and validate source credentials.

    Returns
    -------
    { "token": str, "repo": str, "base_url": str }
    """
    name = "GitLab" if platform == "gitlab" else "GitHub"
    _h2(f"Source  ({name})")

    # Source Repository or URL
    fmt = "namespace/project or URL" if platform == "gitlab" else "owner/repo or URL"
    while True:
        raw_repo = _ask(f"Source Repository or URL  ({fmt})")
        base_url, repo = _parse_url_input(raw_repo, platform)
        if "/" in repo:
            break
        _err(f"Could not parse repository path. Must be in '{fmt}' format (contains a slash).")

    if platform == "gitlab" and base_url != "https://gitlab.com":
        _info(f"Using GitLab Base URL: {base_url}")

    # Token
    scope_hint = (
        "api + read_repository scopes"
        if platform == "gitlab"
        else "repo + read:org scopes"
    )
    _info(f"Personal Access Token  ({scope_hint})")
    token = _ask_secret("Access Token (hidden)")

    # Validate token
    print(f"  {_DIM}Validating token …{_RESET}", end="", flush=True)
    ok, username = validate_token(platform, token, base_url)
    print("\r", end="")  # clear the "Validating" line
    if not ok:
        _err("Token is invalid or the base URL is unreachable.")
        _info("Check your token and try again.")
        # Retry loop
        while True:
            token = _ask_secret("Access Token (hidden)")
            print(f"  {_DIM}Validating …{_RESET}", end="", flush=True)
            ok, username = validate_token(platform, token, base_url)
            print("\r", end="")
            if ok:
                break
            _err("Still invalid. Try again (Ctrl+C to abort).")
    _ok(f"Connected as: {_BOLD}{username}{_RESET}")

    return {"token": token, "repo": repo, "base_url": base_url}


# ══════════════════════════════════════════════════════════════════════════════
# Step 2 — Destination details
# ══════════════════════════════════════════════════════════════════════════════

def _ask_dest(platform: str, source_base_url: str, source_repo: str) -> dict:
    """
    Collect and validate destination details.

    Returns
    -------
    { "token": str, "owner": str, "repo": str, "base_url": str, "private": bool }
    """
    name = "GitLab" if platform == "gitlab" else "GitHub"
    _h2(f"Destination  ({name})")

    # Destination Namespace/Owner or URL
    fmt = "username, group, or URL" if platform == "gitlab" else "username, org, or URL"
    raw_owner = _ask(f"Destination Namespace or URL  ({fmt})")
    base_url, owner = _parse_url_input(raw_owner, platform, default_base_url=source_base_url)

    if platform == "gitlab" and base_url != source_base_url:
        _info(f"Using Destination GitLab URL: {base_url}")

    # Destination token
    scope_hint = (
        "api + write_repository scopes"
        if platform == "gitlab"
        else "repo + workflow scopes"
    )
    _info(f"Personal Access Token for the destination account  ({scope_hint})")
    token = _ask_secret("Destination Token (hidden)")

    # Validate destination token
    print(f"  {_DIM}Validating …{_RESET}", end="", flush=True)
    ok, username = validate_token(platform, token, base_url)
    print("\r", end="")
    if not ok:
        _err("Destination token is invalid or the base URL is unreachable.")
        while True:
            token = _ask_secret("Destination Token (hidden)")
            print(f"  {_DIM}Validating …{_RESET}", end="", flush=True)
            ok, username = validate_token(platform, token, base_url)
            print("\r", end="")
            if ok:
                break
            _err("Still invalid. Try again (Ctrl+C to abort).")
    _ok(f"Connected as: {_BOLD}{username}{_RESET}")

    # New repo / project name
    # Default to the source repo's name (the part after the slash)
    default_name = source_repo.split("/")[-1] if "/" in source_repo else source_repo
    repo_label = (
        "New Project Name"
        if platform == "gitlab"
        else "New Repository Name"
    )
    while True:
        repo = _ask(repo_label, default=default_name)
        if " " not in repo and "/" not in repo:
            break
        _err("Repository name must not contain spaces or slashes.")

    # Visibility
    private = _confirm_bool("Make destination private?", default=True)

    return {
        "token":    token,
        "owner":    owner,
        "repo":     repo,
        "base_url": base_url,
        "private":  private,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Step 3 — LOC method
# ══════════════════════════════════════════════════════════════════════════════

def _ask_loc_method() -> str:
    """Returns 'api' or 'clone'."""
    _h2("Lines of Code Method")
    _info("API method: fast, uses platform language stats (byte approximation).")
    _info("Clone method: accurate, clones repo locally and runs cloc.")
    choice = _pick(["Use API", "Clone Locally"], prompt="LOC method")
    return "api" if choice == "Use API" else "clone"


# ══════════════════════════════════════════════════════════════════════════════
# Step 4 — Task selection
# ══════════════════════════════════════════════════════════════════════════════

def _ask_task(platform: str) -> str:
    """Returns one of the task option strings."""
    mr = "MR" if platform == "gitlab" else "PR"
    _h2("Task Selection")
    options = [
        f"Collect Only  (Task 1 — {mr}s + LOC data)",
        "Migrate Only  (Task 2 — Repo history + issue migration)",
        "Run Both",
    ]
    return _pick(options, prompt="Task")


# ══════════════════════════════════════════════════════════════════════════════
# Pre-flight checks
# ══════════════════════════════════════════════════════════════════════════════

def _check_cloc_installed() -> None:
    if not command_exists("cloc"):
        _warn(
            "cloc is not installed. LOC 'Clone' method will fall back to wc -l.\n"
            "       brew install cloc   (macOS) | sudo apt install cloc  (Linux)"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    _setup_logging()
    _h1("🚀  Repo Migrator")
    logger.info("Repo Migrator — starting up")
    _check_cloc_installed()

    # ── Step 0: Platform ───────────────────────────────────────────────────────
    platform = _ask_platform()
    logger.info("Platform: %s", platform)

    # ── Step 1: Source credentials ─────────────────────────────────────────────
    source = _ask_source(platform)
    logger.info("Source: %s  (base_url=%s)", source["repo"], source["base_url"])

    # ── Step 2: Destination details ────────────────────────────────────────────
    dest = _ask_dest(platform, source_base_url=source["base_url"], source_repo=source["repo"])
    logger.info(
        "Destination: %s/%s  (private=%s)", dest["owner"], dest["repo"], dest["private"]
    )

    # ── Step 3: LOC method ─────────────────────────────────────────────────────
    loc_method = _ask_loc_method()
    logger.info("LOC method: %s", loc_method)

    # ── Step 4: Task ───────────────────────────────────────────────────────────
    task_choice = _ask_task(platform)
    logger.info("Task: %s", task_choice)

    # ── Confirm ────────────────────────────────────────────────────────────────
    print()
    _h2("Ready to run")
    print(f"    Platform   :  {platform.capitalize()}")
    print(f"    Source     :  {source['repo']}  ({source['base_url']})")
    print(f"    Destination:  {dest['owner']}/{dest['repo']}  ({'private' if dest['private'] else 'public'})")
    print(f"    LOC method :  {loc_method}")
    print(f"    Task       :  {task_choice}")
    print()
    if not _confirm_bool("Proceed?", default=True):
        logger.info("Aborted by user.")
        sys.exit(0)

    # ── Assemble RunConfig ─────────────────────────────────────────────────────
    run_cfg = RunConfig(
        platform        = platform,
        source_token    = source["token"],
        source_repo     = source["repo"],
        source_base_url = source["base_url"],
        dest_token      = dest["token"],
        dest_owner      = dest["owner"],
        dest_repo       = dest["repo"],
        dest_base_url   = dest["base_url"],
        dest_private    = dest["private"],
    )

    # ── Build adapter ──────────────────────────────────────────────────────────
    if platform == "github":
        from platforms.github_adapter import GitHubAdapter
        adapter = GitHubAdapter(run_cfg)
    else:
        from platforms.gitlab_adapter import GitLabAdapter
        adapter = GitLabAdapter(run_cfg)

    # ── Execute ────────────────────────────────────────────────────────────────
    import task1_collector
    import task2_migrator

    pr_list: list[dict] = []

    if "Task 1" in task_choice or "Both" in task_choice:
        print()
        pr_list, _loc = task1_collector.run_task1(
            loc_method=loc_method,
            adapter=adapter,
        )

    if "Task 2" in task_choice or "Both" in task_choice:
        if not pr_list:
            pr_json = os.path.join(OUTPUT_DIR, "pr_data.json")
            if os.path.exists(pr_json):
                with open(pr_json, encoding="utf-8") as fh:
                    pr_list = json.load(fh)
                logger.info("Loaded %d PRs from existing pr_data.json.", len(pr_list))
            else:
                _warn(
                    f"No PR data found at {pr_json}. PR migration will be skipped. "
                    "Run Task 1 first."
                )
        print()
        task2_migrator.run_task2(pr_list, adapter=adapter)

    print()
    _ok("All done. Check output/ for CSV/JSON files and migrator.log for details.")


if __name__ == "__main__":
    main()
