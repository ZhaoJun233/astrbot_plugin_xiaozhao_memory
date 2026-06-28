from __future__ import annotations

import asyncio
import time
from pathlib import Path

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.core.platform.message_type import MessageType
from astrbot.core.star.star_tools import StarTools

from .memory_store import SQLiteMemoryStore, build_memory_store
from .memory_scene import (
    MemoryNeedDecision,
    analyze_memory_need_by_rules,
    parse_memory_need_decision,
)


PLUGIN_TAG = "[xiaozhao_memory]"
PLUGIN_NAME = "astrbot_plugin_xiaozhao_memory"
SMART_MENTION_DECISION_EXTRA = "xiaozhao_smart_mention_decision"

MEMORY_JUDGE_SYSTEM_PROMPT = """你是小昭记忆插件的对话场景判断器。
判断当前群聊消息是否需要检索本地短期记忆才能更好回答。
只在用户询问过去说过/喜欢/身份/约定/刚才上下文/让你回忆某人某事时返回 need_memory=true。
普通问候、闲聊、无需历史信息也能回答的问题返回 false。
严格输出 JSON，不要输出多余文字：
{"need_memory": true/false, "query": "用于检索数据库的简短关键词", "reason": "一句话原因"}
"""

MEMORY_REFINE_SYSTEM_PROMPT = """你是小昭记忆插件的候选记忆提炼器。
只从候选记忆里提取和当前问题有关的信息，去掉无关闲聊。
不要编造候选记忆里没有的事实。不要透露数据库、插件、检索机制。
输出不超过 6 条中文要点；如果候选都无关，只输出空字符串。
"""


