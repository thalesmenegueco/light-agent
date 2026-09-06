"""
tests/test_router.py
Unit tests for router.py's warm-up helper (offline -- requests.post is mocked).
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Make the project root importable regardless of how unittest is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

import router
from router import handle_message, warm_up


def _tool_message(name: str, arguments) -> dict:
    return {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
        }
    }


def _text_message(content: str) -> dict:
    return {"message": {"role": "assistant", "content": content}}


class TestHandleMessage(unittest.TestCase):
    """Offline tests for the recursive tool-calling loop (router._call_ollama mocked)."""

    def _config(self, **overrides) -> dict:
        config = {
            "router_model": "qwen3:4b-instruct",
            "ollama_host": "http://localhost:11434",
            "router_temperature": 0.2,
        }
        config.update(overrides)
        return config

    @patch("router.DISPATCH", new_callable=dict)
    def test_single_round_tool_call(self, mock_dispatch):
        mock_dispatch["list_files"] = lambda **k: {"files": ["a.txt"], "folders": [], "path": "."}
        with patch("router._call_ollama", side_effect=[
            _tool_message("list_files", {"path": "."}),
            _text_message("Here are the files."),
        ]) as mock_call:
            reply, history = handle_message(self._config(), [], "list files")

        self.assertEqual(reply, "Here are the files.")
        self.assertEqual(mock_call.call_count, 2)
        self.assertEqual(history, [
            {"role": "user", "content": "list files"},
            {"role": "assistant", "content": "Here are the files."},
        ])

    @patch("router.DISPATCH", new_callable=dict)
    def test_recursive_multi_round_chains_tools(self, mock_dispatch):
        mock_dispatch["list_files"] = lambda **k: {"files": ["a.txt"], "folders": [], "path": "."}
        mock_dispatch["read_file"] = lambda **k: {"path": "a.txt", "content": "hello", "truncated": False}
        with patch("router._call_ollama", side_effect=[
            _tool_message("list_files", {"path": "."}),
            _tool_message("read_file", {"path": "a.txt"}),
            _text_message("a.txt contains hello."),
        ]) as mock_call:
            reply, history = handle_message(self._config(), [], "what is in a.txt")

        self.assertEqual(reply, "a.txt contains hello.")
        self.assertEqual(mock_call.call_count, 3)
        # The second round's prompt must carry the first round's tool result.
        round2_messages = mock_call.call_args_list[1].args[1]
        self.assertIn("tool", [m["role"] for m in round2_messages])

    @patch("router.DISPATCH", new_callable=dict)
    def test_round_cap_forces_final_answer_without_tools(self, mock_dispatch):
        mock_dispatch["list_files"] = lambda **k: {"files": [], "folders": [], "path": "."}
        with patch("router._call_ollama", side_effect=[
            _tool_message("list_files", {"path": "."}),
            _tool_message("list_files", {"path": "."}),
            _text_message("Done."),
        ]) as mock_call:
            reply, history = handle_message(self._config(max_tool_rounds=2), [], "list files")

        self.assertEqual(reply, "Done.")
        self.assertEqual(mock_call.call_count, 3)
        # The forced final call must disable tools.
        self.assertFalse(mock_call.call_args_list[2].kwargs.get("use_tools"))

    @patch("router.DISPATCH", new_callable=dict)
    def test_string_json_arguments_are_parsed(self, mock_dispatch):
        captured = {}
        mock_dispatch["list_files"] = lambda **k: captured.update(k) or {"files": [], "folders": [], "path": "."}
        with patch("router._call_ollama", side_effect=[
            _tool_message("list_files", '{"path": "."}'),
            _text_message("ok"),
        ]):
            handle_message(self._config(), [], "list files")
        self.assertEqual(captured.get("path"), ".")

    @patch("router.DISPATCH", new_callable=dict)
    def test_unknown_tool_returns_error_and_continues(self, mock_dispatch):
        with patch("router._call_ollama", side_effect=[
            _tool_message("not_a_real_tool", {}),
            _text_message("I don't know."),
        ]) as mock_call:
            reply, history = handle_message(self._config(), [], "hi")

        self.assertEqual(reply, "I don't know.")
        round2_messages = mock_call.call_args_list[1].args[1]
        tool_payloads = [m["content"] for m in round2_messages if m["role"] == "tool"]
        self.assertTrue(tool_payloads)
        self.assertIn("Unknown tool", tool_payloads[0])

    @patch("router.DISPATCH", new_callable=dict)
    def test_no_tool_call_returns_text_directly(self, mock_dispatch):
        with patch("router._call_ollama", side_effect=[
            _text_message("Just chatting."),
        ]) as mock_call:
            reply, history = handle_message(self._config(), [], "hello")
        self.assertEqual(reply, "Just chatting.")
        self.assertEqual(mock_call.call_count, 1)
        self.assertEqual(history[-1]["role"], "assistant")


class TestWarmUp(unittest.TestCase):
    @patch("router.requests.post")
    def test_warm_up_payload_and_timeout(self, mock_post):
        mock_resp = mock_post.return_value
        mock_resp.raise_for_status.return_value = None

        warm_up({"router_model": "qwen3:4b-instruct", "ollama_host": "http://localhost:11434"})

        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "http://localhost:11434/api/chat")
        payload = kwargs["json"]
        self.assertEqual(payload["model"], "qwen3:4b-instruct")
        self.assertEqual(payload["stream"], False)
        self.assertEqual(payload["options"]["num_predict"], 1)
        self.assertEqual(kwargs["timeout"], router._WARMUP_TIMEOUT)
        self.assertGreater(kwargs["timeout"], 120)  # beyond the per-turn cap
        mock_resp.raise_for_status.assert_called_once()

    @patch("router.requests.post")
    def test_warm_up_propagates_request_errors(self, mock_post):
        mock_post.side_effect = requests.RequestException("boom")
        with self.assertRaises(requests.RequestException):
            warm_up({"router_model": "x", "ollama_host": "http://h"})


if __name__ == "__main__":
    unittest.main()
