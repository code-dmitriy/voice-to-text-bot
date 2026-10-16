"""SQLite: пользователи, расход минут и запросов к нейросети по дням, тексты расшифровок для кнопок."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    created_at  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL
);

-- day — сутки по МСК (YYYY-MM-DD). seconds — сколько секунд звука расшифровано.
CREATE TABLE IF NOT EXISTS usage (
    user_id       INTEGER NOT NULL,
    day           TEXT NOT NULL,
    seconds       INTEGER NOT NULL DEFAULT 0,
    llm_requests  INTEGER NOT NULL DEFAULT 0,
    files         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);

-- Текст нужен только кнопкам «Кратко»/«Задачи» и удаляется через TRANSCRIPT_TTL_DAYS.
CREATE TABLE IF NOT EXISTS transcripts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    text        TEXT NOT NULL,
    seconds     INTEGER NOT NULL,
    summary     TEXT,
    tasks       TEXT,
    created_at  INTEGER NOT NULL
);
"""


class Database:
    def __init__(self, path: Path, day_offset_hours: int = 3):
        self._path = path
        self._tz = timezone(timedelta(hours=day_offset_hours))
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Database.connect() не вызван"
        return self._conn

    def today(self) -> str:
        return datetime.now(self._tz).strftime("%Y-%m-%d")

    def month(self) -> str:
        return datetime.now(self._tz).strftime("%Y-%m")

    # ---------- пользователи ----------

    async def touch_user(self, user_id: int, username: str | None, first_name: str | None) -> None:
        now = int(time.time())
        await self.conn.execute(
            """INSERT INTO users (user_id, username, first_name, created_at, last_seen) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name,
               last_seen=excluded.last_seen""",
            (user_id, username, first_name, now, now),
        )
        await self.conn.commit()

    # ---------- расход ----------

    async def seconds_used_today(self, user_id: int) -> int:
        row = await (await self.conn.execute(
            "SELECT seconds FROM usage WHERE user_id=? AND day=?", (user_id, self.today()))).fetchone()
        return row["seconds"] if row else 0

    async def llm_used_today(self, user_id: int) -> int:
        row = await (await self.conn.execute(
            "SELECT llm_requests FROM usage WHERE user_id=? AND day=?", (user_id, self.today()))).fetchone()
        return row["llm_requests"] if row else 0

    async def total_seconds_this_month(self) -> int:
        row = await (await self.conn.execute(
            "SELECT COALESCE(SUM(seconds), 0) AS s FROM usage WHERE day LIKE ?", (self.month() + "-%",))).fetchone()
        return row["s"]

    async def total_llm_today(self) -> int:
        row = await (await self.conn.execute(
            "SELECT COALESCE(SUM(llm_requests), 0) AS n FROM usage WHERE day=?", (self.today(),))).fetchone()
        return row["n"]

    async def add_seconds(self, user_id: int, seconds: int) -> None:
        await self.conn.execute(
            """INSERT INTO usage (user_id, day, seconds, files) VALUES (?, ?, ?, 1)
               ON CONFLICT(user_id, day) DO UPDATE SET seconds = seconds + excluded.seconds, files = files + 1""",
            (user_id, self.today(), seconds),
        )
        await self.conn.commit()

    async def add_llm_request(self, user_id: int) -> None:
        await self.conn.execute(
            """INSERT INTO usage (user_id, day, llm_requests) VALUES (?, ?, 1)
               ON CONFLICT(user_id, day) DO UPDATE SET llm_requests = llm_requests + 1""",
            (user_id, self.today()),
        )
        await self.conn.commit()

    # ---------- расшифровки ----------

    async def save_transcript(self, user_id: int, text: str, seconds: int) -> int:
        cur = await self.conn.execute(
            "INSERT INTO transcripts (user_id, text, seconds, created_at) VALUES (?, ?, ?, ?)",
            (user_id, text, seconds, int(time.time())),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def get_transcript(self, transcript_id: int) -> aiosqlite.Row | None:
        return await (await self.conn.execute(
            "SELECT * FROM transcripts WHERE id=?", (transcript_id,))).fetchone()

    async def save_llm_result(self, transcript_id: int, kind: str, text: str) -> None:
        column = {"summary": "summary", "tasks": "tasks"}[kind]
        await self.conn.execute(f"UPDATE transcripts SET {column}=? WHERE id=?", (text, transcript_id))
        await self.conn.commit()

    async def purge_old_transcripts(self, ttl_days: int) -> int:
        cur = await self.conn.execute(
            "DELETE FROM transcripts WHERE created_at < ?", (int(time.time()) - ttl_days * 86400,))
        await self.conn.commit()
        return cur.rowcount

    # ---------- статистика для админа ----------

    async def stats(self) -> dict[str, int]:
        today, month = self.today(), self.month() + "-%"

        async def one(sql: str, *args) -> int:
            row = await (await self.conn.execute(sql, args)).fetchone()
            return row[0] or 0

        return {
            "users": await one("SELECT COUNT(*) FROM users"),
            "new_today": await one("SELECT COUNT(*) FROM users WHERE created_at >= ?", _day_start_ts(self._tz)),
            "active_today": await one("SELECT COUNT(*) FROM usage WHERE day=? AND files > 0", today),
            "files_today": await one("SELECT SUM(files) FROM usage WHERE day=?", today),
            "seconds_today": await one("SELECT SUM(seconds) FROM usage WHERE day=?", today),
            "seconds_month": await one("SELECT SUM(seconds) FROM usage WHERE day LIKE ?", month),
            "llm_today": await one("SELECT SUM(llm_requests) FROM usage WHERE day=?", today),
        }


def _day_start_ts(tz: timezone) -> int:
    now = datetime.now(tz)
    return int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