class Main(Star):
    def __init__(self, context: Context, config=None) -> None:
        super().__init__(context)
        self.context = context
        self.config = config or {}
        self.enabled = bool(self.config.get("enabled", True))
        self.retention_hours = int(self.config.get("retention_hours", 12))
        self.group_limit = int(self.config.get("group_memory_limit", 8))
        self.user_limit = int(self.config.get("user_memory_limit", 6))
        self.max_text_chars = int(self.config.get("max_text_chars", 500))
        self.prune_interval_sec = int(self.config.get("prune_interval_sec", 1800))
        self.storage_backend = str(self.config.get("storage_backend", "postgres")).lower()
        self.memory_judge_mode = str(self.config.get("memory_judge_mode", "hybrid")).lower()
        self.memory_judge_timeout_sec = float(
            self.config.get("memory_judge_timeout_sec", 4),
        )
        self.memory_refine_enabled = bool(self.config.get("memory_refine_enabled", True))
        self.memory_refine_timeout_sec = float(
            self.config.get("memory_refine_timeout_sec", 5),
        )
        self.inject_only_when_smart_reply = bool(
            self.config.get("inject_only_when_smart_reply", False),
        )
        self._last_prune_at = 0.0
        self._lock = asyncio.Lock()
        self.store: SQLiteMemoryStore | None = None

    async def initialize(self) -> None:
        if not self.enabled:
            logger.info("%s disabled", PLUGIN_TAG)
            return
        data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        self.store = build_memory_store(self.config, data_dir)
        try:
            await asyncio.to_thread(self.store.initialize)
        except Exception as exc:
            if self.storage_backend == "sqlite":
                raise
            logger.warning(
                "%s init %s failed, fallback to sqlite: %s",
                PLUGIN_TAG,
                self.storage_backend,
                exc,
            )
            self.store = SQLiteMemoryStore(
                data_dir / "xiaozhao_memory.db",
                retention_hours=self.retention_hours,
            )
            await asyncio.to_thread(self.store.initialize)
        logger.info(
            "%s loaded: backend=%s retention_hours=%s group_limit=%s user_limit=%s judge_mode=%s",
            PLUGIN_TAG,
            type(self.store).__name__,
            self.retention_hours,
            self.group_limit,
            self.user_limit,
            self.memory_judge_mode,
        )

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=-1000)
    async def record_group_message(self, event: AstrMessageEvent) -> None:
        if not self.enabled or self.store is None:
            return
        if event.get_message_type() != MessageType.GROUP_MESSAGE:
            return
        if self._is_self_message(event):
            return

        text = event.get_message_str().strip()
        if not text:
            text = event.get_message_outline().strip()
        if not text:
            return
        text = text[: self.max_text_chars]

        async with self._lock:
            await asyncio.to_thread(
                self.store.record_message,
                platform_id=event.get_platform_id(),
                bot_id=event.get_self_id(),
                group_id=event.get_group_id(),
                user_id=event.get_sender_id(),
                nickname=event.get_sender_name(),
                text=text,
                created_at=time.time(),
            )
            await self._maybe_prune_locked()

    @filter.on_llm_request(priority=70)
    async def inject_memory(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> None:
        if not self.enabled or self.store is None:
            return
        if event.get_message_type() != MessageType.GROUP_MESSAGE:
            return

        query = event.get_message_str().strip() or req.prompt or ""
        if not query:
            return
        if self.inject_only_when_smart_reply and not self._is_smart_reply_request(event):
            return

        decision = await self._analyze_memory_need(event, req, query)
        if not decision.need_memory:
            logger.debug("%s skip inject: %s text=%s", PLUGIN_TAG, decision.reason, query)
            return

        async with self._lock:
            memories = await asyncio.to_thread(
                self.store.retrieve,
                platform_id=event.get_platform_id(),
                bot_id=event.get_self_id(),
                group_id=event.get_group_id(),
                user_id=event.get_sender_id(),
                query=decision.query or query,
                group_limit=self.group_limit,
                user_limit=self.user_limit,
            )
        if not memories:
            return

        refined_memories = await self._refine_memories(event, req, decision, memories)
        if not refined_memories:
            return

        sender_name = event.get_sender_name() or "未知昵称"
        sender_id = event.get_sender_id() or "未知ID"
        note = (
            "<system_reminder>"
            "以下是小昭的本地持久记忆，仅作为理解当前对话的背景。"
            f"记忆保留窗口不少于 {self.retention_hours} 小时；"
            "当前群聊记忆只来自本群；同一用户跨群记忆只来自当前发言人的同一账号；"
            "多机器人场景下，记忆按当前机器人账号隔离。"
            "不要主动透露记忆来源、数据库、插件机制；不要把其他群的群聊秘密说给当前群。"
            f"本次记忆检索原因: {decision.reason}；检索关键词: {decision.query or query}。"
            f"当前发言人: {sender_name}/{sender_id}。\n"
            f"{refined_memories}"
            "</system_reminder>"
        )
        req.system_prompt = (req.system_prompt or "") + "\n" + note

    async def _analyze_memory_need(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        query: str,
    ) -> MemoryNeedDecision:
        rule_decision = analyze_memory_need_by_rules(query)
        if self.memory_judge_mode in {"off", "always"}:
            if self.memory_judge_mode == "always":
                return MemoryNeedDecision(True, query, "mode:always")
            return MemoryNeedDecision(False, "", "mode:off")
        if self.memory_judge_mode == "rules":
            return rule_decision
        if self.memory_judge_mode == "hybrid" and rule_decision.need_memory:
            return rule_decision
        if not self._has_llm_context():
            return rule_decision

        try:
            provider_id = await self.context.get_current_chat_provider_id(event.unified_msg_origin)
            response = await asyncio.wait_for(
                self.context.llm_generate(
                    chat_provider_id=provider_id,
                    prompt=self._build_memory_judge_prompt(event, req, query),
                    system_prompt=MEMORY_JUDGE_SYSTEM_PROMPT,
                    temperature=0,
                ),
                timeout=self.memory_judge_timeout_sec,
            )
        except Exception as exc:
            logger.warning("%s memory judge failed: %s", PLUGIN_TAG, _format_exception(exc))
            return rule_decision

        return parse_memory_need_decision(response.completion_text)

    async def _refine_memories(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        decision: MemoryNeedDecision,
        memories: str,
    ) -> str:
        if not self.memory_refine_enabled or self.memory_judge_mode in {"off", "rules"}:
            return memories
        if not self._has_llm_context():
            return memories

        try:
            provider_id = await self.context.get_current_chat_provider_id(event.unified_msg_origin)
            response = await asyncio.wait_for(
                self.context.llm_generate(
                    chat_provider_id=provider_id,
                    prompt=self._build_memory_refine_prompt(event, req, decision, memories),
                    system_prompt=MEMORY_REFINE_SYSTEM_PROMPT,
                    temperature=0,
                ),
                timeout=self.memory_refine_timeout_sec,
            )
        except Exception as exc:
            logger.warning("%s memory refine failed: %s", PLUGIN_TAG, _format_exception(exc))
            return memories

        refined = " ".join(str(response.completion_text or "").split())
        return refined or memories

    def _build_memory_judge_prompt(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        query: str,
    ) -> str:
        contexts = _format_recent_contexts(req.contexts)
        smart_decision = event.get_extra(SMART_MENTION_DECISION_EXTRA, "")
        return (
            f"当前发言人: {event.get_sender_name() or event.get_sender_id()}\n"
            f"智能回复插件决策: {smart_decision or '未标记'}\n"
            f"当前消息: {query}\n"
            f"最近上下文:\n{contexts or '无'}"
        )

    def _build_memory_refine_prompt(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        decision: MemoryNeedDecision,
        memories: str,
    ) -> str:
        query = event.get_message_str().strip() or req.prompt or decision.query
        return (
            f"当前问题: {query}\n"
            f"检索关键词: {decision.query}\n"
            f"候选记忆:\n{memories}"
        )

    def _is_smart_reply_request(self, event: AstrMessageEvent) -> bool:
        return event.get_extra(SMART_MENTION_DECISION_EXTRA) == "REPLY"

    def _has_llm_context(self) -> bool:
        return bool(
            self.context
            and hasattr(self.context, "get_current_chat_provider_id")
            and hasattr(self.context, "llm_generate")
        )

    async def _maybe_prune_locked(self) -> None:
        if self.store is None:
            return
        now = time.time()
        if now - self._last_prune_at < self.prune_interval_sec:
            return
        self._last_prune_at = now
        deleted = await asyncio.to_thread(self.store.prune, now=now)
        if deleted:
            logger.debug("%s pruned %s expired memories", PLUGIN_TAG, deleted)

    def _is_self_message(self, event: AstrMessageEvent) -> bool:
        return bool(event.get_self_id()) and str(event.get_sender_id()) == str(
            event.get_self_id(),
        )


def _format_recent_contexts(contexts: list[dict], limit: int = 6) -> str:
    lines: list[str] = []
    for ctx in (contexts or [])[-limit:]:
        role = ctx.get("role", "unknown") if isinstance(ctx, dict) else "unknown"
        content = ctx.get("content", "") if isinstance(ctx, dict) else str(ctx)
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            content = " ".join(parts)
        line = " ".join(str(content or "").split())
        if line:
            lines.append(f"{role}: {line[:300]}")
    return "\n".join(lines)


def _format_exception(exc: Exception) -> str:
    name = type(exc).__name__ or exc.__class__.__qualname__ or "Exception"
    message = str(exc)
    return f"{name}: {message}" if message else name
