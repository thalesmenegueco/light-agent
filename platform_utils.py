"""
platform_utils.py
Small dispatch table for OS-specific behavior, so skills never branch on
sys.platform directly. Add new OS-specific helpers here as skills need them.
"""

import subprocess
import sys
from pathlib import Path


def open_path(path: str) -> None:
    """Open a file or folder with the OS default application."""
    p = Path(path)
    if sys.platform == "win32":
        import os
        os.startfile(str(p))  # noqa: S606 - intentional, Windows-only API
    elif sys.platform == "darwin":
        subprocess.run(["open", str(p)], check=False)
    else:
        subprocess.run(["xdg-open", str(p)], check=False)


def normalize_path(raw_path: str) -> Path:
    """Expand ~ and environment vars, resolve to an absolute Path."""
    return Path(raw_path).expanduser().resolve()
