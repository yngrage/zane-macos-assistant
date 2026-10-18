"""Configuration: environment variables, an optional .env file (placeholders only in the repo), and the macOS
Keychain for the API key. Secrets are never stored in source and never appear in repr()."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from zane.security.keychain import keychain_get
from zane.tools.runner import run_cmd

DEFAULT_MODEL = "claude-sonnet-5"


def default_data_dir(platform: str = sys.platform) -> Path:
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ZANE"
    return Path.home() / ".local" / "share" / "zane"


def ensure_private_dir(path: Path) -> Path:
    """Create a directory readable only by the owner (memory, tasks and logs are personal data)."""
    created = not path.exists()
    path.mkdir(parents=True, exist_ok=True)
    if created:
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
    return path


def parse_dotenv(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        elif " #" in val:
            val = val.split(" #", 1)[0].rstrip()
        if key:
            out[key] = val
    return out


def _flag(value: Optional[str], default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _num(value: Optional[str], default: float, cast: Any = float) -> Any:
    try:
        return cast(value) if value not in (None, "") else default
    except ValueError:
        return default


@dataclass
class Config:
    data_dir: Path
    approved_dirs: List[Path] = field(default_factory=list)
    provider: str = "anthropic"
    model: str = DEFAULT_MODEL
    max_tokens: int = 1500
    request_timeout_s: float = 60.0
    max_retries: int = 2
    fast_path: bool = True
    reminders: bool = True
    history_turns: int = 10
    log_level: str = "INFO"
    api_key: Optional[str] = field(default=None, repr=False)
    api_key_source: str = "none"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "zane.db"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def export_dir(self) -> Path:
        return self.data_dir / "exports"

    @classmethod
    def load(
        cls,
        env: Optional[Mapping[str, str]] = None,
        *,
        dotenv_paths: Optional[Sequence[Path]] = None,
        runner: Any = run_cmd,
        platform: str = sys.platform,
    ) -> "Config":
        real_env: Dict[str, str] = dict(os.environ if env is None else env)
        if dotenv_paths is None:
            dotenv_paths = [] if real_env.get("ZANE_NO_DOTENV") else [Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"]
        dotenv: Dict[str, str] = {}
        for p in dotenv_paths:
            try:
                for k, v in parse_dotenv(Path(p).read_text(encoding="utf-8")).items():
                    dotenv.setdefault(k, v)
            except OSError:
                continue
        merged = {**dotenv, **{k: v for k, v in real_env.items() if v != ""}}  # real environment wins

        key, source = real_env.get("ANTHROPIC_API_KEY") or "", "environment"
        if not key and dotenv.get("ANTHROPIC_API_KEY"):
            key, source = dotenv["ANTHROPIC_API_KEY"], ".env"
        if not key and platform == "darwin":
            key, source = keychain_get(runner) or "", "keychain"
        if not key:
            source = "none"

        raw_dirs = merged.get("ZANE_APPROVED_DIRS")
        approved = [Path(p).expanduser() for p in raw_dirs.split(os.pathsep) if p.strip()] if raw_dirs else [Path.home() / "Downloads"]
        return cls(
            data_dir=Path(merged.get("ZANE_HOME") or default_data_dir(platform)).expanduser(),
            approved_dirs=approved,
            model=merged.get("ZANE_MODEL") or DEFAULT_MODEL,
            max_tokens=_num(merged.get("ZANE_MAX_TOKENS"), 1500, int),
            request_timeout_s=_num(merged.get("ZANE_TIMEOUT"), 60.0, float),
            fast_path=_flag(merged.get("ZANE_FAST_PATH"), True),
            reminders=_flag(merged.get("ZANE_REMINDERS"), True),
            log_level=(merged.get("ZANE_LOG_LEVEL") or "INFO").upper(),
            api_key=key or None,
            api_key_source=source,
        )
