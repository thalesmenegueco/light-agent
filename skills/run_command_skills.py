"""
skills/run_command_skills.py
Local command execution behind a deny-by-default safety policy.

`run_command` is the escape hatch: the router can ask to run a single local
command. Because the command string is attacker-influenced (prompt injection,
hallucination, interpolation), the tool is OFF by default and every request
passes through a multi-stage policy before anything executes:

    off         -> refuse (master switch)
    empty/parse -> refuse
    denylist    -> destructive patterns -> refuse
    eval        -> interpreter -c/-e escapes -> refuse
    tty         -> interactive/privileged programs -> refuse
    network     -> network programs -> refuse unless allow_network
    resolve     -> unknown program -> refuse
    cwd         -> missing working dir -> refuse
    classify    -> readonly / allowlisted / needs-confirmation
    confirm     -> human confirmation (fail-closed if no confirmer)
    execute     -> subprocess.run(shell=config, timeout, DEVNULL stdin)
    truncate    -> cap stdout/stderr
    audit       -> log decision + structured result

The tool schema exposes ONLY `command` and `cwd`. Shell, timeout, allowlist,
denylist, network and cwd-confinement are config-side and cannot be changed
by the model.

Like code_skills/meta_skills, this module needs the loaded config, injected
via `bind_config()`. The confirmation hook is injected separately via
`bind_confirmer()` -- main.py binds the interactive terminal prompt, while
tests/demo bind a scripted one (always allow/deny), keeping the whole
pipeline testable offline.
"""

import logging
import re
import shutil
import subprocess
from pathlib import Path

from config import save_config
from platform_utils import split_command

logger = logging.getLogger(__name__)

_CONFIG = None
_CONFIRMER = None  # callable(command, cwd, program) -> "allow" | "deny" | "allow_always"

_MODES = {"off", "confirm", "allowlist", "auto"}

# --- policy tables (see module docstring for pipeline order) ---

# Read-only programs auto-approved only in "auto" mode.
_READONLY_PROGRAMS = {
    "pwd", "ls", "cat", "head", "tail", "wc", "grep", "find", "which",
    "echo", "date", "uname", "du", "sort", "uniq", "env", "printenv",
    "dirname", "basename", "realpath", "tree", "git",
}

# git subcommands that are safe/read-only (the rest may write or touch network).
_READONLY_GIT_SUBCOMMANDS = {
    "status", "log", "diff", "show", "branch", "rev-parse", "ls-files",
    "remote", "tag", "shortlog", "describe", "reflog", "blame", "stash",
}

# Interactive or privileged programs that need a TTY or escalate privilege.
_INTERACTIVE_PROGRAMS = {
    "vim", "vi", "nano", "emacs", "ed",
    "top", "htop", "btop", "glances",
    "less", "more", "most", "man", "info",
    "watch", "screen", "tmux",
    "ssh", "telnet", "su", "sudo", "login",
}

# Eval-capable interpreters: refuse `-c`/`-e`/`/c` style invocations, which
# would otherwise re-enable arbitrary execution through a non-shell argv.
_EVAL_INTERPRETERS = {
    "sh", "bash", "zsh", "dash", "ksh", "fish",
    "node", "nodejs", "perl", "ruby", "php", "pwsh", "powershell", "cmd",
}
_EVAL_FLAGS = {"-c", "--command", "-e", "--eval", "/c", "/k"}

# Programs that reach the network, gated by run_command_allow_network.
_NETWORK_PROGRAMS = {
    "curl", "wget", "pip", "pip3", "npm", "yarn", "pnpm", "npx",
    "cargo", "gem", "go", "rustup",
    "scp", "sftp", "rsync", "ftp", "nc", "netcat", "ncat",
    "svn", "hg", "apt", "apt-get", "dpkg", "dnf", "yum", "pacman", "brew",
    "git",
}
_NETWORK_GIT_SUBCOMMANDS = {"clone", "fetch", "pull", "push", "ls-remote", "submodule"}

# Denylist: matched against the lowercased raw command. Literals are simple
# substrings; rules are compiled regexes for the catastrophic cases.
_DENY_LITERALS = [
    (":(){ :|:& };:", "fork bomb"),
    ("del /f /s /q c:", "recursive drive delete"),
    ("rd /s /q c:", "recursive drive remove"),
]

