from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from loe_core import LoeClient

from .config import Config
from .db import Storage
from .handlers import COMMANDS, router
from .monitor import Monitor


async def run(config: Config) -> None:
    db = Storage(config.db_path)
    bot = Bot(
        config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    try:
        async with aiohttp.ClientSession() as session:
            monitor = Monitor(bot, db, LoeClient(session), poll_interval=config.poll_interval)
            dispatcher["db"] = db
            dispatcher["config"] = config
            dispatcher["monitor"] = monitor
            await bot.set_my_commands(COMMANDS)
            jobs = asyncio.create_task(monitor.run())
            try:
                await dispatcher.start_polling(
                    bot, allowed_updates=dispatcher.resolve_used_update_types()
                )
            finally:
                jobs.cancel()
                with suppress(asyncio.CancelledError):
                    await jobs
    finally:
        await bot.session.close()
        db.close()


def main() -> None:
    config = Config.from_env()
    logging.basicConfig(
        level=config.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)  # a line per update otherwise
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
