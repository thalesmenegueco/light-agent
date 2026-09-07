"""
tests/test_meta_skills.py
Unit tests for the meta skills (list_skills, get_config, set_config).
Standard library only -- no Ollama needed:

    python -m unittest            # from the project root
    python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Make the project root importable regardless of how unittest is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DEFAULT_CONFIG
from skills import DISPATCH, TOOLS
from skills import meta_skills as meta

import main


class TestListSkills(unittest.TestCase):
    def test_lists_every_registered_tool(self):
        result = DISPATCH["list_skills"]()
        self.assertEqual(result["count"], len(TOOLS))
        names = {skill["name"] for skill in result["skills"]}
        self.assertEqual(names, set(DISPATCH))
        for skill in result["skills"]:
            self.assertTrue(skill["description"].strip())


class TestConfigSkills(unittest.TestCase):
    def setUp(self):
        self._saved = meta._CONFIG
        meta._CONFIG = dict(DEFAULT_CONFIG)

    def tearDown(self):
        meta._CONFIG = self._saved

    def test_get_config_returns_config(self):
        result = DISPATCH["get_config"]()
        self.assertEqual(result["config"]["router_model"], DEFAULT_CONFIG["router_model"])

    def test_get_config_without_bind_errors(self):
        meta._CONFIG = None
        self.assertIn("error", DISPATCH["get_config"]())

    def test_set_config_without_bind_errors(self):
        meta._CONFIG = None
        self.assertIn("error", DISPATCH["set_config"](updates={"router_model": "x"}))

    @patch.object(meta, "save_config")
    def test_set_config_valid_persists(self, mock_save):
        result = DISPATCH["set_config"](updates={"router_temperature": 0.5})
        self.assertEqual(result["config"]["router_temperature"], 0.5)
        mock_save.assert_called_once()

    @patch.object(meta, "save_config")
    def test_set_config_unknown_key_rejected(self, mock_save):
        result = DISPATCH["set_config"](updates={"bogus": 1})
        self.assertIn("error", result)
        mock_save.assert_not_called()

    @patch.object(meta, "save_config")
    def test_set_config_bad_type_rejected(self, mock_save):
        result = DISPATCH["set_config"](updates={"max_history_messages": "lots"})
        self.assertIn("error", result)
        mock_save.assert_not_called()

    @patch.object(meta, "save_config")
    def test_set_config_bad_log_level_rejected(self, mock_save):
        result = DISPATCH["set_config"](updates={"log_level": "VERBOSE"})
        self.assertIn("error", result)
        mock_save.assert_not_called()

    @patch.object(meta, "save_config")
    @patch.object(meta, "set_project_root")
    def test_set_config_project_root_reapplies_root(self, mock_set_root, mock_save):
        result = DISPATCH["set_config"](updates={"project_root": "/tmp/x"})
        self.assertEqual(result["config"]["project_root"], "/tmp/x")
        mock_save.assert_called_once()
        mock_set_root.assert_called_once_with("/tmp/x")

    @patch.object(meta, "save_config")
    @patch.object(meta, "set_project_root")
    def test_fast_path_set_project_root(self, mock_set_root, mock_save):
        result = main.try_fast_path("set project root to /tmp/x")
        self.assertIsNotNone(result)
        self.assertIn("/tmp/x", result)
        mock_save.assert_called_once()
        mock_set_root.assert_called_once_with("/tmp/x")

    @patch.object(meta, "save_config")
    @patch.object(meta, "set_project_root")
    def test_fast_path_set_project_root_rejects_unknown_key(self, mock_set_root, mock_save):
        # A non-project-root config phrase must NOT short-circuit (falls through).
        self.assertIsNone(main.try_fast_path("set the temperature to 0.5"))
        mock_save.assert_not_called()
        mock_set_root.assert_not_called()


if __name__ == "__main__":
    unittest.main()
