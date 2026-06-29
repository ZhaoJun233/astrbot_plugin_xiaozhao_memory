from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from memory_store import SQLiteMemoryStore, build_memory_store


class SQLiteMemoryStoreTest(unittest.TestCase):
    def test_builds_sqlite_store_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = build_memory_store(
                {"storage_backend": "sqlite", "retention_hours": 12},
                Path(tmp),
            )

            self.assertIsInstance(store, SQLiteMemoryStore)

    def test_keeps_recent_messages_for_at_least_retention_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteMemoryStore(Path(tmp) / "memory.db", retention_hours=12)
            store.initialize()

            now = 1_000_000.0
            store.record_message(
                platform_id="aiocqhttp",
                bot_id="bot-a",
                group_id="10001",
                user_id="3040470862",
                nickname="zhao",
                text="我喜欢用 Python 写插件",
                created_at=now,
            )
            store.prune(now=now + 11 * 3600 + 59)

            memories = store.retrieve(
                platform_id="aiocqhttp",
                bot_id="bot-a",
                group_id="10001",
                user_id="3040470862",
                query="Python 插件",
                now=now + 12 * 3600,
            )

            self.assertIn("我喜欢用 Python 写插件", memories)

    def test_isolates_group_memory_but_links_same_user_across_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteMemoryStore(Path(tmp) / "memory.db", retention_hours=12)
            store.initialize()

            now = 1_000_000.0
            store.record_message(
                platform_id="aiocqhttp",
                bot_id="bot-a",
                group_id="10001",
                user_id="42",
                nickname="alice",
                text="这个群在聊 Minecraft 服务器",
                created_at=now,
            )
            store.record_message(
                platform_id="aiocqhttp",
                bot_id="bot-a",
                group_id="20002",
                user_id="42",
                nickname="alice",
                text="我最近在调 AstrBot 插件",
                created_at=now + 1,
            )
            store.record_message(
                platform_id="aiocqhttp",
                bot_id="bot-a",
                group_id="20002",
                user_id="99",
                nickname="bob",
                text="另一个群的无关秘密",
                created_at=now + 2,
            )

            memories = store.retrieve(
                platform_id="aiocqhttp",
                bot_id="bot-a",
                group_id="10001",
                user_id="42",
                query="插件",
                now=now + 10,
            )

            self.assertIn("这个群在聊 Minecraft 服务器", memories)
            self.assertIn("我最近在调 AstrBot 插件", memories)
            self.assertNotIn("另一个群的无关秘密", memories)

    def test_isolates_memory_between_multiple_bots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteMemoryStore(Path(tmp) / "memory.db", retention_hours=12)
            store.initialize()

            now = 1_000_000.0
            store.record_message(
                platform_id="aiocqhttp",
                bot_id="bot-a",
                group_id="10001",
                user_id="42",
                nickname="alice",
                text="只告诉 A 机器人的记忆",
                created_at=now,
            )
            store.record_message(
                platform_id="aiocqhttp",
                bot_id="bot-b",
                group_id="10001",
                user_id="42",
                nickname="alice",
                text="只告诉 B 机器人的记忆",
                created_at=now + 1,
            )

            memories = store.retrieve(
                platform_id="aiocqhttp",
                bot_id="bot-a",
                group_id="10001",
                user_id="42",
                query="记忆",
                now=now + 10,
            )

            self.assertIn("只告诉 A 机器人的记忆", memories)
            self.assertNotIn("只告诉 B 机器人的记忆", memories)

    def test_retrieve_can_include_bot_aliases_for_qq_official_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteMemoryStore(Path(tmp) / "memory.db", retention_hours=12)
            store.initialize()

            now = 1_000_000.0
            store.record_message(
                platform_id="default_1903757478",
                bot_id="qq_official",
                group_id="group-openid-a",
                user_id="member-openid-a",
                nickname="alice",
                text="旧版本官方机器人记忆",
                created_at=now,
            )
            store.record_message(
                platform_id="default_1903757478",
                bot_id="default_1903757478",
                group_id="group-openid-a",
                user_id="member-openid-a",
                nickname="alice",
                text="新版本官方机器人记忆",
                created_at=now + 1,
            )

            memories = store.retrieve(
                platform_id="default_1903757478",
                bot_id="default_1903757478",
                bot_aliases=["qq_official"],
                group_id="group-openid-a",
                user_id="member-openid-a",
                query="官方机器人",
                now=now + 10,
            )

            self.assertIn("旧版本官方机器人记忆", memories)
            self.assertIn("新版本官方机器人记忆", memories)

    def test_bot_aliases_do_not_cross_platform_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteMemoryStore(Path(tmp) / "memory.db", retention_hours=12)
            store.initialize()

            now = 1_000_000.0
            store.record_message(
                platform_id="default_1903757478",
                bot_id="default_1903757478",
                group_id="group-openid-a",
                user_id="member-openid-a",
                nickname="alice",
                text="A 官方机器人记忆",
                created_at=now,
            )
            store.record_message(
                platform_id="default_2222222222",
                bot_id="default_2222222222",
                group_id="group-openid-a",
                user_id="member-openid-a",
                nickname="alice",
                text="B 官方机器人记忆",
                created_at=now + 1,
            )

            memories = store.retrieve(
                platform_id="default_1903757478",
                bot_id="default_1903757478",
                bot_aliases=["qq_official"],
                group_id="group-openid-a",
                user_id="member-openid-a",
                query="官方机器人",
                now=now + 10,
            )

            self.assertIn("A 官方机器人记忆", memories)
            self.assertNotIn("B 官方机器人记忆", memories)


if __name__ == "__main__":
    unittest.main()
