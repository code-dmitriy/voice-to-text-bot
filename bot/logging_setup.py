"""Логирование с маскировкой секретов: токен и ключ не попадут в вывод, даже если окажутся в тексте исключения."""
from __future__ import annotations

import logging


class SecretRedactingFilter(logging.Filter):
    def __init__(self, secrets: tuple[str, ...]):
        super().__init__()
        self._secrets = tuple(s for s in secrets if len(s) >= 8)

    def _redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        message = record.getMessage()
        redacted = self._redact(message)
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self._redact(record.exc_text)
        if redacted != message:
            record.msg, record.args = redacted, None
        return True


def setup_logging(level: str, secrets: tuple[str, ...]) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(SecretRedactingFilter(secrets))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Сетевые библиотеки на INFO пишут каждый URL запроса — это шум, а у Telegram в URL лежит токен.
    for noisy in ("httpx", "httpx2", "httpcore", "aiohttp.access", "apscheduler.executors.default"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
