"""
skills/fs_skills.py
Deterministic filesystem skills. These run as plain Python -- no LLM needed
to execute them, only to decide (via tool-calling) that they should run.

Each skill module exposes a SCHEMAS list of (schema_dict, function) pairs.
skills/__init__.py auto-discovers this convention.
"""

from pathlib import Path

from platform_utils import confined_path, open_path

_CONFIG = None
_FILE_CONFIRMER = None  # callable(op, path, detail) -> "allow" | "deny"

_MUTATION_MODES = {"off", "confirm", "allow"}


def bind_config(config: dict) -> None:
    global _CONFIG
    _CONFIG = config


def bind_file_confirmer(confirmer) -> None:
    global _FILE_CONFIRMER
    _FILE_CONFIRMER = confirmer


def terminal_file_confirmer(op: str, path: str, detail: str) -> str:
    """Interactive confirmation prompt for file mutations. Returns allow/deny."""
    print(f"\n{op} wants to mutate a file:")
    print(f"  path  : {path}")
    if detail:
        print(f"  detail: {detail}")
    while True:
        answer = input("Allow? [y]es / [n]o: ").strip().lower()
        if answer in {"y", "yes", "allow"}:
            return "allow"
        if answer in {"n", "no", "deny"}:
            return "deny"
        print("Please answer 'y' or 'n'.")


def _check_mutation(op: str, path: str, detail: str = "") -> dict | None:
    """Return an error dict to refuse a file mutation, or None to allow it.

    Controlled by file_mutation_mode:
      off     -> refuse every mutation
      confirm -> require the injected confirmer (fail-closed if none bound)
      allow   -> proceed without prompting (the default)
    """
    mode = str((_CONFIG or {}).get("file_mutation_mode", "allow")).lower()
    if mode == "off":
        return {
            "error": f"{op} is disabled; set 'file_mutation_mode' in config to enable it.",
            "reason": "mutation_disabled",
            "operation": op,
        }
    if mode not in _MUTATION_MODES:
        return {"error": f"Unknown file_mutation_mode: {mode!r}", "reason": "config", "operation": op}
    if mode == "allow":
        return None

    # mode == "confirm"
    if _FILE_CONFIRMER is None:
        return {
            "error": f"{op} requires confirmation but no confirmer is bound (fail-closed).",
            "reason": "confirm_unavailable",
            "operation": op,
        }
    answer = _FILE_CONFIRMER(op, path, detail)
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


def list_files(path: str) -> dict:
    """List file and folder names inside a given directory."""
    p, err = confined_path(path)
    if err:
        return err
    if not p.exists():
        return {"error": f"Path not found: {p}"}
    if not p.is_dir():
        return {"error": f"Not a directory: {p}"}

    files = sorted(entry.name for entry in p.iterdir() if entry.is_file())
    folders = sorted(entry.name for entry in p.iterdir() if entry.is_dir())
    return {"path": str(p), "files": files, "folders": folders}


def read_file(path: str, max_chars: int = 8000) -> dict:
    """Read a text file's content (truncated for context safety)."""
    p, err = confined_path(path)
    if err:
        return err
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
    src, err = confined_path(source)
    if err:
        return err
    dst, err = confined_path(destination)
    if err:
        return err
    if not src.exists():
        return {"error": f"Source not found: {src}"}

    refused = _check_mutation("move_file", str(src), f"{src} -> {dst}")
    if refused:
        return refused

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
    except OSError as exc:
        return {"error": f"Move failed: {exc}"}

    return {"moved_to": str(dst)}


def write_file(path: str, content: str, overwrite: bool = False) -> dict:
    """Create a text file with the given content (refuses to clobber by default)."""
    p, err = confined_path(path)
    if err:
        return err
    if p.exists() and not overwrite:
        return {"error": f"File already exists: {p} (set overwrite=true to replace it)"}

    refused = _check_mutation("write_file", str(p), f"content: {content[:80]!r}")
    if refused:
        return refused

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except OSError as exc:
        return {"error": f"Write failed: {exc}"}

    return {"written_to": str(p)}


def append_file(path: str, content: str) -> dict:
    """Append text to the end of a file, creating it if needed."""
    p, err = confined_path(path)
    if err:
        return err
    refused = _check_mutation("append_file", str(p), f"append: {content[:80]!r}")
    if refused:
        return refused
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
    p, err = confined_path(path)
    if err:
        return err
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
    refused = _check_mutation("replace_in_file", str(p), f"{old!r} -> {new!r}")
    if refused:
        return refused
    try:
        p.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        return {"error": f"Write failed: {exc}"}

    return {"path": str(p), "replacements": occurrences if replace_all else 1}


def open_file(path: str) -> dict:
    """Open a file or folder with the OS default application."""
    p, err = confined_path(path)
    if err:
        return err
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
