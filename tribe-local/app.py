"""
app.py – FocusOS Local Inference Server (Windows-ready)

Runs a FastAPI HTTP server on http://localhost:8787 that:
  • Accepts POST /predict from the browser extension.
  • Calls the TRIBE v2 inference pipeline (or heuristic stub if unavailable).
  • Returns per-block activation scores + a page-level cognitive load score.
  • Accepts POST /log_session to record reading sessions for the dashboard.
  • Serves GET /dashboard – an HTML activity timeline for today's sessions.

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
from fastapi.responses import HTMLResponse
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
    version="0.2.0",
)

# Allow requests from the browser extension (chrome-extension:// scheme) and
# localhost during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # extension origins are opaque; wildcard is safe for localhost
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

# ── In-memory session log (cleared on server restart) ────────────────────────

_session_log: List[dict] = []

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


class SessionLogEntry(BaseModel):
    url:                str   = Field(..., description="Page URL")
    page_score:         float = Field(..., description="Cognitive load score (0–100)")
    elapsed_minutes:    float = Field(..., description="Active reading time in minutes")
    focus_contribution: float = Field(..., description="Focus-minutes contributed to budget")
    timestamp:          str   = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


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


@app.post("/log_session", tags=["tracking"])
def log_session(entry: SessionLogEntry):
    """
    Record a completed reading session sent by the browser extension.
    Used to populate the activity timeline dashboard.
    """
    _session_log.append(entry.model_dump())
    return {"ok": True, "total_sessions": len(_session_log)}


@app.get("/dashboard", response_class=HTMLResponse, tags=["dashboard"])
def dashboard():
    """
    Serve an HTML activity timeline showing today's reading sessions.
    Open http://127.0.0.1:8787/dashboard in your browser.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_sessions = [
        s for s in _session_log
        if s.get("timestamp", "").startswith(today)
    ]

    rows_html = ""
    if today_sessions:
        for s in today_sessions:
            ts = s.get("timestamp", "")[:19].replace("T", " ")
            url = s.get("url", "")
            short_url = url[:60] + ("…" if len(url) > 60 else "")
            score = s.get("page_score", 0)
            mins = s.get("elapsed_minutes", 0)
            focus = s.get("focus_contribution", 0)
            score_cls = "high" if score >= 60 else ("medium" if score >= 30 else "low")
            rows_html += f"""
            <tr>
              <td class="ts">{ts}</td>
              <td class="url" title="{url}">{short_url}</td>
              <td class="num score {score_cls}">{score:.0f}</td>
              <td class="num">{mins:.1f}</td>
              <td class="num focus">{focus:.2f}</td>
            </tr>"""
    else:
        rows_html = '<tr><td colspan="5" class="empty">No sessions recorded today. Make sure the extension is running and tracking is ON.</td></tr>'

    total_focus = sum(s.get("focus_contribution", 0) for s in today_sessions)
    budget_pct = min(round(total_focus), 100)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>FocusOS – Activity Timeline</title>
  <style>
    :root {{
      --bg: #0f1117; --card: rgba(255,255,255,0.05); --border: rgba(255,255,255,0.09);
      --text: #f0f0f2; --muted: #8b8fa8; --green: #22c55e; --amber: #eab308; --red: #ef4444;
      --blue: #6366f1;
    }}
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 14px; padding: 32px 24px; }}
    h1 {{ font-size: 22px; font-weight: 700; margin-bottom: 4px; }}
    .subtitle {{ color: var(--muted); font-size: 13px; margin-bottom: 24px; }}
    .budget-wrap {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px 20px; margin-bottom: 20px; display: flex; align-items: center; gap: 16px; }}
    .budget-label {{ font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.7px; color: var(--muted); min-width: 120px; }}
    .bar-wrap {{ flex: 1; height: 10px; background: rgba(255,255,255,0.07); border-radius: 5px; overflow: hidden; }}
    .bar {{ height: 100%; border-radius: 5px; background: linear-gradient(90deg, var(--green) 0%, var(--amber) 60%, var(--red) 100%); transition: width 0.5s ease; }}
    .bar-pct {{ font-size: 18px; font-weight: 700; min-width: 48px; text-align: right; }}
    .bar-detail {{ font-size: 12px; color: var(--muted); min-width: 120px; text-align: right; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--card); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }}
    thead tr {{ background: rgba(255,255,255,0.04); }}
    th {{ padding: 10px 14px; text-align: left; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.6px; color: var(--muted); border-bottom: 1px solid var(--border); }}
    td {{ padding: 10px 14px; font-size: 13px; border-bottom: 1px solid rgba(255,255,255,0.04); vertical-align: top; }}
    tr:last-child td {{ border-bottom: none; }}
    .ts {{ color: var(--muted); font-size: 12px; white-space: nowrap; }}
    .url {{ max-width: 320px; color: var(--muted); word-break: break-all; }}
    .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .score.low {{ color: var(--green); }}
    .score.medium {{ color: var(--amber); }}
    .score.high {{ color: var(--red); }}
    .focus {{ color: var(--blue); font-weight: 600; }}
    .empty {{ text-align: center; color: var(--muted); font-style: italic; padding: 24px; }}
    .refresh {{ margin-top: 16px; font-size: 12px; color: var(--muted); }}
    a {{ color: var(--blue); }}
  </style>
</head>
<body>
  <h1>🧠 FocusOS Activity Timeline</h1>
  <p class="subtitle">Today's reading sessions · {today} · All data is local and private</p>

  <div class="budget-wrap">
    <span class="budget-label">Daily Budget</span>
    <div class="bar-wrap"><div class="bar" style="width:{budget_pct}%"></div></div>
    <span class="bar-pct">{budget_pct}%</span>
    <span class="bar-detail">{total_focus:.1f} / 100 focus-min</span>
  </div>

  <table>
    <thead>
      <tr>
        <th>Time</th>
        <th>Page</th>
        <th style="text-align:right">Load Score</th>
        <th style="text-align:right">Read (min)</th>
        <th style="text-align:right">Focus-min</th>
      </tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>

  <p class="refresh">Auto-refresh: <a href="/dashboard">reload page</a> · Sessions: {len(today_sessions)} today</p>
</body>
</html>"""
    return HTMLResponse(content=html)

