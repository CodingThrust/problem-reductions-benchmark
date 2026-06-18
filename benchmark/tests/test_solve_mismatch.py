"""
Tests for issue #16: solve_mismatch violation type.

verify() must:
  - route violation="solve_mismatch" to _check_solve_mismatch
  - accept when pred solve evaluations differ between source and bundle
  - reject when evaluations match (correct reduction)
  - reject on subprocess timeout (instance too large)
  - reject on missing source/bundle

All tests are @pytest.mark.judgment (unit, no real pred calls).
Integration fixture test is @pytest.mark.integration.
"""
import json
import subprocess
import pytest
from unittest.mock import patch, call

pytestmark = pytest.mark.judgment

from benchmark.verify import verify
from benchmark.tests.conftest import MIS_SOURCE, MIS_TO_CLIQUE_BUNDLE


# ── helpers ──────────────────────────────────────────────────────────────────

def _solve_mismatch_cert(**overrides) -> dict:
    base = {
        "rule": "SomeRule",
        "violation": "solve_mismatch",
        "source": MIS_SOURCE,
        "bundle": MIS_TO_CLIQUE_BUNDLE,
    }
    base.update(overrides)
    return base


def _pred_reduce_ok():
    """Successful pred reduce response (re-derives the canonical bundle target)."""
    return (0, json.dumps(MIS_TO_CLIQUE_BUNDLE), "")


def _pred_solve(evaluation: str):
    """Successful pred solve response with given evaluation."""
    return (0, json.dumps({"evaluation": evaluation, "solution": [1, 0, 1]}), "")


# ── schema test ───────────────────────────────────────────────────────────────

class TestSolveMismatchSchema:
    def test_solve_mismatch_in_schema(self):
        """results.schema.json violation enum must include solve_mismatch."""
        from pathlib import Path
        schema_path = Path(__file__).parent.parent / "results.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        violation_enum = (
            schema["properties"]["results"]["items"]
            ["properties"]["certificate"]["properties"]["violation"]["enum"]
        )
        assert "solve_mismatch" in violation_enum, (
            f"solve_mismatch missing from violation enum: {violation_enum}"
        )


# ── missing-field tests ───────────────────────────────────────────────────────

class TestSolveMismatchMissingFields:
    def test_missing_source_rejected(self):
        cert = _solve_mismatch_cert(source=None)
        # patch reduce so we don't need pred; missing source is caught before reduce
        v = verify(cert)
        assert not v.accepted
        assert "source" in v.reason.lower() or "missing" in v.reason.lower()

    def test_missing_bundle_rejected(self):
        cert = _solve_mismatch_cert(bundle=None)
        v = verify(cert)
        assert not v.accepted
        assert "bundle" in v.reason.lower() or "missing" in v.reason.lower()


# ── core logic tests (mock _run_pred) ─────────────────────────────────────────

class TestSolveMismatchLogic:
    def _run_with_mocks(self, source_eval: str, bundle_eval: str):
        """Run verify() with mocked pred: reduce succeeds, two solve calls return given evals."""
        cert = _solve_mismatch_cert()
        responses = [
            _pred_reduce_ok(),          # pred reduce (bundle integrity check)
            _pred_solve(source_eval),   # pred solve source
            _pred_solve(bundle_eval),   # pred solve bundle
        ]
        with patch("benchmark.verify._run_pred", side_effect=responses):
            return verify(cert)

    def test_accepted_when_evaluations_differ(self):
        """Different evaluations → bug confirmed → accepted."""
        v = self._run_with_mocks("Max(2)", "Max(1)")
        assert v.accepted, f"Expected accepted, got: {v.reason}"

    def test_rejected_when_evaluations_match(self):
        """Same evaluations → correct reduction → rejected."""
        v = self._run_with_mocks("Max(2)", "Max(2)")
        assert not v.accepted
        assert "match" in v.reason.lower() or "equal" in v.reason.lower() or "no bug" in v.reason.lower()

    def test_rejected_when_both_none(self):
        """Both infeasible → match → rejected (not a bug)."""
        v = self._run_with_mocks("Max(None)", "Max(None)")
        assert not v.accepted

    def test_details_contain_evaluations(self):
        """Accepted verdict details must include both evaluations."""
        v = self._run_with_mocks("Max(2)", "Max(1)")
        assert v.accepted
        assert "source_evaluation" in v.details or "eval_source" in v.details or any(
            "Max(2)" in str(val) for val in v.details.values()
        )

    def test_rejected_on_timeout(self):
        """TimeoutExpired during solve → rejected, reason mentions 'too large'."""
        cert = _solve_mismatch_cert()

        def side_effect(args, stdin_file=None):
            if args[0] == "reduce":
                return _pred_reduce_ok()
            raise subprocess.TimeoutExpired(cmd=["pred", "solve"], timeout=30)

        with patch("benchmark.verify._run_pred", side_effect=side_effect):
            v = verify(cert)
        assert not v.accepted
        assert "too large" in v.reason.lower() or "timeout" in v.reason.lower()


# ── integration fixture test ──────────────────────────────────────────────────

@pytest.mark.integration
class TestSolveMismatchFixture:
    def test_correct_reduction_fixture_rejected(self):
        """MIS→MaximumClique is a correct reduction: solve_mismatch must be rejected."""
        from pathlib import Path
        fixture_path = (
            Path(__file__).parent / "fixtures" / "solve_mismatch_correct_reduction.json"
        )
        if not fixture_path.exists():
            pytest.skip("fixture not yet created")
        cert = json.loads(fixture_path.read_text(encoding="utf-8"))
        from benchmark.verify import verify
        v = verify(cert)
        assert not v.accepted, (
            f"Correct reduction should be rejected by solve_mismatch, got: {v.reason}"
        )
