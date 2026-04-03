"""
tests/test_model_stub.py – Tests for the heuristic stub inference path.
"""

import os
import sys

# Force stub mode for all tests in this file.
os.environ["FOCUSOS_STUB"] = "1"

# Ensure the tribe-local package root is on the path.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import importlib
import model as _model_module

# Re-import with stub forced (env var already set above).
importlib.reload(_model_module)
from model import predict_blocks, model_status


SAMPLE_BLOCKS = [
    {
        "id": "focusos-block-0",
        "text": (
            "The theory of general relativity posits that massive objects cause "
            "a curvature in space-time, which is felt as gravity. "
            "This fundamentally altered our understanding of the cosmos."
        ),
        "domPath": "article > p:nth-of-type(1)",
        "position": 0,
        "tagName": "p",
    },
    {
        "id": "focusos-block-1",
        "text": "Click here for more information about our privacy policy.",
        "domPath": "footer > p",
        "position": 1,
        "tagName": "p",
    },
]


class TestModelStub:
    def test_mode_is_stub(self):
        status = model_status()
        assert status["mode"] == "heuristic_stub"
        assert status["stub_forced"] is True

    def test_returns_correct_count(self):
        scored, is_real = predict_blocks(SAMPLE_BLOCKS)
        assert len(scored) == len(SAMPLE_BLOCKS)
        assert is_real is False

    def test_output_keys_present(self):
        scored, _ = predict_blocks(SAMPLE_BLOCKS)
        for block in scored:
            assert "load" in block
            assert "lang" in block
            assert "exec" in block
            assert "vis" in block

    def test_scores_in_range(self):
        scored, _ = predict_blocks(SAMPLE_BLOCKS)
        for block in scored:
            assert 0.0 <= block["load"] <= 1.0
            assert 0.0 <= block["lang"] <= 1.0
            assert 0.0 <= block["exec"] <= 1.0
            assert 0.0 <= block["vis"] <= 1.0

    def test_input_block_preserved(self):
        scored, _ = predict_blocks(SAMPLE_BLOCKS)
        for orig, scored_block in zip(SAMPLE_BLOCKS, scored):
            assert scored_block["id"] == orig["id"]
            assert scored_block["text"] == orig["text"]

    def test_empty_text_block(self):
        empty_block = [{"id": "x", "text": "", "domPath": "", "position": 0, "tagName": "p"}]
        scored, _ = predict_blocks(empty_block)
        assert scored[0]["load"] == 0.0
        assert scored[0]["lang"] == 0.0
