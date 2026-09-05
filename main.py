"""
main.py
CLI entry point for mini-agent.

Run: python main.py
"""

import re
import sys
from typing import Callable, NamedTuple

import requests

from config import load_config
from router import handle_message
from skills import DISPATCH, init_skills


# ---------------------------------------------------------------------------
# Fast path: a table of (pattern, skill, build_args, format_result) entries.
#
# Each entry short-circuits a very common, deterministic request straight to a
# skill, skipping the router LLM call entirely. This is an optimization, not a
# requirement -- anything that doesn't match (or whose skill returns an error)
# falls through to the full router, which can still call the same skills.
#
#   build_args(match)   -> dict          kwargs for DISPATCH[skill]
#   format_result(result) -> str | None  None means "fall through to router"
# ---------------------------------------------------------------------------


class FastPath(NamedTuple):
    pattern: re.Pattern
    skill: str
    build_args: Callable
    format_result: Callable


def _build_list_files_args(match) -> dict:
    return {"path": match.group("path").strip()}


def _format_list_files(result: dict) -> str | None:
    if "error" in result:
        return None
    lines = [f"Files in {result['path']}:"]
    files = result.get("files", [])
    if files:
        lines += [f"  - {name}" for name in files]
    else:
        lines.append("  (no files)")
    return "\n".join(lines)


def _build_open_args(match) -> dict:
    return {"path": match.group("path").strip()}


def _format_open(result: dict) -> str | None:
    if "error" in result:
        return None
    return f"Opened {result['opened']}"


def _build_search_content_args(match) -> dict:
    return {
        "path": match.group("path").strip(),
        "query": match.group("query").strip(),
        "mode": "content",
    }


def _build_search_name_args(match) -> dict:
    return {
        "path": match.group("path").strip(),
        "query": match.group("query").strip(),
        "mode": "name",
    }


def _format_search(result: dict) -> str | None:
    if "error" in result:
        return None
    count = result.get("count", 0)
    if count == 0:
        return f"No matches for {result['query']!r} in {result['path']}."
    lines = [f"Found {count} match(es) for {result['query']!r} in {result['path']}:"]
    for match in result.get("matches", []):
        lines.append(f"  {match['file']}:{match['line']}: {match['text']}")
    for file_path in result.get("files", []):
        lines.append(f"  {file_path}")
    if result.get("truncated"):
        lines.append("  (results truncated)")
    return "\n".join(lines)


_FAST_PATHS: list[FastPath] = [
    # "list files in <path>"
    FastPath(
        re.compile(
            r"(?:list|show|what are)\s+(?:the\s+)?(?:files?|names? of files?)\s+.*?"
            r"(?:in|inside|of|from)\s+['\"]?(?P<path>[^'\"]+)['\"]?\s*$",
            re.IGNORECASE,
        ),
        "list_files",
        _build_list_files_args,
        _format_list_files,
    ),
    # "open <path>" / "open the folder <path>"
    FastPath(
        re.compile(
            r"^(?:please\s+)?(?:open|open up|launch)\s+(?:the\s+)?"
            r"(?:file\s+|folder\s+|directory\s+)?['\"]?(?P<path>.+?)['\"]?\s*$",
            re.IGNORECASE,
        ),
        "open_file",
        _build_open_args,
        _format_open,
    ),
    # "search for <query> in <path>" / "grep for <query> in <path>"
    FastPath(
        re.compile(
            r"^(?:please\s+)?(?:search|grep|look)\s+for\s+['\"]?(?P<query>.+?)['\"]?"
            r"\s+(?:in|inside|within)\s+['\"]?(?P<path>[^'\"]+?)['\"]?\s*$",
            re.IGNORECASE,
        ),
        "search_files",
        _build_search_content_args,
        _format_search,
    ),
    # "find files named <query> in <path>"
    FastPath(
        re.compile(
            r"^(?:please\s+)?(?:find|list|show)\s+(?:files?|folders?)\s+"
            r"(?:named|called|matching)\s+['\"]?(?P<query>.+?)['\"]?"
            r"\s+(?:in|inside|within)\s+['\"]?(?P<path>[^'\"]+?)['\"]?\s*$",
            re.IGNORECASE,
        ),
        "search_files",
        _build_search_name_args,
        _format_search,
    ),
]


def try_fast_path(user_message: str):
    """Return a result string if a deterministic fast-path matched, else None."""
    message = user_message.strip()
    for entry in _FAST_PATHS:
        match = entry.pattern.search(message)
        if not match:
            continue
        args = entry.build_args(match)
        result = DISPATCH[entry.skill](**args)
        rendered = entry.format_result(result)
        if rendered is not None:
            return rendered
    return None


def check_ollama(config: dict) -> bool:
    try:
        resp = requests.get(f"{config['ollama_host']}/api/tags", timeout=5)
        resp.raise_for_status()
        return True
    except requests.RequestException:
        return False


def main() -> None:
    config = load_config()
    init_skills(config)

    if not check_ollama(config):
        print(
            f"Could not reach Ollama at {config['ollama_host']}.\n"
            "Make sure Ollama is installed and running, then try again."
        )
        sys.exit(1)

    print("mini-agent ready. Type 'exit' to quit.\n")

    history: list[dict] = []
    max_history = config.get("max_history_messages", 12)

    while True:
        try:
            user_message = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_message:
            continue
        if user_message.lower() in {"exit", "quit"}:
            break

        fast_result = try_fast_path(user_message)
        if fast_result is not None:
            print(fast_result)
            continue

        try:
            reply, history = handle_message(config, history, user_message)
        except requests.RequestException as exc:
            print(f"[Ollama error] {exc}")
            continue

        # Keep context small for the router on limited hardware.
        history = history[-max_history:]
        print(reply)


if __name__ == "__main__":
    main()
