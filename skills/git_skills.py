"""
skills/git_skills.py
Read-only git skills. These shell out to the `git` binary (via `git -C
<path>`) and never mutate the repository, so the agent can inspect project
state without touching it. Runs as plain Python -- no LLM needed to execute,
only to decide (via tool-calling) that they should run.

Output is truncated for context safety, since the router runs on limited
hardware.
"""

import logging
import subprocess
import time
from pathlib import Path

from platform_utils import confined_path

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 30       # seconds; large repos can be slow
_MAX_DIFF_CHARS = 8000  # keep the router's context small
_MAX_LOG_CHARS = 4000

# --- git mutation policy (mirrors fs_skills._check_mutation) ---

_CONFIG = None
_GIT_CONFIRMER = None  # callable(op, path, detail) -> "allow" | "deny"

_GIT_MUTATION_MODES = {"off", "confirm", "allow"}


def bind_config(config: dict) -> None:
    global _CONFIG
    _CONFIG = config


def bind_git_confirmer(confirmer) -> None:
    global _GIT_CONFIRMER
    _GIT_CONFIRMER = confirmer


def terminal_git_confirmer(op: str, path: str, detail: str) -> str:
    """Interactive confirmation prompt for git mutations. Returns allow/deny."""
    print(f"\n{op} wants to mutate the repository:")
    print(f"  repo  : {path}")
    if detail:
        print(f"  detail: {detail}")
    while True:
        answer = input("Allow? [y]es / [n]o: ").strip().lower()
        if answer in {"y", "yes", "allow"}:
            return "allow"
        if answer in {"n", "no", "deny"}:
            return "deny"
        print("Please answer 'y' or 'n'.")


def _check_git_mutation(op: str, path: str, detail: str = "") -> dict | None:
    """Return an error dict to refuse a git mutation, or None to allow it.

    Controlled by git_mutation_mode:
      off     -> refuse every mutation (the default; these are new capabilities)
      confirm -> require the injected confirmer (fail-closed if none bound)
      allow   -> proceed without prompting
    """
    mode = str((_CONFIG or {}).get("git_mutation_mode", "off")).lower()
    if mode == "off":
        return {
            "error": f"{op} is disabled; set 'git_mutation_mode' in config to enable it.",
            "reason": "mutation_disabled",
            "operation": op,
        }
    if mode not in _GIT_MUTATION_MODES:
        return {"error": f"Unknown git_mutation_mode: {mode!r}", "reason": "config", "operation": op}
    if mode == "allow":
        return None

    # mode == "confirm"
    if _GIT_CONFIRMER is None:
        return {
            "error": f"{op} requires confirmation but no confirmer is bound (fail-closed).",
            "reason": "confirm_unavailable",
            "operation": op,
        }
    answer = _GIT_CONFIRMER(op, path, detail)
    if answer == "allow":
        return None
    if answer == "deny":
        return {
            "error": f"{op} not performed (denied).",
            "reason": "user_denied",
            "operation": op,
            "refused": True,
        }
    return {
        "error": "Confirmation did not approve the mutation (fail-closed).",
        "reason": "user_denied",
        "operation": op,
    }


