# light-agent

## Intro

OpenRouter/Kilo Code/DeepSeek's agent harness are the build-time tooling only; the running agent stays 100% local via Ollama. Here's the plan.

## Try it now

**Offline demo — no Ollama needed:**

```bash
python demo.py        # or: python3 demo.py
```

Builds a throwaway sandbox and exercises every deterministic skill
(`list_files`, `read_file`, `write_file`, `append_file`, `move_file`,
`open_file`, `search_files`) plus the fast-path matcher, so you can see what
the agent can do without waiting for a model to load (~30–45 s cold).

**Run the unit tests (stdlib `unittest`, no Ollama needed):**

```bash
python -m unittest        # or: python -m unittest discover -s tests -v
```

**Full agent — needs Ollama running:**

```bash
pip install -r requirements.txt
ollama pull qwen3:4b-instruct
ollama pull qwen2.5-coder:3b
python main.py
```

Then try:

- `list files in .`
- `open README.md`
- `search for normalize_path in .`
- `find files named config in .`
- `write a hello.py file that prints hello`  (router + coder model)
- `why isn't this working?`  (delegates to the coder model)

## Current skills

| Skill | Kind | What it does |
|-------|------|--------------|
| `list_files` | deterministic | List files and folders in a directory |
| `read_file` | deterministic | Read a text file (truncated at 8000 chars) |
| `write_file` | deterministic | Create / overwrite a text file |
| `append_file` | deterministic | Append text to a file |
| `move_file` | deterministic | Move / rename a file |
| `open_file` | deterministic | Open a file or folder in the OS default app |
| `search_files` | deterministic | Find files by name or grep text inside files |
| `run_coder` | LLM (coder leaf) | Write / review / debug code via `qwen2.5-coder:3b` |

## Phase 0 — Foundations (before any agent logic)

