"""
model.py – TRIBE v2 model interface for FocusOS

Provides a single public function `predict_blocks(blocks)` that accepts a list
of text-block dicts and returns per-block activation scores (lang / exec / vis).

Two modes are supported:
  1. TRIBE v2 (real)  — prefers a local checkpoint (`best.ckpt`) when present,
                         otherwise falls back to `facebook/tribev2`.
  2. Stub / heuristic — activated automatically when the TRIBE v2 package is
                         unavailable.  Uses lightweight text statistics
                         (sentence complexity, vocab richness, word length) to
                         produce plausible demo activations.  Clearly labelled
                         as non-model output in the API response.

Set FOCUSOS_STUB=1 to force stub mode and FOCUSOS_CKPT to override checkpoint
path discovery.
"""

from __future__ import annotations

import math
import os
import re
import inspect
from pathlib import Path
from typing import Any

# ── Try importing TRIBE v2 ────────────────────────────────────────────────────

_TRIBE_AVAILABLE = False
_STUB_FORCED     = os.getenv("FOCUSOS_STUB", "").strip() in ("1", "true", "yes")
_MODEL_SOURCE    = "heuristic_stub"
DEFAULT_CHECKPOINT_NAME = "best.ckpt"

if not _STUB_FORCED:
    try:
        # Official package: https://github.com/facebookresearch/tribev2
        import tribev2  # type: ignore  # noqa: F401
        _TRIBE_AVAILABLE = True
    except ImportError:
        pass

# ── Model singleton ───────────────────────────────────────────────────────────

_model: Any = None


def _resolve_local_ckpt() -> Path | None:
    """Resolve local checkpoint path if available."""
    override = os.getenv("FOCUSOS_CKPT", "").strip()
    if override:
        ckpt = Path(override).expanduser().resolve()
        return ckpt if ckpt.is_file() else None

    repo_ckpt = (Path(__file__).resolve().parent.parent / DEFAULT_CHECKPOINT_NAME)
    if repo_ckpt.is_file():
        return repo_ckpt
    return None


def _load_model() -> Any:
    """Load and cache the TRIBE v2 model (first call only)."""
    global _model
    global _MODEL_SOURCE
    if _model is None:
        import tribev2  # type: ignore

        print("[FocusOS] Loading TRIBE v2 model (this may take a while)…")
        ckpt_path = _resolve_local_ckpt()

        if ckpt_path is not None:
            try:
                _model = _load_model_from_ckpt(tribev2, ckpt_path)
                if _model is not None:
                    _MODEL_SOURCE = f"local_ckpt:{ckpt_path}"
            except Exception as exc:
                print(f"[FocusOS] Local checkpoint load failed ({exc}); falling back.")

        if _model is None:
            _model = tribev2.load_model("facebook/tribev2")
            _MODEL_SOURCE = "huggingface:facebook/tribev2"
        print("[FocusOS] TRIBE v2 model loaded.")
    return _model


def _load_model_from_ckpt(tribev2_module: Any, ckpt_path: Path) -> Any | None:
    """
    Load model from local checkpoint using load_model signature introspection.
    Returns None when no compatible local-checkpoint parameter is found.
    """
    load_model = tribev2_module.load_model
    sig = inspect.signature(load_model)
    params = sig.parameters

    candidate_keys = (
        "checkpoint_path",
        "ckpt_path",
        "checkpoint",
        "model_path",
        "path",
    )
    for key in candidate_keys:
        if key in params:
            return load_model(**{key: str(ckpt_path)})

    accepts_kwargs = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
    )
    if accepts_kwargs:
        return load_model(checkpoint_path=str(ckpt_path))

    # Last fallback for signatures that accept a single positional model source.
    if params:
        first = next(iter(params.values()))
        if first.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            return load_model(str(ckpt_path))

    return None


# ── Public API ────────────────────────────────────────────────────────────────

def predict_blocks(blocks: list[dict]) -> tuple[list[dict], bool]:
    """
    Run inference on a list of text blocks.

    Parameters
    ----------
    blocks : list of dicts with at least {"id": str, "text": str}

    Returns
    -------
    scored_blocks : list of dicts
        Each entry mirrors the input block plus:
            lang  float  language-network activation  (0-1)
            exec  float  executive-control activation (0-1)
            vis   float  visual-cortex activation     (0-1)
            load  float  combined weighted load score (0-1)
    is_real_model : bool
        True when TRIBE v2 was used for inference; False when the lightweight
        heuristic stub was used (e.g. when tribev2 is not installed or
        FOCUSOS_STUB=1 is set).  Callers should surface this flag in the API
        response so users know whether predictions are model-derived.
    """
    if _TRIBE_AVAILABLE and not _STUB_FORCED:
        return _predict_tribe(blocks), True
    return _predict_stub(blocks), False


# ── TRIBE v2 inference path ───────────────────────────────────────────────────

