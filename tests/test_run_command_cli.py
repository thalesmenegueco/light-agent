"""
tests/test_run_command_cli.py
Live-terminal integration tests for run_command's confirm mode.

These drive the REAL `terminal_confirmer` through a real subprocess with
piped stdin -- no mocks, no Ollama. The actual `input()` prompt reads our
answers and the actual command executes, so this proves the confirmation ->
execution path behaves exactly as a user would experience it in
`python main.py --run-command-mode confirm`.

Run (offline):
    python -m unittest tests.test_run_command_cli -v
    # or from the project root: python -m unittest discover -s tests -v
"""

import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Make the project root importable regardless of how unittest is invoked.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from main import _parse_args  # noqa: E402

_SNIPPET = """
import json
import sys
from pathlib import Path

sys.path.insert(0, {root!r})

import config as cfgmod
from config import DEFAULT_CONFIG
from skills import run_command_skills as rc

# Redirect config writes (for "always allow") into the throwaway dir.
cfgmod.get_base_dir = lambda: Path({tmp!r})

cfg = dict(DEFAULT_CONFIG)
cfg["run_command_mode"] = {mode!r}
rc.bind_config(cfg)
rc.bind_confirmer(rc.terminal_confirmer)

result = rc.run_command({cmd!r})
print("RESULT_START")
print(json.dumps(result))
print("RESULT_END")
"""


def _run(command: str, stdin: str, mode: str = "confirm", tmp: Path = None):
    if tmp is None:
        tmp = Path(tempfile.mkdtemp(prefix="miniagent-cli-"))
    snippet = _SNIPPET.format(root=str(ROOT), tmp=str(tmp), mode=mode, cmd=command)
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc, tmp


def _parse_result(proc: subprocess.CompletedProcess) -> dict:
    out = proc.stdout
    start = out.index("RESULT_START") + len("RESULT_START")
    end = out.index("RESULT_END")
    return json.loads(out[start:end].strip())


class TestLiveTerminalConfirm(unittest.TestCase):
    def test_allow_runs_real_command(self):
        proc, _ = _run(f"{shlex.quote(sys.executable)} --version", "y\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Allow?", proc.stdout)
        result = _parse_result(proc)
        self.assertEqual(result["approved"], "allow")
        self.assertEqual(result["exit_code"], 0)

    def test_deny_refuses(self):
        proc, _ = _run(f"{shlex.quote(sys.executable)} --version", "n\n")
        result = _parse_result(proc)
        self.assertEqual(result["reason"], "user_denied")
        self.assertTrue(result["refused"])

    def test_always_allow_runs_and_persists(self):
        tmp = Path(tempfile.mkdtemp(prefix="miniagent-cli-persist-"))
        proc, tmp = _run(f"{shlex.quote(sys.executable)} --version", "a\n", tmp=tmp)
        result = _parse_result(proc)
        self.assertEqual(result["approved"], "allow_always")
        self.assertEqual(result["exit_code"], 0)
        saved = json.loads((tmp / "config.json").read_text(encoding="utf-8"))
        self.assertIn(sys.executable, saved["run_command_allowlist"])

    def test_invalid_answer_then_allow(self):
        proc, _ = _run(f"{shlex.quote(sys.executable)} --version", "maybe\ny\n")
        result = _parse_result(proc)
        self.assertEqual(result["approved"], "allow")
        self.assertEqual(result["exit_code"], 0)


class TestCliFlag(unittest.TestCase):
    def test_run_command_mode_flag(self):
        self.assertEqual(
            _parse_args(["--run-command-mode", "confirm"]).run_command_mode, "confirm"
        )

    def test_no_flag_is_none(self):
        self.assertIsNone(_parse_args([]).run_command_mode)


if __name__ == "__main__":
    unittest.main()
