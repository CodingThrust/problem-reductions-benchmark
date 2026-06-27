"""
Tests for benchmark/verify.py (round-trip design).

Unit tests monkeypatch PredSolver so they need no pred binary; integration tests run the
real verifier against the certificate fixtures.

The contract: a reduction A→B is buggy on instance `a` iff solving via the reduction does
not recover the true source answer — solve(a) != solve(reduce(a)), compared by VALUE (opt)
or feasibility (decision). See module docstring of verify.py.
"""
import json
from pathlib import Path

import pytest

from benchmark import verify as vf
from benchmark.verify import Eval, agrees, verify

FIXTURES = Path(__file__).parent / "fixtures"


# ── Eval.parse ────────────────────────────────────────────────────────────────

class TestEvalParse:
    def test_opt_max(self):
        e = Eval.parse("Max(2)")
        assert e.kind == "opt" and e.feasible and e.value == 2 and e.sense == "max"

    def test_opt_min_negative(self):
        e = Eval.parse("Min(-14)")
        assert e.kind == "opt" and e.feasible and e.value == -14 and e.sense == "min"

    def test_opt_infeasible(self):
        e = Eval.parse("Max(None)")
        assert e.kind == "opt" and not e.feasible and e.value is None

    def test_sat_true(self):
        e = Eval.parse("Or(true)")
        assert e.kind == "sat" and e.feasible and e.value is None

    def test_sat_false(self):
        e = Eval.parse("Or(false)")
        assert e.kind == "sat" and not e.feasible

    def test_bare_number(self):
        e = Eval.parse("5")
        assert e.kind == "opt" and e.feasible and e.value == 5

    def test_empty_unknown(self):
        e = Eval.parse("")
        assert e.kind == "unknown" and not e.feasible


# ── agrees ────────────────────────────────────────────────────────────────────

class TestAgrees:
    def test_equal_opt_values_agree(self):
        assert agrees(Eval.parse("Max(2)"), Eval.parse("Max(2)"))

    def test_different_opt_values_disagree(self):
        assert not agrees(Eval.parse("Max(2)"), Eval.parse("Max(1)"))

    def test_feasibility_mismatch_disagrees(self):
        assert not agrees(Eval.parse("Max(2)"), Eval.parse("Max(None)"))

    def test_both_infeasible_agree(self):
        assert agrees(Eval.parse("Max(None)"), Eval.parse("Max(None)"))

    def test_sat_both_satisfiable_agree(self):
        assert agrees(Eval.parse("Or(true)"), Eval.parse("Or(true)"))

    def test_sat_vs_unsat_disagree(self):
        assert not agrees(Eval.parse("Or(true)"), Eval.parse("Or(false)"))

    def test_value_within_tolerance(self):
        assert agrees(Eval("opt", True, 2.0, "max", "Max(2)"),
                      Eval("opt", True, 2.0 + 1e-9, "max", "Max(2)"))


# ── verify() core round-trip (PredSolver monkeypatched) ───────────────────────

_SOURCE = {"type": "MaximumIndependentSet", "data": {}}


def _patch(monkeypatch, *, src: Eval, bnd: Eval, target_type="MaximumClique"):
    """Patch PredSolver so reduce returns a sentinel bundle and solve returns src for the
    source / bnd for the bundle. Returns the bundle sentinel."""
    bundle = {"target": {"type": target_type}, "source": _SOURCE}
    monkeypatch.setattr(vf.PredSolver, "reduce", lambda self, s, t: bundle)

    def fake_solve(self, instance, *, independent=False):
        return bnd if instance is bundle else src
    monkeypatch.setattr(vf.PredSolver, "solve", fake_solve)
    return bundle


def _cert(**over):
    c = {"rule": "r", "source": _SOURCE, "bundle": {"target": {"type": "MaximumClique"}}}
    c.update(over)
    return c


