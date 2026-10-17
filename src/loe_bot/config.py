from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

MIN_POLL_INTERVAL = 60


@dataclass(frozen=True)
class Config:
    bot_token: str
    db_path: Path = Path("data/bot.db")
    #: Seconds between schedule fetches.
    poll_interval: int = 180
    #: Minutes before an outage/restoration to remind new chats; 0 disables reminders.
    default_reminder: int = 15
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Config:
        token = env.get("BOT_TOKEN", "").strip()
        if not token:
            raise SystemExit("BOT_TOKEN is not set")
        return cls(
            bot_token=token,
            db_path=Path(env.get("DB_PATH", cls.db_path)),
            poll_interval=max(MIN_POLL_INTERVAL, int(env.get("POLL_INTERVAL", cls.poll_interval))),
            default_reminder=max(0, int(env.get("DEFAULT_REMINDER", cls.default_reminder))),
            log_level=env.get("LOG_LEVEL", cls.log_level).upper(),
        )
