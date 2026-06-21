"""
Tests for benchmark/verify.py

Design principle: tests must be strictly correct and cannot be "gamed" by
changing the verifier to always accept or always reject. Each test checks
a specific, observable behaviour of the pred-based verification logic.

Test categories:
  A. Pure-logic helpers (_normalize, _structures_match, _parse_numeric_result)
  B. Certificate pre-validation (missing fields, malformed bundles)
  C. Bundle integrity check (tampered target → rejected)
  D. unsound_extraction (valid bug / false alarm / invalid target_config)
  E. incomplete_reduction (source sat + target unsat / source also unsat)
  F. suboptimal_extraction (extracted suboptimal / extracted already optimal)
  G. Unknown violation type
  H. Fixture-level regression (the 3 canonical fixtures must keep their verdicts)
"""

import json
import pytest
from unittest.mock import patch

from benchmark.verify import (
    Verdict,
    _normalize,
    _parse_numeric_result,
    _structures_match,
    verify,
)
from benchmark.tests.conftest import MIS_SOURCE, MIS_TO_CLIQUE_BUNDLE


def _mock_unsound_bug_responses():
    """Mock responses for an unsound_extraction bug: extract returns invalid solution."""
    return [
        (0, json.dumps(MIS_TO_CLIQUE_BUNDLE), ""),           # pred reduce
        (0, json.dumps({"result": "Max(1)"}), ""),            # pred evaluate target_config (valid)
        (0, json.dumps({"solution": [1, 1, 0], "evaluation": "Max(None)"}), ""),  # pred extract
        (0, json.dumps({"result": "Max(None)"}), ""),         # pred evaluate extracted
    ]


# ── A. Pure-logic helpers ─────────────────────────────────────────────────────

class TestNormalize:
    def test_sorts_dict_keys(self):
        result = _normalize({"b": 1, "a": 2})
        assert list(result.keys()) == ["a", "b"]

    def test_nested_dict(self):
        result = _normalize({"z": {"b": 1, "a": 2}})
        assert list(result["z"].keys()) == ["a", "b"]

    def test_list_preserved_order(self):
        # Lists keep their order — normalization only sorts dict keys
        result = _normalize([3, 1, 2])
        assert result == [3, 1, 2]

    def test_nested_list_of_dicts(self):
        result = _normalize([{"b": 1, "a": 2}, {"d": 3, "c": 4}])
        assert result[0] == {"a": 2, "b": 1}
        assert result[1] == {"c": 4, "d": 3}

    def test_scalar_passthrough(self):
        assert _normalize(42) == 42
        assert _normalize("hello") == "hello"
        assert _normalize(None) is None


class TestStructuresMatch:
    def test_identical_dicts(self):
        a = {"type": "MIS", "data": {"num_vertices": 3}}
        assert _structures_match(a, a)

    def test_different_key_order_still_matches(self):
        a = {"b": 1, "a": 2}
        b = {"a": 2, "b": 1}
        assert _structures_match(a, b)

    def test_different_values_do_not_match(self):
        a = {"type": "MIS", "data": {"num_vertices": 3}}
        b = {"type": "MIS", "data": {"num_vertices": 4}}
        assert not _structures_match(a, b)

    def test_extra_key_does_not_match(self):
        a = {"type": "MIS"}
        b = {"type": "MIS", "extra": "field"}
        assert not _structures_match(a, b)


class TestParseNumericResult:
    def test_max_positive(self):
        assert _parse_numeric_result("Max(2)") == 2.0

    def test_max_negative(self):
        assert _parse_numeric_result("Max(-3)") == -3.0

    def test_min_float(self):
        assert _parse_numeric_result("Min(-14.5)") == -14.5

    def test_zero(self):
        assert _parse_numeric_result("Max(0)") == 0.0

    def test_none_result_returns_none(self):
        assert _parse_numeric_result("Max(None)") is None

    def test_false_result_returns_none(self):
        assert _parse_numeric_result("Or(false)") is None

    def test_empty_string_returns_none(self):
        assert _parse_numeric_result("") is None


# ── B. Certificate pre-validation ────────────────────────────────────────────

