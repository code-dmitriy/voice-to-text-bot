import json
import time

import httpx
import pytest

from services.gigachat import API_URL, OAUTH_URL, GigaChat, GigaChatError, QuotaExhausted
from services.llm import LLMError, OpenRouterText, Summarizer


def _token():
    return httpx.Response(200, json={"access_token": "T", "expires_at": int((time.time() + 1800) * 1000)})


def _chat(text):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}], "usage": {"total_tokens": 10}})


def _giga(handler, **kw) -> GigaChat:
    return GigaChat("KEY", "GIGACHAT_API_PERS", audio_models=kw.get("audio", ("Pro", "Max")), text_model="Lite",
                    http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_transcribe_uploads_asks_and_deletes():
    log = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == OAUTH_URL:
            log.append("oauth")
            assert request.headers["Authorization"] == "Basic KEY"
            return _token()
        assert request.headers["Authorization"] == "Bearer T"
        if url == API_URL + "/files":
            log.append("upload")
            assert b"audio/ogg" in request.content
            return httpx.Response(200, json={"id": "F1"})
        if url == API_URL + "/chat/completions":
            body = json.loads(request.content)
            log.append(("chat", body["model"], body["messages"][1]["attachments"]))
            return _chat("Привет. Как дела?")
        if url == API_URL + "/files/F1/delete":
            log.append("delete")
            return httpx.Response(200, json={"deleted": True})
        raise AssertionError(url)

    g = _giga(handler)
    assert await g.transcribe(b"OggS...") == "Привет. Как дела?"
    assert log == ["oauth", "upload", ("chat", "Pro", ["F1"]), "delete"]


async def test_transcribe_falls_back_to_next_model_on_402_and_still_deletes():
    models, deleted = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == OAUTH_URL:
            return _token()
        if url.endswith("/files"):
            return httpx.Response(200, json={"id": "F"})
        if url.endswith("/delete"):
            deleted.append(url)
            return httpx.Response(200, json={})
        model = json.loads(request.content)["model"]
        models.append(model)
        return httpx.Response(402, json={"message": "Payment required"}) if model == "Pro" else _chat("ок")

    assert await _giga(handler).transcribe(b"x") == "ок"
    assert models == ["Pro", "Max"] and len(deleted) == 1

    def all_402(request):
        url = str(request.url)
        if url == OAUTH_URL:
            return _token()
        if url.endswith("/files"):
            return httpx.Response(200, json={"id": "F"})
        if url.endswith("/delete"):
            deleted.append(url)
            return httpx.Response(200, json={})
        return httpx.Response(402, json={})

    with pytest.raises(QuotaExhausted):
        await _giga(all_402).transcribe(b"x")
    assert len(deleted) == 2


async def test_401_refreshes_token():
    tokens = iter(["OLD", "NEW"])

    def handler(request):
        if str(request.url) == OAUTH_URL:
            return httpx.Response(200, json={"access_token": next(tokens),
                                             "expires_at": int((time.time() + 1800) * 1000)})
        if request.headers["Authorization"] == "Bearer OLD":
            return httpx.Response(401, json={})
        return _chat("ответ")

    assert await _giga(handler).complete("sys", "user") == "ответ"


async def test_summarizer_uses_gigachat_then_openrouter():
    def giga_ok(request):
        return _token() if str(request.url) == OAUTH_URL else _chat("**Итог**\n- пункт")

    assert await Summarizer(_giga(giga_ok)).run("summary", "текст") == "Итог\n• пункт"

    def giga_down(request):
        return _token() if str(request.url) == OAUTH_URL else httpx.Response(400, json={})

    with pytest.raises(LLMError):
        await Summarizer(_giga(giga_down)).run("summary", "текст")

    router = OpenRouterText("k", ("m",), http_client=httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "запасной"}}]}))))
    assert await Summarizer(_giga(giga_down), router).run("tasks", "текст") == "запасной"
