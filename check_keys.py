"""Проверка ключей без запуска бота: python check_keys.py [аудиофайл]

Расшифровывает файл через GigaChat и делает по нему «Кратко».
Секреты не печатаются.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

from bot.config import ConfigError, load_settings
from services.audio import decode_to_pcm, pcm_to_ogg, split_pcm
from services.gigachat import GigaChat, GigaChatError
from services.llm import LLMError, OpenRouterText, Summarizer


async def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"❌ {exc}")
        return 1

    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.get(f"https://api.telegram.org/bot{settings.bot_token}/getMe")
        if resp.status_code == 200:
            print(f"✅ Telegram: бот @{resp.json()['result']['username']}")
        else:
            print(f"❌ Telegram: токен не подошёл (HTTP {resp.status_code})")

    if len(sys.argv) < 2:
        print("Укажите аудиофайл, чтобы проверить распознавание.")
        return 0
    pcm = decode_to_pcm(Path(sys.argv[1]).read_bytes(), settings.max_audio_minutes * 60)
    gigachat = GigaChat(settings.gigachat_auth_key, settings.gigachat_scope,
                        audio_models=settings.gigachat_audio_models, text_model=settings.gigachat_text_model)
    openrouter = (OpenRouterText(settings.openrouter_api_key, settings.openrouter_models)
                  if settings.openrouter_api_key else None)
    summarizer = Summarizer(gigachat, openrouter)
    try:
        text = "\n\n".join([await gigachat.transcribe(pcm_to_ogg(c)) for c in split_pcm(pcm)])
        print(f"✅ GigaChat, расшифровка:\n{text}\n")
        for kind in ("summary", "tasks"):
            title = "Кратко" if kind == "summary" else "Задачи"
            print(f"✅ «{title}»:\n" + await summarizer.run(kind, text) + "\n")
    except GigaChatError as exc:
        print(f"❌ GigaChat: {exc}")
        return 1
    except LLMError as exc:
        print(f"❌ Кнопки: {exc.user_message}")
        return 1
    finally:
        await gigachat.close()
        await summarizer.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
