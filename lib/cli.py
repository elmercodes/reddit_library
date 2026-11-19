"""Command line entry point: `uv run rl <command>`."""

from __future__ import annotations

import importlib
import platform
import shutil
import subprocess
import sys

import typer

from . import config as C

app = typer.Typer(help="Reddit Library: collect, summarize, and chat with subreddit books.",
                  no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """Reddit Library commands. Every paid step shows a cost estimate and waits for y/N."""


def _ok(flag: bool) -> str:
    return "OK " if flag else "-- "


@app.command("env-check")
def env_check() -> None:
    """Check tools, dependencies, folders, config, and which secrets are set (never their values)."""
    problems = 0

    py_ok = sys.version_info >= (3, 10)
    problems += not py_ok
    print(f"{_ok(py_ok)} Python {platform.python_version()} (need 3.10+)")

    for tool in ("uv", "git"):
        path = shutil.which(tool)
        version = ""
        if path:
            version = subprocess.run([tool, "--version"], capture_output=True, text=True).stdout.strip()
        problems += not path
        print(f"{_ok(bool(path))} {tool}: {version or 'not found'}")

    for mod in ("anthropic", "apify_client", "httpx", "yaml", "dotenv", "fastapi", "uvicorn", "typer"):
        try:
            importlib.import_module(mod)
            print(f"OK  import {mod}")
        except ImportError:
            problems += 1
            print(f"--  import {mod} FAILED (run `uv sync`)")

    try:
        cfg = C.config()
        name, col = C.active_collector()
        print(f"OK  config.yaml: {len(cfg['subreddits'])} subreddits, collector={name} ({col['actor_id']})")
    except Exception as e:  # noqa: BLE001
        problems += 1
        print(f"--  config.yaml failed to load: {e}")

    for folder in (C.RAW, C.THREADS, C.IMAGES, C.BOOKS, C.MASTER, C.IDEAS, C.GAMES, C.INBOX, C.CHATS):
        exists = folder.is_dir()
        problems += not exists
        print(f"{_ok(exists)} folder {folder.relative_to(C.ROOT)}/")

    env_exists = (C.ROOT / ".env").exists()
    print(f"{_ok(env_exists)} .env file {'present' if env_exists else 'missing (copy .env.example to .env when a key is needed)'}")
    for name in C.SECRET_NAMES:
        print(f"{_ok(bool(C.secret(name)))} {name} {'set' if C.secret(name) else 'not set yet'}")

    print("\nAll required checks passed." if problems == 0 else f"\n{problems} problem(s) found.")
    raise typer.Exit(code=1 if problems else 0)


if __name__ == "__main__":
    app()
