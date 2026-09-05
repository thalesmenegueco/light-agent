"""
platform_utils.py
Small dispatch table for OS-specific behavior, so skills never branch on
sys.platform directly. Add new OS-specific helpers here as skills need them.
"""

import logging
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


def normalize_path(raw_path: str) -> Path:
    """Expand ~ and environment vars, resolve to an absolute Path."""
    return Path(raw_path).expanduser().resolve()