- Install Ollama on both machines, pull `qwen3:4b-instruct` (router) and `qwen2.5-coder:3b` (coder).
- Confirm both respond to `/api/chat` correctly — in particular, that the router returns a **structured `tool_calls` field** (not raw text) when given a `tools` payload. A quick curl/Python test validates the whole architecture before you build on top of it (see [Router model notes](#router-model-notes)).
- Decide the skills storage location: `%APPDATA%\MiniAgent\skills\` (Windows) / `~/.config/mini-agent/skills/` (Linux), same auto-detect pattern as your other tools.

## Phase 1 — Project skeleton

```
mini-agent/
├── main.py              # entry point / CLI loop
├── config.py            # paths, model names, Ollama host — JSON persisted
├── router.py            # talks to qwen3:4b-instruct, owns the tool-calling loop
├── coder.py             # talks to qwen2.5-coder:3b, called AS a skill
├── skills/
│   ├── __init__.py      # registry: auto-discovers skills, builds tools[] schema
│   ├── fs_skills.py      # list_files, read_file, move_file, write_file, append_file, open_file
│   ├── search_skills.py  # search_files (find by name or grep content)
│   └── code_skills.py    # run_coder(prompt, context) -> wraps coder.py
├── platform_utils.py    # pathlib-based OS dispatch (open file, etc.)
├── demo.py              # offline showcase: runs every skill, no Ollama needed
├── tests/
│   └── test_skills.py   # unittest suite for skills + fast path (no Ollama needed)
└── logs/
```

**Skill contract** — every skill is a plain function + a schema dict, e.g.:

```python
# skills/fs_skills.py
def list_files(path: str) -> dict:
    p = Path(path)
    return {"files": [f.name for f in p.iterdir()]} if p.exists() else {"error": "not found"}

SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_files",
        "description": "List file names in a given folder",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"]
        }
    }
}
```

`skills/__init__.py` scans the module, builds `TOOLS = [schema, ...]` and a `dispatch = {name: func}` dict. Adding a skill later = new function + new file, nothing else changes — this is the "expand skills over time" capability you wanted.

## Phase 2 — Router loop

`router.py` does the standard tool-calling cycle against `qwen3:4b-instruct`:
1. Send user message + `TOOLS` list.
2. If response has `tool_calls` → look up in `dispatch`, execute locally, feed result back as a `tool` role message, get final natural-language reply.
3. If no tool call → just return the text (general chat / reasoning that doesn't need a tool).

## Phase 3 — Coder as a skill, not a separate path

`run_coder(prompt, file_content=None)` in `code_skills.py` calls `qwen2.5-coder:3b` directly (no tools needed on that call — it's a leaf, not a sub-router). Keep prompts here tight: coder model gets *only* the file content + question, not the whole conversation history, to save context and stay fast on limited hardware.

## Phase 4 — Two-tier speed optimization

Once the basic loop works, add a cheap pre-filter in `main.py` before even calling the router model: regex/keyword match for very common deterministic commands ("list files in", "list folder"). If matched, call the skill directly and skip the router LLM call entirely. Falls back to the full router for anything ambiguous. This is the "if it's faster, run a Python script directly" behavior from your example — just made explicit and cheap rather than relying on the LLM to always decide correctly.

## Phase 5 — Packaging & cross-platform testing

- Test on Mint first (simpler paths), then Windows.
- PyInstaller build with your known `sys.frozen`/`sys.executable` fix for locating the skills folder relative to the `.exe`.
- Ollama stays an external dependency — check at startup that `localhost:11434` responds, and show a clear error/instructions if not (rather than a silent hang), given both target machines are resource-limited and you don't want it silently trying to auto-launch something heavy.

## Where to use Kilo Code / OpenRouter / DeepSeek's harness

Use them to *write and debug* the modules above — e.g. have Kilo Code (backed by an OpenRouter free model or DeepSeek's agent) scaffold `skills/__init__.py`'s auto-discovery logic, or debug a tricky PySide/CLI issue — same role Claude has played in your other projects, just an additional/parallel assistant. None of that code path touches the running mini-agent.


## Router model notes

The registry wiring is verified end-to-end (imports run, `init_skills` binds config to the coder skill, all 8 tools auto-register), and the full router loop has been tested against live Ollama.

The original plan used `phi4-mini` as the router. Tested live, it **does not emit structured tool calls**: it returns the call as raw text in `content` (e.g. `<|tool_call|>>{"files": ["README.md", ...]}`) with no `message.tool_calls` field, and it hallucinates the result. `router.py` relies on `message.get("tool_calls")`, so that silently fails and the agent returns garbage text.

The real bottleneck on these machines is **CPU-only inference** — no discrete GPU (only integrated graphics), so Ollama runs every model in system RAM at ~5–6 tok/s. That makes *tokens generated per turn* the dominant cost, which rules out "thinking"/chain-of-thought models for the router (they reason aloud for hundreds of tokens before acting).

| Router candidate | Size | Structured `tool_calls` | Tokens per tool call | Warm latency | Verdict |
|------------------|------|-------------------------|----------------------|--------------|---------|
| `phi4-mini` | 2.5 GB | ❌ (raw text) | — | — | broken |
| `qwen3:4b` (Thinking) | 2.7 GB | ✅ | ~291 (verbose) | ~60–90 s | too slow |
| **`qwen3:4b-instruct`** | **2.7 GB** | ✅ | **~34** | **~6.6 s** | **✅ chosen** |
| `llama3.1:8b` | 4.9 GB | ✅ | ~30 | ~11 s | works, heavier |

**`qwen3:4b-instruct` is the router.** It's the non-"Thinking" variant of Qwen3 4B — same weight as the thinking model, but it emits a minimal tool call instead of reasoning aloud, so it stays fast on CPU-only hardware. Cold start is ~30–45 s (model load into RAM); once warm, a tool-call turn is single-digit seconds.

To try it:

```bash
pip install -r requirements.txt
ollama pull qwen3:4b-instruct
ollama pull qwen2.5-coder:3b
python main.py
```

A few notes on what's in there:

- **`try_fast_path` in `main.py`** now uses a small `_FAST_PATHS` table of `(regex, skill, arg_builder, formatter)` entries (Phase 4's optimization). It currently short-circuits "list files", "open", "search/grep for … in …", and "find files named … in …" straight to the deterministic skills, skipping the router LLM. Add a `FastPath` entry to extend it — anything that doesn't match (or whose skill errors) falls through to the full router.
- **`router.py`**'s tool loop currently does one round of tool calls per turn (call tools → feed results back → final answer). If you later want the router to chain multiple tool calls in sequence (e.g. list files, then read one, then diagnose it, all without you re-prompting), that loop needs to become recursive.
- No logging or error-message polish yet — there's an empty `logs/` folder waiting for it.
