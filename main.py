"""
main.py
───────
Entry point for the GitHub Migrator tool.

Startup sequence:
  1. Configure logging (file + console).
  2. Validate all config values and tokens.
  3. Show Dialog 1 → choose LOC method (API | Clone).
  4. Show Dialog 2 → choose run mode (Task 1 | Task 2 | Both).
  5. Execute the selected task(s).
"""

import logging
import os
import sys
import tkinter as tk
from tkinter import messagebox, ttk

import config
from utils import command_exists, ensure_dir


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
# Tkinter dialog helpers
# ══════════════════════════════════════════════════════════════════════════════

class ChoiceDialog(tk.Toplevel):
    """
    A modal dialog that presents a title, a message, and N buttons.
    The clicked button's label is stored in self.result.
    """

    def __init__(self, parent, title: str, message: str, options: list[str]):
        super().__init__(parent)
        self.result: str | None = None

        self.title(title)
        self.resizable(False, False)
        self.grab_set()                    # make it modal
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # ── Layout ────────────────────────────────────────────────────────────
        self.configure(bg="#1e1e2e")

        # Title bar accent
        accent = tk.Frame(self, bg="#7c3aed", height=4)
        accent.pack(fill=tk.X)

        # Icon + message
        body = tk.Frame(self, bg="#1e1e2e", padx=30, pady=24)
        body.pack(fill=tk.BOTH, expand=True)

        icon_label = tk.Label(
            body, text="🐙", font=("Segoe UI Emoji", 36), bg="#1e1e2e", fg="#cdd6f4"
        )
        icon_label.pack(pady=(0, 8))

        msg_label = tk.Label(
            body,
            text=message,
            font=("Segoe UI", 12),
            bg="#1e1e2e",
            fg="#cdd6f4",
            wraplength=380,
            justify="center",
        )
        msg_label.pack(pady=(0, 20))

        # Buttons
        btn_frame = tk.Frame(body, bg="#1e1e2e")
        btn_frame.pack()

        _BUTTON_STYLES = [
            {"bg": "#7c3aed", "active_bg": "#6d28d9"},
            {"bg": "#0f766e", "active_bg": "#0d9488"},
            {"bg": "#1d4ed8", "active_bg": "#2563eb"},
        ]

        for idx, opt in enumerate(options):
            style = _BUTTON_STYLES[idx % len(_BUTTON_STYLES)]
            btn = tk.Button(
                btn_frame,
                text=opt,
                font=("Segoe UI", 11, "bold"),
                bg=style["bg"],
                fg="white",
                activebackground=style["active_bg"],
                activeforeground="white",
                relief=tk.FLAT,
                padx=18,
                pady=10,
                cursor="hand2",
                command=lambda o=opt: self._select(o),
            )
            btn.pack(side=tk.LEFT, padx=8)

        # Centre on screen
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        sw    = self.winfo_screenwidth()
        sh    = self.winfo_screenheight()
        x     = (sw - w) // 2
        y     = (sh - h) // 2
        self.geometry(f"+{x}+{y}")

    def _select(self, option: str) -> None:
        self.result = option
        self.grab_release()
        self.destroy()

    def _on_close(self) -> None:
        """Treat window-close as cancellation → exit the tool."""
        logger.info("Dialog closed by user — exiting.")
        self.grab_release()
        self.destroy()


def _ask(root: tk.Tk, title: str, message: str, options: list[str]) -> str | None:
    """Show a ChoiceDialog and return the selected option (or None if dismissed)."""
    dlg = ChoiceDialog(root, title, message, options)
    root.wait_window(dlg)
    return dlg.result


def _build_root() -> tk.Tk:
    """Create and configure the hidden root Tk window."""
    root = tk.Tk()
    root.withdraw()                        # hide the root window
    root.configure(bg="#1e1e2e")
    # Make sure the app has an icon on macOS / Windows taskbars
    try:
        root.iconbitmap(default="")
    except Exception:
        pass
    return root


# ══════════════════════════════════════════════════════════════════════════════
# Pre-flight checks
# ══════════════════════════════════════════════════════════════════════════════

def _check_cloc_installed() -> None:
    """Warn (don't exit) if cloc is absent; the collector will fall back to wc -l."""
    if not command_exists("cloc"):
        logger.warning(
            "cloc is NOT installed. LOC 'Clone' method will fall back to a simpler "
            "wc -l count with no per-language breakdown.\n"
            "  Install cloc:  brew install cloc   (macOS)\n"
            "                 sudo apt install cloc  (Linux)"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    _setup_logging()
    logger.info("GitHub Migrator — starting up")

    # ── Config validation (exits on failure) ──────────────────────────────────
    config.validate_all()
    _check_cloc_installed()

    # ── Build hidden Tk root ──────────────────────────────────────────────────
    root = _build_root()

    # ── Dialog 1: LOC method ──────────────────────────────────────────────────
    loc_choice = _ask(
        root,
        title   ="GitHub Migrator — Choose LOC Method",
        message ="How would you like to calculate Lines of Code?",
        options =["Use GitHub API", "Clone Locally"],
    )
    if loc_choice is None:
        logger.info("No LOC method selected — exiting.")
        sys.exit(0)

    loc_method = "api" if loc_choice == "Use GitHub API" else "clone"
    logger.info("LOC method selected: %s", loc_method)

    # ── Dialog 2: Run mode ────────────────────────────────────────────────────
    run_choice = _ask(
        root,
        title   ="GitHub Migrator — Choose Task",
        message ="Which task would you like to run?",
        options =["Collect Only (Task 1)", "Migrate Only (Task 2)", "Run Both"],
    )
    if run_choice is None:
        logger.info("No task selected — exiting.")
        sys.exit(0)

    logger.info("Run mode selected: %s", run_choice)
    root.destroy()

    # ── Execute ────────────────────────────────────────────────────────────────
    pr_list: list[dict] = []

    if run_choice in ("Collect Only (Task 1)", "Run Both"):
        from task1_collector import run_task1
        pr_list, _loc = run_task1(loc_method=loc_method)

    if run_choice in ("Migrate Only (Task 2)", "Run Both"):
        # If only Task 2 is requested, try to load previously collected PR data
        if not pr_list:
            pr_json = os.path.join(config.OUTPUT_DIR, "pr_data.json")
            if os.path.exists(pr_json):
                import json
                with open(pr_json, encoding="utf-8") as fh:
                    pr_list = json.load(fh)
                logger.info("Loaded %d PRs from existing pr_data.json.", len(pr_list))
            else:
                logger.warning(
                    "No PR data found at %s. "
                    "PR migration will be skipped. "
                    "Run Task 1 first to collect PR data.",
                    pr_json,
                )

        from task2_migrator import run_task2
        run_task2(pr_list)

    logger.info("All done. Check output/ for CSV/JSON files and migrator.log for details.")


if __name__ == "__main__":
    main()
