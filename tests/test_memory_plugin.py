from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from astrbot.core.platform.message_type import MessageType
from astrbot.core.provider.entities import ProviderRequest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_xiaozhao_memory import main as memory_main
from astrbot_plugin_xiaozhao_memory.main import Main
from astrbot_plugin_xiaozhao_memory.memory_scene import MemoryNeedDecision


class FakeEvent:
    def __init__(
        self,
        *,
        text: str = "",
        group_id: str = "group-a",
        sender_id: str = "user-a",
        self_id: str = "bot-a",
        extras: dict | None = None,
    ) -> None:
        self.unified_msg_origin = f"aiocqhttp:GroupMessage:{group_id}"
        self._text = text
        self._group_id = group_id
        self._sender_id = sender_id
        self._self_id = self_id
        self._extras = extras or {}

    def get_message_type(self):
        return MessageType.GROUP_MESSAGE

    def get_message_str(self) -> str:
        return self._text

    def get_platform_id(self) -> str:
        return "qq"

    def get_self_id(self) -> str:
        return self._self_id

    def get_group_id(self) -> str:
        return self._group_id

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_sender_name(self) -> str:
        return "Alice"

    def get_extra(self, key: str, default=None):
        return self._extras.get(key, default)


class FakeStore:
    def __init__(self, memories: str) -> None:
        self.memories = memories
        self.retrieve_calls = []

    def retrieve(self, **kwargs) -> str:
        self.retrieve_calls.append(kwargs)
        return self.memories


def build_plugin(config=None) -> Main:
    plugin = Main.__new__(Main)
    Main.__init__(plugin, context=None, config=config or {"memory_judge_mode": "rules"})
    return plugin


async def _return_minecraft_decision(event, req, query):
    return MemoryNeedDecision(True, "Minecraft", "llm:问上次内容")


async def _return_refined_memories(event, req, decision, memories):
    return "相关记忆:\n- Alice 曾说喜欢 Python 插件"


class MemoryPluginTest(unittest.TestCase):
    def test_smart_mention_reply_injects_relevant_memory(self) -> None:
        plugin = build_plugin({"memory_judge_mode": "rules"})
        plugin.store = FakeStore("当前群聊记忆:\n- Alice(user-a)：我之前说过喜欢 Python 插件")
        event = FakeEvent(
            text="小昭你还记得我之前说喜欢什么语言吗",
            extras={"xiaozhao_smart_mention_decision": "REPLY"},
        )
        req = ProviderRequest(prompt=event.get_message_str())

        asyncio.run(plugin.inject_memory(event, req))

        self.assertEqual(len(plugin.store.retrieve_calls), 1)
        self.assertIn("本地持久记忆", req.system_prompt)
        self.assertIn("Python 插件", req.system_prompt)

    def test_default_allows_non_smart_reply_requests(self) -> None:
        plugin = build_plugin({"memory_judge_mode": "rules"})
        plugin.store = FakeStore("当前群聊记忆:\n- Alice(user-a)：我之前说过喜欢 Python 插件")
        event = FakeEvent(text="小昭你还记得我之前说喜欢什么语言吗")
        req = ProviderRequest(prompt=event.get_message_str())

        asyncio.run(plugin.inject_memory(event, req))

        self.assertEqual(len(plugin.store.retrieve_calls), 1)
        self.assertIn("Python 插件", req.system_prompt)

    def test_plain_reply_without_memory_scene_does_not_retrieve(self) -> None:
        plugin = build_plugin({"memory_judge_mode": "rules"})
        plugin.store = FakeStore("当前群聊记忆:\n- Alice(user-a)：我之前说过喜欢 Python 插件")
        event = FakeEvent(
            text="小昭早上好",
            extras={"xiaozhao_smart_mention_decision": "REPLY"},
        )
        req = ProviderRequest(prompt=event.get_message_str())

        asyncio.run(plugin.inject_memory(event, req))

        self.assertEqual(plugin.store.retrieve_calls, [])
        self.assertEqual(req.system_prompt, "")

    def test_llm_memory_decision_can_drive_retrieval_query(self) -> None:
        plugin = build_plugin({"memory_judge_mode": "llm"})
        plugin.store = FakeStore("同一用户跨群记忆:\n- Alice(user-a)：上次说正在玩 Minecraft")
        plugin._analyze_memory_need = _return_minecraft_decision
        event = FakeEvent(
            text="小昭我上次说我在玩什么来着",
            extras={"xiaozhao_smart_mention_decision": "REPLY"},
        )
        req = ProviderRequest(prompt=event.get_message_str())

        asyncio.run(plugin.inject_memory(event, req))

        self.assertEqual(plugin.store.retrieve_calls[0]["query"], "Minecraft")
        self.assertIn("Minecraft", req.system_prompt)

    def test_unanswered_group_message_does_not_inject_for_smart_mention_coordination(self) -> None:
        plugin = build_plugin(
            {
                "memory_judge_mode": "rules",
                "inject_only_when_smart_reply": True,
            },
        )
        plugin.store = FakeStore("当前群聊记忆:\n- Alice(user-a)：我之前说过喜欢 Python 插件")
        event = FakeEvent(text="还记得我之前说喜欢什么语言吗")
        req = ProviderRequest(prompt=event.get_message_str())

        asyncio.run(plugin.inject_memory(event, req))

        self.assertEqual(plugin.store.retrieve_calls, [])
        self.assertEqual(req.system_prompt, "")

    def test_refined_memory_context_replaces_noisy_candidates(self) -> None:
        plugin = build_plugin({"memory_judge_mode": "rules"})
        plugin.store = FakeStore(
            "当前群聊记忆:\n"
            "- Alice(user-a)：我之前说过喜欢 Python 插件\n"
            "- Bob(user-b)：不相关的群聊内容",
        )
        plugin._refine_memories = _return_refined_memories
        event = FakeEvent(
            text="小昭你还记得我之前说喜欢什么语言吗",
            extras={"xiaozhao_smart_mention_decision": "REPLY"},
        )
        req = ProviderRequest(prompt=event.get_message_str())

        asyncio.run(plugin.inject_memory(event, req))

        self.assertIn("Alice 曾说喜欢 Python 插件", req.system_prompt)
        self.assertNotIn("不相关的群聊内容", req.system_prompt)

    def test_refine_without_context_falls_back_without_warning(self) -> None:
        plugin = build_plugin({"memory_judge_mode": "llm"})
        event = FakeEvent(text="小昭你还记得我之前说喜欢什么语言吗")
        req = ProviderRequest(prompt=event.get_message_str())
        decision = MemoryNeedDecision(True, "喜欢什么语言", "rule:recall")

        with patch.object(memory_main.logger, "warning") as warning:
            refined = asyncio.run(
                plugin._refine_memories(
                    event,
                    req,
                    decision,
                    "当前群聊记忆:\n- Alice(user-a)：我之前说过喜欢 Python 插件",
                ),
            )

        self.assertIn("Python 插件", refined)
        warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
