"""Deterministic fast path. Unambiguous commands are routed straight to a validated tool call with no model
(instant, free, works offline). Anything ambiguous returns None and goes to the model. The router can only
choose a tool and arguments; the permission pipeline still applies to everything it routes."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from zane.tasks.dates import extract_due_suffix

_WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
            "ten": 10, "eleven": 11, "twelve": 12}
_LEAD = re.compile(
    r"^(?:(?:hey|hi|ok|okay)[,\s]+)?(?:zane[,:\s]+)?(?:(?:please|kindly)\s+)?"
    r"(?:(?:can|could|would|will) you\s+(?:please\s+)?|i need you to\s+|i want you to\s+|go ahead and\s+)?",
    re.I,
)
_NAME = r"[\w .+&'()\-]{1,64}"
_NOT_AN_APP = re.compile(r"\b(project|file|files|folder|directory|repo|repository|workspace|document|path|this|that|it|in|my)\b", re.I)


@dataclass
class Route:
    tool: str
    args: Dict[str, Any] = field(default_factory=dict)


def _clean(text: str) -> str:
    t = _LEAD.sub("", text.strip(), count=1)
    return re.sub(r"[.!?\s]+$", "", t).strip()


def _hours(token: str) -> Optional[float]:
    token = token.lower()
    if token in _WORDNUM:
        return float(_WORDNUM[token])
    try:
        return float(token)
    except ValueError:
        return None


class IntentRouter:
    def match(self, text: str) -> Optional[Route]:
        t = _clean(text)
        if not t or len(t) > 200:
            return None
        low = t.lower()
        for rule in (self._open, self._quit, self._system, self._tasks, self._memory):
            route = rule(t, low)
            if route:
                return route
        return None

    # ---- applications ---------------------------------------------------------------------------
    @staticmethod
    def _open(t: str, low: str) -> Optional[Route]:
        m = re.fullmatch(rf"(?:open|launch)\s+(?:up\s+)?(?:the\s+|my\s+)?({_NAME}?)(?:\s+(?:app|application))?", t, re.I)
        if not m:
            return None
        target = m.group(1).strip()
        folder = re.fullmatch(r"(downloads|desktop|documents|home)(?:\s+(?:folder|directory))?", target.lower())
        if folder:
            return Route("folder_open", {"location": folder.group(1)})
        if not target or _NOT_AN_APP.search(target):
            return None  # needs the model (which project? which file?)
        return Route("app_open", {"name": target})

    @staticmethod
    def _quit(t: str, low: str) -> Optional[Route]:
        m = re.fullmatch(rf"quit\s+(?:the\s+)?({_NAME}?)(?:\s+(?:app|application))?", t, re.I)
        if not m or _NOT_AN_APP.search(m.group(1)):
            return None
        return Route("app_quit", {"name": m.group(1).strip()})

    # ---- system ---------------------------------------------------------------------------------
    @staticmethod
    def _system(t: str, low: str) -> Optional[Route]:
        words = len(low.split())
        if re.search(r"\bbattery\b", low) and words <= 9 and not re.search(r"\b(why|how do|fix|improve|replace|drain\w*|dying|health)\b", low):
            return Route("sys_battery")
        if re.search(r"\b(storage|disk space|free space|disk usage|how much space)\b", low) and words <= 10 \
                and not re.search(r"\b(clean|free up|delete|clear|reclaim)\b", low):
            return Route("sys_disk")
        if (re.search(r"\b(memory pressure|ram usage|memory usage)\b", low) or re.search(r"\bhow much (ram|memory)\b", low)) and words <= 10:
            return Route("sys_memory")
        if re.search(r"\b(top processes|resource hog\w*|most (cpu|memory|resources))\b", low) or \
                re.search(r"\b(which|what)\b.*\bprocess(es)?\b.*\b(most|consum\w*|using|hogging|eating)\b", low):
            return Route("sys_processes")
        if (re.search(r"\bpython\b.*\b(installed|version)\b", low) and words <= 8) or \
                re.search(r"\b(check|verify|is)\b.*\b(dev|development)\b.*\benvironment\b", low):
            return Route("sys_dev_env")
        return None

    # ---- tasks ----------------------------------------------------------------------------------
    @staticmethod
    def _tasks(t: str, low: str) -> Optional[Route]:
        if re.search(r"\b(daily|morning)\s+briefing\b|\bbrief me\b|\bmy briefing\b", low):
            return Route("task_briefing")
        if re.search(r"\boverdue\b", low) and re.search(r"\b(tasks?|work|items|to-?dos?)\b", low):
            return Route("task_list", {"filter": "overdue"})
        m = re.search(r"\bplan\s+(?:my\s+)?(?:next|coming)\s+(\w+(?:\.\d+)?)\s+hours?\b", low)
        if m and _hours(m.group(1)) is not None:
            return Route("task_plan", {"hours": _hours(m.group(1))})
        if re.fullmatch(r"(?:show|list|display|what are|what's)\s+(?:me\s+)?(?:all\s+)?(?:my\s+)?(?:(?:outstanding|pending|open|current)\s+)?(?:tasks|to-?dos?|work)", low) \
                or re.fullmatch(r"summari[sz]e\s+(?:my\s+)?(?:outstanding\s+|pending\s+|open\s+)?(?:work|tasks)", low):
            return Route("task_list", {"filter": "pending"})
        if re.fullmatch(r"(?:show|list)\s+(?:me\s+)?(?:my\s+)?(?:completed|done|finished)\s+(?:tasks|work)", low):
            return Route("task_list", {"filter": "done"})
        m = re.fullmatch(r"(?:add|create)\s+(?:a\s+|an\s+)?(?:(critical|high|low|normal)(?:[\s-]+priority)?\s+)?task\s+(?:to\s+|for\s+|called\s+|named\s+)?(.+)", t, re.I)
        if m:
            title, due = extract_due_suffix(m.group(2))
            if not title:
                return None
            args: Dict[str, Any] = {"title": title[0].upper() + title[1:], "priority": (m.group(1) or "normal").lower()}
            if due:
                args["due"] = due
            return Route("task_add", args)
        m = re.fullmatch(r"(?:mark|set)\s+(?:the\s+)?(.+?)\s+(?:task\s+)?(?:as\s+)?(?:complete|completed|done|finished)", t, re.I) \
            or re.fullmatch(r"(?:complete|finish)\s+(?:the\s+)?(.+?)\s+task", t, re.I)
        if m:
            return Route("task_complete", {"match": m.group(1).strip()})
        return None

    # ---- memory ---------------------------------------------------------------------------------
    @staticmethod
    def _memory(t: str, low: str) -> Optional[Route]:
        m = re.fullmatch(r"remember(?:\s+that)?\s+(.+)", t, re.I)
        if m:
            content = m.group(1).strip()
            kind = "preference" if re.search(r"\b(i prefer|i like|i hate|i always|i never|always|never)\b", content, re.I) \
                else "project" if re.search(r"\bproject\b", content, re.I) else "fact"
            return Route("memory_save", {"content": content[0].upper() + content[1:], "kind": kind})
        m = re.fullmatch(r"(?:what do you (?:remember|know)|show me what you remember|what have you (?:saved|stored))(?:\s+about\s+(.+))?", low)
        if m:
            return Route("memory_search", {"query": (m.group(1) or "").strip()})
        return None
