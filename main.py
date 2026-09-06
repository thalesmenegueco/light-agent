"""
main.py
CLI entry point for mini-agent.

Run: python main.py
"""

import argparse
import logging
import re
import sys
from typing import Callable, NamedTuple

import requests

from config import load_config
from logging_setup import setup_logging
from router import handle_message, warm_up
from skills import DISPATCH, init_skills, run_command_skills

logger = logging.getLogger(__name__)


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


def _build_no_args(match) -> dict:
    return {}


def _build_git_path_args(match) -> dict:
    path = match.groupdict().get("path")
    return {"path": path.strip()} if path else {}


def _build_git_log_args(match) -> dict:
    args = {}
    count = match.groupdict().get("count")
    if count:
        args["max_count"] = int(count)
    path = match.groupdict().get("path")
    if path:
        args["path"] = path.strip()
    return args


def _build_git_diff_args(match) -> dict:
    args = {"staged": bool(match.groupdict().get("staged"))}
    path = match.groupdict().get("path")
    if path:
        args["path"] = path.strip()
    return args


def _build_read_file_args(match) -> dict:
    return {"path": match.group("path").strip()}


def _format_git_status(result: dict) -> str | None:
    if "error" in result:
        return None
    status = result.get("status", "").rstrip()
    header = f"Git status ({result.get('path', '.')}):"
    body = status if status else "(clean working tree)"
    return f"{header}\n{body}"


def _format_git_log(result: dict) -> str | None:
    if "error" in result:
        return None
    log = result.get("log", "").rstrip()
    header = f"Recent commits ({result.get('path', '.')}):"
    body = log if log else "(no commits)"
    suffix = "\n[truncated]" if result.get("truncated") else ""
    return f"{header}\n{body}{suffix}"


def _format_git_diff(result: dict) -> str | None:
    if "error" in result:
        return None
    diff = result.get("diff", "")
    label = "staged changes" if result.get("staged") else "working-tree changes"
    if not diff.strip():
        return f"No {label}."
    suffix = "\n[truncated]" if result.get("truncated") else ""
    return f"Diff of {label}:\n{diff}{suffix}"


def _format_list_skills(result: dict) -> str | None:
    if "error" in result:
        return None
    skills = result.get("skills", [])
    lines = [f"I can use {result.get('count', len(skills))} tools:"]
    for skill in skills:
        name = skill.get("name", "")
        desc = " ".join(skill.get("description", "").split())
        lines.append(f"  - {name}: {desc}")
    return "\n".join(lines)


