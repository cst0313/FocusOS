"""
app.py – FocusOS Local Inference Server (Windows-ready)

Runs a FastAPI HTTP server on http://localhost:8787 that:
  • Accepts POST /predict from the browser extension.
  • Calls the TRIBE v2 inference pipeline (or heuristic stub if unavailable).
  • Returns per-block activation scores + a page-level cognitive load score.

Quick start (Windows):
  python -m uvicorn app:app --host 127.0.0.1 --port 8787 --reload

or via the helper batch script:
  start_server.bat
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from model import model_status, predict_blocks
from scoring import page_score, score_label

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="FocusOS Local Inference Server",
    description=(
        "Local FastAPI service that powers the FocusOS browser extension.\n"
        "Accepts page text blocks and returns cognitive-load scores derived "
        "from TRIBE v2 (or a heuristic stub when the model is unavailable)."
    ),
    version="0.1.0",
)

# Allow requests from the browser extension (chrome-extension:// scheme) and
# localhost during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # extension origins are opaque; wildcard is safe for localhost
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

# ── Request / Response schemas ────────────────────────────────────────────────


class TextBlock(BaseModel):
    id:       str   = Field(..., description="Unique block identifier (set by content.js)")
    text:     str   = Field(..., description="Extracted readable text (max ~600 chars)")
    domPath:  str   = Field("", description="CSS-style DOM path for overlay sync")
    position: int   = Field(0,  description="Block index on the page")
    tagName:  str   = Field("", description="HTML tag name (p, h2, li, …)")


class PredictRequest(BaseModel):
    page_url:  str          = Field(..., description="Full URL of the analysed page")
    timestamp: str          = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    blocks:    List[TextBlock]


class ScoredBlock(BaseModel):
    id:       str
    load:     float = Field(..., description="Weighted cognitive load score (0–1)")
    lang:     float = Field(..., description="Language-network activation (0–1)")
    exec:     float = Field(..., description="Executive-control activation (0–1)")
    vis:      float = Field(..., description="Visual-cortex activation (0–1)")
    domPath:  str   = ""
    position: int   = 0
    tagName:  str   = ""
    text:     str   = ""


class PredictResponse(BaseModel):
    page_url:   str
    page_score: float = Field(..., description="Page-level cognitive cost (0–100)")
    page_label: str   = Field(..., description="'low' | 'good' | 'high'")
    blocks:     List[ScoredBlock]
    model_mode: str   = Field(..., description="'tribe_v2' or 'heuristic_stub'")
    timestamp:  str


# ── Routes ────────────────────────────────────────────────────────────────────


@app.get("/", tags=["health"])
def root():
    """Health check + model status."""
    return {"status": "ok", "server": "FocusOS", **model_status()}


@app.get("/status", tags=["health"])
def status():
    """Model status and server info."""
    return {"status": "ok", **model_status()}


@app.post("/predict", response_model=PredictResponse, tags=["inference"])
def predict(req: PredictRequest):
    """
    Accept a list of text blocks extracted from a web page and return
    per-block cognitive load scores + an aggregated page score.

    The extension sends this request automatically when tracking is ON.
    """
    if not req.blocks:
        raise HTTPException(status_code=422, detail="Request must include at least one block.")

    raw_blocks = [b.model_dump() for b in req.blocks]

    try:
        scored, is_real = predict_blocks(raw_blocks)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Inference error: {exc}") from exc

    block_loads = [b["load"] for b in scored]
    pg_score    = page_score(block_loads)
    pg_label    = score_label(pg_score)

    return PredictResponse(
        page_url   = req.page_url,
        page_score = pg_score,
        page_label = pg_label,
        blocks     = [ScoredBlock(**b) for b in scored],
        model_mode = "tribe_v2" if is_real else "heuristic_stub",
        timestamp  = datetime.now(timezone.utc).isoformat(),
    )
