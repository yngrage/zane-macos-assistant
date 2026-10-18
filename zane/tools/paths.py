"""Path validation: every file tool resolves paths through PathGuard, which only allows locations inside
user-approved directories (symlinks resolved, `..` collapsed) and refuses credential-looking files."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from zane.tools.errors import ToolRefusal


class PathRefused(ToolRefusal):
    pass


SENSITIVE_NAMES = {
    ".netrc", ".npmrc", ".pypirc", ".git-credentials", ".htpasswd", "id_rsa", "id_dsa", "id_ecdsa",
    "id_ed25519", "credentials", "credentials.json", "secrets.json", "secrets.yml", "secrets.yaml",
    "service-account.json",
}
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keychain", ".keychain-db", ".kdbx", ".jks")
SENSITIVE_DIRS = {".ssh", ".aws", ".gnupg", ".kube", "Keychains"}
ENV_SAFE_SUFFIXES = (".example", ".sample", ".template", ".dist")

_FORBIDDEN_TREES = ("/System", "/Library", "/usr", "/bin", "/sbin", "/etc", "/private/etc", "/dev")
_FORBIDDEN_EXACT = {"/", "/Applications", "/Volumes", "/private", "/var", "/tmp", "/Users", "/home"}


def sensitive_reason(path: Path) -> Optional[str]:
    name = path.name.lower()
    if name == ".env" or (name.startswith(".env.") and not name.endswith(ENV_SAFE_SUFFIXES)):
        return "environment files usually hold credentials"
    if name in SENSITIVE_NAMES or name.endswith(SENSITIVE_SUFFIXES):
        return "it looks like a credential or key file"
    if any(part in SENSITIVE_DIRS for part in path.parts):
        return "it lives in a credentials directory"
    if ".git" in path.parts and name in ("config", "credentials"):
        return "git config can embed credentials"
    return None


def _within(child: Path, root: Path) -> bool:
    return child == root or root in child.parents


class PathGuard:
    def __init__(self, roots: Callable[[], Iterable[Path]]) -> None:
        self._roots = roots

    def roots(self) -> List[Path]:
        out = []
        for r in self._roots():
            p = Path(r).expanduser()
            if p.is_dir():
                out.append(Path(os.path.realpath(p)))
        return out

    def resolve(self, raw: str) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise PathRefused("Empty path.")
        if "\x00" in raw:
            raise PathRefused("Invalid path.")
        p = Path(raw).expanduser()
        if not p.is_absolute():
            raise PathRefused("Use an absolute path (or one starting with ~/).")
        real = Path(os.path.realpath(p))  # collapses '..' and follows symlinks BEFORE the check
        for root in self.roots():
            if _within(real, root):
                return real
        raise PathRefused(f"{raw} is outside the approved directories. The user can approve a project folder with /projects add.")

    def is_root(self, path: Path) -> bool:
        return any(path == r for r in self.roots())

    @staticmethod
    def validate_new_root(path: object) -> Path:
        real = Path(os.path.realpath(Path(str(path)).expanduser()))
        home = Path(os.path.realpath(Path.home()))
        if not real.is_dir():
            raise PathRefused(f"{real} is not an existing directory.")
        if str(real) in _FORBIDDEN_EXACT or real == home or real in home.parents:
            raise PathRefused("That directory is too broad to approve. Choose a specific project or folder.")
        for tree in _FORBIDDEN_TREES:
            t = Path(tree)
            if real == t or t in real.parents:
                raise PathRefused("System directories cannot be approved.")
        lib = home / "Library"
        if real == lib or lib in real.parents:
            raise PathRefused("~/Library holds app data and credentials and cannot be approved.")
        return real
