from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MemoryRecord:
    scope: str
    platform_id: str
    bot_id: str
    group_id: str
    user_id: str
    nickname: str
    text: str
    created_at: float


class SQLiteMemoryStore:
    def __init__(self, db_path: str | Path, retention_hours: int = 12) -> None:
        self.db_path = Path(db_path)
        self.retention_seconds = max(1, int(retention_hours)) * 3600

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform_id TEXT NOT NULL,
                    bot_id TEXT NOT NULL DEFAULT '',
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    nickname TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """,
            )
            self._ensure_sqlite_column(conn, "bot_id", "TEXT NOT NULL DEFAULT ''")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_group_time
                ON memories(platform_id, bot_id, group_id, created_at DESC)
                """,
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_user_time
                ON memories(platform_id, bot_id, user_id, created_at DESC)
                """,
            )

    def record_message(
        self,
        *,
        platform_id: str,
        bot_id: str = "",
        group_id: str,
        user_id: str,
        nickname: str,
        text: str,
        created_at: float | None = None,
    ) -> None:
        text = _normalize_text(text)
        if not text:
            return
        created_at = time.time() if created_at is None else float(created_at)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memories(platform_id, bot_id, group_id, user_id, nickname, text, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(platform_id or ""),
                    str(bot_id or ""),
                    str(group_id or ""),
                    str(user_id or ""),
                    str(nickname or ""),
                    text,
                    created_at,
                ),
            )

    def prune(self, *, now: float | None = None) -> int:
        now = time.time() if now is None else float(now)
        cutoff = now - self.retention_seconds
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM memories WHERE created_at < ?",
                (cutoff,),
            )
            return int(cursor.rowcount or 0)

    def retrieve(
        self,
        *,
        platform_id: str = "",
        bot_id: str = "",
        group_id: str,
        user_id: str,
        query: str,
        now: float | None = None,
        group_limit: int = 8,
        user_limit: int = 6,
    ) -> str:
        now = time.time() if now is None else float(now)
        cutoff = now - self.retention_seconds
        query_terms = _tokenize(query)

        group_records = self._fetch_group_records(
            platform_id=str(platform_id or ""),
            bot_id=str(bot_id or ""),
            group_id=str(group_id or ""),
            cutoff=cutoff,
            limit=max(group_limit * 4, group_limit),
        )
        user_records = self._fetch_user_records(
            platform_id=str(platform_id or ""),
            bot_id=str(bot_id or ""),
            user_id=str(user_id or ""),
            cutoff=cutoff,
            limit=max(user_limit * 4, user_limit),
        )

        group_records = _rank_records(group_records, query_terms)[:group_limit]
        user_records = [
            record for record in _rank_records(user_records, query_terms)
            if record.group_id != str(group_id or "")
        ][:user_limit]

        sections: list[str] = []
        if group_records:
            sections.append(
                "当前群聊记忆:\n"
                + "\n".join(_format_record(record) for record in group_records),
            )
        if user_records:
            sections.append(
                "同一用户跨群记忆:\n"
                + "\n".join(_format_record(record) for record in user_records),
            )
        return "\n\n".join(sections)

    def _fetch_group_records(
        self,
        *,
        platform_id: str,
        bot_id: str,
        group_id: str,
        cutoff: float,
        limit: int,
    ) -> list[MemoryRecord]:
        if not group_id:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT platform_id, bot_id, group_id, user_id, nickname, text, created_at
                FROM memories
                WHERE platform_id = ? AND bot_id = ? AND group_id = ? AND created_at >= ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (platform_id, bot_id, group_id, cutoff, int(limit)),
            ).fetchall()
        return [MemoryRecord("group", *row) for row in rows]

    def _fetch_user_records(
        self,
        *,
        platform_id: str,
        bot_id: str,
        user_id: str,
        cutoff: float,
        limit: int,
    ) -> list[MemoryRecord]:
        if not user_id:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT platform_id, bot_id, group_id, user_id, nickname, text, created_at
                FROM memories
                WHERE platform_id = ? AND bot_id = ? AND user_id = ? AND created_at >= ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (platform_id, bot_id, user_id, cutoff, int(limit)),
            ).fetchall()
        return [MemoryRecord("user", *row) for row in rows]

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA busy_timeout=3000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_sqlite_column(
        self,
        conn: sqlite3.Connection,
        column: str,
        ddl: str,
    ) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(memories)")}
        if column not in columns:
            conn.execute(f"ALTER TABLE memories ADD COLUMN {column} {ddl}")


class PostgresMemoryStore(SQLiteMemoryStore):
    def __init__(
        self,
        dsn: str,
        retention_hours: int = 12,
    ) -> None:
        super().__init__(":memory:", retention_hours=retention_hours)
        self.dsn = dsn

    def initialize(self) -> None:
        self._ensure_driver()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS memories (
                        id BIGSERIAL PRIMARY KEY,
                        platform_id TEXT NOT NULL,
                        bot_id TEXT NOT NULL DEFAULT '',
                        group_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        nickname TEXT NOT NULL,
                        text TEXT NOT NULL,
                        created_at DOUBLE PRECISION NOT NULL
                    )
                    """,
                )
                self._ensure_postgres_column(cur, "bot_id", "TEXT NOT NULL DEFAULT ''")
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_memories_group_time
                    ON memories(platform_id, bot_id, group_id, created_at DESC)
                    """,
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_memories_user_time
                    ON memories(platform_id, bot_id, user_id, created_at DESC)
                    """,
                )

    def record_message(
        self,
        *,
        platform_id: str,
        bot_id: str = "",
        group_id: str,
        user_id: str,
        nickname: str,
        text: str,
        created_at: float | None = None,
    ) -> None:
        text = _normalize_text(text)
        if not text:
            return
        created_at = time.time() if created_at is None else float(created_at)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO memories(platform_id, bot_id, group_id, user_id, nickname, text, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(platform_id or ""),
                        str(bot_id or ""),
                        str(group_id or ""),
                        str(user_id or ""),
                        str(nickname or ""),
                        text,
                        created_at,
                    ),
                )

    def prune(self, *, now: float | None = None) -> int:
        now = time.time() if now is None else float(now)
        cutoff = now - self.retention_seconds
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM memories WHERE created_at < %s", (cutoff,))
                return int(cur.rowcount or 0)

    def _fetch_group_records(
        self,
        *,
        platform_id: str,
        bot_id: str,
        group_id: str,
        cutoff: float,
        limit: int,
    ) -> list[MemoryRecord]:
        if not group_id:
            return []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT platform_id, bot_id, group_id, user_id, nickname, text, created_at
                    FROM memories
                    WHERE platform_id = %s AND bot_id = %s AND group_id = %s AND created_at >= %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (platform_id, bot_id, group_id, cutoff, int(limit)),
                )
                rows = cur.fetchall()
        return [MemoryRecord("group", *row) for row in rows]

    def _fetch_user_records(
        self,
        *,
        platform_id: str,
        bot_id: str,
        user_id: str,
        cutoff: float,
        limit: int,
    ) -> list[MemoryRecord]:
        if not user_id:
            return []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT platform_id, bot_id, group_id, user_id, nickname, text, created_at
                    FROM memories
                    WHERE platform_id = %s AND bot_id = %s AND user_id = %s AND created_at >= %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (platform_id, bot_id, user_id, cutoff, int(limit)),
                )
                rows = cur.fetchall()
        return [MemoryRecord("user", *row) for row in rows]

    @contextmanager
    def _connect(self):
        psycopg = self._ensure_driver()
        conn = psycopg.connect(self.dsn, connect_timeout=3)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_driver(self):
        import psycopg

        return psycopg

    def _ensure_postgres_column(self, cur, column: str, ddl: str) -> None:
        cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'memories' AND column_name = %s
            """,
            (column,),
        )
        if cur.fetchone() is None:
            cur.execute(f"ALTER TABLE memories ADD COLUMN {column} {ddl}")


def build_memory_store(config: dict, data_dir: str | Path):
    retention_hours = int(config.get("retention_hours", 12))
    backend = str(config.get("storage_backend", "sqlite")).lower()
    if backend == "postgres":
        dsn = str(
            config.get(
                "postgres_dsn",
                "postgresql://xiaozhao:xiaozhao_memory@xiaozhao_memory_db:5432/xiaozhao_memory",
            ),
        )
        return PostgresMemoryStore(dsn, retention_hours=retention_hours)

    data_dir = Path(data_dir)
    return SQLiteMemoryStore(data_dir / "xiaozhao_memory.db", retention_hours=retention_hours)


def _normalize_text(text: str, limit: int = 500) -> str:
    text = " ".join(str(text or "").split())
    if len(text) > limit:
        return text[:limit]
    return text


def _tokenize(text: str) -> set[str]:
    compact = _normalize_text(text).lower()
    terms = {token for token in compact.split() if len(token) >= 2}
    for size in (2, 3, 4):
        terms.update(compact[i : i + size] for i in range(max(0, len(compact) - size + 1)))
    return terms


def _rank_records(records: list[MemoryRecord], query_terms: set[str]) -> list[MemoryRecord]:
    def score(record: MemoryRecord) -> tuple[int, float]:
        text = record.text.lower()
        hits = sum(1 for term in query_terms if term and term in text)
        return hits, record.created_at

    return sorted(records, key=score, reverse=True)


def _format_record(record: MemoryRecord) -> str:
    label = record.nickname or record.user_id or "未知用户"
    return f"- {label}({record.user_id})：{record.text}"
