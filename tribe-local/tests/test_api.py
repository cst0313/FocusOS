"""
tests/test_api.py – Integration tests for the FastAPI endpoints.

These tests use httpx's ASGI transport to call the app directly without
needing a running server process.
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["FOCUSOS_STUB"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timezone

# Reload modules so the env-var override takes effect.
import importlib
import model as _m
import app as _a

importlib.reload(_m)
importlib.reload(_a)

from app import app


BLOCKS_PAYLOAD = {
    "page_url": "https://example.com/article",
    "timestamp": "2025-01-01T12:00:00",
    "blocks": [
        {
            "id": "focusos-block-0",
            "text": (
                "Quantum mechanics is the branch of physics relating to the very small. "
                "It results in what may appear to be some very strange conclusions about the "
                "physical world at the subatomic level."
            ),
            "domPath": "article > p",
            "position": 0,
            "tagName": "p",
        },
        {
            "id": "focusos-block-1",
            "text": "Sign up for our newsletter to get the latest updates.",
            "domPath": "footer > p",
            "position": 1,
            "tagName": "p",
        },
    ],
}

SESSION_PAYLOAD = {
    "page_url": "https://example.com/article",
    "timestamp": "2025-01-01T12:00:30Z",
    "page_score": 55.0,
    "page_label": "good",
    "active_seconds": 30.0,
    "lang_mean": 0.7,
    "exec_mean": 0.5,
    "vis_mean": 0.2,
    "blocks": [
        {
            "id": "focusos-block-0",
            "load": 0.72,
            "lang": 0.80,
            "exec": 0.60,
            "vis": 0.45,
            "domPath": "article > p",
            "position": 0,
            "tagName": "p",
        },
        {
            "id": "focusos-block-1",
            "load": 0.30,
            "lang": 0.25,
            "exec": 0.15,
            "vis": 0.10,
            "domPath": "footer > p",
            "position": 1,
            "tagName": "p",
        },
    ],
}


@pytest.mark.asyncio
async def test_health_check():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_predict_returns_200():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/predict", json=BLOCKS_PAYLOAD)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_predict_response_schema():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/predict", json=BLOCKS_PAYLOAD)
    body = resp.json()

    assert "page_score" in body
    assert "page_label" in body
    assert "blocks" in body
    assert "model_mode" in body
    assert body["model_mode"] == "heuristic_stub"


@pytest.mark.asyncio
async def test_predict_block_count():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/predict", json=BLOCKS_PAYLOAD)
    body = resp.json()
    assert len(body["blocks"]) == len(BLOCKS_PAYLOAD["blocks"])


@pytest.mark.asyncio
async def test_predict_block_score_fields():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/predict", json=BLOCKS_PAYLOAD)
    body = resp.json()
    for block in body["blocks"]:
        assert "load" in block
        assert "lang" in block
        assert "exec" in block
        assert "vis"  in block
        assert 0.0 <= block["load"] <= 1.0


@pytest.mark.asyncio
async def test_predict_page_label_values():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/predict", json=BLOCKS_PAYLOAD)
    body = resp.json()
    assert body["page_label"] in ("low", "good", "high")


@pytest.mark.asyncio
async def test_predict_empty_blocks_returns_422():
    payload = {**BLOCKS_PAYLOAD, "blocks": []}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/predict", json=payload)
    assert resp.status_code == 422


# ── /session and /timeline tests ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_record_returns_ok(tmp_path, monkeypatch):
    """POST /session stores a chunk and returns ok."""
    monkeypatch.setattr(_a, "SESSIONS_FILE", tmp_path / "sessions.json")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/session", json=SESSION_PAYLOAD)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_timeline_returns_today_sessions(tmp_path, monkeypatch):
    """GET /timeline returns only sessions for the requested date."""
    monkeypatch.setattr(_a, "SESSIONS_FILE", tmp_path / "sessions.json")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Record a session
        await client.post("/session", json=SESSION_PAYLOAD)
        # Fetch timeline for the same date
        resp = await client.get("/timeline?date=2025-01-01")
    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2025-01-01"
    assert len(body["sessions"]) == 1
    assert body["sessions"][0]["page_url"] == SESSION_PAYLOAD["page_url"]


@pytest.mark.asyncio
async def test_timeline_empty_for_other_date(tmp_path, monkeypatch):
    """GET /timeline returns no sessions for a date with no data."""
    monkeypatch.setattr(_a, "SESSIONS_FILE", tmp_path / "sessions.json")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/session", json=SESSION_PAYLOAD)
        resp = await client.get("/timeline?date=1999-01-01")
    assert resp.status_code == 200
    assert resp.json()["sessions"] == []


@pytest.mark.asyncio
async def test_dashboard_returns_html():
    """GET /dashboard returns a valid HTML page."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/dashboard")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "FocusOS" in resp.text
    assert "chart.js" in resp.text.lower()


