"""
tests/test_api.py – Integration tests for the FastAPI /predict endpoint.

These tests use httpx's ASGI transport to call the app directly without
needing a running server process.
"""

import os
import sys

os.environ["FOCUSOS_STUB"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from httpx import AsyncClient, ASGITransport

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