_DENY_RULES = [
    (
        re.compile(
            r"\brm\b[^\n]*"
            r"(?:-(?:rf|fr)\b|--recursive[^\n]*--force\b|--force[^\n]*--recursive\b)"
            r"[^\n]*"
            r"(?:\s+--no-preserve-root|\s+/\*?\s*$|\s+~+\s*$|\s+\.{1,2}\s*$)"
        ),
        "recursive deletion of a root/home path",
    ),
    (re.compile(r"\b(?:mkfs|diskpart)\b"), "filesystem formatting/partitioning"),
    (re.compile(r"\bformat\s+[a-z]:\s*"), "drive formatting"),
    (re.compile(r"\bdd\b[^\n]*\bof=\s*/dev/"), "raw device write"),
    (
        re.compile(r"\b(?:shutdown|reboot|halt|poweroff|init\s+0|init\s+6)\b"),
        "system shutdown/reboot",
    ),
]


def bind_config(config: dict) -> None:
    global _CONFIG
    _CONFIG = config


def bind_confirmer(confirmer) -> None:
    global _CONFIRMER
    _CONFIRMER = confirmer


def terminal_confirmer(command: str, cwd: str, program: str) -> str:
    """Interactive confirmation prompt for main.py. Returns allow/deny/allow_always."""
    print("\nrun_command wants to run:")
    print(f"  command: {command}")
    print(f"  cwd    : {cwd}")
    while True:
        answer = input("Allow? [y]es / [n]o / [a]lways allow this program: ").strip().lower()
        if answer in {"y", "yes", "allow"}:
            return "allow"
        if answer in {"n", "no", "deny"}:
            return "deny"
        if answer in {"a", "always", "always allow", "allow always"}:
            return "allow_always"
        print("Please answer 'y', 'n', or 'a'.")


