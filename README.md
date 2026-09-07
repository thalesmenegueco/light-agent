# light-agent

## Intro

OpenRouter/Kilo Code/DeepSeek's agent harness are the build-time tooling only; the running agent stays 100% local via Ollama. The **Phases** below are the original build plan (mostly complete); the **Current state** section is the up-to-date reference — read it first when picking this project back up.

## Current state (context for future sessions)

### What it is
A fully-local AI coding assistant CLI in Python, running 100% against a local Ollama server (`http://localhost:11434`). Internally named **"MiniAgent"** (`APP_NAME = "MiniAgent"`, MIT). Only runtime dependency is `requests`; everything else is stdlib. The running agent never calls a cloud model — build-time tooling (Kilo Code / OpenRouter / DeepSeek harness) is used only to write/debug code.

### Architecture — two local models
| Role | Model | Job |
|------|-------|-----|
| Router | `qwen3:4b-instruct` | conversation + the **tool-calling loop** (decides which skill to run) |
| Coder | `qwen2.5-coder:3b` | a "leaf" node (no tools, no history) called *as a skill* |

Both via Ollama's `/api/chat`. CPU-only inference (~5–6 tok/s) — tokens-per-turn is the dominant cost, which ruled out "thinking"/CoT models for the router (see [Router model notes](#router-model-notes)).

### File structure (current)
```
light-agent/
├── main.py              # CLI entry: argparse (--run-command-mode/--autopilot), warm-up, fast-path, loop, history trim, errors
├── config.py            # cross-platform config, JSON-persisted (DEFAULT_CONFIG + save/load)
├── router.py            # recursive tool-calling loop + warm_up + plan_goal (planner) + token accounting
├── coder.py             # thin wrapper around qwen2.5-coder:3b (leaf)
├── autopilot.py         # unattended planner/executor loop: plan -> execute steps -> verify, with budget + resume
├── session.py           # durable session state (goal/plan/progress/budget/history) -> session.json
├── platform_utils.py    # OS dispatch (open_path) + path confinement (normalize_path/confined_path/set_project_root)
├── logging_setup.py     # rotating file logging → logs/mini-agent.log (1 MB × 3)
├── demo.py              # offline showcase (no Ollama)
├── requirements.txt     # requests>=2.31,<3.0  (the only runtime dep)
├── requirements-build.txt # pyinstaller (build-time only)
├── mini-agent.spec      # PyInstaller build config (one-file console app)
├── skills/
│   ├── __init__.py      # registry: auto-discovers SCHEMAS → TOOLS[] + DISPATCH{}; init_skills(config)
│   ├── fs_skills.py     # list/read/write/append/move/replace_in_file/open_file (+ file-mutation gate)
│   ├── search_skills.py # search_files (name/content/both; results sorted)
│   ├── git_skills.py    # git_status/diff/log (read-only) + git_commit/checkpoint/rollback (gated)
│   ├── code_skills.py   # run_coder → coder leaf (bind_config)
│   ├── meta_skills.py   # list_skills/get_config/set_config (validates + persists)
│   ├── run_command_skills.py  # run_command (deny-by-default safety policy)
│   └── verify_skills.py # run_tests (runs the config-side test_command; verification loop)
├── tests/               # 184 tests (offline, no Ollama)
│   ├── test_skills.py             # registry + fs/search + fast path
│   ├── test_git_skills.py         # read-only git (+ git fast paths)
│   ├── test_git_mutation_policy.py # git commit/checkpoint/rollback gate
│   ├── test_meta_skills.py        # meta skills + set_config re-apply + in-place config
│   ├── test_logging.py            # logging setup
│   ├── test_run_command.py        # run_command policy (scripted confirmer)
│   ├── test_run_command_cli.py    # live-terminal subprocess test (real stdin)
│   ├── test_router.py             # warm_up + recursive tool loop
│   ├── test_path_confinement.py   # path confinement
│   ├── test_file_mutation_policy.py  # file mutation gate
│   ├── test_verify_skills.py      # run_tests verification skill
│   ├── test_session.py            # session-state persistence
│   ├── test_autopilot.py          # budget + planner parsing + resume loop
│   └── test_run_command_path_confinement.py  # argv-level run_command confinement
├── logs/                # mini-agent.log (.gitkeep, gitignored)
├── assets/              # (empty, reserved for packaging assets)
├── .gitignore
├── LICENSE
└── README.md
```

