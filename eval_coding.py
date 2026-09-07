"""
eval_coding.py
Offline-first coding-skill evaluation harness for the coder leaf.

Runs a JSON list of tasks against the coder model (qwen2.5-coder:3b, via
coder.ask_coder) and scores each with deterministic check rules, so you get a
pass rate instead of eyeballing output. The runner/scorer/loader are pure
Python and unit-testable without Ollama; only the actual model call needs
Ollama running.

Usage:
    python eval_coding.py                       # run all tasks in coding_tasks.json
    python eval_coding.py --tasks my.json --limit 2 --verbose

Task shape (see coding_tasks.json):
    {
        "id":     "reverse_string",        # short label
        "prompt": "Write a Python function ...",   # sent to the coder
        "context": "x = 1",                # optional file content folded in
        "checks": [                        # all must pass for the task to pass
            {"type": "contains",     "value": "def reverse_string"},
            {"type": "not_contains", "value": "```"},
            {"type": "regex",        "value": "s[::-1]|reversed\\("}
        ]
    }

Check types:
    contains      output contains the value (substring)
    not_contains  output does NOT contain the value
    regex         value matches the output via re.search
Each check may set "case_sensitive": false to match case-insensitively.

Exit code is the number of failed tasks (0 = all passed), capped at 255.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from coder import ask_coder
from config import load_config

_CHECK_TYPES = {"contains", "not_contains", "regex"}


def load_tasks(path: str | Path) -> list[dict]:
    """Load a JSON task list, skipping entries with no prompt."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"Task file {path!r} must contain a JSON list.")
    return [t for t in raw if isinstance(t, dict) and (t.get("prompt") or "").strip()]


def run_checks(output: str, checks: list[dict]) -> list[dict]:
    """Apply every check to the output; return the list of failed checks.

    A failed check is the original dict, shallow-copied and annotated with a
    short `error` field explaining why it failed (or that it is malformed).
    """
    failures: list[dict] = []
    for check in checks or []:
        ctype = str(check.get("type") or "").lower()
        value = check.get("value", "")
        case_sensitive = bool(check.get("case_sensitive", True))
        failed: dict | None = None

        if ctype == "contains":
            hay = output if case_sensitive else output.lower()
            needle = value if case_sensitive else value.lower()
            if needle not in hay:
                failed = {**check, "error": f"missing substring {value!r}"}
        elif ctype == "not_contains":
            hay = output if case_sensitive else output.lower()
            needle = value if case_sensitive else value.lower()
            if needle in hay:
                failed = {**check, "error": f"forbidden substring present: {value!r}"}
        elif ctype == "regex":
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                if re.search(value, output, flags) is None:
                    failed = {**check, "error": f"regex did not match: {value!r}"}
            except re.error as exc:
                failed = {**check, "error": f"bad regex {value!r}: {exc}"}
        else:
            failed = {**check, "error": f"unknown check type {ctype!r}"}

        if failed is not None:
            failures.append(failed)
    return failures


def evaluate_task(config: dict, task: dict, ask=ask_coder) -> dict:
    """Run one task against the coder and score it. Never raises."""
    prompt = (task.get("prompt") or "").strip()
    context = task.get("context") or None
    try:
        output = ask(config, prompt, context)
    except Exception as exc:  # Ollama down / timeout / transport error
        return {
            "id": task.get("id"),
            "passed": False,
            "output": "",
            "failures": [{"type": "transport", "error": str(exc)}],
        }
    failures = run_checks(output, task.get("checks", []))
    return {
        "id": task.get("id"),
        "passed": not failures,
        "output": output,
        "failures": failures,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="eval_coding",
        description="Score coding tasks against the coder leaf model.",
    )
    parser.add_argument(
        "--tasks", default="coding_tasks.json", help="Path to the JSON task list."
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="Only run the first N tasks (0 = all)."
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Print each task's full output."
    )
    args = parser.parse_args(argv)

    try:
        tasks = load_tasks(args.tasks)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[error] Could not load tasks: {exc}", file=sys.stderr)
        return 2

    if args.limit > 0:
        tasks = tasks[: args.limit]
    if not tasks:
        print("[error] No tasks to run.", file=sys.stderr)
        return 2

    config = load_config()
    passed = 0
    for index, task in enumerate(tasks, 1):
        result = evaluate_task(config, task)
        ok = result["passed"]
        passed += ok
        print(f"[{index}/{len(tasks)}] {'PASS' if ok else 'FAIL'}  {result['id']}")
        if not ok:
            for failure in result["failures"]:
                print(f"      check: {failure}")
        if args.verbose:
            print(f"      prompt: {task.get('prompt', '')!r}")
            print(f"      output: {result['output']!r}")

    print(f"\n{passed}/{len(tasks)} tasks passed.")
    return min(passed == len(tasks) and 0 or len(tasks) - passed, 255)


if __name__ == "__main__":
    sys.exit(main())
