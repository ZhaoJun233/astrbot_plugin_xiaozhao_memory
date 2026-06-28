from __future__ import annotations

import re
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryNeedDecision:
    need_memory: bool
    query: str
    reason: str


RECALL_PATTERNS = [
    re.compile(pattern)
    for pattern in (
        r"(?:还记得|记不记得|记得).*?(?:之前|以前|刚才|上次|我说|他说|她说|他们说)",
        r"(?:之前|以前|刚才|上次).*?(?:说过|聊过|提过|问过|告诉过|记得)",
        r"(?:我|他|她|他们|群里).*(?:喜欢|讨厌|想要|要做|叫什么|是谁|在哪|什么时候)",
    )
]


def analyze_memory_need_by_rules(text: str) -> MemoryNeedDecision:
    compact = " ".join(str(text or "").split())
    if not compact:
        return MemoryNeedDecision(False, "", "empty")

    for pattern in RECALL_PATTERNS:
        if pattern.search(compact):
            return MemoryNeedDecision(True, _strip_bot_name(compact), f"rule:{pattern.pattern}")

    return MemoryNeedDecision(False, "", "no_rule_match")


def parse_memory_need_decision(raw_text: str) -> MemoryNeedDecision:
    payload = _load_json_object(raw_text)
    if not payload:
        return MemoryNeedDecision(False, "", "llm_parse_failed")

    need_memory = bool(payload.get("need_memory"))
    query = _clean_query(payload.get("query") or "")
    reason = _clean_query(payload.get("reason") or "")
    if not need_memory:
        return MemoryNeedDecision(False, "", f"llm:{reason or 'skip'}")
    if not query:
        return MemoryNeedDecision(False, "", "llm_missing_query")
    return MemoryNeedDecision(True, query, f"llm:{reason or 'need_memory'}")


def _strip_bot_name(text: str) -> str:
    return re.sub(r"^(?:小昭猫娘|小昭)[,，!！?？\s]*", "", text).strip() or text


def _load_json_object(raw_text: str) -> dict | None:
    text = str(raw_text or "").strip()
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _clean_query(value: object, limit: int = 120) -> str:
    return " ".join(str(value or "").split())[:limit]