def _format_read_file(result: dict) -> str | None:
    if "error" in result:
        return None
    content = result.get("content", "")
    header = f"--- {result.get('path', '')} ---"
    suffix = "\n[truncated]" if result.get("truncated") else ""
    return f"{header}\n{content}{suffix}"


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
    # "git status" / "what's the git status?" / "what changed?" (optional "in <path>")
    FastPath(
        re.compile(
            r"^(?:please\s+)?(?:git\s+status"
            r"|(?:what(?:'s| is)\s+)?(?:the\s+)?git\s+status"
            r"|what(?:'s| is)\s+(?:the\s+)?status"
            r"|what(?:'s| is)\s+changed"
            r"|working\s+tree\s+status)"
            r"(?:\s+in\s+['\"]?(?P<path>.+?)['\"]?)?"
            r"[?.!]?\s*$",
            re.IGNORECASE,
        ),
        "git_status",
        _build_git_path_args,
        _format_git_status,
    ),
    # "git log" / "show me the latest commits" / "last 5 commits" / "commit history"
    FastPath(
        re.compile(
            r"^(?:please\s+)?(?:git\s+log"
            r"|(?:show\s+(?:me\s+)?)?(?:the\s+)?(?:latest|recent|last|current)\s+(?:(?P<count>\d+)\s+)?commits"
            r"|(?:show\s+(?:me\s+)?)?(?:the\s+)?(?:commit\s+history|commits))"
            r"(?:\s+in\s+['\"]?(?P<path>.+?)['\"]?)?"
            r"[?.!]?\s*$",
            re.IGNORECASE,
        ),
        "git_log",
        _build_git_log_args,
        _format_git_log,
    ),
    # "git diff" / "show me the diff" / "diff the working tree" (optional "staged", "in <path>")
    FastPath(
        re.compile(
            r"^(?:please\s+)?(?:git\s+diff"
            r"|(?:show\s+(?:me\s+)?(?:the\s+)?diff)"
            r"|diff\s+the\s+working\s+tree"
            r"|what(?:'s| is)\s+(?:the\s+)?diff)"
            r"(?:\s+(?P<staged>--staged|staged))?"
            r"(?:\s+in\s+['\"]?(?P<path>.+?)['\"]?)?"
            r"[?.!]?\s*$",
            re.IGNORECASE,
        ),
        "git_diff",
        _build_git_diff_args,
        _format_git_diff,
    ),
    # "what can you do?" / "list skills" / "help"
    FastPath(
        re.compile(
            r"^(?:please\s+)?(?:what\s+can\s+(?:you|i)\s+do"
            r"|show\s+me\s+what\s+you\s+can\s+do"
            r"|list\s+(?:your\s+)?(?:skills|tools)"
            r"|what\s+tools\s+(?:do\s+you\s+have|are\s+available)"
            r"|what\s+are\s+you\s+capable\s+of"
            r"|help)"
            r"[?.!]?\s*$",
            re.IGNORECASE,
        ),
        "list_skills",
        _build_no_args,
        _format_list_skills,
    ),
    # "read <path>" / "cat <path>" / "show me <path>" / "show me the file <path>"
    FastPath(
        re.compile(
            r"^(?:please\s+)?(?:read|cat|display|type|show)"
            r"(?:\s+(?:me\s+)?)?(?:the\s+)?(?:file\s+|contents?\s+of\s+)?"
            r"['\"]?(?P<path>.+?)['\"]?\s*$",
            re.IGNORECASE,
        ),
        "read_file",
        _build_read_file_args,
        _format_read_file,
    ),
    # "what's in <path>?"
    FastPath(
        re.compile(
            r"^(?:please\s+)?what(?:'s| is)\s+in\s+['\"]?(?P<path>.+?)['\"]?[?.!]?\s*$",
            re.IGNORECASE,
        ),
        "read_file",
        _build_read_file_args,
        _format_read_file,
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
    except requests.RequestException as exc:
        logger.warning("Ollama health check failed: %s", exc)
        return False
    except Exception:
        logger.exception("Unexpected error during Ollama health check")
        return False


def _parse_args(argv=None):
    """Parse CLI flags; --run-command-mode is a session-only (non-persisted) override."""
    parser = argparse.ArgumentParser(
        prog="mini-agent",
        description="Fully-local AI coding assistant backed by Ollama.",
    )
    parser.add_argument(
        "--run-command-mode",
        choices=["off", "confirm", "allowlist", "auto"],
        default=None,
        help="Override run_command_mode for this session only (does not persist to config).",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args()
    config = load_config()
    if args.run_command_mode:
        config["run_command_mode"] = args.run_command_mode
    init_skills(config)
    # Bind the interactive confirmation prompt for run_command. It stays inert
    # while run_command_mode is "off" (the default); the user opts in via config.
    run_command_skills.bind_confirmer(run_command_skills.terminal_confirmer)

    log_file = setup_logging(config)
    if args.run_command_mode:
        logger.info("run_command_mode overridden to %r for this session", args.run_command_mode)
    logger.info("mini-agent starting (log file: %s)", log_file)

    if not check_ollama(config):
        logger.error("Ollama unreachable at %s", config["ollama_host"])
        print(
            f"Could not reach Ollama at {config['ollama_host']}.\n"
            "Make sure Ollama is installed and running, then try again.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Pre-load the router model so the first turn doesn't incur the cold-start
    # delay (~30-45s on CPU-only hardware). Shown so the user knows why startup
    # can pause on first run.
    print("Loading the router model into memory… (first run can take ~30-45s)", flush=True)
    try:
        warm_up(config)
    except requests.RequestException as exc:
        logger.warning("Router warm-up failed: %s", exc)
        print(
            "[warning] Could not pre-load the router model; "
            "the first request may be slow or fail. See the log for details.",
            file=sys.stderr,
        )

    print("\nmini-agent ready. Type 'exit' to quit.")
    if config.get("run_command_mode", "off") != "off":
        print(
            f"[run_command] mode: {config['run_command_mode']} — "
            "commands will prompt for confirmation before running.\n"
        )

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
            logger.error("Ollama request failed: %s", exc)
            print(f"[Ollama error] Could not reach the model: {exc}", file=sys.stderr)
            continue
        except Exception:
            logger.exception("Unhandled error while processing: %r", user_message)
            print(
                "[error] Something went wrong. Details were written to the log file.",
                file=sys.stderr,
            )
            continue

        # Keep context small for the router on limited hardware.
        history = history[-max_history:]
        print(reply)


if __name__ == "__main__":
    main()
