"""
Tests for issue #5: robust equality + novelty filter.
All tests marked @pytest.mark.judgment — run via `make verify-judgment`.

Categories:
  I.   Float tolerance (_is_strictly_better)
  II.  Reordered JSON (_normalize / _structures_match)
  III. Novelty key (_novelty_key)
  IV.  Novelty filter — verify() with known_bugs (integration, needs pred)
"""
import json
import pytest
from unittest.mock import patch

from benchmark.verify import (
    FLOAT_TOLERANCE,
    _is_strictly_better,
    _normalize,
    _novelty_key,
    _structures_match,
    verify,
)
from benchmark.tests.conftest import MIS_SOURCE, MIS_TO_CLIQUE_BUNDLE

pytestmark = pytest.mark.judgment


# ── I. Float tolerance ────────────────────────────────────────────────────────

class TestFloatTolerance:
    def test_within_tolerance_max_not_better(self):
        assert not _is_strictly_better(2.0, 2.0 + FLOAT_TOLERANCE * 0.5, is_max=True)

    def test_beyond_tolerance_max_is_better(self):
        assert _is_strictly_better(1.0, 2.0, is_max=True)

    def test_within_tolerance_min_not_better(self):
        assert not _is_strictly_better(2.0, 2.0 - FLOAT_TOLERANCE * 0.5, is_max=False)

    def test_beyond_tolerance_min_is_better(self):
        assert _is_strictly_better(5.0, 3.0, is_max=False)

    def test_equal_values_not_better(self):
        assert not _is_strictly_better(2.0, 2.0, is_max=True)
        assert not _is_strictly_better(2.0, 2.0, is_max=False)


# ── II. Reordered JSON ────────────────────────────────────────────────────────

class TestReorderedJSON:
    def test_reordered_keys_normalize_equal(self):
        a = {"z": 1, "a": 2}
        b = {"a": 2, "z": 1}
        assert _normalize(a) == _normalize(b)

    def test_reordered_target_passes_structures_match(self):
        """Bundle target with reordered top-level keys must not be rejected as tampered."""
        original = MIS_TO_CLIQUE_BUNDLE["target"]
        reordered = {k: original[k] for k in reversed(list(original.keys()))}
        assert _structures_match(original, reordered)

    def test_nested_reorder_passes_structures_match(self):
        a = {"type": "X", "data": {"num_vertices": 3, "graph": {"edges": [[0, 1]], "num_vertices": 2}}}
        b = {"data": {"graph": {"num_vertices": 2, "edges": [[0, 1]]}, "num_vertices": 3}, "type": "X"}
        assert _structures_match(a, b)


# ── III. Novelty key ──────────────────────────────────────────────────────────

class TestNoveltyKey:
    def test_same_rule_same_source_equal_key(self):
        assert _novelty_key("RuleA", MIS_SOURCE) == _novelty_key("RuleA", MIS_SOURCE)

    def test_different_rule_different_key(self):
        assert _novelty_key("RuleA", MIS_SOURCE) != _novelty_key("RuleB", MIS_SOURCE)

    def test_reordered_source_same_key(self):
        """Source with different key order → same novelty key."""
        reordered = {k: MIS_SOURCE[k] for k in reversed(list(MIS_SOURCE.keys()))}
        assert _novelty_key("RuleA", MIS_SOURCE) == _novelty_key("RuleA", reordered)

    def test_rule_name_substring_different_key(self):
        """'MaximumClique' in ledger must NOT match 'MaximumCliqueToSAT'."""
        assert _novelty_key("MaximumClique", MIS_SOURCE) != _novelty_key("MaximumCliqueToSAT", MIS_SOURCE)


# ── IV. Novelty filter ────────────────────────────────────────────────────────
# MIS→MaximumClique is a correct reduction — pred extract always returns a valid
# solution, so a real unsound_extraction bug cannot be demonstrated against it.
# These tests mock _run_pred to simulate buggy extraction and focus on the
# novelty-filter logic itself.

_UNSOUND_CERT = {
    "rule": "MaximumIndependentSetToMaximumClique",
    "violation": "unsound_extraction",
    "source": MIS_SOURCE,
    "bundle": MIS_TO_CLIQUE_BUNDLE,
    "target_config": "1,0,0",
    "claimed_source_solution": [1, 1, 0],
}


def _mock_bug_responses():
    """reduce → bundle; evaluate target → valid; extract → invalid [1,1,0]; evaluate source → None."""
    return [
        (0, json.dumps(MIS_TO_CLIQUE_BUNDLE), ""),
        (0, json.dumps({"result": "Max(1)"}), ""),
        (0, json.dumps({"solution": [1, 1, 0], "evaluation": "Max(None)"}), ""),
        (0, json.dumps({"result": "Max(None)"}), ""),
    ]


def _mock_no_bug_responses():
    """reduce → bundle; evaluate target → valid; extract → valid [1,0,0]; evaluate source → Max(1)."""
    return [
        (0, json.dumps(MIS_TO_CLIQUE_BUNDLE), ""),
        (0, json.dumps({"result": "Max(1)"}), ""),
        (0, json.dumps({"solution": [1, 0, 0], "evaluation": "Max(1)"}), ""),
        (0, json.dumps({"result": "Max(1)"}), ""),
    ]


class TestNoveltyFilter:
    def test_novel_when_not_in_ledger(self):
        with patch("benchmark.verify._run_pred", side_effect=_mock_bug_responses()):
            v = verify(_UNSOUND_CERT, known_bugs=[])
        assert v.accepted
        assert v.novelty == "novel"

    def test_known_when_in_ledger(self):
        with patch("benchmark.verify._run_pred", side_effect=_mock_bug_responses()):
            v = verify(_UNSOUND_CERT, known_bugs=[_UNSOUND_CERT])
        assert v.accepted
        assert v.novelty == "known"

    def test_rejected_has_none_novelty(self):
        false_alarm = {**_UNSOUND_CERT, "claimed_source_solution": [1, 0, 1]}
        with patch("benchmark.verify._run_pred", side_effect=_mock_no_bug_responses()):
            v = verify(false_alarm)
        assert not v.accepted
        assert v.novelty is None

    def test_reordered_source_in_ledger_recognized(self):
        """Known bug with reordered source keys still counts as known."""
        reordered_source = {k: MIS_SOURCE[k] for k in reversed(list(MIS_SOURCE.keys()))}
        known = [{**_UNSOUND_CERT, "source": reordered_source}]
        with patch("benchmark.verify._run_pred", side_effect=_mock_bug_responses()):
            v = verify(_UNSOUND_CERT, known_bugs=known)
        assert v.accepted
        assert v.novelty == "known"

    def test_rule_name_substring_not_duplicate(self):
        """Bug for 'MaximumClique' in ledger must NOT mark 'MaximumCliqueToSAT' cert as known."""
        known = [{"rule": "MaximumClique", "source": MIS_SOURCE}]
        with patch("benchmark.verify._run_pred", side_effect=_mock_bug_responses()):
            v = verify({**_UNSOUND_CERT, "rule": "MaximumCliqueToSAT"}, known_bugs=known)
        assert v.novelty != "known"
