"""
tests/test_logging.py
Unit tests for the logging setup layer. Standard library only -- no Ollama
needed:

    python -m unittest            # from the project root
    python -m unittest discover -s tests -v
"""

import logging
import sys
import tempfile
import unittest
from pathlib import Path

# Make the project root importable regardless of how unittest is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DEFAULT_CONFIG
from logging_setup import get_log_file, setup_logging


def _detach_root_handlers() -> None:
    """Close and remove every handler currently attached to the root logger."""
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()


class TestLoggingSetup(unittest.TestCase):
    def setUp(self):
        root = logging.getLogger()
        self._saved_handlers = root.handlers[:]
        self._saved_level = root.level

    def tearDown(self):
        # Restore the root logger to its pre-test state so setup_logging's
        # global reconfiguration never leaks into other test modules.
        _detach_root_handlers()
        root = logging.getLogger()
        root.setLevel(self._saved_level)
        for handler in self._saved_handlers:
            root.addHandler(handler)

    def test_default_log_path(self):
        cfg = dict(DEFAULT_CONFIG)
        cfg["log_file"] = ""
        path = get_log_file(cfg)
        self.assertEqual(path.name, "mini-agent.log")
        self.assertEqual(path.parent.name, "logs")

    def test_override_log_path(self):
        cfg = dict(DEFAULT_CONFIG)
        cfg["log_file"] = "~/mini-agent-test.log"
        self.assertEqual(get_log_file(cfg), Path.home() / "mini-agent-test.log")

    def test_setup_writes_to_log_file(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            cfg = dict(DEFAULT_CONFIG)
            cfg["log_file"] = str(Path(tmp.name) / "app.log")

            path = setup_logging(cfg)
            logging.getLogger("test-logging").info("hello from test")
            for handler in logging.getLogger().handlers:
                handler.flush()

            _detach_root_handlers()  # release the file before cleanup below
            content = path.read_text(encoding="utf-8")
        finally:
            tmp.cleanup()

        self.assertIn("hello from test", content)


if __name__ == "__main__":
    unittest.main()
