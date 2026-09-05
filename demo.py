"""
demo.py
Offline showcase for mini-agent's deterministic skills and fast paths.

Run:
    python demo.py        (or: python3 demo.py)

This does NOT need Ollama running: it calls the plain-Python skills and the
fast-path matcher directly, so you can see what the agent can do without
waiting for a model to load (~30-45 s cold). For the LLM-backed parts -- the
tool-calling router and the coder model -- see README.md and run
`python main.py`.
"""

import shutil
import tempfile
from pathlib import Path

from main import try_fast_path
from skills import DISPATCH, TOOLS


def _section(title: str) -> None:
    print()
    print("=" * 64)
    print(title)
    print("=" * 64)


def _show(label: str, result: dict) -> None:
    print(f"\n$ {label}")
    print("  ->", result)


def main() -> None:
    # 1) What's registered with the router.
    _section("1. Registered tools (available to the router)")
    for name in sorted(DISPATCH):
        print(f"  - {name}")
    print(f"\n{len(TOOLS)} tools total.")

    # 2) A throwaway "project" to play in.
    sandbox = Path(tempfile.mkdtemp(prefix="miniagent-demo-"))
    (sandbox / "src").mkdir()
    (sandbox / "README.txt").write_text("MiniAgent demo project\n", encoding="utf-8")
    (sandbox / "src" / "greet.py").write_text(
        "def greet(name):\n"
        "    return f'Hello, {name}!'\n\n"
        "if __name__ == '__main__':\n"
        "    print(greet('world'))\n",
        encoding="utf-8",
    )
    (sandbox / "src" / "config_FOO.json").write_text('{"mode": "demo"}\n', encoding="utf-8")
    print(f"\nSandbox created at: {sandbox}")

    # 3) Filesystem skills.
    _section("2. Filesystem skills")
    _show("list_files(sandbox)", DISPATCH["list_files"](path=str(sandbox)))
    _show(
        "read_file(sandbox/src/greet.py)",
        DISPATCH["read_file"](path=str(sandbox / "src" / "greet.py")),
    )
    _show(
        "write_file(sandbox/notes.txt, 'hello')",
        DISPATCH["write_file"](path=str(sandbox / "notes.txt"), content="hello\n"),
    )
    _show(
        "append_file(sandbox/notes.txt, 'world')",
        DISPATCH["append_file"](path=str(sandbox / "notes.txt"), content="world\n"),
    )
    _show("read_file(sandbox/notes.txt)", DISPATCH["read_file"](path=str(sandbox / "notes.txt")))
    _show(
        "write_file(sandbox/notes.txt) again -- no overwrite",
        DISPATCH["write_file"](path=str(sandbox / "notes.txt"), content="x"),
    )
    _show(
        "move_file(sandbox/notes.txt -> sandbox/src/notes.txt)",
        DISPATCH["move_file"](
            source=str(sandbox / "notes.txt"),
            destination=str(sandbox / "src" / "notes.txt"),
        ),
    )
    _show(
        "open_file(sandbox/missing.txt)",
        DISPATCH["open_file"](path=str(sandbox / "missing.txt")),
    )
    print("\n  (open_file on a *real* path launches the OS default app --")
    print("   try 'open README.txt' from the interactive shell.)")

    # 4) Search.
    _section("3. search_files")
    _show(
        "search_files(query='foo', mode='name')",
        DISPATCH["search_files"](path=str(sandbox), query="foo", mode="name"),
    )
    _show(
        "search_files(query='greet', mode='content')",
        DISPATCH["search_files"](path=str(sandbox), query="greet", mode="content"),
    )
    _show(
        "search_files(query='demo', mode='both')",
        DISPATCH["search_files"](path=str(sandbox), query="demo", mode="both"),
    )

    # 5) Fast path: skip the router LLM entirely.
    _section("4. Fast path (skips the LLM entirely)")
    for phrase in [
        f"list files in {sandbox}",
        f"search for greet in {sandbox}",
        f"find files named foo in {sandbox}",
        "tell me a joke",  # no fast path -> falls through to the router
    ]:
        result = try_fast_path(phrase)
        print(f"\n$ {phrase}")
        print("  ->", repr(result))
    print("\n  ('tell me a joke' returns None, so it would go to the router LLM.)")

    # Cleanup.
    shutil.rmtree(sandbox, ignore_errors=True)
    _section("Done")
    print(f"Cleaned up sandbox {sandbox}.")
    print("\nTo try the full LLM-backed agent, run:  python main.py")


if __name__ == "__main__":
    main()