# ── Per-block activation tests ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_with_blocks_stored(tmp_path, monkeypatch):
    """POST /session persists per-block activations."""
    monkeypatch.setattr(_a, "SESSIONS_FILE", tmp_path / "sessions.json")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/session", json=SESSION_PAYLOAD)
    assert resp.status_code == 200
    # Verify blocks are persisted
    sessions = _a._load_sessions()
    assert len(sessions) == 1
    assert "blocks" in sessions[0]
    assert len(sessions[0]["blocks"]) == 2


@pytest.mark.asyncio
async def test_session_blocks_have_correct_fields(tmp_path, monkeypatch):
    """Stored blocks contain id, load, lang, exec, vis, domPath, position, tagName."""
    monkeypatch.setattr(_a, "SESSIONS_FILE", tmp_path / "sessions.json")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/session", json=SESSION_PAYLOAD)
    block = _a._load_sessions()[0]["blocks"][0]
    for field in ("id", "load", "lang", "exec", "vis", "domPath", "position", "tagName"):
        assert field in block, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_session_blocks_no_text_field(tmp_path, monkeypatch):
    """Blocks stored in sessions must not contain a 'text' field."""
    monkeypatch.setattr(_a, "SESSIONS_FILE", tmp_path / "sessions.json")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/session", json=SESSION_PAYLOAD)
    for block in _a._load_sessions()[0]["blocks"]:
        assert "text" not in block


@pytest.mark.asyncio
async def test_session_blocks_values_in_range(tmp_path, monkeypatch):
    """Stored block activation values should remain in the expected range."""
    monkeypatch.setattr(_a, "SESSIONS_FILE", tmp_path / "sessions.json")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/session", json=SESSION_PAYLOAD)
    for block in _a._load_sessions()[0]["blocks"]:
        for field in ("load", "lang", "exec", "vis"):
            assert 0.0 <= block[field] <= 1.0, f"{field}={block[field]} out of range"


@pytest.mark.asyncio
async def test_session_without_blocks_defaults_to_empty(tmp_path, monkeypatch):
    """POST /session without blocks field stores an empty blocks list."""
    monkeypatch.setattr(_a, "SESSIONS_FILE", tmp_path / "sessions.json")
    payload_no_blocks = {k: v for k, v in SESSION_PAYLOAD.items() if k != "blocks"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/session", json=payload_no_blocks)
    assert resp.status_code == 200
    sessions = _a._load_sessions()
    assert sessions[0]["blocks"] == []


@pytest.mark.asyncio
async def test_timeline_sessions_include_blocks(tmp_path, monkeypatch):
    """GET /timeline returns sessions that include the blocks array."""
    monkeypatch.setattr(_a, "SESSIONS_FILE", tmp_path / "sessions.json")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/session", json=SESSION_PAYLOAD)
        resp = await client.get("/timeline?date=2025-01-01")
    body = resp.json()
    session = body["sessions"][0]
    assert "blocks" in session
    assert len(session["blocks"]) == len(SESSION_PAYLOAD["blocks"])


@pytest.mark.asyncio
async def test_dashboard_has_playback_section():
    """GET /dashboard HTML includes the session timeline playback section."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/dashboard")
    assert "playback-section" in resp.text
    assert "session-slider" in resp.text
    assert "block-list" in resp.text

