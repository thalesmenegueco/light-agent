"""
skills/verify_skills.py
Deterministic test-runner skill for the autopilot's verification loop.

`run_tests` runs the project's *pre-configured* test command (config key
`test_command`) and reports pass/fail in a structured form, so a higher-level
loop can feed failures back to the coder for iteration.

Safety: the command string is config-side only. The model cannot inject an
arbitrary command -- it can only trigger running the command a human already
configured. The command is executed with shell=False (no shell operators),
inside the confined project root (or an optional cwd), with a timeout and
output truncation.
"""

import logging
import subprocess

from platform_utils import confined_path, split_command

logger = logging.getLogger(__name__)

_CONFIG = None


def bind_config(config: dict) -> None:
    global _CONFIG
    _CONFIG = config


def _resolve_test_cwd(config: dict, cwd_arg: str):
    """Return a working-directory string, an error dict, or None (inherit)."""
    raw = (cwd_arg or "").strip() or (config.get("project_root") or "").strip()
    if not raw:
        return None
    p, err = confined_path(raw)
    if err:
        return {"error": err["error"], "reason": "cwd_outside_root"}
    if not p.is_dir():
        return {"error": f"Test working directory not found: {raw}", "reason": "cwd"}
    return str(p)


def run_tests(cwd: str = "") -> dict:
    """Run the configured test command and report pass/fail.

    Refuses (with a structured error) until `test_command` is set in config,
    so the verification loop is opt-in and the command is always human-chosen.
    """
    config = _CONFIG or {}
    command = (config.get("test_command") or "").strip()
    if not command:
        return {
            "error": "test_command is not configured; set it in config to enable verification.",
            "reason": "not_configured",
        }

    try:
        argv = split_command(command)
    except ValueError as exc:
        return {"error": f"Could not parse test_command: {exc}", "reason": "parse"}
    if not argv:
        return {"error": "test_command must not be empty", "reason": "parse"}

    workdir = _resolve_test_cwd(config, cwd)
    if isinstance(workdir, dict):
        return workdir

    timeout = int(config.get("test_timeout", 120))
    max_out = int(config.get("test_max_output", 8000))

    try:
        proc = subprocess.run(
            argv, cwd=workdir, timeout=timeout,
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        logger.warning("run_tests timed out after %ss", timeout)
        return {"error": f"Test command timed out after {timeout}s.", "reason": "timeout", "command": command}
    except OSError as exc:
        logger.error("run_tests execution failed: %s", exc)
        return {"error": f"Could not run test command: {exc}", "reason": "exec", "command": command}

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    logger.info("run_tests exit=%s", proc.returncode)

    return {
        "command": command,
        "cwd": workdir,
        "exit_code": proc.returncode,
        "passed": proc.returncode == 0,
        "stdout": stdout[:max_out],
        "stderr": stderr[:max_out],
        "stdout_truncated": len(stdout) > max_out,
        "stderr_truncated": len(stderr) > max_out,
    }


SCHEMAS = [
    (
        {
            "type": "function",
            "function": {
                "name": "run_tests",
                "description": (
                    "Run the project's pre-configured test command (config key "
                    "test_command) and report pass/fail. The command is fixed by "
                    "config, so this only runs the human-chosen suite. Use it to "
                    "verify code changes; feed failures back to the coder to fix."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cwd": {
                            "type": "string",
                            "description": "Optional working directory (defaults to the project root).",
                        },
                    },
                    "required": [],
                },
            },
        },
        run_tests,
    ),
]
