"""
Tests for benchmark/preflight.py — the pre-batch config validator.

The real model call can't be exercised without credentials, so we mock the three external
touch-points (pred version, rule listing, model build) and assert the check aggregation and
the all-pass/any-fail reporting are correct.
"""
from benchmark import preflight as pf


class _FakeModel:
    def __init__(self, content="OK", cost=0.000123, raise_exc=None):
        self._content, self._cost, self._raise = content, cost, raise_exc

    def query(self, messages):
        if self._raise is not None:
            raise self._raise
        return {"content": self._content, "extra": {"cost": self._cost}}


def _patch(monkeypatch, *, ver="0.6.0", rules=("a", "b"), model=None, build_exc=None):
    monkeypatch.setattr(pf, "verify_pred_version", lambda *a, **k: ver)
    monkeypatch.setattr(pf, "list_rules", lambda repo: list(rules))

    def _build(*a, **k):
        if build_exc is not None:
            raise build_exc
        return model if model is not None else _FakeModel()
    monkeypatch.setattr(pf, "_build_model", _build)


class TestRunChecks:
    def test_all_pass(self, monkeypatch):
        _patch(monkeypatch)
        results = pf.run_checks("anthropic/x", repo_dir="/repo")
        assert [ok for _, ok, _ in results] == [True, True, True]
        assert pf.format_report(results) is True

    def test_pred_failure_reported(self, monkeypatch):
        _patch(monkeypatch)
        monkeypatch.setattr(pf, "verify_pred_version",
                            lambda *a, **k: (_ for _ in ()).throw(ValueError("0.5.0 != 0.6.0")))
        results = pf.run_checks("anthropic/x", repo_dir="/repo")
        names = {n: (ok, d) for n, ok, d in results}
        assert names["pred binary"][0] is False and "0.5.0" in names["pred binary"][1]
        assert pf.format_report(results) is False

    def test_no_rules_fails(self, monkeypatch):
        _patch(monkeypatch, rules=())
        results = pf.run_checks("anthropic/x", repo_dir="/repo")
        assert dict((n, ok) for n, ok, _ in results)["library rules"] is False

    def test_model_call_failure_fails(self, monkeypatch):
        _patch(monkeypatch, model=_FakeModel(raise_exc=RuntimeError("401 unauthorized")))
        results = pf.run_checks("anthropic/x", repo_dir="/repo", price=None)
        call = dict((n, (ok, d)) for n, ok, d in results)["model call"]
        assert call[0] is False and "401" in call[1]

    def test_model_call_cost_shown(self, monkeypatch):
        _patch(monkeypatch, model=_FakeModel(cost=0.000456))
        results = pf.run_checks("anthropic/x", repo_dir="/repo")
        detail = dict((n, d) for n, _, d in results)["model call"]
        assert "0.000456" in detail
