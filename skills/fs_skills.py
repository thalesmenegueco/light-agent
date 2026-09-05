"""
skills/fs_skills.py
Deterministic filesystem skills. These run as plain Python -- no LLM needed
to execute them, only to decide (via tool-calling) that they should run.

Each skill module exposes a SCHEMAS list of (schema_dict, function) pairs.
skills/__init__.py auto-discovers this convention.
"""

from pathlib import Path

from platform_utils import normalize_path, open_path


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


def write_file(path: str, content: str, overwrite: bool = False) -> dict:
    """Create a text file with the given content (refuses to clobber by default)."""
    p = normalize_path(path)
    if p.exists() and not overwrite:
        return {"error": f"File already exists: {p} (set overwrite=true to replace it)"}

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except OSError as exc:
        return {"error": f"Write failed: {exc}"}

    return {"written_to": str(p)}


def append_file(path: str, content: str) -> dict:
    """Append text to the end of a file, creating it if needed."""
    p = normalize_path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(content)
    except OSError as exc:
        return {"error": f"Append failed: {exc}"}

    return {"appended_to": str(p)}


def replace_in_file(path: str, old: str, new: str, replace_all: bool = False) -> dict:
    """Replace the first occurrence (or all) of `old` with `new` in a text file.

    Refuses ambiguous edits: if `old` appears more than once and `replace_all`
    is false, returns an error so the caller can provide a more specific match.
    """
    p = normalize_path(path)
    if not p.exists():
        return {"error": f"File not found: {p}"}
    if not p.is_file():
        return {"error": f"Not a file: {p}"}
    if not old:
        return {"error": "old must not be empty"}

    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"error": f"Not a UTF-8 text file: {p}"}
    except OSError as exc:
        return {"error": f"Could not read file: {exc}"}

    occurrences = text.count(old)
    if occurrences == 0:
        return {"error": f"Text not found in {p}: {old!r}"}
    if occurrences > 1 and not replace_all:
        return {
            "error": (
                f"Text appears {occurrences} times in {p}. "
                "Provide a more specific `old` string, or set replace_all=true."
            )
        }

    new_text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    try:
        p.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        return {"error": f"Write failed: {exc}"}

    return {"path": str(p), "replacements": occurrences if replace_all else 1}


def open_file(path: str) -> dict:
    """Open a file or folder with the OS default application."""
    p = normalize_path(path)
    if not p.exists():
        return {"error": f"Path not found: {p}"}

    open_path(str(p))
    return {"opened": str(p)}


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
    (
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Create a text file with the given content, or overwrite it if overwrite is true.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path to write."},
                        "content": {"type": "string", "description": "Full text content to write."},
                        "overwrite": {
                            "type": "boolean",
                            "description": "Whether to replace an existing file. Defaults to false.",
                        },
                    },
                    "required": ["path", "content"],
                },
            },
        },
        write_file,
    ),
    (
        {
            "type": "function",
            "function": {
                "name": "append_file",
                "description": "Append text to the end of a file, creating it if needed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path to append to."},
                        "content": {"type": "string", "description": "Text to append."},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        append_file,
    ),
    (
        {
            "type": "function",
            "function": {
                "name": "replace_in_file",
                "description": (
                    "Replace text in a file: the first occurrence by default, or every "
                    "occurrence when replace_all is true. Refuses ambiguous edits when "
                    "the text appears multiple times and replace_all is false."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path to edit."},
                        "old": {
                            "type": "string",
                            "description": "Exact text to find (must be unique unless replace_all is true).",
                        },
                        "new": {"type": "string", "description": "Replacement text."},
                        "replace_all": {
                            "type": "boolean",
                            "description": "Replace every occurrence instead of just the first. Defaults to false.",
                        },
                    },
                    "required": ["path", "old", "new"],
                },
            },
        },
        replace_in_file,
    ),
    (
        {
            "type": "function",
            "function": {
                "name": "open_file",
                "description": "Open a file or folder with the OS default application.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File or folder path to open."},
                    },
                    "required": ["path"],
                },
            },
        },
        open_file,
    ),
]
