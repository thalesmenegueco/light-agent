"""
tests/test_session.py
Unit tests for the session-state persistence module (session.py). No Ollama
needed; every call uses an explicit temp path so nothing touches the real
config directory:

    python -m unittest            # from the project root
    python -m unittest discover -s tests -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

# Make the project root importable regardless of how unittest is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import session as session_mod


class TestSession(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "session.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_new_session_shape(self):
        state = session_mod.new_session("build x")
        self.assertEqual(state["goal"], "build x")
        self.assertEqual(state["current_step"], 0)
        self.assertEqual(state["steps_used"], 0)
        self.assertEqual(state["tokens_used"], 0)
        self.assertEqual(state["status"], "planning")
        self.assertEqual(state["plan"], [])

    def test_save_and_load_roundtrip(self):
        state = session_mod.new_session("build x")
        state["plan"] = [{"step": "a", "status": "done", "result": "ok"}]
        state["current_step"] = 1
        session_mod.save_session(state, self.path)
        self.assertTrue(self.path.exists())

        loaded = session_mod.load_session(self.path)
        self.assertEqual(loaded["goal"], "build x")
        self.assertEqual(loaded["current_step"], 1)
        self.assertEqual(loaded["plan"][0]["status"], "done")

    def test_load_missing_returns_none(self):
        self.assertIsNone(session_mod.load_session(self.path))

    def test_load_corrupted_returns_none(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(session_mod.load_session(self.path))

    def test_clear_removes_file(self):
        session_mod.save_session(session_mod.new_session("x"), self.path)
        self.assertTrue(self.path.exists())
        session_mod.clear_session(self.path)
        self.assertFalse(self.path.exists())

    def test_save_updates_timestamp(self):
        state = session_mod.new_session("x")
        session_mod.save_session(state, self.path)
        first = session_mod.load_session(self.path)["updated_at"]
        state["status"] = "done"
        session_mod.save_session(state, self.path)
        second = session_mod.load_session(self.path)["updated_at"]
        self.assertGreaterEqual(second, first)

    def test_clear_missing_is_noop(self):
        session_mod.clear_session(self.path)  # must not raise


if __name__ == "__main__":
    unittest.main()
