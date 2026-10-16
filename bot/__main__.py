"""Запуск: python -m bot"""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from bot import handlers
from bot.config import ConfigError, load_settings
from bot.db import Database
from bot.logging_setup import setup_logging
from services.gigachat import GigaChat
from services.llm import OpenRouterText, Summarizer

log = logging.getLogger("bot")


async def purge_loop(db: Database, ttl_days: int) -> None:
    while True:
        try:
            removed = await db.purge_old_transcripts(ttl_days)
            if removed:
                log.info("Удалено старых расшифровок: %d", removed)
        except Exception:
            log.exception("Ошибка очистки расшифровок")
        await asyncio.sleep(3600)


async def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        sys.exit(1)
    setup_logging(settings.log_level, settings.secrets)

    db = Database(settings.db_path, settings.day_reset_utc_offset_hours)
    await db.connect()
    gigachat = GigaChat(settings.gigachat_auth_key, settings.gigachat_scope,
                        audio_models=settings.gigachat_audio_models, text_model=settings.gigachat_text_model)
    openrouter = (OpenRouterText(settings.openrouter_api_key, settings.openrouter_models)
                  if settings.openrouter_api_key else None)
    summarizer = Summarizer(gigachat, openrouter)
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    dp = Dispatcher(db=db, settings=settings, gigachat=gigachat, summarizer=summarizer)
    dp.include_router(handlers.router)

    purge = asyncio.create_task(purge_loop(db, settings.transcript_ttl_days))
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Что умеет бот"),
            BotCommand(command="limit", description="Сколько минут осталось сегодня"),
        ])
        log.info("Бот запущен: аудио %s, текст %s, запасной OpenRouter: %s, лимит %d мин/сутки",
                 "/".join(settings.gigachat_audio_models), settings.gigachat_text_model,
                 "да" if openrouter else "нет", settings.daily_minutes_per_user)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        purge.cancel()
        await bot.session.close()
        await gigachat.close()
        await summarizer.close()
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
