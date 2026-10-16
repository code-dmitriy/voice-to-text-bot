import httpx
import pytest

from bot.db import Database
from bot.handlers import split_text
from services.llm import LLMError, OpenRouterText, clean_markdown


def test_split_text_by_sentences():
    text = ("Первое предложение. " * 300).strip()
    parts = split_text(text, limit=1000)
    assert all(len(p) <= 1000 for p in parts)
    assert all(p.endswith(".") for p in parts)
    assert " ".join(parts) == text


def test_clean_markdown():
    assert clean_markdown("**Итог**\n- раз\n* два\n## Заголовок") == "Итог\n• раз\n• два\nЗаголовок"


async def test_usage_limits(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    await db.touch_user(1, "u", "U")
    await db.add_seconds(1, 90)
    await db.add_seconds(1, 30)
    await db.add_llm_request(1)
    assert await db.seconds_used_today(1) == 120
    assert await db.llm_used_today(1) == 1
    assert await db.total_seconds_this_month() == 120
    tid = await db.save_transcript(1, "текст", 120)
    await db.save_llm_result(tid, "summary", "суть")
    row = await db.get_transcript(tid)
    assert row["summary"] == "суть" and row["tasks"] is None
    stats = await db.stats()
    assert stats["users"] == 1 and stats["files_today"] == 2
    await db.close()


async def test_llm_falls_back_to_next_model_and_detects_daily_limit():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        model = json.loads(request.content)["model"]
        seen.append(model)
        if model == "a":
            return httpx.Response(429, json={"error": {"message": "temporarily rate-limited upstream"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "- пункт"}}]})

    s = OpenRouterText("k", ("a", "b"), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await s.run("system", "текст") == "• пункт"
    assert seen == ["a", "b"]

    daily = OpenRouterText("k", ("a",), http_client=httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(429, json={"error": {"message": "Rate limit exceeded: free-models-per-day"}}))))
    with pytest.raises(LLMError) as err:
        await daily.run("system", "текст")
    assert err.value.daily_limit
