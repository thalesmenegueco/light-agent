"""
tests/test_run_command.py
Unit tests for the run_command safety policy. Standard library only -- no
Ollama needed:

    python -m unittest            # from the project root
    python -m unittest discover -s tests -v

These exercise the full pipeline (denylist, eval, tty, network, confirmation,
execution, truncation) using a scripted confirmer and harmless commands, so
nothing dangerous ever runs for real.
"""

import shlex
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Make the project root importable regardless of how unittest is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DEFAULT_CONFIG
from skills import run_command_skills as rc


def _config(mode: str = "off", **overrides) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg["run_command_mode"] = mode
    cfg.update(overrides)
    return cfg


def _confirmer(answer: str):
    return lambda command, cwd, program: answer


class TestRunCommandPolicy(unittest.TestCase):
    def setUp(self):
        self._saved_config = rc._CONFIG
        self._saved_confirmer = rc._CONFIRMER

    def tearDown(self):
        rc._CONFIG = self._saved_config
        rc._CONFIRMER = self._saved_confirmer

    def _bind(self, mode: str = "off", confirmer=None, **overrides) -> None:
        rc.bind_config(_config(mode, **overrides))
        rc.bind_confirmer(confirmer)

    # --- master gate / basic input ---

    def test_off_mode_disabled(self):
        self._bind("off")
        result = rc.run_command("ls")
        self.assertIn("error", result)
        self.assertEqual(result["reason"], "disabled")

    def test_empty_command(self):
        self._bind("confirm", _confirmer("allow"))
        result = rc.run_command("   ")
        self.assertEqual(result["reason"], "empty")

    def test_unknown_program(self):
        self._bind("confirm", _confirmer("allow"))
        result = rc.run_command("definitely_not_a_real_cmd_abc123")
        self.assertEqual(result["reason"], "unknown_program")

    # --- denylist ---

    def test_denylist_destructive_commands(self):
        self._bind("confirm", _confirmer("allow"))
        for cmd in ("rm -rf /", "rm -rf /*", "rm -rf ~", "mkfs.ext4 /dev/sda1",
                    "dd if=/dev/zero of=/dev/sda", "shutdown -h now"):
            result = rc.run_command(cmd)
            self.assertEqual(result["reason"], "denylist", cmd)

    def test_plain_rm_not_denylisted(self):
        # A non-recursive, non-root rm must fall through to confirmation.
        self._bind("confirm")  # no confirmer -> fail closed
        result = rc.run_command("rm /tmp/somefile")
        self.assertEqual(result["reason"], "confirm_unavailable")

    def test_user_denylist_literal(self):
        self._bind("confirm", _confirmer("allow"), run_command_denylist=["echo danger"])
        result = rc.run_command("echo danger")
        self.assertEqual(result["reason"], "denylist")

    # --- eval / tty / network gates ---

    def test_eval_refused(self):
        self._bind("confirm", _confirmer("allow"))
        cmd = f"{shlex.quote(sys.executable)} -c \"print(1)\""
        result = rc.run_command(cmd)
        self.assertEqual(result["reason"], "eval")

    def test_interactive_refused_even_if_missing(self):
        self._bind("confirm", _confirmer("allow"))
        result = rc.run_command("vim /tmp/x")
        self.assertEqual(result["reason"], "tty")

    def test_network_refused_without_optin(self):
        self._bind("confirm", _confirmer("allow"))
        result = rc.run_command("curl http://example.com")
        self.assertEqual(result["reason"], "network")

    # --- confirmation ---

    def test_confirm_without_confirmer_fails_closed(self):
        self._bind("confirm")
        result = rc.run_command(f"{shlex.quote(sys.executable)} --version")
        self.assertEqual(result["reason"], "confirm_unavailable")

    def test_confirm_deny_refuses(self):
        self._bind("confirm", _confirmer("deny"))
        result = rc.run_command(f"{shlex.quote(sys.executable)} --version")
        self.assertEqual(result["reason"], "user_denied")
        self.assertTrue(result.get("refused"))

    def test_confirm_allow_runs(self):
        self._bind("confirm", _confirmer("allow"))
        result = rc.run_command(f"{shlex.quote(sys.executable)} --version")
        self.assertNotIn("error", result)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["approved"], "allow")

    @patch.object(rc, "save_config")
    def test_allow_always_runs_and_persists(self, mock_save):
        cfg = _config("allowlist")
        rc.bind_config(cfg)
        rc.bind_confirmer(_confirmer("allow_always"))
        result = rc.run_command(f"{shlex.quote(sys.executable)} --version")
        self.assertEqual(result["exit_code"], 0)
        self.assertIn(sys.executable, cfg["run_command_allowlist"])
        mock_save.assert_called_once()

    # --- allowlist / auto modes ---

    def test_allowlist_mode_auto_runs_listed(self):
        self._bind("allowlist", run_command_allowlist=[sys.executable])
        result = rc.run_command(f"{shlex.quote(sys.executable)} --version")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["approved"], "auto")

    def test_allowlist_mode_unlisted_requires_confirmation(self):
        self._bind("allowlist")  # no confirmer -> fail closed
        result = rc.run_command(f"{shlex.quote(sys.executable)} --version")
        self.assertEqual(result["reason"], "confirm_unavailable")

    @unittest.skipUnless(shutil.which("cat"), "cat not available")
    def test_auto_mode_runs_readonly_without_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x.txt"
            f.write_text("hello", encoding="utf-8")
            self._bind("auto")  # no confirmer
            result = rc.run_command(f"cat {shlex.quote(str(f))}")
            self.assertEqual(result["exit_code"], 0)
            self.assertIn("hello", result["stdout"])
            self.assertEqual(result["approved"], "auto")

    # --- cwd / timeout ---

    def test_invalid_cwd_refuses(self):
        self._bind("confirm", _confirmer("allow"))
        result = rc.run_command(
            f"{shlex.quote(sys.executable)} --version", cwd="/no/such/dir"
        )
        self.assertEqual(result["reason"], "cwd")

    @unittest.skipUnless(shutil.which("sleep"), "sleep not available")
    def test_timeout(self):
        self._bind("confirm", _confirmer("allow"), run_command_timeout=1)
        result = rc.run_command("sleep 5")
        self.assertEqual(result["reason"], "timeout")


class TestPolicyHelpers(unittest.TestCase):
    """Pure-function checks for the classification predicates."""

    def test_is_eval(self):
        self.assertTrue(rc._is_eval(["python", "-c", "x"], "python"))
        self.assertTrue(rc._is_eval(["python3.10", "-c", "x"], "python3.10"))
        self.assertFalse(rc._is_eval(["python", "--version"], "python"))
        self.assertFalse(rc._is_eval(["ls", "-c"], "ls"))

    def test_is_network(self):
        self.assertTrue(rc._is_network(["curl", "http://x"], "curl"))
        self.assertTrue(rc._is_network(["git", "fetch"], "git"))
        self.assertFalse(rc._is_network(["git", "status"], "git"))
        self.assertFalse(rc._is_network(["ls"], "ls"))

    def test_is_readonly(self):
        self.assertTrue(rc._is_readonly(["git", "status"], "git"))
        self.assertTrue(rc._is_readonly(["ls"], "ls"))
        self.assertFalse(rc._is_readonly(["git", "commit", "-m", "x"], "git"))
        self.assertFalse(rc._is_readonly(["rm", "x"], "rm"))


if __name__ == "__main__":
    unittest.main()
