"""
Tests for #15 part 2: order_reversal violation type (solver-free suboptimality check).

A bug is proven when two target configs c_lo, c_hi satisfy:
  obj_B(c_lo) < obj_B(c_hi)  but  obj_A(extract(c_lo)) > obj_A(extract(c_hi))

This proves the reduction does not preserve ordering — no solver needed.

All tests are @pytest.mark.judgment (unit, no real pred calls).
"""
import json
import pytest
from unittest.mock import patch

pytestmark = pytest.mark.judgment

from benchmark.verify import verify
from benchmark.tests.conftest import MIS_SOURCE, MIS_TO_CLIQUE_BUNDLE


def _order_reversal_cert(**overrides) -> dict:
    base = {
        "rule": "SomeRule",
        "violation": "order_reversal",
        "source": MIS_SOURCE,
        "bundle": MIS_TO_CLIQUE_BUNDLE,
        "target_config_lo": "1,0,0",
        "target_config_hi": "1,0,1",
    }
    base.update(overrides)
    return base


def _pred_reduce_ok():
    return (0, json.dumps(MIS_TO_CLIQUE_BUNDLE), "")


def _pred_extract(solution: list, obj_val: str):
    return (0, json.dumps({"solution": solution, "evaluation": obj_val}), "")


def _pred_evaluate(result: str):
    return (0, json.dumps({"result": result}), "")


class TestOrderReversalSchema:
    def test_order_reversal_in_schema(self):
        """results.schema.json violation enum must include order_reversal."""
        from pathlib import Path
        schema_path = Path(__file__).parent.parent / "results.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        violation_enum = (
            schema["properties"]["results"]["items"]
            ["properties"]["certificate"]["properties"]["violation"]["enum"]
        )
        assert "order_reversal" in violation_enum, (
            f"order_reversal missing from violation enum: {violation_enum}"
        )


class TestOrderReversalMissingFields:
    def test_missing_target_config_lo(self):
        cert = _order_reversal_cert(target_config_lo=None)
        v = verify(cert)
        assert not v.accepted
        assert "target_config_lo" in v.reason

    def test_missing_target_config_hi(self):
        cert = _order_reversal_cert(target_config_hi=None)
        v = verify(cert)
        assert not v.accepted
        assert "target_config_hi" in v.reason


class TestOrderReversalLogic:
    def _run(self, obj_b_lo, obj_b_hi, obj_a_lo, obj_a_hi):
        """
        Mock pred calls:
          reduce → bundle
          extract c_lo → sol_lo with obj_a_lo
          extract c_hi → sol_hi with obj_a_hi
          evaluate target c_lo → obj_b_lo
          evaluate target c_hi → obj_b_hi
        """
        responses = [
            _pred_reduce_ok(),
            _pred_extract([1, 0, 0], obj_a_lo),   # extract c_lo
            _pred_extract([1, 0, 1], obj_a_hi),   # extract c_hi
            _pred_evaluate(obj_b_lo),              # evaluate target c_lo
            _pred_evaluate(obj_b_hi),              # evaluate target c_hi
        ]
        cert = _order_reversal_cert()
        with patch("benchmark.verify._run_pred", side_effect=responses):
            return verify(cert)

    def test_accepted_when_order_reversed(self):
        """c_lo has lower target value but higher source value after extract → bug."""
        # obj_B(c_lo)=1 < obj_B(c_hi)=2, but obj_A(extract(c_lo))=2 > obj_A(extract(c_hi))=1
        v = self._run("Max(1)", "Max(2)", "Max(2)", "Max(1)")
        assert v.accepted, f"Expected accepted, got: {v.reason}"

    def test_rejected_when_order_preserved(self):
        """c_lo has lower target value and lower source value → correct ordering → rejected."""
        # obj_B(c_lo)=1 < obj_B(c_hi)=2, and obj_A(extract(c_lo))=1 < obj_A(extract(c_hi))=2
        v = self._run("Max(1)", "Max(2)", "Max(1)", "Max(2)")
        assert not v.accepted
        assert "order" in v.reason.lower() or "no bug" in v.reason.lower() or "preserved" in v.reason.lower()

    def test_rejected_when_equal_target_values(self):
        """Both configs have equal target value → not an order reversal."""
        v = self._run("Max(2)", "Max(2)", "Max(1)", "Max(2)")
        assert not v.accepted

    def test_details_contain_both_configs(self):
        """Accepted verdict details must reference both config evaluations."""
        v = self._run("Max(1)", "Max(2)", "Max(2)", "Max(1)")
        assert v.accepted
        detail_str = str(v.details)
        assert "Max(1)" in detail_str or "Max(2)" in detail_str
