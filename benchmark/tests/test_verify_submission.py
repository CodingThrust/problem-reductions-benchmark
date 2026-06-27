"""
Tests for benchmark/verify_submission.py — the authoritative backend scorer.

Unit tests monkeypatch ``verify`` so they need no pred binary; integration tests wrap
the real certificate fixtures and require pred.
"""
import json
from pathlib import Path

import pytest

from benchmark import verify_submission as vs
from benchmark.verify import Verdict

FIXTURES = Path(__file__).parent / "fixtures"


def _submission(results, **over) -> dict:
    base = {
        "schema_version": "1.0",
        "model": "anthropic/test",
        "library_commit": "deadbeef",
        "budget_cap": 20,
        "bugs_found": 999,  # deliberately wrong — the scorer must ignore it
        "total_cost_usd": 2.0,
        "total_tokens_k": 50.0,
        "rules_tested": len(results),
        "results": results,
    }
    base.update(over)
    return base


# ── scoring logic (no pred — verify monkeypatched) ────────────────────────────

class TestScoreSubmission:
    def test_ignores_self_reported_count(self, monkeypatch):
        monkeypatch.setattr(vs, "verify", lambda c, r=None: Verdict(True, "ok"))
        sub = _submission([
            {"rule": "r1", "result": "bug_found", "cost": 1.0, "tokens_k": 10.0,
             "certificate": {"rule": "r1", "violation": "solve_mismatch",
                             "source": {}, "bundle": {}}},
        ], bugs_found=999)
        scored, _ = vs.score_submission(sub)
        assert scored["bugs_found"] == 1  # not 999

    def test_rejected_certificate_does_not_count(self, monkeypatch):
        monkeypatch.setattr(vs, "verify", lambda c, r=None: Verdict(False, "nope"))
        sub = _submission([
            {"rule": "r1", "result": "bug_found", "cost": 1.0, "tokens_k": 10.0,
             "certificate": {"rule": "r1", "violation": "solve_mismatch",
                             "source": {}, "bundle": {}}},
        ])
        scored, report = vs.score_submission(sub)
        assert scored["bugs_found"] == 0
        assert scored["results"][0]["result"] == "rejected"
        assert scored["results"][0]["reject_reason"] == "nope"
        assert report[0]["accepted"] is False

    def test_distinct_rule_dedup(self, monkeypatch):
        monkeypatch.setattr(vs, "verify", lambda c, r=None: Verdict(True, "ok"))
        cert = lambda rule, v: {"rule": rule, "violation": v, "source": {}, "bundle": {}}
        sub = _submission([
            {"rule": "r1", "result": "bug_found", "cost": 1.0, "tokens_k": 10.0,
             "certificate": cert("r1", "solve_mismatch")},
            {"rule": "r1", "result": "bug_found", "cost": 1.0, "tokens_k": 10.0,
             "certificate": cert("r1", "unsound_extraction")},
            {"rule": "r2", "result": "bug_found", "cost": 1.0, "tokens_k": 10.0,
             "certificate": cert("r2", "solve_mismatch")},
        ])
        scored, _ = vs.score_submission(sub)
        assert scored["bugs_found"] == 2  # {r1, r2}, two certs on r1 collapse

    def test_rows_without_certificate_passthrough(self, monkeypatch):
        monkeypatch.setattr(vs, "verify", lambda c, r=None: Verdict(True, "ok"))
        sub = _submission([
            {"rule": "r1", "result": "no_certificate", "cost": 0.5, "tokens_k": 5.0},
        ])
        scored, report = vs.score_submission(sub)
        assert scored["bugs_found"] == 0
        assert report == []

    def test_scored_is_results_schema_shaped(self, monkeypatch):
        monkeypatch.setattr(vs, "verify", lambda c, r=None: Verdict(True, "ok"))
        sub = _submission([
            {"rule": "r1", "result": "bug_found", "cost": 1.0, "tokens_k": 10.0,
             "certificate": {"rule": "r1", "violation": "solve_mismatch",
                             "source": {}, "bundle": {}}},
        ])
        scored, _ = vs.score_submission(sub)
        for field in ("model", "library_commit", "bugs_found", "total_cost_usd",
                      "total_tokens_k", "efficiency_bugs_per_ktok",
                      "efficiency_bugs_per_dollar", "rules_tested", "results"):
            assert field in scored


# ── leaderboard entry ─────────────────────────────────────────────────────────

class TestLeaderboardEntry:
    def test_carries_budget_cap_and_certs(self, monkeypatch):
        monkeypatch.setattr(vs, "verify", lambda c, r=None: Verdict(True, "ok"))
        sub = _submission([
            {"rule": "r1", "result": "bug_found", "cost": 1.0, "tokens_k": 10.0,
             "certificate": {"rule": "r1", "violation": "solve_mismatch",
                             "source": {"type": "A"},
                             "bundle": {"target": {"type": "B"}}, "note": "x"}},
        ], budget_cap=20)
        scored, _ = vs.score_submission(sub)
        entry = vs.leaderboard_entry(sub, scored)
        assert entry["budget_cap"] == 20
        assert entry["placeholder"] is False
        assert entry["bugs_found"] == 1
        assert len(entry["bug_certificates"]) == 1
        c = entry["bug_certificates"][0]
        assert c["rule"] == "r1" and c["source_type"] == "A" and c["target_type"] == "B"


# ── integration: real fixtures + pred ─────────────────────────────────────────

@pytest.mark.integration
class TestRealVerification:
    def _wrap(self, fixture_name: str) -> dict:
        cert = json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))
        return _submission([
            {"rule": cert.get("rule", "r"), "result": "bug_found",
             "cost": 1.0, "tokens_k": 10.0, "certificate": cert},
        ])

    def test_valid_bug_is_not_a_bug_under_roundtrip(self):
        # Re-classified: the old "valid_bug" fixture used a non-optimal target_config; the
        # round-trip recovers the optimum, so it is NOT a bug. (A genuine-bug fixture for
        # the accept path is still TODO — see verify.py calibration note.)
        scored, report = vs.score_submission(self._wrap("valid_bug.json"))
        assert scored["bugs_found"] == 0
        assert report[0]["accepted"] is False

    def test_wrong_target_rejected(self):
        scored, _ = vs.score_submission(self._wrap("wrong_target.json"))
        assert scored["bugs_found"] == 0

    def test_false_alarm_rejected(self):
        scored, _ = vs.score_submission(self._wrap("valid_solution_claimed_invalid.json"))
        assert scored["bugs_found"] == 0
