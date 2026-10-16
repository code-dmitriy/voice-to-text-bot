"""Тексты сообщений бота."""
from __future__ import annotations

from bot.config import Settings


def fmt_duration(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def fmt_minutes(seconds: int) -> str:
    minutes = seconds / 60
    return f"{minutes:.0f}" if minutes >= 10 or minutes == int(minutes) else f"{minutes:.1f}".replace(".", ",")


def start_text(settings: Settings) -> str:
    lines = [
        "👋 Я превращаю голосовые в текст.",
        "",
        "Пришлите или перешлите мне <b>голосовое, кружок или аудиофайл</b> — "
        "через несколько секунд верну текст с пунктуацией.",
        "",
        "Под текстом будут кнопки:",
        "📝 <b>Кратко</b> — суть в паре пунктов",
        "✅ <b>Задачи</b> — что сделать, сроки, договорённости",
    ]
    lines += [
        "",
        f"Бесплатно: {settings.daily_minutes_per_user} мин в сутки, одна запись — до {settings.max_audio_minutes} мин.",
        "Остаток на сегодня — /limit",
        "",
        "🔒 Ничего не публикую. Запись распознаёт нейросеть GigaChat (Сбер), файл сразу удаляется. "
        f"Текст храню {settings.transcript_ttl_days} дн., чтобы работали кнопки, потом удаляю.",
    ]
    return "\n".join(lines)


HINT = "Пришлите или перешлите голосовое, кружок или аудиофайл — я превращу его в текст."
WORKING = "🎧 Расшифровываю…"
BUSY = "⏳ Ещё расшифровываю предыдущую запись — пришлите эту, когда я закончу."
NOT_AUDIO = "Это не похоже на аудио. Пришлите голосовое, кружок, аудио- или видеофайл."
TOO_BIG = "Файл больше 20 МБ — Telegram не даёт ботам скачивать такие. Пришлите запись покороче."
NOTHING_HEARD = "🤷 Не расслышал ни слова. Может, запись без звука?"
FAILED = "Что-то пошло не так. Попробуйте ещё раз чуть позже."
EXPIRED = "Эта расшифровка уже удалена. Пришлите голосовое заново."
LLM_TITLES = {"summary": "📝 Кратко", "tasks": "✅ Задачи"}
LLM_WORKING = {"summary": "⏳ Выделяю суть…", "tasks": "⏳ Ищу задачи и сроки…"}


def limit_reached(settings: Settings) -> str:
    return (f"На сегодня бесплатные {settings.daily_minutes_per_user} мин закончились. "
            "Лимит обновится в полночь по МСК.")


def not_enough_left(duration: int, left: int) -> str:
    return (f"В этой записи {fmt_duration(duration)}, а на сегодня осталось {fmt_minutes(left)} мин. "
            "Пришлите запись покороче или приходите завтра.")


def too_long(settings: Settings) -> str:
    return f"Запись слишком длинная: можно до {settings.max_audio_minutes} мин за раз."
