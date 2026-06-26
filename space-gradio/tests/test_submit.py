"""Unit tests for space-gradio/submit.py — structural validation (no gradio, no HF)."""
import submit


def _good(**over) -> dict:
    base = {
        "schema_version": "1.0",
        "model": "anthropic/claude-sonnet-4-6",
        "library_commit": "deadbeef",
        "budget_cap": 20,
        "bugs_found": 1,
        "total_cost_usd": 18.5,
        "total_tokens_k": 400.0,
        "rules_tested": 30,
        "results": [
            {"rule": "r1", "result": "bug_found", "cost": 1.0, "tokens_k": 10.0,
             "certificate": {"rule": "r1", "violation": "solve_mismatch",
                             "source": {}, "bundle": {}}},
            {"rule": "r2", "result": "no_certificate", "cost": 0.5, "tokens_k": 5.0},
        ],
    }
    base.update(over)
    return base


class TestValidate:
    def test_good_submission_passes(self):
        ok, errors, summary = submit.validate_submission(_good())
        assert ok is True
        assert errors == []
        assert summary["claimed_distinct_bugs"] == 1
        assert summary["budget_cap"] == 20

    def test_not_an_object(self):
        ok, errors, _ = submit.validate_submission([1, 2, 3])
        assert ok is False and errors

    def test_missing_required_field(self):
        data = _good()
        del data["model"]
        ok, errors, _ = submit.validate_submission(data)
        assert ok is False
        assert any("model" in e for e in errors)

    def test_wrong_budget_rejected(self):
        ok, errors, _ = submit.validate_submission(_good(budget_cap=10))
        assert ok is False
        assert any("budget_cap" in e for e in errors)

    def test_over_budget_rejected(self):
        ok, errors, _ = submit.validate_submission(_good(total_cost_usd=25.0))
        assert ok is False
        assert any("exceeds" in e for e in errors)

    def test_empty_results_rejected(self):
        ok, errors, _ = submit.validate_submission(_good(results=[]))
        assert ok is False
        assert any("results" in e for e in errors)

    def test_bad_row_rejected(self):
        ok, errors, _ = submit.validate_submission(
            _good(results=[{"rule": "r1"}]))  # missing result/cost/tokens_k
        assert ok is False
        assert any("results[0]" in e for e in errors)

    def test_distinct_claimed_bugs_dedup(self):
        cert = lambda v: {"rule": "r1", "violation": v, "source": {}, "bundle": {}}
        data = _good(results=[
            {"rule": "r1", "result": "bug_found", "cost": 1.0, "tokens_k": 10.0,
             "certificate": cert("solve_mismatch")},
            {"rule": "r1", "result": "bug_found", "cost": 1.0, "tokens_k": 10.0,
             "certificate": cert("unsound_extraction")},
        ])
        _, _, summary = submit.validate_submission(data)
        assert summary["claimed_distinct_bugs"] == 1  # same rule


class TestPush:
    def test_push_without_token_raises(self):
        try:
            submit.push_submission(_good(), repo="x/y", token=None)
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "token" in str(e).lower()

    def test_submission_path_is_deterministic(self):
        data = _good(created_at="2026-06-27T00:00:00+00:00")
        p = submit.submission_path(data, "alice")
        assert p == "submissions/alice/anthropic_claude-sonnet-4-6-2026-06-27T00_00_00+00_00.json"