def _predict_tribe(blocks: list[dict]) -> list[dict]:
    """
    Call TRIBE v2 for each block, aggregate fsaverage5 vertex activations
    into three network scores, and return scored-block dicts.

    Network → vertex ROI mapping uses a simple hard-coded percentile selection
    over the fsaverage5 output until a formal parcellation atlas is wired in.
    """
    import numpy as np  # type: ignore

    model = _load_model()
    results = []

    for block in blocks:
        text = block.get("text", "")
        if not text.strip():
            lang, exc, vis = 0.0, 0.0, 0.0
        else:
            # Build a minimal events dataframe as described in the model card.
            import pandas as pd  # type: ignore

            events = pd.DataFrame({
                "onset":    [0.0],
                "duration": [2.0],
                "text":     [text[:500]],
            })
            # Shape: (time_points, n_vertices ~20k)
            response = model.predict(events=events)

            # Aggregate over canonical network vertex ranges.
            # These are approximate indices on fsaverage5 (~20 484 vertices).
            # Replace with a proper parcellation atlas for production.
            lang = float(np.mean(np.abs(response[:, :4000])))
            exc  = float(np.mean(np.abs(response[:, 4000:9000])))
            vis  = float(np.mean(np.abs(response[:, 9000:15000])))

            # Normalise to [0, 1] using a soft sigmoid stretch.
            lang = _soft_norm(lang)
            exc  = _soft_norm(exc)
            vis  = _soft_norm(vis)

        from scoring import block_load_score  # local import to avoid cycles
        load = block_load_score(lang, exc, vis)

        results.append({**block, "lang": lang, "exec": exc, "vis": vis, "load": load})

    return results


# ── Heuristic stub (no model required) ───────────────────────────────────────

_RE_SENTENCE_END = re.compile(r'[.!?]+')
_RE_WORD         = re.compile(r'\b[a-zA-Z]+\b')


def _predict_stub(blocks: list[dict]) -> list[dict]:
    """
    Lightweight text-feature heuristic that approximates cognitive load without
    running TRIBE v2.  Useful for demos and testing on CPU-only machines.

    Heuristics:
      lang  ← sentence length variability + vocabulary richness (type–token ratio)
      exec  ← average word length + proportion of long sentences (>25 words)
      vis   ← presence of numbers, URLs, code markers (backtick, <>, {})
    """
    from scoring import block_load_score  # local import

    results = []

    for block in blocks:
        text = block.get("text", "")
        words = _RE_WORD.findall(text.lower())

        if not words:
            lang = exc = vis = 0.0
        else:
            # Language-network proxy: vocabulary diversity
            ttr       = len(set(words)) / max(len(words), 1)
            sentences = [s.strip() for s in _RE_SENTENCE_END.split(text) if s.strip()]
            sent_lens = [len(_RE_WORD.findall(s)) for s in sentences]
            avg_slen  = sum(sent_lens) / max(len(sent_lens), 1)
            slen_norm = _soft_norm(avg_slen / 30.0)  # 30 words/sentence ≈ complex
            lang = _clamp(0.5 * ttr + 0.5 * slen_norm)

            # Executive-control proxy: cognitive density
            avg_wlen  = sum(len(w) for w in words) / max(len(words), 1)
            long_sent = sum(1 for sl in sent_lens if sl > 25) / max(len(sent_lens), 1)
            exc = _clamp(0.5 * _soft_norm(avg_wlen / 8.0) + 0.5 * long_sent)

            # Visual-cortex proxy: non-prose markers
            num_ratio  = len(re.findall(r'\d', text)) / max(len(text), 1)
            code_ratio = len(re.findall(r'[`<>{}]', text)) / max(len(text), 1)
            url_ratio  = len(re.findall(r'https?://', text)) / max(len(text), 1) * 20
            vis = _clamp(num_ratio * 5 + code_ratio * 10 + url_ratio)

        load = block_load_score(lang, exc, vis)
        results.append({**block, "lang": lang, "exec": exc, "vis": vis, "load": load})

    return results


# ── Utilities ─────────────────────────────────────────────────────────────────

def _soft_norm(x: float) -> float:
    """Map any positive float to (0, 1) via a logistic curve."""
    return 1.0 / (1.0 + math.exp(-5.0 * (x - 0.5)))


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# ── Status helpers ────────────────────────────────────────────────────────────

def model_status() -> dict:
    mode = "tribe_v2" if (_TRIBE_AVAILABLE and not _STUB_FORCED) else "heuristic_stub"
    source = _MODEL_SOURCE if mode == "tribe_v2" else "heuristic_stub"
    return {
        "tribe_available": _TRIBE_AVAILABLE,
        "stub_forced":     _STUB_FORCED,
        "mode":            mode,
        "model_source":    source,
        "local_ckpt_found": _resolve_local_ckpt() is not None,
    }
