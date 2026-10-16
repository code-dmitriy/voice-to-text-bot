"""Обработчики: /start, /limit, /stats, приём аудио и кнопки «Кратко»/«Задачи»."""
from __future__ import annotations

import asyncio
import html
import logging
import math
import time
from pathlib import PurePath

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           Message)

from bot import texts
from bot.config import Settings
from bot.db import Database
from services.audio import (AudioError, AudioTooLong, decode_to_pcm, duration_seconds, is_silent, pcm_to_ogg,
                            split_pcm)
from services.gigachat import GigaChat, GigaChatError
from services.llm import LLMError, Summarizer

log = logging.getLogger(__name__)

router = Router()
router.message.filter(F.chat.type == ChatType.PRIVATE)

TELEGRAM_DOWNLOAD_LIMIT = 20 * 1024 * 1024
MESSAGE_LIMIT = 3800        # запас до 4096 под заголовок и html-экранирование
MAX_TEXT_MESSAGES = 4       # длиннее — шлём первую часть + весь текст файлом
AUDIO_EXTENSIONS = {".ogg", ".oga", ".opus", ".mp3", ".m4a", ".aac", ".wav", ".flac", ".wma", ".amr",
                    ".mp4", ".mov", ".webm", ".mkv", ".3gp"}

_busy: set[int] = set()           # кто сейчас ждёт расшифровку — второй файл параллельно не берём
_llm_busy: set[int] = set()
_llm_exhausted_day: list[str] = [""]  # день, когда OpenRouter сказал «лимит на сутки всё»


def _is_admin(settings: Settings, user_id: int) -> bool:
    return user_id in settings.admin_ids


# ---------- команды ----------

@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_start(message: Message, db: Database, settings: Settings) -> None:
    await db.touch_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await message.answer(texts.start_text(settings))


@router.message(Command("limit"))
async def cmd_limit(message: Message, db: Database, settings: Settings) -> None:
    user_id = message.from_user.id
    if _is_admin(settings, user_id):
        await message.answer("У вас нет лимита (админ).")
        return
    left = max(0, settings.daily_minutes_per_user * 60 - await db.seconds_used_today(user_id))
    lines = [f"Сегодня осталось: <b>{texts.fmt_minutes(left)} мин</b> из {settings.daily_minutes_per_user}."]
    llm_left = max(0, settings.llm_per_user_per_day - await db.llm_used_today(user_id))
    lines.append(f"Кнопок «Кратко»/«Задачи»: {llm_left} из {settings.llm_per_user_per_day}.")
    lines.append("Лимит обновляется в полночь по МСК.")
    await message.answer("\n".join(lines))


@router.message(Command("stats"))
async def cmd_stats(message: Message, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, message.from_user.id):
        await message.answer(texts.HINT)
        return
    s = await db.stats()
    await message.answer(
        "📊 <b>Статистика</b>\n"
        f"Пользователей всего: {s['users']} (новых сегодня: {s['new_today']})\n"
        f"Расшифровывали сегодня: {s['active_today']} чел., файлов: {s['files_today']}\n"
        f"Минут сегодня: {texts.fmt_minutes(s['seconds_today'])}, за месяц: {texts.fmt_minutes(s['seconds_month'])}\n"
        f"Запросов к нейросети сегодня: {s['llm_today']} из {settings.llm_total_per_day}"
    )


# ---------- аудио ----------

def _pick_media(message: Message):
    """(файл, длительность в секундах или None) или (None, None), если это не аудио/видео."""
    if message.voice:
        return message.voice, message.voice.duration
    if message.video_note:
        return message.video_note, message.video_note.duration
    if message.audio:
        return message.audio, message.audio.duration
    if message.video:
        return message.video, message.video.duration
    doc = message.document
    if doc:
        mime = doc.mime_type or ""
        ext = PurePath(doc.file_name or "").suffix.lower()
        if mime.startswith(("audio/", "video/")) or ext in AUDIO_EXTENSIONS:
            return doc, None
    return None, None


