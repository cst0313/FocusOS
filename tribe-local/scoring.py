"""
scoring.py – FocusOS Cognitive Load Scoring

Converts raw TRIBE v2 network activations (lang / exec / vis)
into a normalised 0–1 load score and a 0–100 page-level score.

Weights are based on the spec:
    load = 0.45 * lang + 0.35 * exec + 0.20 * vis

Page score = mean of the top-30 % highest-load blocks, scaled to 0-100.
Daily budget = cumulative sum of page scores (managed by the caller).
"""

from __future__ import annotations

from typing import TypedDict


# ── Weighting constants ────────────────────────────────────────────────────────

WEIGHT_LANG = 0.45
WEIGHT_EXEC = 0.35
WEIGHT_VIS  = 0.20


class BlockActivation(TypedDict):
    lang: float  # language-network activation  (0-1)
    exec: float  # executive-control activation (0-1)
    vis:  float  # visual-cortex activation     (0-1)


# ── Per-block scoring ──────────────────────────────────────────────────────────

def block_load_score(lang: float, exec_: float, vis: float) -> float:
    """Return a normalised cognitive load score in [0, 1]."""
    lang  = _clamp(lang)
    exec_ = _clamp(exec_)
    vis   = _clamp(vis)
    return WEIGHT_LANG * lang + WEIGHT_EXEC * exec_ + WEIGHT_VIS * vis


# ── Page-level scoring ─────────────────────────────────────────────────────────

def page_score(block_loads: list[float]) -> float:
    """
    Compute a page-level score (0-100) as the mean of the top-30 % of
    block load values, scaled to 0-100.

    Returns 0.0 if the list is empty.
    """
    if not block_loads:
        return 0.0

    sorted_loads = sorted(block_loads, reverse=True)
    top_n = max(1, int(len(sorted_loads) * 0.30))
    top_mean = sum(sorted_loads[:top_n]) / top_n

    return round(top_mean * 100, 2)


# ── Score categorisation ───────────────────────────────────────────────────────

def score_label(page_sc: float) -> str:
    """Map a page score (0-100) to a human-readable demand label."""
    if page_sc < 30:
        return "low"
    if page_sc < 60:
        return "good"
    return "high"


# ── Private helpers ────────────────────────────────────────────────────────────

def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))
