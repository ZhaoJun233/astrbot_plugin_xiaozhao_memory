from __future__ import annotations

import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

def _install_astrbot_test_stubs() -> None:
    astrbot = types.ModuleType("astrbot")

    class _Logger:
        def debug(self, *args, **kwargs) -> None:
            pass

        def info(self, *args, **kwargs) -> None:
            pass

        def warning(self, *args, **kwargs) -> None:
            pass

    astrbot.logger = _Logger()
    sys.modules["astrbot"] = astrbot

    api_event = types.ModuleType("astrbot.api.event")

    class AstrMessageEvent:
        pass

    class _Filter:
        class EventMessageType:
            GROUP_MESSAGE = "GROUP_MESSAGE"

        @staticmethod
        def event_message_type(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def on_llm_request(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

    api_event.AstrMessageEvent = AstrMessageEvent
    api_event.filter = _Filter
    sys.modules["astrbot.api.event"] = api_event

    provider_entities = types.ModuleType("astrbot.core.provider.entities")

    class ProviderRequest:
        def __init__(self, *, prompt=None, system_prompt="", contexts=None) -> None:
            self.prompt = prompt
            self.system_prompt = system_prompt
            self.contexts = contexts or []

    provider_entities.ProviderRequest = ProviderRequest
    sys.modules["astrbot.core.provider.entities"] = provider_entities

    api_provider = types.ModuleType("astrbot.api.provider")
    api_provider.ProviderRequest = ProviderRequest
    sys.modules["astrbot.api.provider"] = api_provider

    api_star = types.ModuleType("astrbot.api.star")

    class Context:
        pass

    class Star:
        def __init__(self, context) -> None:
            self.context = context

    api_star.Context = Context
    api_star.Star = Star
    sys.modules["astrbot.api.star"] = api_star

    message_type = types.ModuleType("astrbot.core.platform.message_type")

    class MessageType:
        GROUP_MESSAGE = "GROUP_MESSAGE"
        FRIEND_MESSAGE = "FRIEND_MESSAGE"

    message_type.MessageType = MessageType
    sys.modules["astrbot.core.platform.message_type"] = message_type

    star_tools = types.ModuleType("astrbot.core.star.star_tools")

    class StarTools:
        @staticmethod
        def get_data_dir(plugin_name: str) -> str:
            return "."

    star_tools.StarTools = StarTools
    sys.modules["astrbot.core.star.star_tools"] = star_tools


try:
    from astrbot.core.platform.message_type import MessageType
    from astrbot.core.provider.entities import ProviderRequest
except ModuleNotFoundError:
    _install_astrbot_test_stubs()
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
        platform_id: str = "qq_personal_onebot",
        platform_name: str = "aiocqhttp",
        extras: dict | None = None,
    ) -> None:
        self.unified_msg_origin = f"{platform_id}:GroupMessage:{group_id}"
        self._text = text
        self._group_id = group_id
        self._sender_id = sender_id
        self._self_id = self_id
        self._platform_id = platform_id
        self._platform_name = platform_name
        self._extras = extras or {}

    def get_message_type(self):
        return MessageType.GROUP_MESSAGE

    def get_message_str(self) -> str:
        return self._text

    def get_platform_id(self) -> str:
        return self._platform_id

    def get_platform_name(self) -> str:
        return self._platform_name

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
        self.record_calls = []
        self.prune_calls = 0

    def retrieve(self, **kwargs) -> str:
        self.retrieve_calls.append(kwargs)
        return self.memories

    def record_message(self, **kwargs) -> None:
        self.record_calls.append(kwargs)

    def prune(self, **kwargs) -> int:
        self.prune_calls += 1
        return 0


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

    def test_qq_official_record_uses_platform_instance_as_bot_id(self) -> None:
        plugin = build_plugin({"memory_judge_mode": "rules"})
        plugin.store = FakeStore("")
        event = FakeEvent(
            text="官方机器人也要记住这句",
            platform_name="qq_official",
            platform_id="default_1903757478",
            group_id="group-openid-a",
            sender_id="member-openid-a",
            self_id="qq_official",
        )

        asyncio.run(plugin.record_group_message(event))

        call = plugin.store.record_calls[0]
        self.assertEqual(call["platform_id"], "default_1903757478")
        self.assertEqual(call["bot_id"], "default_1903757478")
        self.assertEqual(call["group_id"], "group-openid-a")
        self.assertEqual(call["user_id"], "member-openid-a")

    def test_qq_official_retrieve_uses_bot_aliases(self) -> None:
        plugin = build_plugin({"memory_judge_mode": "always"})
        plugin.store = FakeStore("当前群聊记忆:\n- Alice(member-openid-a)：官方机器人旧记忆")
        event = FakeEvent(
            text="小昭你还记得刚才吗",
            platform_name="qq_official",
            platform_id="default_1903757478",
            group_id="group-openid-a",
            sender_id="member-openid-a",
            self_id="qq_official",
        )
        req = ProviderRequest(prompt=event.get_message_str())

        asyncio.run(plugin.inject_memory(event, req))

        call = plugin.store.retrieve_calls[0]
        self.assertEqual(call["platform_id"], "default_1903757478")
        self.assertEqual(call["bot_id"], "default_1903757478")
        self.assertIn("qq_official", call["bot_aliases"])
        self.assertEqual(call["group_id"], "group-openid-a")
        self.assertEqual(call["user_id"], "member-openid-a")

    def test_onebot_record_keeps_numeric_bot_id_for_isolation(self) -> None:
        plugin = build_plugin({"memory_judge_mode": "rules"})
        plugin.store = FakeStore("")
        event = FakeEvent(
            text="OneBot 数字号记忆",
            platform_name="aiocqhttp",
            platform_id="qq_personal_onebot",
            group_id="10001",
            sender_id="3040470862",
            self_id="123456789",
        )

        asyncio.run(plugin.record_group_message(event))

        call = plugin.store.record_calls[0]
        self.assertEqual(call["platform_id"], "qq_personal_onebot")
        self.assertEqual(call["bot_id"], "123456789")
        self.assertEqual(call["user_id"], "3040470862")


if __name__ == "__main__":
    unittest.main()