def _program_name(token: str) -> str:
    """Normalize a program token to a lowercase basename, stripping OS suffixes."""
    name = Path(token).name.lower()
    for suffix in (".exe", ".bat", ".cmd", ".com"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name


def _denylist_reason(config: dict, command: str) -> str | None:
    low = command.lower()
    for literal, reason in _DENY_LITERALS:
        if literal in low:
            return reason
    for pattern, reason in _DENY_RULES:
        if pattern.search(low):
            return reason
    for literal in (config.get("run_command_denylist") or []):
        literal = str(literal).strip().lower()
        if literal and literal in low:
            return "user-denylist match"
    return None


def _is_eval(argv: list[str], prog_name: str) -> bool:
    if len(argv) < 2 or argv[1].lower() not in _EVAL_FLAGS:
        return False
    return prog_name in _EVAL_INTERPRETERS or prog_name.startswith("python")


def _is_network(argv: list[str], prog_name: str) -> bool:
    if prog_name not in _NETWORK_PROGRAMS:
        return False
    if prog_name == "git":
        sub = argv[1] if len(argv) > 1 else ""
        return sub in _NETWORK_GIT_SUBCOMMANDS
    return True


def _is_readonly(argv: list[str], prog_name: str) -> bool:
    if prog_name not in _READONLY_PROGRAMS:
        return False
    if prog_name == "git":
        sub = argv[1] if len(argv) > 1 else ""
        return sub in _READONLY_GIT_SUBCOMMANDS
    return True


def _is_allowlisted(config: dict, program_token: str, prog_name: str) -> bool:
    allow = {str(x).strip().lower() for x in (config.get("run_command_allowlist") or [])}
    allow.discard("")
    return program_token.lower() in allow or prog_name in allow


def _resolve_cwd(config: dict, cwd_arg: str):
    """Return a working-directory string, or an error dict."""
    raw = (cwd_arg or "").strip() or (config.get("run_command_cwd") or "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.is_dir():
        return {"error": f"Working directory not found: {raw}", "reason": "cwd"}
    return str(p.resolve())


def _add_to_allowlist(config: dict, program: str) -> None:
    allow = list(config.get("run_command_allowlist") or [])
    if program not in allow:
        allow.append(program)
        config["run_command_allowlist"] = allow
        try:
            save_config(config)
        except Exception:
            logger.exception("Could not persist allowlist update")


def _confirm(command: str, workdir: str, program_token: str):
    """Return "allow"/"allow_always" to proceed, or an error dict to refuse."""
    if _CONFIRMER is None:
        return {
            "error": "Confirmation required but no confirmer is bound (fail-closed).",
            "reason": "confirm_unavailable",
            "command": command,
        }
    answer = _CONFIRMER(command, workdir or "(inherit)", program_token)
    if answer == "deny":
        return {
            "error": "Command not run (denied).",
            "reason": "user_denied",
            "refused": True,
            "command": command,
        }
    if answer == "allow_always":
        _add_to_allowlist(_CONFIG, program_token)
        return "allow_always"
    if answer == "allow":
        return "allow"
    return {
        "error": "Confirmation did not approve the command (fail-closed).",
        "reason": "user_denied",
        "command": command,
    }


def _execute(config: dict, command: str, argv: list[str], program_token: str,
             workdir: str | None, approved: str) -> dict:
    shell = bool(config.get("run_command_shell", False))
    timeout = int(config.get("run_command_timeout", 30))
    max_out = int(config.get("run_command_max_output", 8000))

    try:
        if shell:
            proc = subprocess.run(
                command, shell=True, cwd=workdir, timeout=timeout,
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
        else:
            proc = subprocess.run(
                argv, cwd=workdir, timeout=timeout,
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
    except subprocess.TimeoutExpired:
        logger.warning("run_command timed out after %ss: %r", timeout, command)
        return {"error": f"Command timed out after {timeout}s.", "reason": "timeout", "command": command}
    except OSError as exc:
        logger.error("run_command execution failed: %s", exc)
        return {"error": f"Could not run command: {exc}", "reason": "exec", "command": command}

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    logger.info("run_command %s %r exit=%s", program_token, command, proc.returncode)

    return {
        "command": command,
        "program": program_token,
        "cwd": workdir,
        "approved": approved,
        "exit_code": proc.returncode,
        "stdout": stdout[:max_out],
        "stderr": stderr[:max_out],
        "stdout_truncated": len(stdout) > max_out,
        "stderr_truncated": len(stderr) > max_out,
    }


def run_command(command: str, cwd: str = "") -> dict:
    """Run a single local command under the safety policy (disabled by default)."""
    config = _CONFIG or {}
    mode = str(config.get("run_command_mode", "off")).lower()
    if mode == "off":
        return {
            "error": "run_command is disabled; set 'run_command_mode' in config to enable it.",
            "reason": "disabled",
        }
    if mode not in _MODES:
        return {"error": f"Unknown run_command_mode: {mode!r}", "reason": "config"}

    command = (command or "").strip()
    if not command:
        return {"error": "command must not be empty", "reason": "empty"}

    try:
        argv = split_command(command)
    except ValueError as exc:
        return {"error": f"Could not parse command: {exc}", "reason": "parse"}
    if not argv:
        return {"error": "command must not be empty", "reason": "empty"}

    program_token = argv[0]
    prog_name = _program_name(program_token)

    deny = _denylist_reason(config, command)
    if deny:
        logger.warning("run_command refused (denylist): %r", command)
        return {"error": f"Refusing to run ({deny}).", "reason": "denylist", "command": command}

    if _is_eval(argv, prog_name):
        logger.warning("run_command refused (eval): %r", command)
        return {"error": "Refusing interpreter eval (e.g. -c/-e) that would bypass the safety policy.",
                "reason": "eval", "command": command}

    if prog_name in _INTERACTIVE_PROGRAMS:
        logger.warning("run_command refused (tty): %r", command)
        return {"error": f"Refusing interactive/privileged program: {prog_name!r}",
                "reason": "tty", "command": command}

    if not bool(config.get("run_command_allow_network", False)) and _is_network(argv, prog_name):
        logger.warning("run_command refused (network): %r", command)
        return {"error": f"Network command refused; set 'run_command_allow_network' to allow: {prog_name!r}",
                "reason": "network", "command": command}

    if shutil.which(program_token) is None:
        return {"error": f"Unknown program: {program_token!r}", "reason": "unknown_program",
                "command": command}

    workdir = _resolve_cwd(config, cwd)
    if isinstance(workdir, dict):
        return workdir

    approved: str | None = None
    auto = (mode == "auto" and _is_readonly(argv, prog_name)) or (
        mode in {"allowlist", "auto"} and _is_allowlisted(config, program_token, prog_name)
    )
    if auto:
        approved = "auto"
    else:
        decision = _confirm(command, workdir, program_token)
        if isinstance(decision, dict):
            return decision
        approved = decision

    return _execute(config, command, argv, program_token, workdir, approved)


SCHEMAS = [
    (
        {
            "type": "function",
            "function": {
                "name": "run_command",
                "description": (
                    "Run a single local command (no shell operators), subject to the "
                    "safety policy. Disabled by default; enable via run_command_mode."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "One command with arguments; no shell operators like | && ; >.",
                        },
                        "cwd": {
                            "type": "string",
                            "description": "Optional working directory.",
                        },
                    },
                    "required": ["command"],
                },
            },
        },
        run_command,
    ),
]
