"""
tests/test_coder.py
Unit tests for coder.ask_coder (offline -- requests.post is mocked), including
that the coder leaf's tokens are tallied into the session budget so an
autopilot token cap bounds the whole run, not just the router.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Make the project root importable regardless of how unittest is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import coder
import router


def _chat_response(content="print('hi')", eval_count=7, prompt_eval_count=11):
    return {
        "message": {"role": "assistant", "content": content},
        "eval_count": eval_count,
        "prompt_eval_count": prompt_eval_count,
    }


class TestAskCoder(unittest.TestCase):
    def setUp(self):
        # ask_coder lazily warms the coder model on first use; patch it so the
        # offline tests never hit Ollama, and reset the module flag per test.
        patcher = patch("router.warm_up")
        self.mock_warm_up = patcher.start()
        self.addCleanup(patcher.stop)
        coder._WARMED = False

    def _config(self):
        return {
            "coder_model": "qwen2.5-coder:3b",
            "ollama_host": "http://localhost:11434",
            "coder_temperature": 0.1,
        }

    @patch("coder.requests.post")
    def test_returns_model_content(self, mock_post):
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = _chat_response()

        result = coder.ask_coder(self._config(), "write hello", "x = 1")

        self.assertEqual(result, "print('hi')")
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "qwen2.5-coder:3b")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1]["role"], "user")
        self.assertIn("write hello", payload["messages"][1]["content"])
        self.assertIn("x = 1", payload["messages"][1]["content"])

    @patch("coder.requests.post")
    def test_includes_coding_output_contract(self, mock_post):
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = _chat_response()

        coder.ask_coder(self._config(), "write hello")

        system = mock_post.call_args.kwargs["json"]["messages"][0]["content"]
        self.assertIn("output ONLY the code", system)
        self.assertIn("markdown fences", system)

    @patch("coder.requests.post")
    def test_accumulates_tokens_into_session_budget(self, mock_post):
        router.reset_token_counter()
        self.addCleanup(router.reset_token_counter)
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = _chat_response(
            eval_count=7, prompt_eval_count=11
        )

        coder.ask_coder(self._config(), "write hello")

        self.assertEqual(router.get_token_count(), 18)

    @patch("coder.requests.post")
    def test_uses_configured_timeout(self, mock_post):
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = _chat_response()

        config = self._config()
        config["ollama_timeout"] = 300
        coder.ask_coder(config, "hi")

        self.assertEqual(mock_post.call_args.kwargs["timeout"], 300)

    @patch("coder.requests.post")
    def test_warms_coder_on_first_use_only(self, mock_post):
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = _chat_response()

        config = self._config()
        coder.ask_coder(config, "hi")
        coder.ask_coder(config, "hi")

        self.mock_warm_up.assert_called_once_with(config, "qwen2.5-coder:3b")


if __name__ == "__main__":
    unittest.main()
