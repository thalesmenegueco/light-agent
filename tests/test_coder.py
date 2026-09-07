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
        self.assertIn("write hello", payload["messages"][0]["content"])
        self.assertIn("x = 1", payload["messages"][0]["content"])

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


if __name__ == "__main__":
    unittest.main()