@router.message(F.voice | F.video_note | F.audio | F.video | F.document)
async def on_media(message: Message, bot: Bot, db: Database, settings: Settings,
                   gigachat: GigaChat) -> None:
    user = message.from_user
    await db.touch_user(user.id, user.username, user.first_name)

    media, duration = _pick_media(message)
    if media is None:
        await message.reply(texts.NOT_AUDIO)
        return
    if media.file_size and media.file_size > TELEGRAM_DOWNLOAD_LIMIT:
        await message.reply(texts.TOO_BIG)
        return

    admin = _is_admin(settings, user.id)
    max_seconds = settings.max_audio_minutes * 60
    left = max_seconds if admin else settings.daily_minutes_per_user * 60 - await db.seconds_used_today(user.id)
    if left <= 0:
        await message.reply(texts.limit_reached(settings))
        return
    if duration and duration > max_seconds:
        await message.reply(texts.too_long(settings))
        return
    if duration and duration > left + 5:  # +5 с: длительность от Telegram округлена
        await message.reply(texts.not_enough_left(duration, left))
        return
    if settings.monthly_minutes_total and not admin:
        if await db.total_seconds_this_month() >= settings.monthly_minutes_total * 60:
            await message.reply("Бесплатные минуты бота на этот месяц закончились. Загляните в начале следующего.")
            return

    if user.id in _busy:
        await message.reply(texts.BUSY)
        return
    _busy.add(user.id)
    status = await message.reply(texts.WORKING)
    started = time.monotonic()
    try:
        file = await bot.download(media.file_id)
        data = file.getvalue()
        pcm = await asyncio.to_thread(decode_to_pcm, data, max_seconds)
        seconds = max(1, math.ceil(duration_seconds(pcm)))
        if not admin and seconds > left + 5:
            await status.edit_text(texts.not_enough_left(seconds, left))
            return
        if await asyncio.to_thread(is_silent, pcm):
            await status.edit_text(texts.NOTHING_HEARD)
            return

        chunks = await asyncio.to_thread(split_pcm, pcm)
        text = await _recognize_all(gigachat, chunks, status)
        if not text:
            await status.edit_text(texts.NOTHING_HEARD)
            return

        await db.add_seconds(user.id, seconds)
        transcript_id = await db.save_transcript(user.id, text, seconds)
        log.info("Расшифровано: user=%s %d с звука, %d кусков, %d симв., %.1f с работы",
                 user.id, seconds, len(chunks), len(text), time.monotonic() - started)
        await _send_transcript(message, status, text, seconds, transcript_id)
    except AudioTooLong:
        await status.edit_text(texts.too_long(settings))
    except AudioError as exc:
        await status.edit_text(exc.user_message)
    except GigaChatError as exc:
        log.warning("GigaChat: %s", exc)
        await status.edit_text(exc.user_message)
    except TelegramBadRequest as exc:
        if "too big" in str(exc).lower():
            await status.edit_text(texts.TOO_BIG)
        else:
            log.exception("Telegram отклонил запрос")
            await status.edit_text(texts.FAILED)
    except Exception:
        log.exception("Ошибка расшифровки")
        await status.edit_text(texts.FAILED)
    finally:
        _busy.discard(user.id)


async def _recognize_all(gigachat: GigaChat, chunks: list[bytes], status: Message) -> str:
    """Куски по очереди (GigaChat у физлица — один запрос за раз), прогресс в статусе для длинных записей."""
    parts: list[str] = []
    for i, chunk in enumerate(chunks):
        if len(chunks) > 1 and i > 0:
            try:
                await status.edit_text(f"{texts.WORKING} {i * 100 // len(chunks)}%")
            except TelegramBadRequest:
                pass
        ogg = await asyncio.to_thread(pcm_to_ogg, chunk)
        parts.append(await gigachat.transcribe(ogg))
    return "\n\n".join(p for p in parts if p).strip()


