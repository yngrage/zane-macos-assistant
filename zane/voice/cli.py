"""Push-to-talk style voice loop using Apple's on-device Speech framework and `say`."""
from __future__ import annotations

import sys
from typing import Any, Callable


def speak(text: str) -> None:
    if sys.platform != "darwin" or not text:
        return
    import subprocess
    subprocess.run(["say", text], check=False)


def voice_repl(app: Any, *, input_fn: Callable[[str], str], out: Callable[[str], None]) -> int:
    from zane.app.commands import handle_command
    from zane.core.cancel import CancelToken

    out("ZANE voice mode. Enable macOS Dictation in System Settings > Keyboard, then dictate at the prompt and press Return. Replies are spoken. Type /quit to exit.")
    if app.scheduler:
        app.scheduler.start()
    try:
        while True:
            try:
                command = input_fn("Dictate or type (or /quit) > ").strip()
            except (EOFError, KeyboardInterrupt):
                out("")
                break
            if command.startswith("/"):
                if handle_command(app, command, out) == "quit":
                    break
                continue
            if not command:
                continue
            text = command
            out(f"you > {text}")
            reply = app.assistant.handle(text, cancel=CancelToken())
            out(f"zane > {reply.text}")
            speak(reply.text)
    finally:
        app.close()
    return 0