### Skill contract
Each skill module exposes `SCHEMAS = [(schema_dict, function), ...]`; `skills/__init__.py` auto-registers them into `TOOLS` (the OpenAI-style function schemas sent to the router) and `DISPATCH` (`{name: function}`). **Adding a skill = new file + one import in `skills/__init__.py`; `router.py` never changes.** Some skills need config or a confirmer, injected at startup via `bind_config()` / `bind_confirmer()` / `bind_file_confirmer()` / `bind_git_confirmer()` (see [Startup flow](#startup-flow)).

### Configuration (every key in `DEFAULT_CONFIG`)
| Key | Default | Meaning |
|-----|---------|---------|
| `ollama_host` | `http://localhost:11434` | Ollama server |
| `router_model` | `qwen3:4b-instruct` | tool-calling model |
| `coder_model` | `qwen2.5-coder:3b` | coding leaf model |
| `router_temperature` | `0.2` | router sampling temp |
| `coder_temperature` | `0.1` | coder sampling temp |
| `max_history_messages` | `12` | conversation context window (read per-turn) |
| `max_tool_rounds` | `4` | tool-calling rounds before forcing a final answer |
| `log_level` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `log_file` | `""` | `""` = `<app_dir>/logs/mini-agent.log` |
| `project_root` | `""` | path-confinement root; `""` = no confinement |
| `file_mutation_mode` | `allow` | `off` / `confirm` / `allow` for write/append/move/replace |
| `git_mutation_mode` | `off` | `off` / `confirm` / `allow` for commit/checkpoint/rollback |
| `run_command_mode` | `off` | `off` / `confirm` / `allowlist` / `auto` |
| `run_command_allowlist` | `[]` | programs allowed without confirmation |
| `run_command_denylist` | `[]` | extra refusal literals (merged with built-ins) |
| `run_command_timeout` | `30` | seconds before a command is killed |
| `run_command_max_output` | `8000` | chars, per stdout/stderr |
| `run_command_cwd` | `""` | `""` = inherit; else fixed working dir |
| `run_command_shell` | `false` | allow shell operators at all |
| `run_command_allow_network` | `false` | allow network-touching programs |
| `test_command` | `""` | command `run_tests` runs (config-side only; `""` = verification disabled) |
| `test_timeout` | `120` | seconds before a test run is killed |
| `test_max_output` | `8000` | chars, per stdout/stderr |
| `max_session_steps` | `0` | `0` = unlimited; cap autonomous steps per run |
| `session_timeout_seconds` | `0` | `0` = unlimited; wall-clock cap for a whole run |
| `max_session_tokens` | `0` | `0` = unlimited; router token cap per run |
| `verify_rounds` | `2` | max fix iterations per step when tests fail |

Config lives at `%APPDATA%\MiniAgent\config.json` (Windows) / `~/.config/mini-agent/config.json` (Linux). `set_config` validates against these keys and persists; `project_root` is re-applied live (all other consumers read keys lazily).

### Safety layers (four, complementary)
1. **`run_command`** — deny-by-default local command execution (off by default; see below).
2. **`project_root`** — path confinement for every file/git/search skill and `run_command`'s cwd (off by default).
3. **`file_mutation_mode`** — gates `write/append/move/replace` (`allow` by default; `confirm`/`off` for unattended).
4. **`git_mutation_mode`** — gates `git_commit/git_checkpoint/git_rollback` (`off` by default; the mutating git skills ship disabled).

`run_tests` adds a fifth, differently-shaped safety property: it runs only the human-configured `test_command`, never a model-supplied command, so the verification loop can run unattended without re-opening arbitrary execution.

### Startup flow / CLI
`python main.py` → load config → `init_skills(config)` (binds config to coder/meta/run_command/fs/git/verify + sets `project_root`) → bind the three terminal confirmers (run_command / file-mutation / git-mutation) → setup logging → Ollama health check (exit if unreachable) → **warm-up** (a `num_predict:1` chat call so the first turn doesn't hit the ~30–45s cold load; non-fatal) → ready banner → input loop.

- Flag `--run-command-mode {off,confirm,allowlist,auto}` is a session-only (non-persisted) override; a startup hint prints when `run_command_mode` ≠ `off` or `file_mutation_mode` ≠ `allow`.
- **Fast path** (`_FAST_PATHS` in `main.py`) short-circuits ~9 deterministic phrasings (list/open/read/cat/search/find/git-status/git-log/git-diff/list-skills/set-project-root) straight to the skill, skipping the router; anything unmatched (or whose skill errors) falls through to the router.
- The router's tool loop is **recursive** (multi-round), bounded by `max_tool_rounds`; intermediate tool messages never enter the persisted history.

### Autopilot (unattended)
`python main.py --autopilot "<goal>"` runs the **planner → executor** loop with no input prompt:

1. **plan** — `router.plan_goal(goal)` asks the router (JSON mode) to break the goal into an ordered list of steps.
2. **execute** — each step runs through the normal tool-calling loop (`router.handle_message`), so the coder leaf and every deterministic skill are available.
3. **verify** — when `test_command` is set, `run_tests` runs after each step and failures are fed back to the executor up to `verify_rounds` times (the code → test → fix loop).
4. **budget** — before every step, `max_session_steps` / `session_timeout_seconds` / `max_session_tokens` are checked; hitting any of them stops the run and marks it `blocked`.
5. **persist/resume** — progress (goal, plan, step status, budget counters, compact history) is written to `session.json` after every step, so a crashed run resumes from the last completed step. Re-run the same `--autopilot "<goal>"` to resume; `--new-session` discards saved state and starts fresh.

In autopilot mode **no terminal confirmers are bound**, so every `confirm`-gated path fails closed rather than hanging on an absent human. For unattended use, configure `project_root` (boundary), `file_mutation_mode`/`git_mutation_mode` (`off` or `allow` per your trust), and `test_command` + the budget keys.

### Testing
```bash
python3 -m unittest   # 184 tests pass, offline, no Ollama (~4 s)
python3 demo.py       # offline showcase, reports 20 tools
```
`tests/test_run_command_cli.py` drives the real `terminal_confirmer` through a real subprocess with real stdin (the only test that touches a live terminal). Everything else uses scripted/mocked confirmers and `requests`.

### Roadmap / known gaps
- Fast path is a fixed phrase table (extend via a `FastPath` entry); the tool loop is recursive but capped at `max_tool_rounds`.
- `run_command` "always allow" persists to the allowlist but only auto-runs in `allowlist`/`auto` modes.
- `run_command` argv-level confinement is heuristic: it flags argument paths that escape `project_root` (absolute, `..`, `~`, symlinks) but skips option flags and plain words; a non-path argument that happens to look like an absolute path (e.g. a `grep` pattern) is refused on the safe side. The program token itself (`argv[0]`) is not confined — running an outside program binary is still governed by the deny/allowlist/confirm model.
- The session token budget counts the **router** model only (eval + prompt-eval per call); the coder leaf's tokens aren't tallied yet.
- Resume is "at-least-once": a step marked `in_progress` when a crash lands may re-run on resume.
- Natural next skills: `copy_file`, `delete_file`/`move_to_trash`, `file_info`, `tree`, `count_lines`, `diff_files`, `fetch_url` (network, opt-in). Mutating/network ones must land behind the same gating as the existing policies.
- Phase 5 packaging config (`mini-agent.spec`, `requirements-build.txt`, frozen log path) is in place; the actual PyInstaller build still needs to be run and smoke-tested on Mint first, then Windows (see [Phase 5](#phase-5--packaging--cross-platform-testing)).

## Try it now

**Offline demo — no Ollama needed:**

```bash
python demo.py        # or: python3 demo.py
```

Builds a throwaway sandbox and exercises every deterministic skill
(`list_files`, `read_file`, `write_file`, `append_file`, `move_file`,
`replace_in_file`, `open_file`, `search_files`) plus the fast-path matcher,
so you can see what the agent can do without waiting for a model to load
(~30–45 s cold). The git and meta skills are covered by the unit tests.

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

To enable the off-by-default `run_command` skill with per-command confirmation, start with `python main.py --run-command-mode confirm`.

To run unattended, set a project root and a test command first, then:

```bash
python main.py --autopilot "implement the missing unit tests for search_files"
```

It plans the goal into steps, executes each through the tool loop, runs `test_command` between steps, and resumes if interrupted (add `--new-session` to start over).

On startup, `main.py` pre-loads the router model into memory (with a visible progress message), so the first turn doesn't pay the ~30–45s cold-start load.

Then try:

- `list files in .`
- `open README.md`
- `read main.py` / `cat main.py` / `show me README.md`  (deterministic read)
- `search for normalize_path in .`
- `find files named config in .`
- `replace "could not reach" with "cannot reach" in main.py`  (deterministic edit)
- `what's the git status?` / `show me the latest commits` / `diff the working tree`
- `what can you do?`  (lists skills) / `what config are you using?`
- `write a hello.py file that prints hello`  (router + coder model)
- `why isn't this working?`  (delegates to the coder model)

The list / open / read / search / find / git-status / git-log / git-diff / list-skills phrasings above are all **fast-path** matches: `main.py` recognizes them deterministically and calls the skill directly, skipping the router LLM entirely. Anything else falls through to the router.

## Current skills

| Skill | Kind | What it does |
|-------|------|--------------|
| `list_files` | deterministic | List files and folders in a directory |
| `read_file` | deterministic | Read a text file (truncated at 8000 chars) |
| `write_file` | deterministic (gated) | Create / overwrite a text file |
| `append_file` | deterministic (gated) | Append text to a file |
| `move_file` | deterministic (gated) | Move / rename a file |
| `replace_in_file` | deterministic (gated) | Replace text in a file (first occurrence, or all with `replace_all=true`) |
| `open_file` | deterministic | Open a file or folder in the OS default app |
| `search_files` | deterministic | Find files by name or grep text inside files |
| `git_status` | deterministic (read-only) | Show branch + working-tree status |
| `git_diff` | deterministic (read-only) | Unified diff of working-tree / staged changes |
| `git_log` | deterministic (read-only) | Recent commit history, one line per commit |
| `git_commit` | deterministic (gated) | Commit changes (tracked + untracked) — `git_mutation_mode` |
| `git_checkpoint` | deterministic (gated) | Snapshot the working tree as a commit; returns its hash |
| `git_rollback` | deterministic (gated) | Discard all changes since a checkpoint (`reset --hard` + `clean`) |
| `list_skills` | deterministic | List every available tool with its description |
| `get_config` | deterministic | Read the current runtime config |
| `set_config` | deterministic | Validate, apply, and persist config changes |
| `run_coder` | LLM (coder leaf) | Write / review / debug code via `qwen2.5-coder:3b` |
| `run_command` | deterministic (gated) | Run a local command under a deny-by-default safety policy (off by default) |
| `run_tests` | deterministic (config-side) | Run the pre-configured `test_command`; reports pass/fail for the verification loop |

### `run_command` safety model

`run_command` executes a local command but ships **disabled** (`run_command_mode: "off"`); enabling it is a deliberate opt-in. Every request passes through a deny-by-default pipeline before anything runs:

1. **denylist** — destructive patterns (`rm -rf /`, `mkfs`, `dd … of=/dev/…`, shutdown, fork bomb) are refused outright.
2. **no eval** — interpreter `-c`/`-e` escapes (`python -c`, `sh -c`) are refused.
3. **no TTY** — interactive/privileged programs (`vim`, `sudo`, `ssh`, pagers) are refused.
4. **no network** — `curl`, `pip`, `git push`, etc. are refused unless `run_command_allow_network` is set.
5. **argv confinement** — when `project_root` is set, argument paths that escape it (absolute paths, `..`, `~`, symlinks) are refused; the working directory also defaults to the root when none is given.
6. **human confirmation** — anything else prompts with the exact command + cwd (fail-closed if no confirmer is bound); "always allow" persists the program to the allowlist.

The model only ever sees `command` and `cwd`; shell, timeout, allowlist, denylist, network, argv and cwd-confinement are config-side. Modes: `off` → `confirm` → `allowlist` → `auto`.

**Enabling** (session-only, recommended): start with `python main.py --run-command-mode confirm`, then ask it to "run the command `python3 --version`". To persist, set `run_command_mode` in `config.json`, or tell the agent `set run_command_mode to confirm`. The confirmation → execution path is covered by live-terminal tests in `tests/test_run_command_cli.py` (real subprocess + real stdin, no Ollama).

### Path confinement

By default the agent can read/write any path (`project_root` is empty). Set `project_root` to a directory, and every filesystem, git, and search skill resolves paths against it — and refuses anything that escapes it via `..`, an absolute path, or a symlink. `run_command` is confined the same way: its working directory defaults to the root, and argument paths that escape it are refused. This is the first safety layer for unattended/autopilot use: confine the agent to the project it's allowed to touch.

```json
{ "project_root": "/home/you/your-project" }
```

An empty `project_root` (the default) disables confinement and paths behave exactly as before. Set it in `config.json`, or tell the agent `set project_root to /home/you/your-project`.

### File mutation policy

The mutating filesystem skills (`write_file`, `append_file`, `move_file`, `replace_in_file`) are gated by `file_mutation_mode`, mirroring the `run_command` safety model:

- `allow` (default) — mutations run without prompting (the original behavior).
- `confirm` — every mutation prompts for confirmation; **fails closed** if no confirmer is bound, so an unattended agent cannot write.
- `off` — all mutations are refused; the agent becomes read-only.

```json
{ "file_mutation_mode": "confirm" }
```

For unattended/autopilot use, set `file_mutation_mode` to `off` (read-only) or `confirm` (only works with a human at the terminal), and rely on `project_root` as the boundary when you do allow writes. `open_file` and the read-only skills are unaffected.

### Git mutation policy (checkpoint / rollback)

The mutating git skills (`git_commit`, `git_checkpoint`, `git_rollback`) ship **disabled** (`git_mutation_mode: "off"`) and are gated the same way as file mutations:

- `off` (default) — all three refuse; the agent stays read-only on git.
- `confirm` — prompts for confirmation; **fails closed** if no confirmer is bound.
- `allow` — run without prompting.

```json
{ "git_mutation_mode": "allow" }
```

`git_checkpoint` snapshots the whole working tree as a commit (tracked + untracked) and returns its hash; `git_rollback <hash>` returns to that commit via `reset --hard` + `clean -fd`. Together they give an autopilot a revert point before a multi-step change. Enable them (`allow`) when you trust the boundary set by `project_root`, and checkpoint before each batch of autonomous edits.

### Verification loop (`run_tests`)

Set `test_command` to the suite you want the agent to run (e.g. `python -m unittest`, `pytest -q`), and `run_tests` executes exactly that command — never a model-supplied one — with `shell=False`, inside `project_root`, bounded by `test_timeout`/`test_max_output`. It reports a structured `{passed, exit_code, stdout, stderr}` result. In the autopilot, failures are fed back to the executor up to `verify_rounds` times, closing the code → test → fix loop without re-opening arbitrary command execution.

## Phase 0 — Foundations (before any agent logic)

- Install Ollama on both machines, pull `qwen3:4b-instruct` (router) and `qwen2.5-coder:3b` (coder).
- Confirm both respond to `/api/chat` correctly — in particular, that the router returns a **structured `tool_calls` field** (not raw text) when given a `tools` payload. A quick curl/Python test validates the whole architecture before you build on top of it (see [Router model notes](#router-model-notes)).
- Decide the skills storage location: `%APPDATA%\MiniAgent\skills\` (Windows) / `~/.config/mini-agent/skills/` (Linux), same auto-detect pattern as your other tools.

## Phase 1 — Project skeleton

*(Original plan layout — see [Current state](#current-state-context-for-future-sessions) for the files as they exist today, which include `run_command_skills.py` and the extra test files.)*

```
mini-agent/
├── main.py              # entry point / CLI loop
├── config.py            # paths, model names, Ollama host — JSON persisted
├── router.py            # talks to qwen3:4b-instruct, owns the tool-calling loop
├── coder.py             # talks to qwen2.5-coder:3b, called AS a skill
├── skills/
│   ├── __init__.py      # registry: auto-discovers skills, builds tools[] schema
│   ├── fs_skills.py      # list_files, read_file, move_file, write_file, append_file, replace_in_file, open_file
│   ├── search_skills.py  # search_files (find by name or grep content)
│   ├── git_skills.py     # git_status, git_diff, git_log (read-only)
│   ├── code_skills.py    # run_coder(prompt, context) -> wraps coder.py
│   └── meta_skills.py    # list_skills, get_config, set_config
├── platform_utils.py    # pathlib-based OS dispatch (open file, etc.)
├── logging_setup.py     # rotating file logging (logs/mini-agent.log)
├── demo.py              # offline showcase: runs every skill, no Ollama needed
├── tests/
│   ├── test_skills.py   # unittest suite for skills + fast path (no Ollama needed)
│   ├── test_git_skills.py  # unittest suite for the read-only git skills
│   ├── test_meta_skills.py # unittest suite for the meta skills
│   └── test_logging.py  # unittest suite for the logging setup
└── logs/                # mini-agent.log written here at runtime
```

**Skill contract** — every skill is a plain function + a schema dict, exposed as `SCHEMAS = [(schema, function), ...]`, e.g.:

```python
# skills/fs_skills.py
def list_files(path: str) -> dict:
    p = Path(path)
    return {"files": [f.name for f in p.iterdir()]} if p.exists() else {"error": "not found"}

SCHEMAS = [
    (
        {
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
        },
        list_files,
    ),
]
```

`skills/__init__.py` scans each module's `SCHEMAS`, builds `TOOLS = [schema, ...]` and `DISPATCH = {name: func}`. Adding a skill later = new function + new file, nothing else changes — this is the "expand skills over time" capability you wanted.

## Phase 2 — Router loop

`router.py` does the tool-calling cycle against `qwen3:4b-instruct`:
1. Send user message + `TOOLS` list.
2. If response has `tool_calls` → look up in `DISPATCH`, execute locally, feed result back as a `tool` role message, and ask again.
3. If no tool call → return the text (general chat / reasoning that doesn't need a tool).

*(Now implemented as a **recursive** loop, bounded by `max_tool_rounds` — see [Current state](#current-state-context-for-future-sessions).)*

## Phase 3 — Coder as a skill, not a separate path

`run_coder(prompt, file_content=None)` in `code_skills.py` calls `qwen2.5-coder:3b` directly (no tools needed on that call — it's a leaf, not a sub-router). Keep prompts here tight: coder model gets *only* the file content + question, not the whole conversation history, to save context and stay fast on limited hardware.

## Phase 4 — Two-tier speed optimization

Once the basic loop works, add a cheap pre-filter in `main.py` before even calling the router model: regex/keyword match for very common deterministic commands ("list files in", "list folder"). If matched, call the skill directly and skip the router LLM call entirely. Falls back to the full router for anything ambiguous. This is the "if it's faster, run a Python script directly" behavior from your example — just made explicit and cheap rather than relying on the LLM to always decide correctly.

## Phase 5 — Packaging & cross-platform testing

Build-time only (PyInstaller is not a runtime dependency). The build environment needs the runtime deps (`requests`) **and** PyInstaller:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-build.txt
.venv/bin/pyinstaller --clean --noconfirm mini-agent.spec
```

Output is a single-file console binary at `dist/mini-agent` (Linux/macOS) or `dist/mini-agent.exe` (Windows).

Notes on the frozen app:

- **No on-disk skills folder.** Skills are imported as Python modules (`skills/__init__.py`), so PyInstaller bundles them into the binary automatically — the original "locate the skills folder relative to the .exe" fix is moot. `mini-agent.spec` therefore collects no data files.
- **Writable locations.** Config lives at `%APPDATA%\MiniAgent\` / `~/.config/mini-agent/` (`config.get_base_dir`), which is already per-user. When frozen, logs go to `<base_dir>/logs/mini-agent.log` (`logging_setup.get_log_file`) instead of next to the exe, which may be read-only (e.g. Program Files).
- **Ollama stays external.** `main.py` already checks `localhost:11434` at startup and exits with a clear message instead of hanging (see `check_ollama`).

Test on Mint first (simpler paths), then Windows — both still use Ollama as the external model server.

## Where to use Kilo Code / OpenRouter / DeepSeek's harness

Use them to *write and debug* the modules above — e.g. have Kilo Code (backed by an OpenRouter free model or DeepSeek's agent) scaffold `skills/__init__.py`'s auto-discovery logic, or debug a tricky PySide/CLI issue — same role Claude has played in your other projects, just an additional/parallel assistant. None of that code path touches the running mini-agent.


## Router model notes

The registry wiring is verified end-to-end (imports run, `init_skills` binds config to the coder skill, all 20 tools auto-register), and the full router loop has been tested against live Ollama.

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

- **`try_fast_path` in `main.py`** now uses a small `_FAST_PATHS` table of `(regex, skill, arg_builder, formatter)` entries (Phase 4's optimization). It short-circuits "list files", "open", "search/grep for … in …", "find files named … in …", "read/cat/show me …", "git status / git log / git diff", and "what can you do / list skills" straight to the deterministic skills, skipping the router LLM. Add a `FastPath` entry to extend it — anything that doesn't match (or whose skill errors) falls through to the full router.
- **`router.py`'s tool loop is recursive** (multi-round). Each turn sends the conversation + tools, executes any requested tool calls locally, feeds the results back, and asks again — so the router can chain e.g. `list files` → `read file` → `diagnose` without a fresh user prompt. A per-turn cap bounds the token cost on CPU-only hardware: `max_tool_rounds` in `config.json` (default 4) is the number of tool-calling rounds before the loop forces a final plain-text answer. Intermediate tool messages stay inside the turn and never enter the persisted history.
- **Logging & error polish** — `logging_setup.py` routes diagnostics and errors to a rotating `logs/mini-agent.log` (1 MB, 3 backups). `log_file` and `log_level` in `config.json` override the location and verbosity. Stdout stays for user-facing output only; the router's tool dispatch, the CLI loop, and the OS opener all log unexpected errors instead of crashing or failing silently, and a corrupted `config.json` logs a warning while falling back to defaults.