class TestMissingFields:
    def test_missing_source(self):
        cert = {"bundle": MIS_TO_CLIQUE_BUNDLE, "violation": "unsound_extraction"}
        v = verify(cert)
        assert not v.accepted
        assert "missing required fields" in v.reason

    def test_missing_bundle(self):
        cert = {"source": MIS_SOURCE, "violation": "unsound_extraction"}
        v = verify(cert)
        assert not v.accepted
        assert "missing required fields" in v.reason

    def test_missing_violation(self):
        cert = {"source": MIS_SOURCE, "bundle": MIS_TO_CLIQUE_BUNDLE}
        v = verify(cert)
        assert not v.accepted
        assert "missing required fields" in v.reason

    def test_bundle_missing_target(self):
        bad_bundle = {"source": MIS_SOURCE}  # no "target"
        cert = {"source": MIS_SOURCE, "bundle": bad_bundle, "violation": "unsound_extraction"}
        v = verify(cert)
        assert not v.accepted
        assert "bundle missing" in v.reason

    def test_bundle_target_missing_type(self):
        bad_bundle = {**MIS_TO_CLIQUE_BUNDLE, "target": {"data": {}}}  # no "type"
        cert = {"source": MIS_SOURCE, "bundle": bad_bundle, "violation": "unsound_extraction"}
        v = verify(cert)
        assert not v.accepted
        assert "no type field" in v.reason

    def test_unsound_missing_target_config(self):
        cert = {
            "source": MIS_SOURCE,
            "bundle": MIS_TO_CLIQUE_BUNDLE,
            "violation": "unsound_extraction",
            # target_config intentionally omitted
            "claimed_source_solution": [1, 1, 0],
        }
        v = verify(cert)
        assert not v.accepted
        assert "target_config" in v.reason

    def test_unsound_missing_claimed_source_solution(self):
        # claimed_source_solution is no longer required — verifier runs pred extract itself
        cert = {
            "source": MIS_SOURCE,
            "bundle": MIS_TO_CLIQUE_BUNDLE,
            "violation": "unsound_extraction",
            "target_config": "1,0,0",
            # claimed_source_solution intentionally omitted — should not block verification
        }
        with patch("benchmark.verify._run_pred", side_effect=_mock_unsound_bug_responses()):
            v = verify(cert)
        assert v.accepted, f"missing claimed_source_solution should not block verification, got: {v.reason}"

    def test_suboptimal_missing_target_config(self):
        cert = {
            "source": MIS_SOURCE,
            "bundle": MIS_TO_CLIQUE_BUNDLE,
            "violation": "suboptimal_extraction",
            "brute_force_solution": [1, 0, 1],
        }
        v = verify(cert)
        assert not v.accepted
        assert "target_config" in v.reason

    def test_suboptimal_missing_brute_force_solution(self):
        cert = {
            "source": MIS_SOURCE,
            "bundle": MIS_TO_CLIQUE_BUNDLE,
            "violation": "suboptimal_extraction",
            "target_config": "1,0,0",
        }
        v = verify(cert)
        assert not v.accepted
        assert "brute_force_solution" in v.reason


# ── C. Bundle integrity check ─────────────────────────────────────────────────

class TestBundleIntegrity:
    def test_tampered_target_rejected(self, tampered_bundle_cert):
        """Bundle target with fabricated edge must be rejected."""
        v = verify(tampered_bundle_cert)
        assert not v.accepted
        assert "does not match" in v.reason

    def test_correct_bundle_passes_integrity(self):
        """A real (unmodified) bundle must survive the integrity check."""
        cert = {
            "rule": "MaximumIndependentSetToMaximumClique",
            "violation": "unsound_extraction",
            "source": MIS_SOURCE,
            "bundle": MIS_TO_CLIQUE_BUNDLE,
            "target_config": "1,0,0",
        }
        with patch("benchmark.verify._run_pred", side_effect=_mock_unsound_bug_responses()):
            v = verify(cert)
        # If bundle integrity failed, reason would mention "does not match"
        assert "does not match" not in v.reason


# ── D. unsound_extraction ─────────────────────────────────────────────────────

