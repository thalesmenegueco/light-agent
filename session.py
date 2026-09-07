"""
session.py
Durable session state for unattended autopilot runs: goal, plan, progress,
budget counters, and a compact conversation history. Persisted to JSON so a
crashed or interrupted run can be resumed instead of restarted from zero.

State lives at <base_dir>/session.json by default (see config.get_base_dir);
pass an explicit `path` to any function to override (used by tests).
"""

import json
import logging
import time
from pathlib import Path

from config import get_base_dir

logger = logging.getLogger(__name__)

_SESSION_FILE = "session.json"


def default_session_path() -> Path:
    """Where session state lives: <base_dir>/session.json (dir is created)."""
    base = get_base_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base / _SESSION_FILE


def new_session(goal: str) -> dict:
    now = time.time()
    return {
        "goal": goal,
        "plan": [],           # [{"step": str, "status": "pending|in_progress|done", "result": str}]
        "current_step": 0,
        "status": "planning",  # planning | running | blocked | done
        "steps_used": 0,
        "tokens_used": 0,
        "started_at": now,
        "updated_at": now,
        "history": [],        # compact conversation, for resume context
        "blocked_reason": None,
    }


def load_session(path: Path | None = None) -> dict | None:
    p = path or default_session_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Corrupted session state at %s; ignoring.", p)
        return None


def save_session(state: dict, path: Path | None = None) -> None:
    state["updated_at"] = time.time()
    p = path or default_session_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.error("Could not save session state to %s: %s", p, exc)


def clear_session(path: Path | None = None) -> None:
    p = path or default_session_path()
    try:
        p.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not clear session state at %s: %s", p, exc)
