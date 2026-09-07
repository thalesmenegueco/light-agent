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
from pathlib import Path

from platform_utils import confined_path

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 30       # seconds; large repos can be slow
_MAX_DIFF_CHARS = 8000  # keep the router's context small
_MAX_LOG_CHARS = 4000


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
]
