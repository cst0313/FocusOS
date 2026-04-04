"""
app.py – FocusOS Local Inference Server (Windows-ready)

Runs a FastAPI HTTP server on http://localhost:8787 that:
  • Accepts POST /predict from the browser extension.
  • Calls the TRIBE v2 inference pipeline (or heuristic stub if unavailable).
  • Returns per-block activation scores + a page-level cognitive load score.
  • Accepts POST /session to store reading-session data for the timeline.
  • Serves GET /timeline to return today's session history as JSON.
  • Serves GET /dashboard for the local activity-timeline web dashboard.

Quick start (Windows):
  python -m uvicorn app:app --host 127.0.0.1 --port 8787 --reload

or via the helper batch script:
  start_server.bat
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
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

# ── Session storage ───────────────────────────────────────────────────────────

SESSIONS_FILE = Path(__file__).parent / "sessions.json"


def _load_sessions() -> list[dict]:
    if SESSIONS_FILE.exists():
        try:
            with SESSIONS_FILE.open(encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _append_session(session: dict) -> None:
    sessions = _load_sessions()
    sessions.append(session)
    with SESSIONS_FILE.open("w", encoding="utf-8") as fh:
        json.dump(sessions, fh, ensure_ascii=False, indent=2)


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


class SessionBlock(BaseModel):
    """Per-block brain activation snapshot (no text content)."""
    id:       str   = Field("",  description="Block identifier set by content.js")
    load:     float = Field(0.0, description="Weighted cognitive load score (0–1)")
    lang:     float = Field(0.0, description="Language-network activation (0–1)")
    exec:     float = Field(0.0, description="Executive-control activation (0–1)")
    vis:      float = Field(0.0, description="Visual-cortex activation (0–1)")
    domPath:  str   = Field("",  description="CSS-style DOM path for overlay sync")
    position: int   = Field(0,   description="Block index on the page")
    tagName:  str   = Field("",  description="HTML tag name (p, h2, li, …)")


class SessionRequest(BaseModel):
    """A reading-time chunk reported by the extension every ~30 seconds."""
    page_url:       str   = Field(..., description="URL of the page being read")
    timestamp:      str   = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO-8601 UTC timestamp of this chunk",
    )
    page_score:     float = Field(0.0, description="Page-level cognitive cost (0–100)")
    page_label:     str   = Field("",  description="'low' | 'good' | 'high'")
    active_seconds: float = Field(0.0, description="Seconds of active reading in this chunk")
    lang_mean:      float = Field(0.0, description="Mean language-network activation (0–1)")
    exec_mean:      float = Field(0.0, description="Mean executive-control activation (0–1)")
    vis_mean:       float = Field(0.0, description="Mean visual-cortex activation (0–1)")
    blocks:         List[SessionBlock] = Field(
        default_factory=list,
        description="Per-block brain activation snapshots for this session chunk (no text)",
    )


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


@app.post("/session", tags=["timeline"])
def record_session(req: SessionRequest):
    """
    Store a reading-session chunk sent by the extension every ~30 s.
    Used to build the activity timeline on the dashboard.
    """
    _append_session(req.model_dump())
    return {"ok": True}


@app.get("/timeline", tags=["timeline"])
def get_timeline(date: str | None = None):
    """
    Return session chunks for a given day (defaults to today).
    ``date`` should be an ISO date string such as ``2025-06-01``.
    """
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    all_sessions = _load_sessions()
    day_sessions = [
        s for s in all_sessions
        if s.get("timestamp", "").startswith(target_date)
    ]
    return {"date": target_date, "sessions": day_sessions}


@app.get("/dashboard", response_class=HTMLResponse, tags=["timeline"])
def dashboard():
    """Serve the local activity-timeline web dashboard."""
    return HTMLResponse(content=_DASHBOARD_HTML, status_code=200)


# ── Dashboard HTML ────────────────────────────────────────────────────────────

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>FocusOS – Brain Activity Timeline</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
  <style>
    :root {
      --bg:        #0f1117;
      --surface:   #1a1d27;
      --border:    rgba(255,255,255,0.08);
      --text:      #f0f0f2;
      --muted:     #8b8fa8;
      --green:     #22c55e;
      --amber:     #eab308;
      --red:       #ef4444;
      --blue:      #6366f1;
      --indigo:    #818cf8;
      --purple:    #a78bfa;
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      font-size: 14px;
      line-height: 1.55;
      min-height: 100vh;
      padding: 32px 24px 48px;
    }
    header {
      display: flex;
      align-items: baseline;
      gap: 12px;
      margin-bottom: 28px;
    }
    h1 { font-size: 22px; font-weight: 700; letter-spacing: -0.4px; }
    h1 span { font-size: 26px; }
    .subtitle { font-size: 13px; color: var(--muted); }
    .date-bar {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 24px;
    }
    .date-bar label { font-size: 13px; color: var(--muted); }
    .date-bar input[type=date] {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      color: var(--text);
      font-size: 13px;
      padding: 6px 10px;
      cursor: pointer;
    }
    .date-bar input[type=date]:focus { outline: 2px solid var(--blue); }
    .date-bar button {
      background: var(--blue);
      border: none;
      border-radius: 8px;
      color: #fff;
      cursor: pointer;
      font-size: 13px;
      padding: 6px 14px;
      transition: opacity 0.2s;
    }
    .date-bar button:hover { opacity: 0.85; }
    .cards {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
      gap: 14px;
      margin-bottom: 28px;
    }
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px;
    }
    .card-label {
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.7px;
      color: var(--muted);
      margin-bottom: 6px;
    }
    .card-value {
      font-size: 26px;
      font-weight: 700;
      letter-spacing: -0.5px;
    }
    .card-unit { font-size: 12px; color: var(--muted); margin-left: 2px; }
    .chart-wrap {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 20px 20px 16px;
      margin-bottom: 20px;
    }
    .chart-title {
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.6px;
      color: var(--muted);
      margin-bottom: 14px;
    }
    canvas { max-height: 280px; }
    .empty-state {
      text-align: center;
      color: var(--muted);
      font-size: 13px;
      padding: 48px 0;
    }
    .empty-state .icon { font-size: 36px; margin-bottom: 10px; }
    footer {
      margin-top: 40px;
      font-size: 11px;
      color: var(--muted);
      text-align: center;
    }
    /* ── Timeline playback ──────────────────────────────────────────── */
    .playback-controls {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 14px;
      flex-wrap: wrap;
    }
    .playback-controls button {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      color: var(--text);
      cursor: pointer;
      font-size: 13px;
      padding: 5px 12px;
      transition: background 0.15s;
      white-space: nowrap;
    }
    .playback-controls button:hover { background: rgba(255,255,255,0.07); }
    .playback-controls input[type=range] {
      flex: 1;
      min-width: 120px;
      accent-color: var(--blue);
      cursor: pointer;
    }
    .playback-counter {
      font-size: 12px;
      color: var(--muted);
      white-space: nowrap;
    }
    .session-meta {
      margin-bottom: 14px;
      padding: 10px 14px;
      background: rgba(255,255,255,0.03);
      border: 1px solid var(--border);
      border-radius: 8px;
    }
    .session-meta .meta-url {
      font-size: 13px;
      color: var(--indigo);
      word-break: break-all;
      margin-bottom: 4px;
    }
    .session-meta .meta-detail {
      font-size: 11px;
      color: var(--muted);
    }
    .block-list-header {
      display: grid;
      grid-template-columns: 2fr 1fr 1fr 1fr 1fr;
      gap: 6px;
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.6px;
      color: var(--muted);
      padding: 4px 6px;
      margin-bottom: 4px;
    }
    .block-row {
      display: grid;
      grid-template-columns: 2fr 1fr 1fr 1fr 1fr;
      gap: 6px;
      align-items: center;
      padding: 5px 6px;
      border-radius: 6px;
      margin-bottom: 3px;
      background: rgba(255,255,255,0.02);
    }
    .block-row:hover { background: rgba(255,255,255,0.05); }
    .block-id {
      font-size: 11px;
      color: var(--muted);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .bar-cell { display: flex; align-items: center; gap: 5px; }
    .bar-track {
      flex: 1;
      height: 8px;
      background: rgba(255,255,255,0.07);
      border-radius: 4px;
      overflow: hidden;
    }
    .bar-fill {
      height: 100%;
      border-radius: 4px;
      transition: width 0.25s;
    }
    .bar-val { font-size: 10px; color: var(--muted); width: 30px; text-align: right; }
    .no-blocks {
      font-size: 12px;
      color: var(--muted);
      padding: 12px 6px;
      text-align: center;
    }
  </style>
</head>
<body>
  <header>
    <h1><span>🧠</span> FocusOS</h1>
    <p class="subtitle">Brain Activity Timeline · local-only</p>
  </header>

  <div class="date-bar">
    <label for="date-picker">Date:</label>
    <input type="date" id="date-picker" />
    <button id="load-btn">Load</button>
  </div>

  <div class="cards" id="stat-cards">
    <div class="card">
      <div class="card-label">Total focus time</div>
      <div class="card-value" id="stat-total-min">—<span class="card-unit">min</span></div>
    </div>
    <div class="card">
      <div class="card-label">Weighted budget used</div>
      <div class="card-value" id="stat-budget">—<span class="card-unit">pts</span></div>
    </div>
    <div class="card">
      <div class="card-label">Pages visited</div>
      <div class="card-value" id="stat-pages">—</div>
    </div>
    <div class="card">
      <div class="card-label">Peak load score</div>
      <div class="card-value" id="stat-peak">—</div>
    </div>
  </div>

  <div class="chart-wrap">
    <div class="chart-title">Cognitive Load Over Time (focus-minutes weighted)</div>
    <canvas id="timeline-chart"></canvas>
  </div>

  <div class="chart-wrap">
    <div class="chart-title">Network Activations Over Time</div>
    <canvas id="network-chart"></canvas>
  </div>

  <!-- ── Session Timeline Playback ─────────────────────────────────────────── -->
  <div class="chart-wrap" id="playback-section" style="display:none">
    <div class="chart-title">Session Timeline Playback</div>
    <div class="playback-controls">
      <button id="prev-btn">◀ Prev</button>
      <input type="range" id="session-slider" min="0" max="0" value="0" />
      <button id="next-btn">Next ▶</button>
      <span class="playback-counter" id="session-counter"></span>
    </div>
    <div id="session-meta" class="session-meta" style="display:none">
      <div class="meta-url" id="meta-url"></div>
      <div class="meta-detail" id="meta-detail"></div>
    </div>
    <div id="block-area">
      <div class="block-list-header" id="block-list-header" style="display:none">
        <span>Block / Tag</span>
        <span>Load</span>
        <span>Lang</span>
        <span>Exec</span>
        <span>Vis</span>
      </div>
      <div id="block-list"></div>
    </div>
  </div>

  <div id="empty-msg" class="empty-state" style="display:none">
    <div class="icon">📭</div>
    <p>No data for this date yet.<br>Start browsing with FocusOS tracking enabled.</p>
  </div>

  <footer>
    Data is stored locally on your machine. Not shared with any external service.
    &nbsp;·&nbsp; FocusOS v0.2
  </footer>

  <script>
    const API_BASE = window.location.origin;
    let timelineChart = null;
    let networkChart  = null;
    let _allSessions  = [];
    let _sessionIdx   = 0;

    // ── Initialise date picker to today ──────────────────────────────────────
    const picker = document.getElementById('date-picker');
    const today  = new Date().toISOString().slice(0, 10);
    picker.value = today;

    document.getElementById('load-btn').addEventListener('click', () => {
      fetchAndRender(picker.value);
    });

    fetchAndRender(today);

    // ── Fetch + render ────────────────────────────────────────────────────────
    async function fetchAndRender(date) {
      let data;
      try {
        const resp = await fetch(`${API_BASE}/timeline?date=${date}`);
        data = await resp.json();
      } catch (e) {
        console.error('[FocusOS dashboard] Failed to fetch timeline:', e);
        return;
      }

      const sessions = data.sessions ?? [];
      const empty    = document.getElementById('empty-msg');

      if (sessions.length === 0) {
        empty.style.display = 'block';
        updateStats([], date);
        destroyCharts();
        hidePlayback();
        return;
      }

      empty.style.display = 'none';
      updateStats(sessions, date);
      renderTimelineChart(sessions);
      renderNetworkChart(sessions);
      setupPlayback(sessions);
    }

    // ── Stats cards ───────────────────────────────────────────────────────────
    function updateStats(sessions, date) {
      if (sessions.length === 0) {
        ['stat-total-min', 'stat-budget', 'stat-pages', 'stat-peak'].forEach(id => {
          document.getElementById(id).innerHTML = '—';
        });
        return;
      }

      const totalSec  = sessions.reduce((s, r) => s + (r.active_seconds ?? 0), 0);
      const totalMin  = (totalSec / 60).toFixed(1);
      const budget    = sessions.reduce((s, r) => {
        return s + (r.page_score ?? 0) * (r.active_seconds ?? 0) / 6000;
      }, 0).toFixed(1);
      const pages     = new Set(sessions.map(r => r.page_url)).size;
      const peakScore = Math.max(...sessions.map(r => r.page_score ?? 0)).toFixed(0);

      document.getElementById('stat-total-min').innerHTML = `${totalMin}<span class="card-unit">min</span>`;
      document.getElementById('stat-budget').innerHTML     = `${budget}<span class="card-unit">pts</span>`;
      document.getElementById('stat-pages').innerHTML      = pages;
      document.getElementById('stat-peak').innerHTML       = peakScore;
    }

    // ── Timeline chart (total load × time) ───────────────────────────────────
    function renderTimelineChart(sessions) {
      const ctx = document.getElementById('timeline-chart').getContext('2d');

      // Group into 5-minute buckets, accumulate weighted focus-minutes.
      const buckets = buildBuckets(sessions, r => {
        return (r.page_score ?? 0) * (r.active_seconds ?? 0) / 6000;
      });

      if (timelineChart) timelineChart.destroy();

      timelineChart = new Chart(ctx, {
        type: 'bar',
        data: {
          labels: buckets.labels,
          datasets: [{
            label: 'Weighted focus-minutes',
            data: buckets.values,
            backgroundColor: 'rgba(99, 102, 241, 0.45)',
            borderColor: 'rgba(99, 102, 241, 0.9)',
            borderWidth: 1,
            borderRadius: 3,
          }],
        },
        options: chartOptions('Weighted focus-min per 5-min slot'),
      });
    }

    // ── Network activation chart ──────────────────────────────────────────────
    function renderNetworkChart(sessions) {
      const ctx = document.getElementById('network-chart').getContext('2d');

      const langB = buildBuckets(sessions, r => r.lang_mean ?? 0, 'mean');
      const execB = buildBuckets(sessions, r => r.exec_mean ?? 0, 'mean');
      const visB  = buildBuckets(sessions, r => r.vis_mean  ?? 0, 'mean');

      if (networkChart) networkChart.destroy();

      networkChart = new Chart(ctx, {
        type: 'line',
        data: {
          labels: langB.labels,
          datasets: [
            lineDataset('Language', langB.values, '#22c55e'),
            lineDataset('Executive', execB.values, '#6366f1'),
            lineDataset('Visual',    visB.values,  '#eab308'),
          ],
        },
        options: chartOptions('Mean network activation (0–1)'),
      });
    }

    // ── Session timeline playback ─────────────────────────────────────────────
    function setupPlayback(sessions) {
      _allSessions = sessions.slice().sort((a, b) =>
        new Date(a.timestamp) - new Date(b.timestamp)
      );
      _sessionIdx = 0;

      const section = document.getElementById('playback-section');
      const slider  = document.getElementById('session-slider');
      section.style.display = 'block';
      slider.min   = 0;
      slider.max   = _allSessions.length - 1;
      slider.value = 0;

      slider.oninput = () => {
        _sessionIdx = parseInt(slider.value, 10);
        renderSessionDetail(_sessionIdx);
      };

      document.getElementById('prev-btn').onclick = () => {
        if (_sessionIdx > 0) {
          _sessionIdx--;
          slider.value = _sessionIdx;
          renderSessionDetail(_sessionIdx);
        }
      };

      document.getElementById('next-btn').onclick = () => {
        if (_sessionIdx < _allSessions.length - 1) {
          _sessionIdx++;
          slider.value = _sessionIdx;
          renderSessionDetail(_sessionIdx);
        }
      };

      renderSessionDetail(0);
    }

    function hidePlayback() {
      document.getElementById('playback-section').style.display = 'none';
    }

    function renderSessionDetail(idx) {
      const session = _allSessions[idx];
      if (!session) return;

      // counter
      document.getElementById('session-counter').textContent =
        `${idx + 1} / ${_allSessions.length}`;

      // meta
      const metaEl    = document.getElementById('session-meta');
      const urlEl     = document.getElementById('meta-url');
      const detailEl  = document.getElementById('meta-detail');
      metaEl.style.display = 'block';
      urlEl.textContent    = session.page_url ?? '';
      const ts = session.timestamp
        ? new Date(session.timestamp).toLocaleString()
        : '';
      const secs  = (session.active_seconds ?? 0).toFixed(0);
      const score = (session.page_score ?? 0).toFixed(1);
      detailEl.textContent =
        `${ts}  ·  ${secs}s active  ·  load score: ${score}  ·  label: ${session.page_label ?? '—'}`;

      // blocks
      const blocks = session.blocks ?? [];
      const header = document.getElementById('block-list-header');
      const list   = document.getElementById('block-list');
      list.innerHTML = '';

      if (blocks.length === 0) {
        header.style.display = 'none';
        list.innerHTML = '<div class="no-blocks">No per-block data for this session.</div>';
        return;
      }

      header.style.display = 'grid';
      blocks.forEach(b => {
        const row = document.createElement('div');
        row.className = 'block-row';

        const labelText = b.tagName
          ? `${b.tagName}#${b.position ?? 0}`
          : (b.id ?? '');

        row.innerHTML = `
          <span class="block-id" title="${b.id ?? ''}">${labelText}</span>
          ${barCell(b.load ?? 0, '#6366f1')}
          ${barCell(b.lang ?? 0, '#22c55e')}
          ${barCell(b.exec ?? 0, '#818cf8')}
          ${barCell(b.vis  ?? 0, '#eab308')}
        `;
        list.appendChild(row);
      });
    }

    function barCell(value, color) {
      const pct = Math.min(Math.max(value, 0), 1) * 100;
      return `
        <div class="bar-cell">
          <div class="bar-track">
            <div class="bar-fill" style="width:${pct.toFixed(1)}%;background:${color}"></div>
          </div>
          <span class="bar-val">${pct.toFixed(0)}%</span>
        </div>`;
    }

    // ── Helpers ───────────────────────────────────────────────────────────────
    function buildBuckets(sessions, valueFn, mode = 'sum') {
      const BUCKET_MIN = 5;
      const map = {};

      sessions.forEach(r => {
        const t    = new Date(r.timestamp);
        const slot = Math.floor((t.getHours() * 60 + t.getMinutes()) / BUCKET_MIN) * BUCKET_MIN;
        const hh   = String(Math.floor(slot / 60)).padStart(2, '0');
        const mm   = String(slot % 60).padStart(2, '0');
        const key  = `${hh}:${mm}`;
        const val  = valueFn(r);

        if (!map[key]) map[key] = { sum: 0, count: 0 };
        map[key].sum   += val;
        map[key].count += 1;
      });

      const keys   = Object.keys(map).sort();
      const values = keys.map(k => mode === 'mean'
        ? +(map[k].sum / map[k].count).toFixed(3)
        : +map[k].sum.toFixed(3)
      );

      return { labels: keys, values };
    }

    function lineDataset(label, data, color) {
      return {
        label,
        data,
        borderColor: color,
        backgroundColor: 'transparent',
        pointRadius: 3,
        pointBackgroundColor: color,
        tension: 0.35,
        borderWidth: 2,
      };
    }

    function chartOptions(yLabel) {
      return {
        responsive: true,
        maintainAspectRatio: true,
        plugins: {
          legend: { labels: { color: '#8b8fa8', font: { size: 12 } } },
          tooltip: { mode: 'index', intersect: false },
        },
        scales: {
          x: {
            ticks: { color: '#8b8fa8', font: { size: 11 }, maxRotation: 0 },
            grid:  { color: 'rgba(255,255,255,0.05)' },
          },
          y: {
            title: { display: true, text: yLabel, color: '#5a5d73', font: { size: 11 } },
            ticks: { color: '#8b8fa8', font: { size: 11 } },
            grid:  { color: 'rgba(255,255,255,0.05)' },
            beginAtZero: true,
          },
        },
      };
    }

    function destroyCharts() {
      if (timelineChart) { timelineChart.destroy(); timelineChart = null; }
      if (networkChart)  { networkChart.destroy();  networkChart  = null; }
    }
  </script>
</body>
</html>
"""

