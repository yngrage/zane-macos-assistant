"""Three-level permission policy. Only the user can change it (slash commands / UI), never a tool."""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Any, List, Optional, Set


class Level(IntEnum):
    SAFE = 1  # runs automatically when its category is enabled
    CONFIRM = 2  # shows action/target/consequences and waits for approval
    HIGH_RISK = 3  # blocked unless narrowly allow-listed, then needs a typed confirmation


class Decision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    STRICT_ASK = "strict_ask"
    DENY = "deny"


CATEGORIES = ("applications", "filesystem", "system", "development", "memory", "tasks", "web")
DEFAULT_ENABLED = ("applications", "filesystem", "system", "development", "memory", "tasks")


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    reason: str = ""


class PermissionPolicy:
    def __init__(self, store: Optional[Any] = None) -> None:
        """`store` needs get_setting(key, default) / set_setting(key, value) (the Database does)."""
        self._store = store
        self._enabled: Set[str] = set(DEFAULT_ENABLED)
        self._high_risk: Set[str] = set()
        if store is not None:
            raw = store.get_setting("perm.categories", None)
            if raw is not None:
                self._enabled = {c for c in json.loads(raw) if c in CATEGORIES}
            raw = store.get_setting("perm.high_risk_allow", None)
            if raw is not None:
                self._high_risk = set(json.loads(raw))

    def _save(self) -> None:
        if self._store is not None:
            self._store.set_setting("perm.categories", json.dumps(sorted(self._enabled)))
            self._store.set_setting("perm.high_risk_allow", json.dumps(sorted(self._high_risk)))

    def is_enabled(self, category: str) -> bool:
        return category in self._enabled

    def enabled(self) -> List[str]:
        return [c for c in CATEGORIES if c in self._enabled]

    def enable(self, category: str) -> None:
        if category not in CATEGORIES:
            raise ValueError(f"Unknown category '{category}'. Choose from: {', '.join(CATEGORIES)}")
        self._enabled.add(category)
        self._save()

    def disable(self, category: str) -> None:
        if category not in CATEGORIES:
            raise ValueError(f"Unknown category '{category}'. Choose from: {', '.join(CATEGORIES)}")
        self._enabled.discard(category)
        self._save()

    def allow_high_risk(self, tool_name: str) -> None:
        """Narrow, explicit, per-tool authorization for a Level-3 tool (user-only operation)."""
        self._high_risk.add(tool_name)
        self._save()

    def revoke_high_risk(self, tool_name: str) -> None:
        self._high_risk.discard(tool_name)
        self._save()

    def high_risk_allowed(self, tool_name: str) -> bool:
        return tool_name in self._high_risk

    def decide(self, tool_name: str, category: str, level: Level) -> PolicyDecision:
        if category not in self._enabled:
            return PolicyDecision(
                Decision.DENY, f"The '{category}' permission is disabled. The user can enable it with /permissions."
            )
        if level == Level.SAFE:
            return PolicyDecision(Decision.ALLOW)
        if level == Level.CONFIRM:
            return PolicyDecision(Decision.ASK)
        if tool_name in self._high_risk:
            return PolicyDecision(Decision.STRICT_ASK)
        return PolicyDecision(Decision.DENY, "Level 3 (high-risk) actions are blocked by default.")