class TestUnsoundExtraction:
    def test_genuine_bug_accepted(self):
        """Mocked: extract returns invalid solution → real bug → accepted."""
        cert = {
            "rule": "MaximumIndependentSetToMaximumClique",
            "violation": "unsound_extraction",
            "source": MIS_SOURCE,
            "bundle": MIS_TO_CLIQUE_BUNDLE,
            "target_config": "1,0,0",
            "claimed_source_solution": [1, 1, 0],
        }
        with patch("benchmark.verify._run_pred", side_effect=_mock_unsound_bug_responses()):
            v = verify(cert)
        assert v.accepted
        assert "invalid" in v.reason

    def test_false_alarm_rejected(self):
        """Mocked: extract returns valid solution → not a bug → rejected."""
        responses = [
            (0, json.dumps(MIS_TO_CLIQUE_BUNDLE), ""),           # pred reduce
            (0, json.dumps({"result": "Max(2)"}), ""),            # pred evaluate target_config
            (0, json.dumps({"solution": [1, 0, 1], "evaluation": "Max(2)"}), ""),  # pred extract
            (0, json.dumps({"result": "Max(2)"}), ""),            # pred evaluate extracted
        ]
        cert = {
            "rule": "MaximumIndependentSetToMaximumClique",
            "violation": "unsound_extraction",
            "source": MIS_SOURCE,
            "bundle": MIS_TO_CLIQUE_BUNDLE,
            "target_config": "1,0,1",
            "claimed_source_solution": [1, 0, 1],
        }
        with patch("benchmark.verify._run_pred", side_effect=responses):
            v = verify(cert)
        assert not v.accepted
        assert "valid" in v.reason

    def test_verdict_details_on_acceptance(self):
        """Accepted verdict must carry details with evaluation and extracted_solution."""
        cert = {
            "rule": "MaximumIndependentSetToMaximumClique",
            "violation": "unsound_extraction",
            "source": MIS_SOURCE,
            "bundle": MIS_TO_CLIQUE_BUNDLE,
            "target_config": "1,0,0",
            "claimed_source_solution": [1, 1, 0],
        }
        with patch("benchmark.verify._run_pred", side_effect=_mock_unsound_bug_responses()):
            v = verify(cert)
        assert v.accepted
        assert "evaluation" in v.details
        assert "extracted_solution" in v.details

    def test_invalid_target_config_rejected(self):
        """If target_config is not a valid target solution, reject."""
        responses = [
            (0, json.dumps(MIS_TO_CLIQUE_BUNDLE), ""),           # pred reduce
            (0, json.dumps({"result": "Max(None)"}), ""),         # pred evaluate target_config → invalid
        ]
        cert = {
            "rule": "MaximumIndependentSetToMaximumClique",
            "violation": "unsound_extraction",
            "source": MIS_SOURCE,
            "bundle": MIS_TO_CLIQUE_BUNDLE,
            "target_config": "1,1,0",
            "claimed_source_solution": [1, 1, 0],
        }
        with patch("benchmark.verify._run_pred", side_effect=responses):
            v = verify(cert)
        assert not v.accepted

    def test_unsound_uses_pred_extract(self):
        """Verifier must call pred extract — not trust claimed_source_solution."""
        cert = {
            "rule": "MaximumIndependentSetToMaximumClique",
            "violation": "unsound_extraction",
            "source": MIS_SOURCE,
            "bundle": MIS_TO_CLIQUE_BUNDLE,
            "target_config": "1,0,0",
            "claimed_source_solution": [0, 0, 0],  # wrong — verifier should ignore this
        }
        with patch("benchmark.verify._run_pred", side_effect=_mock_unsound_bug_responses()) as mock_pred:
            verify(cert)
        called_verbs = [c.args[0][0] for c in mock_pred.call_args_list]
        assert "extract" in called_verbs, "verifier must call pred extract"

    def test_unsound_wrong_claimed_but_real_bug(self):
        """AI provides wrong claimed_source_solution — verifier ignores it and uses pred extract."""
        cert = {
            "rule": "MaximumIndependentSetToMaximumClique",
            "violation": "unsound_extraction",
            "source": MIS_SOURCE,
            "bundle": MIS_TO_CLIQUE_BUNDLE,
            "target_config": "1,0,0",
            "claimed_source_solution": [0, 0, 0],  # wrong
        }
        with patch("benchmark.verify._run_pred", side_effect=_mock_unsound_bug_responses()):
            v = verify(cert)
        assert v.accepted, f"Real bug should be accepted even with wrong claimed_source_solution, got: {v.reason}"


# ── E. incomplete_reduction ───────────────────────────────────────────────────

