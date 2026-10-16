"""Настройки из переменных окружения / .env. Секреты нигде не печатаются."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Аудио понимают только Pro и Max (у GigaChat-2 «Model does not support audio»). Max — запас, если у Pro кончатся токены.
DEFAULT_AUDIO_MODELS = ("GigaChat-2-Pro", "GigaChat-2-Max")
DEFAULT_TEXT_MODEL = "GigaChat-2-Pro"  # точнее Lite в «Задачах»: не путает сроки между пунктами

# Запасной вариант для «Кратко»/«Задачи», если GigaChat не ответил: бесплатные модели OpenRouter по очереди.
DEFAULT_OPENROUTER_MODELS = (
    "nvidia/nemotron-3-super-120b-a12b:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
)


class ConfigError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    # Пустое значение (как в .env.example) считаем «не задано».
    return os.getenv(name, "").strip() or default


def _env_int(name: str, default: int) -> int:
    raw = _env(name, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} должно быть целым числом") from exc


def _env_list(name: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in _env(name).split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    # repr=False: секреты не попадут в лог даже при случайном print(settings).
    bot_token: str = field(repr=False)
    gigachat_auth_key: str = field(repr=False)
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_audio_models: tuple[str, ...] = DEFAULT_AUDIO_MODELS
    gigachat_text_model: str = DEFAULT_TEXT_MODEL
    openrouter_api_key: str = field(default="", repr=False)
    openrouter_models: tuple[str, ...] = DEFAULT_OPENROUTER_MODELS
    admin_ids: frozenset[int] = frozenset()
    db_path: Path = PROJECT_ROOT / "data" / "bot.db"
    daily_minutes_per_user: int = 15
    max_audio_minutes: int = 30          # один файл длиннее — отказ (иначе он съест память при декодировании)
    monthly_minutes_total: int = 0       # общий потолок на всех за месяц, 0 = без потолка
    llm_per_user_per_day: int = 20       # нажатий «Кратко»/«Задачи» на человека в сутки
    llm_total_per_day: int = 1000        # на всех: страховка от накрутки, токенов GigaChat хватает с запасом
    transcript_ttl_days: int = 3         # сколько хранить текст расшифровки для кнопок
    day_reset_utc_offset_hours: int = 3  # сутки лимита считаются по МСК
    log_level: str = "INFO"

    @property
    def secrets(self) -> tuple[str, ...]:
        return tuple(s for s in (self.bot_token, self.gigachat_auth_key, self.openrouter_api_key) if s)


def load_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env", override=False)

    bot_token = _env("BOT_TOKEN")
    if not bot_token:
        raise ConfigError("не задан BOT_TOKEN (токен от @BotFather) в .env")
    gigachat_key = _env("GIGACHAT_AUTH_KEY")
    if not gigachat_key:
        raise ConfigError("не задан GIGACHAT_AUTH_KEY (Authorization key проекта GigaChat API) в .env")

    models = _env_list("OPENROUTER_MODELS")
    audio_models = _env_list("GIGACHAT_AUDIO_MODELS")
    try:
        admin_ids = frozenset(int(x) for x in _env("ADMIN_IDS").replace(" ", "").split(",") if x)
    except ValueError as exc:
        raise ConfigError("ADMIN_IDS — числовые id через запятую") from exc

    db_path = _env("DB_PATH")
    return Settings(
        bot_token=bot_token,
        gigachat_auth_key=gigachat_key,
        gigachat_scope=_env("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
        gigachat_audio_models=audio_models or DEFAULT_AUDIO_MODELS,
        gigachat_text_model=_env("GIGACHAT_TEXT_MODEL", DEFAULT_TEXT_MODEL),
        openrouter_api_key=_env("OPENROUTER_API_KEY"),
        openrouter_models=models or DEFAULT_OPENROUTER_MODELS,
        admin_ids=admin_ids,
        db_path=Path(db_path) if db_path else PROJECT_ROOT / "data" / "bot.db",
        daily_minutes_per_user=_env_int("DAILY_MINUTES_PER_USER", 15),
        max_audio_minutes=_env_int("MAX_AUDIO_MINUTES", 30),
        monthly_minutes_total=_env_int("MONTHLY_MINUTES_TOTAL", 0),
        llm_per_user_per_day=_env_int("LLM_PER_USER_PER_DAY", 20),
        llm_total_per_day=_env_int("LLM_TOTAL_PER_DAY", 1000),
        transcript_ttl_days=_env_int("TRANSCRIPT_TTL_DAYS", 3),
        day_reset_utc_offset_hours=_env_int("DAY_RESET_UTC_OFFSET_HOURS", 3),
        log_level=_env("LOG_LEVEL", "INFO").upper(),
    )
