"""
tests/test_scoring.py – Unit tests for the cognitive load scoring module.
"""

import pytest
from scoring import block_load_score, page_score, score_label


class TestBlockLoadScore:
    def test_all_zero(self):
        assert block_load_score(0.0, 0.0, 0.0) == pytest.approx(0.0)

    def test_all_one(self):
        # 0.45*1 + 0.35*1 + 0.20*1 = 1.0
        assert block_load_score(1.0, 1.0, 1.0) == pytest.approx(1.0)

    def test_weights_sum(self):
        # Lang-only
        assert block_load_score(1.0, 0.0, 0.0) == pytest.approx(0.45)
        # Exec-only
        assert block_load_score(0.0, 1.0, 0.0) == pytest.approx(0.35)
        # Vis-only
        assert block_load_score(0.0, 0.0, 1.0) == pytest.approx(0.20)

    def test_clamping_over_one(self):
        result = block_load_score(2.0, 2.0, 2.0)
        assert result == pytest.approx(1.0)

    def test_clamping_below_zero(self):
        result = block_load_score(-1.0, -1.0, -1.0)
        assert result == pytest.approx(0.0)

    def test_mixed_values(self):
        result = block_load_score(0.8, 0.6, 0.4)
        expected = 0.45 * 0.8 + 0.35 * 0.6 + 0.20 * 0.4
        assert result == pytest.approx(expected, rel=1e-6)


class TestPageScore:
    def test_empty(self):
        assert page_score([]) == 0.0

    def test_single_block(self):
        # Single block → top 30% of 1 = 1 block → mean = that value × 100
        assert page_score([0.5]) == pytest.approx(50.0)

    def test_top_30_percent(self):
        # 10 blocks: top 30% = top 3
        loads = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        # Top 3: [1.0, 0.9, 0.8] → mean = 0.9 → score = 90.0
        assert page_score(loads) == pytest.approx(90.0)

    def test_all_same(self):
        loads = [0.5] * 20
        assert page_score(loads) == pytest.approx(50.0)


class TestScoreLabel:
    def test_low(self):
        assert score_label(0.0) == "low"
        assert score_label(29.9) == "low"

    def test_good(self):
        assert score_label(30.0) == "good"
        assert score_label(59.9) == "good"

    def test_high(self):
        assert score_label(60.0) == "high"
        assert score_label(100.0) == "high"
