# 🐙 GitHub Migrator

> A Python tool that **fully migrates a GitHub repository** — commit history, all branches, tags, and every Pull Request — to a new GitHub account or organisation.  
> Controlled through two simple pop-up dialogs. No CLI flags to memorise.

---

## Table of Contents

1. [What This Tool Does](#1-what-this-tool-does)
2. [How It Works — Overview](#2-how-it-works--overview)
3. [System Requirements](#3-system-requirements)
4. [Installation](#4-installation)
5. [Configuration — Creating Your `.env` File](#5-configuration--creating-your-env-file)
6. [Generating GitHub Personal Access Tokens](#6-generating-github-personal-access-tokens)
7. [Running the Tool](#7-running-the-tool)
8. [Understanding the Two Dialogs](#8-understanding-the-two-dialogs)
9. [What Each Task Does in Detail](#9-what-each-task-does-in-detail)
10. [Output Files Reference](#10-output-files-reference)
11. [PR Migration Behaviour](#11-pr-migration-behaviour)
12. [Project File Structure](#12-project-file-structure)
13. [Troubleshooting](#13-troubleshooting)
14. [FAQ](#14-faq)

---

## 1. What This Tool Does

When you need to move a repository from one GitHub account (or org) to another, a simple "fork" loses Pull Request history, and a manual export is tedious. This tool automates the entire process:

| What gets migrated | How |
|---|---|
| ✅ All commits | `git clone --mirror` preserves the full commit graph |
| ✅ All branches | Mirror push transfers every branch ref |
| ✅ All tags | Mirror push transfers every tag ref |
| ✅ Pull Request metadata | Recreated as Issues (and real PRs where possible) |
| ✅ PR descriptions, stats, labels | Embedded in each migrated Issue body |
| ✅ Lines-of-Code report | Via GitHub API or `cloc` |
| ❌ PR review threads / inline comments | GitHub does not expose these for third-party recreation |

---

## 2. How It Works — Overview

```
python main.py
      │
      ├─ Dialog 1 ──► Choose LOC method: [Use GitHub API]  or  [Clone Locally]
      │
      ├─ Dialog 2 ──► Choose task:  [Collect Only]  [Migrate Only]  [Run Both]
      │
      ├─── TASK 1 — COLLECT ────────────────────────────────────────────────────
      │    1. Connect to source repo via GitHub API
      │    2. Fetch all PRs (open + closed + merged) — up to 100 per page
      │    3. Calculate LOC (API bytes  OR  clone + cloc)
      │    4. Write: output/pr_data.csv, pr_data.json
      │              output/loc_data.csv, loc_data.json
      │
      └─── TASK 2 — MIGRATE ────────────────────────────────────────────────────
           1. Create new (empty) repo on destination account via API
           2. git clone --mirror <source>  →  git push --mirror <dest>
              (transfers ALL commits, branches, tags in one shot)
           3. Set default branch (main / master)
           4. For every PR collected in Task 1:
              - Merged       → closed Issue  (label: merged, migrated-pr)
              - Open + branch exists → real Pull Request
              - Open + branch missing → open Issue  (label: open-pr, migrated-pr)
              - Closed/unmerged → closed Issue  (label: closed-unmerged, migrated-pr)
           5. Write: output/migration_report.json
```

---

## 3. System Requirements

| Requirement | Minimum version | Check |
|---|---|---|
| **Python** | 3.10 | `python3 --version` |
| **Git** | Any modern version | `git --version` |
| **cloc** | Any *(optional)* | `cloc --version` |
| **Internet access** | — | Must reach `api.github.com` |

### Installing system tools (if missing)

**macOS (Homebrew)**
```bash
brew install python git      # required
brew install cloc            # optional — for accurate line counts
```

**Ubuntu / Debian**
```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv git   # required
sudo apt install cloc                                    # optional
```

**Windows**
- Python: https://www.python.org/downloads/ (tick *"Add to PATH"*)
- Git: https://git-scm.com/download/win
- cloc: `winget install cloc` or download from https://github.com/AlDanial/cloc

> **Note on `tkinter`:** The pop-up dialogs use `tkinter`, which ships with the standard Python installer on macOS and Windows.  
> On Linux, install it with: `sudo apt install python3-tk`

---

## 4. Installation

### Step 1 — Clone this repository

```bash
git clone https://github.com/<your-username>/github_migrator.git
cd github_migrator
```

### Step 2 — Create a Python virtual environment

A virtual environment keeps dependencies isolated and avoids conflicts with other Python projects on your machine.

```bash
python3 -m venv .venv
```

### Step 3 — Activate the virtual environment

| Platform | Command |
|---|---|
| macOS / Linux | `source .venv/bin/activate` |
| Windows (cmd) | `.venv\Scripts\activate.bat` |
| Windows (PowerShell) | `.venv\Scripts\Activate.ps1` |

You should see `(.venv)` at the start of your terminal prompt — this confirms it's active.

### Step 4 — Install Python dependencies

```bash
pip install -r requirements.txt
```

This installs:
- **PyGithub** — Python wrapper for the GitHub REST API
- **requests** — HTTP client used for direct API calls
- **python-dotenv** — reads your `.env` configuration file

---

## 5. Configuration — Creating Your `.env` File

The tool reads all secrets from a `.env` file in the project root. **This file is gitignored and must never be committed.**

```bash
# Copy the template
cp .env.example .env

# Open it in your editor
nano .env          # or: code .env / vim .env / open -e .env
```

Fill in every value:

```ini
# ── Source repository (where you are migrating FROM) ──────────────────────────
SOURCE_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
SOURCE_REPO=original-owner/repository-name

# ── Destination (where you are migrating TO) ───────────────────────────────────
DEST_TOKEN=ghp_yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy
DEST_OWNER=destination-github-username-or-org-name
DEST_REPO=new-repository-name

# ── Options ────────────────────────────────────────────────────────────────────
DEST_PRIVATE=true     # true = private repo  |  false = public repo
```

### Variable explanations

| Variable | Description | Example |
|---|---|---|
| `SOURCE_TOKEN` | Personal Access Token for the **source** GitHub account | `ghp_abc123...` |
| `SOURCE_REPO` | Source repo in `owner/repo` format | `acme-corp/backend-api` |
| `DEST_TOKEN` | Personal Access Token for the **destination** GitHub account | `ghp_xyz789...` |
| `DEST_OWNER` | GitHub username or org name of the destination | `my-new-org` |
| `DEST_REPO` | Name to give the **new** repo on the destination | `backend-api-migrated` |
| `DEST_PRIVATE` | Whether the new repo is private | `true` or `false` |

---

## 6. Generating GitHub Personal Access Tokens

You need **two separate tokens** — one for each GitHub account.

### Steps (repeat for each account)

1. Go to **GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)**  
   Direct link: https://github.com/settings/tokens

2. Click **"Generate new token (classic)"**

3. Give it a descriptive name, e.g. `github-migrator-source`

4. Set expiration (suggest 7 days — enough to complete the migration)

5. Tick the required scopes:

| Token | Required scopes |
|---|---|
| **SOURCE_TOKEN** | `repo` (full), `read:org` |
| **DEST_TOKEN** | `repo` (full), `workflow` |

6. Click **"Generate token"** and copy the value immediately — GitHub only shows it once.

7. Paste it into your `.env` file.

> **⚠️ Security reminder:** Treat your tokens like passwords. Do not paste them into Slack, email, or any chat. The `.env` file is gitignored for this reason.

---

## 7. Running the Tool

Make sure your virtual environment is **activated** (you see `(.venv)` in your prompt):

```bash
source .venv/bin/activate    # skip if already active
python main.py
```

The tool will:
1. Load your `.env` file
2. Validate both tokens against GitHub's API (exits with a clear error if a token is wrong)
3. Check whether `cloc` is installed (warns if not, continues with fallback)
4. Open the first dialog box

---

## 8. Understanding the Two Dialogs

### Dialog 1 — LOC Method

**"How would you like to calculate Lines of Code?"**

| Button | What it does |
|---|---|
| **Use GitHub API** | Calls `GET /repos/{owner}/{repo}/languages`. Fast, no cloning. Returns **byte counts** (not true line counts). Best for a quick approximation. |
| **Clone Locally** | Clones the repo with `--depth=1`, then runs `cloc` to count actual lines per language. If `cloc` is not installed, falls back to a plain file-line count. Best for accuracy. |

### Dialog 2 — Task Selection

**"Which task would you like to run?"**

| Button | What happens |
|---|---|
| **Collect Only (Task 1)** | Fetches PR data + LOC, writes 4 output files. Does **not** touch the destination repo. |
| **Migrate Only (Task 2)** | Runs the migration. Loads `output/pr_data.json` from a previous Task 1 run. |
| **Run Both** | Runs Task 1 then Task 2 in sequence. Recommended for a first migration. |

---

## 9. What Each Task Does in Detail

### Task 1 — Collect

```
task1_collector.py
```

1. Authenticates to GitHub using `SOURCE_TOKEN`
2. Fetches all Pull Requests in pages of 100 (state: `all` — open, closed, merged)
3. For each PR collects 21 fields:
   - Number, title, state, merged flag
   - Author, created/updated/closed/merged timestamps
   - Base branch, head branch
   - Commit count, additions, deletions, changed files
   - Body (description), labels, assignees
   - Review comment count, comment count, merge commit SHA
4. Calculates LOC using the selected method
5. Writes all data to `output/` as CSV and JSON

**Rate limiting:** The tool adds a 100 ms delay between PR fetches and auto-waits if GitHub returns a rate limit error.

---

### Task 2 — Migrate

```
task2_migrator.py
```

**Step 1 — Create destination repo**  
Calls `POST /user/repos` (or `POST /orgs/{org}/repos`) with your `DEST_TOKEN`.  
If the repo already exists, a warning is logged and the step is skipped.

**Step 2 — Mirror clone and push**  
```bash
# What happens internally:
git clone --mirror https://<SOURCE_TOKEN>@github.com/<SOURCE_REPO>.git ./mirror_repo.git
git remote set-url origin https://<DEST_TOKEN>@github.com/<DEST_OWNER>/<DEST_REPO>.git
git push --mirror
```
This is the most reliable way to transfer full history. The temporary `mirror_repo.git` folder is deleted automatically after the push.

**Step 3 — Set default branch**  
Calls `PATCH /repos/{owner}/{repo}` to set `main` (or `master` if `main` doesn't exist) as the default branch.

**Step 4 — Migrate PRs**  
Creates labels on the destination repo, then iterates every PR (sorted by number ascending).  
Each PR becomes either a real Pull Request or a detailed Issue (see [section 11](#11-pr-migration-behaviour)).

**Step 5 — Write migration report**  
Creates `output/migration_report.json` with a full summary.

---

## 10. Output Files Reference

All files are written to the `output/` folder (created automatically).

| File | Format | Contents |
|---|---|---|
| `pr_data.csv` | CSV | One row per PR · all 21 metadata fields |
| `pr_data.json` | JSON | Same data as CSV, as a JSON array |
| `loc_data.csv` | CSV | Per-language breakdown (or single summary row for API method) |
| `loc_data.json` | JSON | Full LOC data — method used, notes, language breakdown, totals |
| `migration_report.json` | JSON | Migration summary (see below) |
| `migrator.log` | Plain text | Timestamped log of every action and error |

### Example `migration_report.json`

```json
{
  "source_repo": "acme-corp/backend-api",
  "dest_repo": "my-new-org/backend-api-migrated",
  "migrated_at": "2025-06-08T10:00:00+00:00",
  "commit_history": "success",
  "branches_mirrored": ["main", "dev", "feature/payments"],
  "prs_total": 87,
  "prs_as_real_pr": 3,
  "prs_as_issue": 82,
  "prs_failed": 2
}
```

---

## 11. PR Migration Behaviour

Pull Requests are GitHub metadata — they do not exist in the Git data that a mirror clone transfers. Each PR is recreated on the destination using the GitHub Issues/PRs API.

| Original PR state | Action on destination repo |
|---|---|
| **Merged** | Closed Issue · labels: `merged`, `migrated-pr` |
| **Open** + head branch exists in mirror | Real Pull Request (open) |
| **Open** + head branch NOT in mirror | Open Issue · labels: `open-pr`, `migrated-pr` |
| **Closed** (not merged) | Closed Issue · labels: `closed-unmerged`, `migrated-pr` |

### What the migrated Issue body looks like

Every migrated PR/Issue body contains:

```
⚠️ This issue was automatically migrated from the source repository.

## Original Description
<the original PR description goes here>

## Migration Metadata
| Field           | Value              |
|-----------------|--------------------|
| Original PR     | #42                |
| Author          | @username          |
| State           | merged             |
| Created At      | 2024-01-15         |
| Merged At       | 2024-01-18         |
| Base Branch     | main               |
| Head Branch     | feature/payments   |
| Commits         | 3                  |
| +additions      | 120                |
| -deletions      | 45                 |
| Changed Files   | 7                  |
| Merge Commit    | abc1234...         |
```

---

## 12. Project File Structure

```
github_migrator/
│
├── main.py               ← Entry point. Run this. Shows the two dialogs.
├── config.py             ← Reads .env, validates both tokens at startup
├── task1_collector.py    ← PR collection + LOC calculation + CSV/JSON export
├── task2_migrator.py     ← Mirror clone, repo creation, PR→Issue migration
├── utils.py              ← Shared helpers: retry logic, subprocess runner, etc.
│
├── requirements.txt      ← Python package dependencies
├── .env.example          ← Template — copy to .env and fill in your values
├── .gitignore            ← .env, output/, mirror dirs, log file are all ignored
├── README.md             ← This file
│
└── output/               ← Created automatically when the tool runs
    ├── pr_data.csv
    ├── pr_data.json
    ├── loc_data.csv
    ├── loc_data.json
    └── migration_report.json
```

> Temporary directories (`mirror_repo.git/`, `temp_loc_repo/`) are created and **deleted automatically** during a run. You will not see them unless something crashes mid-way.

---

## 13. Troubleshooting

### ❌ `Missing required config: SOURCE_TOKEN`
Your `.env` file is missing or the variable name is wrong.  
→ Check that `.env` exists in the project root (not `.env.example`).  
→ Make sure there are no spaces around the `=` sign: `SOURCE_TOKEN=ghp_abc` ✅, not `SOURCE_TOKEN = ghp_abc` ❌

### ❌ `Token is INVALID or expired (HTTP 401)`
The token in your `.env` is wrong, expired, or was regenerated.  
→ Go to https://github.com/settings/tokens and generate a new one.

### ❌ `Token lacks required scopes (HTTP 403)`
The token exists but doesn't have the right permissions.  
→ Regenerate the token and tick `repo` + `read:org` (source) or `repo` + `workflow` (dest).

### ❌ `Mirror push failed` / destination repo already has commits
The destination repo must be completely empty before Task 2 runs.  
→ Delete the repo on GitHub and run again, **or** create it fresh with `auto_init: false`.

### ❌ `cloc not found` warning
This is not a fatal error. The tool will fall back to a plain line count with no per-language breakdown.  
→ Install cloc if you need per-language stats: `brew install cloc` (macOS) or `sudo apt install cloc` (Linux).

### ❌ Pop-up dialog doesn't appear
- On macOS, Python must be granted access to display windows. If running in a remote terminal/SSH session, tkinter dialogs won't work — run from a local terminal instead.
- On Linux without a display (headless server), set up a display or run: `DISPLAY=:0 python main.py`

### ⏳ Tool is running very slowly
Large repos with hundreds of PRs take time — GitHub's API returns 100 PRs per page and the tool adds a 0.5 s sleep between PR creations to avoid rate limits. This is expected behaviour. Check `migrator.log` to see live progress.

---

## 14. FAQ

**Q: Will the original repository be affected in any way?**  
A: No. The tool only *reads* from the source repository. Nothing is written to or deleted from the source.

**Q: Can I run Task 1 and Task 2 separately on different days?**  
A: Yes. Run *Collect Only* first — this saves `output/pr_data.json`. Later, run *Migrate Only* and the tool will automatically load that file.

**Q: What if a PR migration fails partway through?**  
A: The tool never crashes mid-migration. Each PR is wrapped in error handling — failures are logged to `migrator.log` with the PR number and reason, and the loop continues to the next PR. Check `migration_report.json` for the `prs_failed` count and `migrator.log` for details.

**Q: The LOC numbers from the API look wrong.**  
A: GitHub's language API returns **byte counts**, not line counts. This is a known limitation documented in `loc_data.json` under the `note` field. Use *Clone Locally* with `cloc` installed for true line counts.

**Q: The destination repo has a different default branch (e.g. `master` instead of `main`).**  
A: The tool automatically sets the default branch to `main` after the mirror push. If `main` doesn't exist, it uses the first branch it finds. You can change it manually in the repo settings afterward.

**Q: Can I migrate to a GitHub Organisation instead of a personal account?**  
A: Yes. Set `DEST_OWNER` to the organisation name. The tool will detect that the owner is not your personal account and use `POST /orgs/{org}/repos` automatically. Make sure your `DEST_TOKEN` has `repo` scope and is a member of the org with sufficient permissions.

**Q: What's the rate limit situation?**  
A: GitHub's REST API allows 5 000 requests per hour per token. For repos with many PRs (500+), the built-in 0.5 s sleeps and automatic rate-limit wait logic will handle this — the migration will just take longer. You can monitor progress in `migrator.log`.

---

## Licence

MIT — use and adapt freely.
