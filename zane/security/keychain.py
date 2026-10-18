"""macOS Keychain access through the `security` tool (argument arrays only). The secret is typed into
`security` itself at its own prompt, so it never appears in argv, shell history or ZANE's memory."""
from __future__ import annotations

import getpass
import subprocess
from typing import Any, Optional

SERVICE = "zane-anthropic"


def _account(account: Optional[str]) -> str:
    if account:
        return account
    try:
        return getpass.getuser()
    except Exception:
        return "zane"


def keychain_get(runner: Any, account: Optional[str] = None) -> Optional[str]:
    res = runner(["security", "find-generic-password", "-s", SERVICE, "-a", _account(account), "-w"], timeout=10)
    return (res.stdout.strip() or None) if res.ok else None


def keychain_set_interactive(account: Optional[str] = None) -> int:
    return subprocess.call(["security", "add-generic-password", "-U", "-s", SERVICE, "-a", _account(account), "-w"])


def keychain_clear(runner: Any, account: Optional[str] = None) -> bool:
    return bool(runner(["security", "delete-generic-password", "-s", SERVICE, "-a", _account(account)], timeout=10).ok)