class TestVerifyRoundTrip:
    def test_match_is_no_bug(self, monkeypatch):
        _patch(monkeypatch, src=Eval.parse("Max(2)"), bnd=Eval.parse("Max(2)"))
        assert not verify(_cert()).accepted

    def test_value_mismatch_is_bug(self, monkeypatch):
        _patch(monkeypatch, src=Eval.parse("Max(2)"), bnd=Eval.parse("Max(1)"))
        v = verify(_cert())
        assert v.accepted and v.details["label"] == "optimum_not_preserved"

    def test_feasibility_mismatch_is_bug(self, monkeypatch):
        _patch(monkeypatch, src=Eval.parse("Max(2)"), bnd=Eval.parse("Max(None)"))
        v = verify(_cert())
        assert v.accepted and v.details["label"] == "feasibility_not_preserved"

    def test_spurious_solution_is_bug(self, monkeypatch):
        _patch(monkeypatch, src=Eval.parse("Max(None)"), bnd=Eval.parse("Max(2)"))
        v = verify(_cert())
        assert v.accepted and v.details["label"] == "spurious_solution"

    def test_sat_preserved_is_no_bug(self, monkeypatch):
        _patch(monkeypatch, src=Eval.parse("Or(true)"), bnd=Eval.parse("Or(true)"))
        assert not verify(_cert()).accepted

    def test_inconclusive_is_rejected(self, monkeypatch):
        bundle = {"target": {"type": "MaximumClique"}, "source": _SOURCE}
        monkeypatch.setattr(vf.PredSolver, "reduce", lambda self, s, t: bundle)

        def boom(self, instance, *, independent=False):
            raise vf.Inconclusive("timed out")
        monkeypatch.setattr(vf.PredSolver, "solve", boom)
        v = verify(_cert())
        assert not v.accepted and "inconclusive" in v.reason.lower()

    def test_ilp_target_rule_solves_source_independently(self, monkeypatch):
        seen = {}
        bundle = {"target": {"type": "ILP"}, "source": _SOURCE}
        monkeypatch.setattr(vf.PredSolver, "reduce", lambda self, s, t: bundle)

        def fake_solve(self, instance, *, independent=False):
            if instance is not bundle:
                seen["src_independent"] = independent
            return Eval.parse("Max(2)")
        monkeypatch.setattr(vf.PredSolver, "solve", fake_solve)
        verify(_cert(bundle={"target": {"type": "ILP"}}))
        assert seen["src_independent"] is True  # source of an *→ILP rule solved independently


class TestVerifyGuards:
    def test_missing_source_rejected(self):
        assert not verify({"rule": "r", "bundle": {"target": {"type": "X"}}}).accepted

    def test_missing_target_type_rejected(self):
        assert not verify({"rule": "r", "source": _SOURCE}).accepted

    def test_oversize_source_rejected(self):
        big = {"type": "X", "data": {"blob": "x" * (vf.MAX_INPUT_BYTES + 1)}}
        assert not verify({"rule": "r", "source": big,
                           "bundle": {"target": {"type": "X"}}}).accepted


# ── witness check (round-trip clean, but a specific target solution mis-extracts) ──

class TestWitness:
    def test_unsound_witness_caught(self, monkeypatch):
        bundle = _patch(monkeypatch, src=Eval.parse("Max(2)"), bnd=Eval.parse("Max(2)"))
        # target_config is a valid target solution, but its extraction is infeasible in source
        monkeypatch.setattr(
            vf.PredSolver, "evaluate",
            lambda self, inst, cfg: Eval.parse("Max(2)") if inst is bundle["target"]
            else Eval.parse("Max(None)"))
        monkeypatch.setattr(vf.PredSolver, "extract",
                            lambda self, b, cfg: ([1, 1, 0], Eval.parse("Max(None)")))
        v = verify(_cert(target_config="1,1,0"))
        assert v.accepted and "unsound_extraction" in v.reason

    def test_witness_valid_extraction_no_bug(self, monkeypatch):
        _patch(monkeypatch, src=Eval.parse("Max(2)"), bnd=Eval.parse("Max(2)"))
        monkeypatch.setattr(vf.PredSolver, "evaluate",
                            lambda self, inst, cfg: Eval.parse("Max(2)"))
        monkeypatch.setattr(vf.PredSolver, "extract",
                            lambda self, b, cfg: ([1, 0, 1], Eval.parse("Max(2)")))
        assert not verify(_cert(target_config="1,0,1")).accepted


# ── integration: real pred on the fixtures ────────────────────────────────────

@pytest.mark.integration
class TestRealFixtures:
    @pytest.mark.parametrize("name", [
        "valid_bug.json", "wrong_target.json", "valid_solution_claimed_invalid.json",
    ])
    def test_non_bug_fixtures_are_rejected(self, name):
        # These are non-bugs under the round-trip contract
        # (the old "valid_bug" used a non-optimal target_config — see verify.py calibration).
        cert = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        assert not verify(cert).accepted

    @pytest.mark.parametrize("name,label", [
        # weighted_mis: default-ILP source path; binpacking_zero: independent brute-force
        # source path (it is an *->ILP rule). Both confirmed against pred 0.6.0.
        # These are real bugs (the answer key) — kept in the gitignored private dir, so the
        # test skips when the private fixtures are absent (e.g. a fresh public clone).
        ("genuine_bug_weighted_mis.json", "optimum_not_preserved"),
        ("genuine_bug_binpacking_zero.json", "optimum_not_preserved"),
    ])
    def test_genuine_bug_fixtures_are_accepted(self, name, label):
        path = vf.PRIVATE_FIXTURES_DIR / name
        if not path.exists():
            pytest.skip(f"private accept-path fixture absent: {path}")
        v = verify(json.loads(path.read_text(encoding="utf-8")))
        assert v.accepted and v.details["label"] == label
