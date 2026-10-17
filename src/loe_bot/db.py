"""SQLite storage: chats, their group subscriptions, the last known schedule, sent reminders."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from loe_core import DaySchedule

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    chat_id INTEGER PRIMARY KEY,
    reminder_minutes INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS subscriptions (
    chat_id INTEGER NOT NULL REFERENCES chats (chat_id) ON DELETE CASCADE ON UPDATE CASCADE,
    grp TEXT NOT NULL,
    PRIMARY KEY (chat_id, grp)
);
CREATE TABLE IF NOT EXISTS schedules (
    day TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sent_reminders (
    chat_id INTEGER NOT NULL,
    grp TEXT NOT NULL,
    kind TEXT NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (chat_id, grp, kind, at)
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Subscriber:
    chat_id: int
    groups: frozenset[str]
    reminder_minutes: int


class Storage:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._db.execute("BEGIN")
        try:
            yield
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    # Chats and subscriptions

    def ensure_chat(self, chat_id: int, reminder_minutes: int) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO chats (chat_id, reminder_minutes) VALUES (?, ?)",
            (chat_id, reminder_minutes),
        )

    def delete_chat(self, chat_id: int) -> None:
        with self._transaction():
            self._db.execute("DELETE FROM chats WHERE chat_id = ?", (chat_id,))
            self._db.execute("DELETE FROM sent_reminders WHERE chat_id = ?", (chat_id,))

    def migrate_chat(self, old_id: int, new_id: int) -> None:
        """A group became a supergroup and got a new id."""
        with self._transaction():
            self._db.execute("UPDATE chats SET chat_id = ? WHERE chat_id = ?", (new_id, old_id))
            self._db.execute(
                "UPDATE sent_reminders SET chat_id = ? WHERE chat_id = ?", (new_id, old_id)
            )

    def groups(self, chat_id: int) -> set[str]:
        rows = self._db.execute("SELECT grp FROM subscriptions WHERE chat_id = ?", (chat_id,))
        return {grp for (grp,) in rows}

    def toggle_group(self, chat_id: int, group: str) -> set[str]:
        with self._transaction():
            deleted = self._db.execute(
                "DELETE FROM subscriptions WHERE chat_id = ? AND grp = ?", (chat_id, group)
            ).rowcount
            if not deleted:
                self._db.execute(
                    "INSERT INTO subscriptions (chat_id, grp) VALUES (?, ?)", (chat_id, group)
                )
        return self.groups(chat_id)

    def reminder(self, chat_id: int) -> int | None:
        row = self._db.execute(
            "SELECT reminder_minutes FROM chats WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return row[0] if row else None

    def set_reminder(self, chat_id: int, minutes: int) -> None:
        self._db.execute(
            "UPDATE chats SET reminder_minutes = ? WHERE chat_id = ?", (minutes, chat_id)
        )

    def subscribers(self) -> list[Subscriber]:
        """Chats subscribed to at least one group."""
        rows = self._db.execute(
            "SELECT c.chat_id, c.reminder_minutes, group_concat(s.grp) "
            "FROM chats c JOIN subscriptions s USING (chat_id) GROUP BY c.chat_id"
        )
        return [
            Subscriber(chat_id, frozenset(groups.split(",")), reminder)
            for chat_id, reminder, groups in rows
        ]

    # Schedules

    def load_schedules(self) -> dict[date, DaySchedule]:
        rows = self._db.execute("SELECT data FROM schedules ORDER BY day")
        schedules = (DaySchedule.from_dict(json.loads(data)) for (data,) in rows)
        return {s.day: s for s in schedules}

    def save_schedules(self, schedules: Mapping[date, DaySchedule]) -> None:
        with self._transaction():
            self._db.execute("DELETE FROM schedules")
            self._db.executemany(
                "INSERT INTO schedules (day, data) VALUES (?, ?)",
                [
                    (day.isoformat(), json.dumps(s.to_dict(), ensure_ascii=False))
                    for day, s in schedules.items()
                ],
            )

    # Reminders

    def claim_reminder(self, chat_id: int, group: str, kind: str, at: datetime) -> bool:
        """Record a reminder as sent; False if it was already sent."""
        return bool(
            self._db.execute(
                "INSERT OR IGNORE INTO sent_reminders (chat_id, grp, kind, at) VALUES (?, ?, ?, ?)",
                (chat_id, group, kind, _utc(at)),
            ).rowcount
        )

    def prune_reminders(self, before: datetime) -> None:
        self._db.execute("DELETE FROM sent_reminders WHERE at < ?", (_utc(before),))

    # Key/value

    def get_meta(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def _utc(moment: datetime) -> str:
    # UTC ISO strings sort in time order, unlike local ones around the DST switch.
    return moment.astimezone(UTC).isoformat()
