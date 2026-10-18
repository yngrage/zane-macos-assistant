"""Command-line interface: chat REPL, one-shot `ask`, `doctor`, and `key` (Keychain) management."""
from __future__ import annotations

import argparse
import os
import signal
import sys
from typing import Any, Callable, List, Optional

from zane import __version__
from zane.app.commands import HELP, handle_command
from zane.app.config import Config
from zane.app.doctor import run_doctor
from zane.app.lifecycle import App, build_app, setup_logging
from zane.core.cancel import CancelToken
from zane.security.approvals import CliApprover
from zane.security.keychain import keychain_clear, keychain_set_interactive
from zane.tools.runner import run_cmd
from zane.voice.cli import voice_repl


def _banner(app: App) -> str:
    cfg = app.config
    model = f"{cfg.model} (cloud)" if app.assistant.provider and app.assistant.provider.is_configured() else "none - offline mode (direct commands only)"
    return (f"ZANE {__version__} online. Model: {model}. Fast path: {'on' if cfg.fast_path else 'off'}. "
            f"Approved folders: {len(app.registry.ctx.guard.roots())}. /help for commands.")


def repl(app: App, input_fn: Callable[[str], str], out: Callable[[str], None]) -> int:
    out(_banner(app))
    if app.scheduler:
        app.scheduler.start()
    try:
        while True:
            try:
                line = input_fn("you > ")
            except EOFError:
                out("")
                break
            except KeyboardInterrupt:
                out("")
                continue
            line = line.strip()
            if not line:
                continue
            if line.startswith("/"):
                if handle_command(app, line, out) == "quit":
                    break
                continue
            token = CancelToken()
            previous: Any = None
            try:  # Ctrl-C cancels the running request instead of killing ZANE
                previous = signal.signal(signal.SIGINT, lambda *_: token.cancel())
            except ValueError:
                pass  # not the main thread
            try:
                reply = app.assistant.handle(line, cancel=token)
            finally:
                if previous is not None:
                    signal.signal(signal.SIGINT, previous)
            out(f"zane > {reply.text}")
    finally:
        app.close()
    return 0


def main(argv: Optional[List[str]] = None, *, input_fn: Callable[[str], str] = input, out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(prog="zane", description="ZANE - personal AI operator for macOS")
    parser.add_argument("--offline", action="store_true", help="never call a model; direct commands only")
    parser.add_argument("--voice", action="store_true", help="use macOS Dictation input and spoken replies")
    parser.add_argument("--home", help="data directory (default: ~/Library/Application Support/ZANE)")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("chat", help="interactive session (default)")
    ask = sub.add_parser("ask", help="run one request and exit")
    ask.add_argument("text", nargs="+")
    doc = sub.add_parser("doctor", help="audit the environment and smoke-test the tools")
    doc.add_argument("--raw", action="store_true", help="also print raw command output (macOS parser verification)")
    key = sub.add_parser("key", help="manage the API key in the macOS Keychain")
    key.add_argument("action", choices=["set", "status", "clear"])
    args = parser.parse_args(argv)

    env = dict(os.environ)
    if args.home:
        env["ZANE_HOME"] = args.home
    config = Config.load(env=env)

    if args.cmd == "key":
        if args.action == "status":
            out(f"API key: {'configured via ' + config.api_key_source if config.api_key else 'not configured'}")
            return 0
        if sys.platform != "darwin":
            out("Keychain is macOS-only. Set ANTHROPIC_API_KEY in your environment or .env instead.")
            return 1
        if args.action == "set":
            out("Enter your Anthropic API key at the prompt below (it is typed into macOS `security`, not into ZANE).")
            return keychain_set_interactive()
        out("Removed from Keychain." if keychain_clear(run_cmd) else "No ZANE key found in Keychain.")
        return 0

    setup_logging(config)
    app = build_app(config, approver=CliApprover(input_fn, out), provider=_provider_arg(args))
    try:
        if args.cmd == "doctor":
            out(run_doctor(app, raw=args.raw))
            return 0
        if args.cmd == "ask":
            reply = app.assistant.handle(" ".join(args.text))
            out(reply.text)
            return 1 if reply.route == "error" else 0
        if args.voice:
            return voice_repl(app, input_fn=input_fn, out=out)
    finally:
        if args.cmd in ("doctor", "ask"):
            app.close()
    return repl(app, input_fn, out)


def _provider_arg(args: argparse.Namespace) -> Any:
    """--offline -> None (no model); otherwise the configured default."""
    from zane.app.lifecycle import _DEFAULT

    return None if args.offline else _DEFAULT


if __name__ == "__main__":
    sys.exit(main())
