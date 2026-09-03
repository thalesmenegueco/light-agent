"""
skills/code_skills.py
Wraps the coder model (qwen2.5-coder:3b) as a skill the router can invoke.
This module needs access to the loaded config, so __init__.py injects it
via `bind_config()` before the schema is used (see skills/__init__.py).
"""

from coder import ask_coder

_CONFIG = None


def bind_config(config: dict) -> None:
    global _CONFIG
    _CONFIG = config


def run_coder(instruction: str, file_content: str = "") -> dict:
    """Delegate a coding/debugging question to the coder model."""
    if _CONFIG is None:
        return {"error": "code_skills not initialized with config"}
    answer = ask_coder(_CONFIG, instruction, file_content or None)
    return {"answer": answer}


SCHEMAS = [
    (
        {
            "type": "function",
            "function": {
                "name": "run_coder",
                "description": (
                    "Use the specialized coding model to write, review, or debug code. "
                    "Use this for anything requiring code understanding or generation, "
                    "such as explaining why code isn't working."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "instruction": {
                            "type": "string",
                            "description": "What to do (e.g. 'explain why this fails').",
                        },
                        "file_content": {
                            "type": "string",
                            "description": "Relevant code/file text, if any. Empty string if none.",
                        },
                    },
                    "required": ["instruction"],
                },
            },
        },
        run_coder,
    ),
]
