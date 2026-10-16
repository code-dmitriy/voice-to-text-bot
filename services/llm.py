"""«Кратко» и «Задачи» по тексту расшифровки.

Сначала GigaChat (у физлица большой бесплатный запас токенов), если он не ответил — бесплатные модели
OpenRouter по очереди до первого ответа (если задан OPENROUTER_API_KEY)."""
from __future__ import annotations

import logging
import re

import httpx

from services.gigachat import GigaChat, GigaChatError

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_INPUT_CHARS = 30_000  # ~30 минут речи; длиннее — обрезаем, бесплатные модели на огромном тексте падают

PROMPTS = {
    "summary": (
        "Ты помогаешь человеку, которому прислали голосовое сообщение. Ниже его расшифровка.\n"
        "Сделай краткое содержание на русском: 2–6 коротких пунктов, каждый с новой строки и начинается с «• ».\n"
        "Только суть, без вступлений и без фраз вроде «в сообщении говорится». "
        "Не придумывай того, чего нет в тексте. Без markdown."
    ),
    "tasks": (
        "Ниже расшифровка голосового сообщения. Выпиши на русском всё, что требует действий от слушателя или автора: "
        "задачи, просьбы, обещания ответить, договорённости, а также важные даты, время, суммы, адреса.\n"
        "Каждый пункт с новой строки, начинается с «• », формулируй как действие («Отправить…», «Ответить…»). "
        "Срок пиши в скобках, только если он прямо сказан про это действие; не переноси даты между пунктами.\n"
        "Если ничего такого нет, ответь одной фразой: «Задач и договорённостей нет.» "
        "Не придумывай. Без вступлений и без markdown."
    ),
}


class LLMError(Exception):
    def __init__(self, user_message: str, *, daily_limit: bool = False):
        super().__init__(user_message)
        self.user_message = user_message
        self.daily_limit = daily_limit


class Summarizer:
    def __init__(self, gigachat: GigaChat, openrouter: "OpenRouterText | None" = None):
        self._gigachat = gigachat
        self._openrouter = openrouter

    async def close(self) -> None:
        if self._openrouter:
            await self._openrouter.close()

    async def run(self, kind: str, transcript: str) -> str:
        user = f'Расшифровка:\n"""\n{transcript[:MAX_INPUT_CHARS]}\n"""'
        try:
            answer = clean_markdown(await self._gigachat.complete(PROMPTS[kind], user))
            if answer:
                return answer
            log.warning("GigaChat: пустой ответ на %s", kind)
        except GigaChatError as exc:
            log.warning("GigaChat не справился с %s: %s", kind, exc)
            if not self._openrouter:
                raise LLMError("Нейросеть сейчас недоступна. Попробуйте через пару минут.") from exc
        if not self._openrouter:
            raise LLMError("Нейросеть не смогла ответить. Попробуйте ещё раз.")
        return await self._openrouter.run(PROMPTS[kind], user)


class OpenRouterText:
    def __init__(self, api_key: str, models: tuple[str, ...], *, timeout: float = 120.0,
                 http_client: httpx.AsyncClient | None = None):
        self._http = http_client or httpx.AsyncClient(timeout=timeout)
        self._headers = {"Authorization": f"Bearer {api_key}", "X-Title": "Voice to text bot"}
        self._models = models

    async def close(self) -> None:
        await self._http.aclose()

    async def run(self, system: str, user: str) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        for model in self._models:
            try:
                resp = await self._http.post(
                    OPENROUTER_URL, headers=self._headers,
                    json={"model": model, "messages": messages, "temperature": 0.2, "max_tokens": 1500},
                )
            except httpx.HTTPError as exc:
                log.warning("OpenRouter %s: %s", model, type(exc).__name__)
                continue
            if resp.status_code == 429:
                if _is_daily_limit(resp.text):
                    log.warning("OpenRouter: дневной лимит аккаунта исчерпан")
                    raise LLMError("Лимит нейросети на сегодня закончился. Расшифровка работает, "
                                   "а «Кратко» и «Задачи» снова будут завтра.", daily_limit=True)
                log.info("OpenRouter %s занята (429), пробую следующую", model)
                continue
            if resp.status_code in (401, 402, 403):
                log.error("OpenRouter %s: %s %s", model, resp.status_code, resp.text[:200])
                raise LLMError("Нейросеть временно недоступна. Попробуйте позже.")
            if resp.status_code != 200:
                log.warning("OpenRouter %s: %s %s", model, resp.status_code, resp.text[:200])
                continue
            answer = _extract(resp.json())
            if answer:
                log.info("OpenRouter: ответ от %s", model)
                return answer
            log.warning("OpenRouter %s: пустой ответ", model)
        raise LLMError("Нейросеть сейчас перегружена. Попробуйте через пару минут.")


def _is_daily_limit(text: str) -> bool:
    low = text.lower()
    return "per-day" in low or "per day" in low or "free-models-per-day" in low


def _extract(payload: dict) -> str:
    try:
        content = payload["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return ""
    return clean_markdown(content)


def clean_markdown(text: str) -> str:
    # Модели всё равно иногда отвечают markdown-разметкой — в Telegram без parse_mode она торчит звёздочками.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*]\s+", "• ", text, flags=re.M)
    return text.strip()