def _run_git(repo: Path, *args: str) -> dict:
    """Run a git command and return {"ok": True, "stdout": ...} or {"ok": False, "error": ...}."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        logger.warning("git binary not found on PATH")
        return {"ok": False, "error": "git is not installed or not on PATH"}
    except subprocess.TimeoutExpired:
        logger.warning("git command timed out: %r", args)
        return {"ok": False, "error": f"git {args[0] if args else ''} timed out"}
    except OSError as exc:
        logger.warning("git failed to run: %s", exc)
        return {"ok": False, "error": f"git failed to run: {exc}"}

    if proc.returncode != 0:
        # git's own messages (e.g. "fatal: not a git repository") are useful.
        err = (proc.stderr or proc.stdout or "").strip()
        return {"ok": False, "error": err or f"git exited with code {proc.returncode}"}

    return {"ok": True, "stdout": proc.stdout, "stderr": proc.stderr}


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def git_status(path: str = ".") -> dict:
    """Working-tree status: current branch plus short status lines."""
    repo, err = confined_path(path)
    if err:
        return err
    result = _run_git(repo, "status", "--short", "--branch")
    if not result["ok"]:
        return {"error": result["error"]}
    return {"path": str(repo), "status": result["stdout"].rstrip()}


def git_diff(path: str = ".", staged: bool = False) -> dict:
    """Unified diff of working-tree changes (or staged changes)."""
    repo, err = confined_path(path)
    if err:
        return err
    args = ["diff", "--staged"] if staged else ["diff"]
    result = _run_git(repo, *args)
    if not result["ok"]:
        return {"error": result["error"]}
    diff, truncated = _truncate(result["stdout"], _MAX_DIFF_CHARS)
    return {"path": str(repo), "staged": staged, "diff": diff, "truncated": truncated}


def git_log(path: str = ".", max_count: int = 20) -> dict:
    """Recent commit history, one line per commit (hash + subject)."""
    repo, err = confined_path(path)
    if err:
        return err
    try:
        max_count = int(max_count)
    except (TypeError, ValueError):
        max_count = 20
    max_count = max(1, min(max_count, 100))

    result = _run_git(repo, "log", f"--max-count={max_count}", "--oneline")
    if not result["ok"]:
        return {"error": result["error"]}
    log_text, truncated = _truncate(result["stdout"], _MAX_LOG_CHARS)
    return {"path": str(repo), "log": log_text.rstrip(), "truncated": truncated}


def git_commit(path: str = ".", message: str = "", all: bool = True) -> dict:
    """Commit working-tree changes (tracked + untracked when all is true)."""
    repo, err = confined_path(path)
    if err:
        return err
    message = (message or "").strip()
    if not message:
        return {"error": "message must not be empty"}

    refused = _check_git_mutation("git_commit", str(repo), f"message: {message!r}")
    if refused:
        return refused

    if all:
        add = _run_git(repo, "add", "-A")
        if not add["ok"]:
            return {"error": f"git add failed: {add['error']}"}
    result = _run_git(repo, "commit", "-m", message)
    if not result["ok"]:
        return {"error": result["error"]}

    head = _run_git(repo, "rev-parse", "HEAD")
    commit = head["stdout"].strip() if head["ok"] else ""
    return {"path": str(repo), "committed": True, "commit": commit, "message": message}


def git_checkpoint(path: str = ".") -> dict:
    """Snapshot the whole working tree as a commit, returning its hash.

    Stages tracked and untracked changes (`git add -A`) and commits them with
    a generated message. Used as a revert point for git_rollback. Always
    records a commit (empty allowed) so rollback has a known target even when
    the tree is clean.
    """
    repo, err = confined_path(path)
    if err:
        return err
    refused = _check_git_mutation("git_checkpoint", str(repo), "snapshot commit")
    if refused:
        return refused

    message = f"light-agent checkpoint {time.strftime('%Y-%m-%d %H:%M:%S')}"
    add = _run_git(repo, "add", "-A")
    if not add["ok"]:
        return {"error": f"git add failed: {add['error']}"}
    result = _run_git(repo, "commit", "-m", message, "--allow-empty")
    if not result["ok"]:
        return {"error": result["error"]}

    head = _run_git(repo, "rev-parse", "HEAD")
    commit = head["stdout"].strip() if head["ok"] else ""
    return {"path": str(repo), "checkpoint": commit, "message": message}


def git_rollback(path: str = ".", checkpoint: str = "") -> dict:
    """Discard all changes since a checkpoint, returning to that commit.

    Resets the branch to the checkpoint commit and removes untracked files
    (`git reset --hard` + `git clean -fd`). This is destructive by design and
    gated by git_mutation_mode; ignored files are left untouched.
    """
    repo, err = confined_path(path)
    if err:
        return err
    checkpoint = (checkpoint or "").strip()
    if not checkpoint:
        return {"error": "checkpoint must be a commit hash"}

    refused = _check_git_mutation("git_rollback", str(repo), f"reset to {checkpoint!r}")
    if refused:
        return refused

    check = _run_git(repo, "rev-parse", "--verify", f"{checkpoint}^{{commit}}")
    if not check["ok"]:
        return {"error": f"Not a valid commit: {checkpoint!r}"}

    reset = _run_git(repo, "reset", "--hard", checkpoint)
    if not reset["ok"]:
        return {"error": f"reset failed: {reset['error']}"}
    clean = _run_git(repo, "clean", "-fd")
    if not clean["ok"]:
        return {"error": f"clean failed: {clean['error']}"}

    return {"path": str(repo), "rolled_back_to": checkpoint}


SCHEMAS = [
    (
        {
            "type": "function",
            "function": {
                "name": "git_status",
                "description": (
                    "Show git working-tree status: current branch plus changed files. "
                    "Read-only; never modifies the repository."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Directory inside the repo (defaults to the current directory).",
                        },
                    },
                    "required": [],
                },
            },
        },
        git_status,
    ),
    (
        {
            "type": "function",
            "function": {
                "name": "git_diff",
                "description": (
                    "Show the unified diff of uncommitted changes (or staged changes "
                    "when staged is true). Read-only; never modifies the repository."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Directory inside the repo (defaults to the current directory).",
                        },
                        "staged": {
                            "type": "boolean",
                            "description": "Show staged (index) changes instead of working-tree changes. Defaults to false.",
                        },
                    },
                    "required": [],
                },
            },
        },
        git_diff,
    ),
    (
        {
            "type": "function",
            "function": {
                "name": "git_log",
                "description": (
                    "Show recent git commit history, one line per commit. "
                    "Read-only; never modifies the repository."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Directory inside the repo (defaults to the current directory).",
                        },
                        "max_count": {
                            "type": "integer",
                            "description": "Maximum commits to show (1-100, defaults to 20).",
                        },
                    },
                    "required": [],
                },
            },
        },
        git_log,
    ),
    (
        {
            "type": "function",
            "function": {
                "name": "git_commit",
                "description": (
                    "Commit working-tree changes (tracked and, when all is true, untracked). "
                    "Mutating; gated by git_mutation_mode (off/confirm/allow)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Directory inside the repo (defaults to the current directory).",
                        },
                        "message": {
                            "type": "string",
                            "description": "Commit message (required).",
                        },
                        "all": {
                            "type": "boolean",
                            "description": "Stage tracked and untracked changes before committing. Defaults to true.",
                        },
                    },
                    "required": ["message"],
                },
            },
        },
        git_commit,
    ),
    (
        {
            "type": "function",
            "function": {
                "name": "git_checkpoint",
                "description": (
                    "Snapshot the whole working tree as a commit and return its hash, "
                    "for later rollback. Mutating; gated by git_mutation_mode."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Directory inside the repo (defaults to the current directory).",
                        },
                    },
                    "required": [],
                },
            },
        },
        git_checkpoint,
    ),
    (
        {
            "type": "function",
            "function": {
                "name": "git_rollback",
                "description": (
                    "Discard all changes since a checkpoint commit (reset --hard + clean). "
                    "Destructive; gated by git_mutation_mode (off/confirm/allow)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Directory inside the repo (defaults to the current directory).",
                        },
                        "checkpoint": {
                            "type": "string",
                            "description": "Commit hash to return to (required).",
                        },
                    },
                    "required": ["checkpoint"],
                },
            },
        },
        git_rollback,
    ),
]
