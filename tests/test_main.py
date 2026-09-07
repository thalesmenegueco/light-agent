"""
tests/test_main.py
Unit tests for main.py helpers that don't need Ollama (the read-timeout hint).
"""

import sys
import unittest
from pathlib import Path

# Make the project root importable regardless of how unittest is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

import main


class TestTimeoutHint(unittest.TestCase):
    def test_read_timeout_returns_hint_with_current_value(self):
        hint = main._ollama_timeout_hint(
            {"ollama_timeout": 120},
            requests.exceptions.ReadTimeout("Read timed out."),
        )
        self.assertIsNotNone(hint)
        self.assertIn("120000 ms", hint)
        self.assertIn("increase the timeout", hint)

    def test_read_timeout_hint_reflects_override(self):
        hint = main._ollama_timeout_hint(
            {"ollama_timeout": 300},
            requests.exceptions.ReadTimeout("Read timed out."),
        )
        self.assertIn("300000 ms", hint)

    def test_non_timeout_errors_have_no_hint(self):
        self.assertIsNone(
            main._ollama_timeout_hint({}, requests.exceptions.RequestException("boom"))
        )
        self.assertIsNone(
            main._ollama_timeout_hint({}, requests.exceptions.ConnectionError("boom"))
        )


if __name__ == "__main__":
    unittest.main()
