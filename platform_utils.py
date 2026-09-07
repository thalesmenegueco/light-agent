"""
platform_utils.py
Small dispatch table for OS-specific behavior, so skills never branch on
sys.platform directly. Add new OS-specific helpers here as skills need them.
"""

import logging
import shlex
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def open_path(path: str) -> None:
    """Open a file or folder with the OS default application."""
    p = Path(path)
    if sys.platform == "win32":
        import os
        try:
            os.startfile(str(p))  # noqa: S606 - intentional, Windows-only API
        except OSError as exc:
            logger.warning("Could not open %r: %s", path, exc)
        return

    cmd = ["open", str(p)] if sys.platform == "darwin" else ["xdg-open", str(p)]
    try:
        proc = subprocess.run(cmd, check=False)
    except OSError as exc:
        logger.warning("Could not launch %r: %s", cmd, exc)
        return
    if proc.returncode != 0:
        logger.warning("Opener %r exited with code %s", cmd, proc.returncode)


class PathOutsideRootError(ValueError):
    """Raised when a path resolves outside the configured project root."""


_PROJECT_ROOT: Path | None = None


def set_project_root(root: str) -> None:
    """Set the directory all file/git/search paths must resolve within.

    An empty string (or None) disables confinement -- the default -- so paths
    behave exactly as before: resolved against the current working directory.
    """
    global _PROJECT_ROOT
    raw = (root or "").strip()
    _PROJECT_ROOT = Path(raw).expanduser().resolve() if raw else None


def get_project_root() -> Path | None:
    return _PROJECT_ROOT


def normalize_path(raw_path: str) -> Path:
    """Expand ~ and environment vars and resolve to an absolute Path.

    When a project root is configured, relative paths are resolved against it,
    and any path that escapes the root (via '..', an absolute path, or a
    symlink) raises PathOutsideRootError.
    """
    p = Path(raw_path).expanduser()
    root = _PROJECT_ROOT
    if root is None:
        return p.resolve()

    if not p.is_absolute():
        p = root / p
    resolved = p.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise PathOutsideRootError(
            f"Path is outside the project root ({root}): {raw_path!r}"
        ) from None
    return resolved


def confined_path(raw_path: str) -> tuple[Path | None, dict | None]:
    """Resolve a path within the project root.

    Returns (resolved_path, None) on success, or (None, {"error": ...}) when
    the path escapes the configured root. Skill functions use this so a
    confinement violation becomes a normal error result instead of an exception.
    """
    try:
        return normalize_path(raw_path), None
    except PathOutsideRootError as exc:
        return None, {"error": str(exc)}


def split_command(command: str) -> list[str]:
    """Split a command line into argv (shell=False), honoring OS quoting rules."""
    return shlex.split(command, posix=(sys.platform != "win32"))
