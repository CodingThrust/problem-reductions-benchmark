"""
Tests for #15 part 3: incomplete_reduction timeout protection.

_check_incomplete_reduction runs pred solve on source and bundle.
Large instances must time out gracefully — not crash with unhandled TimeoutExpired.

All tests are @pytest.mark.judgment (unit, no real pred calls).
"""
import json
import subprocess
import pytest
from unittest.mock import patch

pytestmark = pytest.mark.judgment

from benchmark.verify import verify
from benchmark.tests.conftest import MIS_SOURCE, MIS_TO_CLIQUE_BUNDLE


def _incomplete_cert() -> dict:
    return {
        "rule": "SomeRule",
        "violation": "incomplete_reduction",
        "source": MIS_SOURCE,
        "bundle": MIS_TO_CLIQUE_BUNDLE,
    }


def _pred_reduce_ok():
    return (0, json.dumps(MIS_TO_CLIQUE_BUNDLE), "")


def _pred_solve(evaluation: str):
    return (0, json.dumps({"evaluation": evaluation, "solution": [1, 0, 1]}), "")


class TestIncompleteReductionTimeout:
    def test_timeout_on_source_solve(self):
        """TimeoutExpired during source solve → rejected with 'too large' or 'timeout'."""
        def side_effect(args, stdin_file=None):
            if args[0] == "reduce":
                return _pred_reduce_ok()
            if args[0] == "solve":
                raise subprocess.TimeoutExpired(cmd=["pred", "solve"], timeout=30)
            return (0, "{}", "")

        cert = _incomplete_cert()
        with patch("benchmark.verify._run_pred", side_effect=side_effect):
            v = verify(cert)
        assert not v.accepted
        assert "too large" in v.reason.lower() or "timeout" in v.reason.lower()

    def test_timeout_on_bundle_solve(self):
        """TimeoutExpired during bundle solve → rejected with 'too large' or 'timeout'."""
        call_count = {"n": 0}

        def side_effect(args, stdin_file=None):
            if args[0] == "reduce":
                return _pred_reduce_ok()
            if args[0] == "solve":
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return _pred_solve("Max(2)")   # source solve succeeds
                raise subprocess.TimeoutExpired(cmd=["pred", "solve"], timeout=30)
            return (0, "{}", "")

        cert = _incomplete_cert()
        with patch("benchmark.verify._run_pred", side_effect=side_effect):
            v = verify(cert)
        assert not v.accepted
        assert "too large" in v.reason.lower() or "timeout" in v.reason.lower()

    def test_normal_flow_unaffected(self):
        """No timeout: normal flow still works — target has solution → rejected as expected."""
        def side_effect(args, stdin_file=None):
            if args[0] == "reduce":
                return _pred_reduce_ok()
            if args[0] == "solve":
                return _pred_solve("Max(2)")   # both source and bundle have solutions
            return (0, "{}", "")

        cert = _incomplete_cert()
        with patch("benchmark.verify._run_pred", side_effect=side_effect):
            v = verify(cert)
        # target has a solution → not an incomplete reduction → rejected
        assert not v.accepted
        assert "solution" in v.reason.lower() or "no incomplete" in v.reason.lower()
