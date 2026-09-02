"""
main.py
CLI entry point for mini-agent.

Run: python main.py
"""

import re
import sys

import requests

from config import load_config
from router import handle_message
from skills import DISPATCH, init_skills

# Quick keyword prefilter for very common deterministic requests, so we can
# skip the router LLM call entirely for the most frequent commands.
# This is an optimization, not a requirement -- anything not matched here
# falls through to the full router, which can still call the same skills.
_LIST_FILES_PATTERN = re.compile(
    r"(?:list|show|what are)\s+(?:the\s+)?(?:files?|names? of files?)\s+.*?"
    r"(?:in|inside|of|from)\s+['\"]?(?P<path>[^'\"]+)['\"]?\s*$",
    re.IGNORECASE,
)


def try_fast_path(user_message: str):
    """Return a result string if a deterministic fast-path matched, else None."""
    match = _LIST_FILES_PATTERN.search(user_message.strip())
    if not match:
        return None
    path = match.group("path").strip()
    result = DISPATCH["list_files"](path=path)
    if "error" in result:
        return None  # let the full router handle ambiguous/failed cases
    lines = [f"Files in {result['path']}:"]
    lines += [f"  - {name}" for name in result["files"]] or ["  (no files)"]
    return "\n".join(lines)


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
