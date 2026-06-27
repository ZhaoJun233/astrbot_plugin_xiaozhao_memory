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


PLUGIN_TAG = "[xiaozhao_memory]"
PLUGIN_NAME = "astrbot_plugin_xiaozhao_memory"


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
            "%s loaded: backend=%s retention_hours=%s group_limit=%s user_limit=%s",
            PLUGIN_TAG,
            type(self.store).__name__,
            self.retention_hours,
            self.group_limit,
            self.user_limit,
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
        async with self._lock:
            memories = await asyncio.to_thread(
                self.store.retrieve,
                platform_id=event.get_platform_id(),
                bot_id=event.get_self_id(),
                group_id=event.get_group_id(),
                user_id=event.get_sender_id(),
                query=query,
                group_limit=self.group_limit,
                user_limit=self.user_limit,
            )
        if not memories:
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
            f"当前发言人: {sender_name}/{sender_id}。\n"
            f"{memories}"
            "</system_reminder>"
        )
        req.system_prompt = (req.system_prompt or "") + "\n" + note

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
