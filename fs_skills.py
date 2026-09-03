"""
skills/fs_skills.py
Deterministic filesystem skills. These run as plain Python -- no LLM needed
to execute them, only to decide (via tool-calling) that they should run.

Each skill module exposes a SCHEMAS list of (schema_dict, function) pairs.
skills/__init__.py auto-discovers this convention.
"""

from pathlib import Path

from platform_utils import normalize_path


def list_files(path: str) -> dict:
    """List file and folder names inside a given directory."""
    p = normalize_path(path)
    if not p.exists():
        return {"error": f"Path not found: {p}"}
    if not p.is_dir():
        return {"error": f"Not a directory: {p}"}

    files = sorted(entry.name for entry in p.iterdir() if entry.is_file())
    folders = sorted(entry.name for entry in p.iterdir() if entry.is_dir())
    return {"path": str(p), "files": files, "folders": folders}


def read_file(path: str, max_chars: int = 8000) -> dict:
    """Read a text file's content (truncated for context safety)."""
    p = normalize_path(path)
    if not p.exists():
        return {"error": f"File not found: {p}"}
    if not p.is_file():
        return {"error": f"Not a file: {p}"}

    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"error": f"Could not read file: {exc}"}

    truncated = len(content) > max_chars
    return {
        "path": str(p),
        "content": content[:max_chars],
        "truncated": truncated,
    }


def move_file(source: str, destination: str) -> dict:
    """Move or rename a file."""
    src = normalize_path(source)
    dst = normalize_path(destination)
    if not src.exists():
        return {"error": f"Source not found: {src}"}

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
    except OSError as exc:
        return {"error": f"Move failed: {exc}"}

    return {"moved_to": str(dst)}


SCHEMAS = [
    (
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List file and folder names inside a given directory path.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Folder path to list."}
                    },
                    "required": ["path"],
                },
            },
        },
        list_files,
    ),
    (
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read the text content of a file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path to read."}
                    },
                    "required": ["path"],
                },
            },
        },
        read_file,
    ),
    (
        {
            "type": "function",
            "function": {
                "name": "move_file",
                "description": "Move or rename a file from source to destination path.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "destination": {"type": "string"},
                    },
                    "required": ["source", "destination"],
                },
            },
        },
        move_file,
    ),
]
