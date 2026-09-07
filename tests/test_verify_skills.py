"""
tests/test_verify_skills.py
Unit tests for the run_tests verification skill (skills/verify_skills.py). No
Ollama needed:

    python -m unittest            # from the project root
    python -m unittest discover -s tests -v
"""

import sys
import tempfile
import unittest
from pathlib import Path

# Make the project root importable regardless of how unittest is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DEFAULT_CONFIG
from skills import verify_skills as verify


def _config(**overrides) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(overrides)
    return cfg


def _cmd(code: str, exit_code: int = 0) -> str:
    """Build a shell-safe test_command that prints `code` and exits."""
    return f'{sys.executable} -c "import sys; print({code!r}); sys.exit({exit_code})"'


class TestRunTests(unittest.TestCase):
    def setUp(self):
        self._saved_config = verify._CONFIG

    def tearDown(self):
        verify._CONFIG = self._saved_config

    def test_not_configured_refuses(self):
        verify.bind_config(_config(test_command=""))
        result = verify.run_tests()
        self.assertEqual(result["reason"], "not_configured")

    def test_passing_command(self):
        verify.bind_config(_config(test_command=_cmd("ok", 0)))
        result = verify.run_tests()
        self.assertNotIn("error", result)
        self.assertTrue(result["passed"])
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("ok", result["stdout"])

    def test_failing_command(self):
        verify.bind_config(_config(test_command=_cmd("bad", 1)))
        result = verify.run_tests()
        self.assertNotIn("error", result)
        self.assertFalse(result["passed"])
        self.assertEqual(result["exit_code"], 1)
        self.assertIn("bad", result["stdout"])

    def test_runs_in_project_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            verify.bind_config(_config(test_command="pwd", project_root=tmp))
            result = verify.run_tests()
            self.assertNotIn("error", result)
            self.assertEqual(result["cwd"], tmp)

    def test_timeout_reported(self):
        # A command that sleeps past the timeout must be killed and reported.
        command = f'{sys.executable} -c "import time; time.sleep(5)"'
        verify.bind_config(_config(test_command=command, test_timeout=1))
        result = verify.run_tests()
        self.assertEqual(result["reason"], "timeout")

    def test_bad_command_reported(self):
        verify.bind_config(_config(test_command="definitely-not-a-real-program-xyz"))
        result = verify.run_tests()
        self.assertEqual(result["reason"], "exec")


if __name__ == "__main__":
    unittest.main()
