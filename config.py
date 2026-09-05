"""
config.py
Handles cross-platform config storage for mini-agent (Windows 10 / Linux Mint).

Config lives at:
  Windows: %APPDATA%\\MiniAgent\\config.json
  Linux:   ~/.config/mini-agent/config.json
"""

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

APP_NAME = "MiniAgent"

DEFAULT_CONFIG = {
    "ollama_host": "http://localhost:11434",
    "router_model": "qwen3:4b-instruct",
    "coder_model": "qwen2.5-coder:3b",
    "router_temperature": 0.2,
    "coder_temperature": 0.1,
    "max_history_messages": 12,   # keep the router's context small on limited hardware
    "log_level": "INFO",          # DEBUG / INFO / WARNING / ERROR for mini-agent.log
    "log_file": "",               # empty = <app_dir>/logs/mini-agent.log
}


def get_base_dir() -> Path:
    """Where config.json / skills manifest live (persistent, per-user)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / APP_NAME
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        return base / "mini-agent"


def get_app_dir() -> Path:
    """
    Where the app itself lives on disk (for locating the skills/ folder
    next to the executable when frozen with PyInstaller).
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def load_config() -> dict:
    base_dir = get_base_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    config_path = base_dir / "config.json"

    if not config_path.exists():
        config_path.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
        return dict(DEFAULT_CONFIG)

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        merged = dict(DEFAULT_CONFIG)
        merged.update(data)
        return merged
    except (json.JSONDecodeError, OSError):
        # Corrupted config: fall back to defaults rather than crashing, but say so.
        logger.warning("Corrupted config at %s; using defaults.", config_path)
        return dict(DEFAULT_CONFIG)


def save_config(config: dict) -> None:
    base_dir = get_base_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / "config.json"
    try:
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.error("Could not save config to %s: %s", path, exc)