class TestIncompleteReduction:
    """
    incomplete_reduction: source is satisfiable but the target bundle has no solution.
    Hard to construct without a real buggy rule, so we test the rejection logic
    by crafting a cert where both source AND target have solutions (→ rejected).
    """

    def test_when_target_has_solution_rejected(self):
        """Both source and target have solutions → not an incomplete reduction."""
        cert = {
            "rule": "MaximumIndependentSetToMaximumClique",
            "violation": "incomplete_reduction",
            "source": MIS_SOURCE,
            "bundle": MIS_TO_CLIQUE_BUNDLE,
        }
        responses = [
            (0, json.dumps(MIS_TO_CLIQUE_BUNDLE), ""),                         # reduce
            (0, json.dumps({"evaluation": "Max(2)", "solution": [1,0,1]}), ""),# solve source → has sol
            (0, json.dumps({"evaluation": "Max(2)", "solution": [1,0,1]}), ""),# solve bundle → has sol
        ]
        with patch("benchmark.verify._run_pred", side_effect=responses):
            v = verify(cert)
        assert not v.accepted
        assert "has a solution" in v.reason

    def test_unsatisfiable_source_rejected(self):
        """
        KColoring with k=1 and an edge: source itself is unsat.
        incomplete_reduction requires source to be satisfiable — so rejected.
        """
        # 1-coloring of a graph with any edge is impossible
        k1_source = {
            "data": {"graph": {"edges": [[0, 1]], "num_vertices": 2}, "k": 1},
            "type": "KColoring",
            "variant": {"graph": "SimpleGraph"},
        }
        # Reduce KColoring(k=1, edge 0-1) to SAT (any reachable target)
        import subprocess, json as _json, tempfile, os
        enc_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as f:
                _json.dump(k1_source, f)
                enc_path = f.name
            r = subprocess.run(
                ["pred", "reduce", enc_path, "--to", "SAT", "--json"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                pytest.skip("KColoring→SAT reduction unavailable in this build")
            bundle = _json.loads(r.stdout)
        finally:
            if enc_path and os.path.exists(enc_path):
                os.unlink(enc_path)

        cert = {
            "rule": "KColoringToSAT",
            "violation": "incomplete_reduction",
            "source": k1_source,
            "bundle": bundle,
        }
        v = verify(cert)
        # Source itself has no solution → verifier must reject
        assert not v.accepted
        assert "no solution" in v.reason or "satisfiable" in v.reason


# ── F. suboptimal_extraction ──────────────────────────────────────────────────

class TestSuboptimalExtraction:
    def test_already_optimal_rejected(self):
        """
        Extract MIS→MaximumClique with optimal config [1,0,1] (size 2).
        Claim [1,0,0] (size 1) is better — it's NOT. Verifier must reject.
        """
        cert = {
            "rule": "MaximumIndependentSetToMaximumClique",
            "violation": "suboptimal_extraction",
            "source": MIS_SOURCE,
            "bundle": MIS_TO_CLIQUE_BUNDLE,
            "target_config": "1,0,1",       # extracts solution [1,0,1] with value Max(2)
            "claimed_source_solution": [1, 0, 1],
            "brute_force_solution": [1, 0, 0],  # size 1 — actually WORSE
        }
        v = verify(cert)
        assert not v.accepted
        assert "optimal" in v.reason

    def test_genuinely_suboptimal_accepted(self):
        """
        Extract with config [1,0,0] → solution size 1.
        Claim [1,0,1] (size 2) is better. This IS a real suboptimality.
        """
        cert = {
            "rule": "MaximumIndependentSetToMaximumClique",
            "violation": "suboptimal_extraction",
            "source": MIS_SOURCE,
            "bundle": MIS_TO_CLIQUE_BUNDLE,
            "target_config": "1,0,0",       # extracts solution [1,0,0] with value Max(1)
            "claimed_source_solution": [1, 0, 0],
            "brute_force_solution": [1, 0, 1],  # size 2 — strictly better
        }
        v = verify(cert)
        assert v.accepted
        assert "suboptimal" in v.reason


# ── G. Unknown violation type ─────────────────────────────────────────────────

class TestUnknownViolation:
    def test_unknown_violation_rejected(self):
        cert = {
            "source": MIS_SOURCE,
            "bundle": MIS_TO_CLIQUE_BUNDLE,
            "violation": "invented_violation_type",
        }
        v = verify(cert)
        assert not v.accepted
        assert "unknown violation" in v.reason


# ── H. Fixture regression ─────────────────────────────────────────────────────

class TestFixtureRegression:
    """
    The 3 canonical fixture files must keep their verdicts across all code changes.
    This is the same check that `make verify-calibration` runs, expressed as pytest
    so failures appear in the standard test report.
    """

    FIXTURES_DIR = (
        __import__("pathlib").Path(__file__).parent / "fixtures"
    )

    def _load(self, name: str) -> dict:
        with open(self.FIXTURES_DIR / name, encoding="utf-8") as f:
            return json.load(f)

    def test_valid_bug_accepted(self):
        cert = self._load("valid_bug.json")
        v = verify(cert)
        assert v.accepted, f"valid_bug.json should be accepted but got: {v.reason}"

    def test_wrong_target_rejected(self):
        cert = self._load("wrong_target.json")
        v = verify(cert)
        assert not v.accepted, f"wrong_target.json should be rejected but got: {v.reason}"
        assert "does not match" in v.reason

    def test_valid_solution_claimed_invalid_rejected(self):
        cert = self._load("valid_solution_claimed_invalid.json")
        v = verify(cert)
        assert not v.accepted, (
            f"valid_solution_claimed_invalid.json should be rejected but got: {v.reason}"
        )
        assert "valid" in v.reason