def _llm_keyboard(transcript_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📝 Кратко", callback_data=f"s:{transcript_id}"),
        InlineKeyboardButton(text="✅ Задачи", callback_data=f"t:{transcript_id}"),
    ]])


def split_text(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Режет по концу предложения, иначе по пробелу, иначе как получится."""
    parts: list[str] = []
    while len(text) > limit:
        cut = max(text.rfind(". ", 0, limit), text.rfind("? ", 0, limit), text.rfind("! ", 0, limit))
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        cut = cut + 1 if cut > 0 else limit
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


async def _send_transcript(message: Message, status: Message, text: str, seconds: int,
                           transcript_id: int) -> None:
    header = f"🎙 <b>Расшифровка</b> · {texts.fmt_duration(seconds)}\n\n"
    keyboard = _llm_keyboard(transcript_id)
    parts = split_text(text)

    if len(parts) > MAX_TEXT_MESSAGES:
        await status.edit_text(header + html.escape(parts[0]) + "\n\n…весь текст — в файле ниже.")
        await message.reply_document(
            BufferedInputFile(text.encode("utf-8"), filename="расшифровка.txt"), reply_markup=keyboard)
        return

    for i, part in enumerate(parts):
        body = (header if i == 0 else "") + html.escape(part)
        markup = keyboard if i == len(parts) - 1 else None
        if i == 0:
            await status.edit_text(body, reply_markup=markup)
        else:
            await message.answer(body, reply_markup=markup)


# ---------- кнопки «Кратко» / «Задачи» ----------

@router.callback_query(F.data.regexp(r"^[st]:\d+$"))
async def on_llm_button(callback: CallbackQuery, db: Database, settings: Settings,
                        summarizer: Summarizer) -> None:
    kind = "summary" if callback.data[0] == "s" else "tasks"
    transcript_id = int(callback.data[2:])
    user_id = callback.from_user.id

    row = await db.get_transcript(transcript_id)
    if row is None or row["user_id"] != user_id:
        await callback.answer(texts.EXPIRED, show_alert=True)
        return

    title = texts.LLM_TITLES[kind]
    if row[kind]:  # уже считали — не тратим лимит повторно
        await callback.answer()
        await callback.message.reply(f"<b>{title}</b>\n\n{html.escape(row[kind])}")
        return

    if _llm_exhausted_day[0] == db.today():
        await callback.answer("Лимит нейросети на сегодня закончился. Завтра снова заработает.", show_alert=True)
        return
    if not _is_admin(settings, user_id):
        if await db.llm_used_today(user_id) >= settings.llm_per_user_per_day:
            await callback.answer(f"На сегодня кнопки закончились ({settings.llm_per_user_per_day} в сутки).",
                                  show_alert=True)
            return
    if await db.total_llm_today() >= settings.llm_total_per_day:
        await callback.answer("Нейросеть на сегодня устала. Завтра снова заработает.", show_alert=True)
        return
    if user_id in _llm_busy:
        await callback.answer("Уже думаю над предыдущим, секунду…")
        return

    _llm_busy.add(user_id)
    await callback.answer()
    status = await callback.message.reply(texts.LLM_WORKING[kind])
    try:
        result = await summarizer.run(kind, row["text"])
        await db.add_llm_request(user_id)
        await db.save_llm_result(transcript_id, kind, result)
        await status.edit_text(f"<b>{title}</b>\n\n{html.escape(result)}"[:4096])
    except LLMError as exc:
        if exc.daily_limit:
            _llm_exhausted_day[0] = db.today()
        await status.edit_text(exc.user_message)
    except Exception:
        log.exception("Ошибка LLM")
        await status.edit_text(texts.FAILED)
    finally:
        _llm_busy.discard(user_id)


# ---------- всё остальное ----------

@router.message()
async def fallback(message: Message) -> None:
    await message.answer(texts.HINT)
