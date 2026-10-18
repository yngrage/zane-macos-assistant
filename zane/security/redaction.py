"""Secret redaction used by the audit log, logging, memory and file-read tools."""
from __future__ import annotations

import re

REDACTED = "[REDACTED]"

_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=\-]{16,}", re.I),
]
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S
)
_ASSIGN = re.compile(
    r"""\b([\w.\-]*(?:password|passwd|pwd|secret|token|api[_\-]?key|apikey|access[_\-]?key|private[_\-]?key|credential)[\w.\-]*)"""
    r"""(\s*[:=]\s*)("[^"\n]*"|'[^'\n]*'|[^\s,;]+)""",
    re.I,
)
_NATURAL = re.compile(
    r"\b(?:password|passcode|passwd|api\s*key|secret|token)\s+(?:is|was|=|:)\s*(\S+)", re.I
)


def redact(text: str) -> str:
    if not text:
        return text
    out = _PRIVATE_KEY.sub(REDACTED, text)
    for pat in _PATTERNS:
        out = pat.sub(REDACTED, out)
    out = _ASSIGN.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", out)
    out = _NATURAL.sub(lambda m: m.group(0)[: m.start(1) - m.start(0)] + REDACTED, out)
    return out


def contains_secret(text: str) -> bool:
    return bool(text) and redact(text) != text
