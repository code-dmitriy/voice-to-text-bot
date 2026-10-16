# Голосовые → текст (@sutgolosa_bot)

Telegram-бот: присылаешь или пересылаешь голосовое, кружок или аудиофайл — получаешь текст с пунктуацией и абзацами.
Под текстом кнопки «📝 Кратко» (суть в пунктах) и «✅ Задачи» (дела, сроки, договорённости).

## Как устроено

```
Telegram ──► bot/handlers.py ──► services/audio.py ──────► services/gigachat.py ──► текст
  голосовое     лимиты, очередь    любой формат → PCM 16 кГц,   файл в /files, GigaChat-2-Pro
  кружок        на человека        нарезка по 5 мин по тихим    с attachment, файл удаляется
  аудиофайл                        местам → ogg/opus
                                   кнопки ──► services/llm.py ──► GigaChat-2-Pro (запасной — OpenRouter)
```

- **Распознавание** — GigaChat-2-Pro понимает вложенное аудио (у GigaChat-2/Lite «Model does not support audio»).
  Запись 4 мин: ~20 с, ~600 токенов на минуту звука. Если у Pro кончатся токены (402), берётся GigaChat-2-Max.
- **Тариф Freemium физлица** — 1 одновременный запрос, поэтому все обращения к GigaChat идут через очередь
  (семафор в `GigaChat`). Карта не привязана: когда токены кончатся, API ответит 402, денег не спишется.
- **Декодирование** — PyAV (ffmpeg внутри пакета), ставить ffmpeg в систему не нужно.
- **Сертификат Сбера** — `certs/russian_trusted_root_ca.cer` (корневой УЦ Минцифры), подключается только для запросов к Сберу.
- **Лимиты** — минут в сутки на человека, максимум на одну запись, общий потолок в месяц, нажатий кнопок в сутки. Сутки по МСК.
- **Приватность** — аудиофайл удаляется из GigaChat сразу после ответа; текст хранится `TRANSCRIPT_TTL_DAYS` дней
  только ради кнопок. Текст в лог не пишется.
- **Админ** (`ADMIN_IDS`) — без лимитов, команда `/stats`.

## Запуск на ПК

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python check_keys.py путь/к/аудио.ogg   # проверить ключи: расшифровка + «Кратко» + «Задачи»
.venv/Scripts/python -m bot
```

Настройки — в `.env` (образец `.env.example`). Один токен — один запущенный бот: перед запуском на сервере останови локальный.

Тесты: `.venv/Scripts/python -m pip install -r requirements-dev.txt && .venv/Scripts/python -m pytest -q`

## VPS (Ubuntu/Debian)

Бот работает через long polling: входящий порт не нужен. Служба `voice-bot.service` с потолком памяти 350 МБ.

```bash
sudo useradd --system --create-home --home-dir /opt/voice-bot --shell /usr/sbin/nologin voicebot
# распаковать проект в /opt/voice-bot, затем:
sudo chown -R voicebot:voicebot /opt/voice-bot && sudo chmod 600 /opt/voice-bot/.env
cd /opt/voice-bot && sudo -u voicebot python3 -m venv .venv && sudo -u voicebot .venv/bin/pip install -r requirements.txt
sudo cp voice-bot.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now voice-bot
journalctl -u voice-bot -f
```
