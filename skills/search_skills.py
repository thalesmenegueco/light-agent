"""
skills/search_skills.py
Deterministic file search. Runs as plain Python -- no LLM needed to execute
it, only to decide (via tool-calling) that it should run.

Searches a directory tree for files by name or for lines of text by content.
Name matching is case-insensitive; content matching is case-sensitive
(grep-like), so searching for an identifier finds exact occurrences only.
"""

import os
from pathlib import Path

from platform_utils import normalize_path

# Directories skipped during the walk, so searching the project root doesn't
# drown in venv / cache / VCS noise.
_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", ".idea", ".vscode", "node_modules"}
_SKIP_FILE_SUFFIXES = (".pyc",)

_DEFAULT_MAX_RESULTS = 50
_MAX_RESULTS_CAP = 500
_MAX_LINE_CHARS = 300


def search_files(path: str, query: str, mode: str = "content", max_results: int = _DEFAULT_MAX_RESULTS) -> dict:
    """Search a directory for files by name or for text inside files.

    mode: 'name' (filenames contain query), 'content' (lines contain query),
          or 'both'. Name matching is case-insensitive; content is case-sensitive.
    """
    p = normalize_path(path)
    if not p.exists():
        return {"error": f"Path not found: {p}"}
    if not p.is_dir():
        return {"error": f"Not a directory: {p}"}
    if not query or not query.strip():
        return {"error": "Search query must not be empty."}

    mode = (mode or "content").lower()
    if mode not in ("content", "name", "both"):
        return {"error": f"Invalid mode: {mode!r} (use 'content', 'name', or 'both')."}

    try:
        max_results = int(max_results)
    except (TypeError, ValueError):
        max_results = _DEFAULT_MAX_RESULTS
    max_results = max(1, min(max_results, _MAX_RESULTS_CAP))

    query_lower = query.lower()
    files: list[str] = []
    matches: list[dict] = []
    truncated = False

    for root, dirs, filenames in os.walk(p):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in filenames:
            if name.endswith(_SKIP_FILE_SUFFIXES):
                continue
            full = Path(root) / name

            if mode in ("name", "both") and query_lower in name.lower():
                if len(files) < max_results:
                    files.append(str(full))
                else:
                    truncated = True

            if mode in ("content", "both"):
                try:
                    text = full.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue  # binary or unreadable: skip
                for lineno, line in enumerate(text.splitlines(), 1):
                    if query in line:
                        if len(matches) < max_results:
                            matches.append(
                                {
                                    "file": str(full),
                                    "line": lineno,
                                    "text": line.strip()[:_MAX_LINE_CHARS],
                                }
                            )
                        else:
                            truncated = True
                            break

    # Sort deterministically: os.walk yields entries in raw filesystem order
    # (unsorted, OS-dependent), so without this the results -- and anything the
    # router treats as "the first one" -- would be arbitrary across machines.
    files.sort()
    matches.sort(key=lambda m: (m["file"], m["line"]))

    return {
        "path": str(p),
        "query": query,
        "mode": mode,
        "files": files,
        "matches": matches,
        "count": len(files) + len(matches),
        "truncated": truncated,
    }


SCHEMAS = [
    (
        {
            "type": "function",
            "function": {
                "name": "search_files",
                "description": (
                    "Search a directory tree for files by name or for text inside files. "
                    "Use mode 'name' to find files whose name contains the query, "
                    "'content' to find lines containing the query, or 'both'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Directory to search recursively.",
                        },
                        "query": {
                            "type": "string",
                            "description": "Text (or filename substring) to search for.",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["content", "name", "both"],
                            "description": "What to search. Defaults to 'content'.",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum matches to return. Defaults to 50.",
                        },
                    },
                    "required": ["path", "query"],
                },
            },
        },
        search_files,
    ),
]
