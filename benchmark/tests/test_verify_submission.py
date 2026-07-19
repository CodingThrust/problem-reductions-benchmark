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


def _traj(cert: dict) -> list[dict]:
    """A minimal trajectory that reproduces ``cert`` — satisfies the provenance gate."""
    return [{"role": "assistant",
             "content": "CERTIFICATE_START\n" + json.dumps(cert) + "\nCERTIFICATE_END"}]


def _bug_row(cert: dict, **over) -> dict:
    row = {"rule": cert.get("rule", "r"), "result": "bug_found",
           "tokens_k": 10.0, "certificate": cert, "trajectory": _traj(cert)}
    row.update(over)
    return row


def _submission(results, **over) -> dict:
    base = {
        "model": "anthropic/test",
        "library_commit": "deadbeef",
        "bugs_found": 999,  # deliberately wrong — the scorer must ignore it
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
            _bug_row({"rule": "r1", "violation": "solve_mismatch", "source": {}, "bundle": {}}),
        ], bugs_found=999)
        scored, _ = vs.score_submission(sub)
        assert scored["bugs_found"] == 1  # not 999

    def test_rejected_certificate_does_not_count(self, monkeypatch):
        monkeypatch.setattr(vs, "verify", lambda c, r=None: Verdict(False, "nope"))
        sub = _submission([
            _bug_row({"rule": "r1", "violation": "solve_mismatch", "source": {}, "bundle": {}}),
        ])
        scored, report = vs.score_submission(sub)
        assert scored["bugs_found"] == 0
        assert scored["results"][0]["result"] == "rejected"
        assert scored["results"][0]["reject_reason"] == "nope"
        assert report[0]["accepted"] is False

    def test_no_trajectory_not_counted(self, monkeypatch):
        # pred confirms the round-trip failure, but no trajectory is attached → the bug is
        # not scored (provenance gate): a pasted answer key must not count.
        monkeypatch.setattr(vs, "verify", lambda c, r=None: Verdict(True, "ok"))
        sub = _submission([
            {"rule": "r1", "result": "bug_found", "tokens_k": 10.0,
             "certificate": {"rule": "r1", "violation": "solve_mismatch", "source": {}, "bundle": {}}},
        ])
        scored, report = vs.score_submission(sub)
        assert scored["bugs_found"] == 0
        assert scored["results"][0]["result"] == "rejected"
        assert "provenance" in scored["results"][0]["reject_reason"]
        assert report[0]["accepted"] is False

    def test_trajectory_source_mismatch_not_counted(self, monkeypatch):
        # A trajectory whose certificate names a different source than the submitted one is
        # not a valid provenance proof.
        monkeypatch.setattr(vs, "verify", lambda c, r=None: Verdict(True, "ok"))
        cert = {"rule": "r1", "violation": "solve_mismatch", "source": {"n": 1}, "bundle": {}}
        other = {"rule": "r1", "violation": "solve_mismatch", "source": {"n": 999}, "bundle": {}}
        sub = _submission([_bug_row(cert, trajectory=_traj(other))])
        scored, _ = vs.score_submission(sub)
        assert scored["bugs_found"] == 0

    def test_multi_cert_trajectory_provenance(self, monkeypatch):
        # A whole-repo session emits MANY certificates in one shared trajectory. Each bug row
        # must pass provenance against ANY matching block — not just the last one.
        monkeypatch.setattr(vs, "verify", lambda c, r=None: Verdict(True, "ok"))
        c1 = {"rule": "r1", "violation": "solve_mismatch", "source": {"n": 1}, "bundle": {}}
        c2 = {"rule": "r2", "violation": "solve_mismatch", "source": {"n": 2}, "bundle": {}}
        shared = _traj(c1) + _traj(c2)  # both certs in the one session trajectory
        sub = _submission([_bug_row(c1, trajectory=shared),
                           _bug_row(c2, trajectory=shared)])
        scored, _ = vs.score_submission(sub)
        assert scored["bugs_found"] == 2  # both counted, even though c1 is not the last block
        assert all(r["result"] == "bug_found" for r in scored["results"])

    def test_envelope_trajectory_provenance(self, monkeypatch):
        # whole-repo: rows carry NO trajectory; the shared session log is on the envelope,
        # parsed once. Each bug's cert is matched against it.
        monkeypatch.setattr(vs, "verify", lambda c, r=None: Verdict(True, "ok"))
        c1 = {"rule": "r1", "violation": "solve_mismatch", "source": {"n": 1}, "bundle": {}}
        c2 = {"rule": "r2", "violation": "solve_mismatch", "source": {"n": 2}, "bundle": {}}
        rows = [{"rule": "r1", "result": "bug_found", "tokens_k": 0.0, "certificate": c1},
                {"rule": "r2", "result": "bug_found", "tokens_k": 0.0, "certificate": c2}]
        sub = _submission(rows, trajectory=_traj(c1) + _traj(c2))
        scored, _ = vs.score_submission(sub)
        assert scored["bugs_found"] == 2

    def test_no_trajectory_anywhere_not_counted(self, monkeypatch):
        # A cert with neither a row trajectory nor an envelope trajectory fails provenance.
        monkeypatch.setattr(vs, "verify", lambda c, r=None: Verdict(True, "ok"))
        c1 = {"rule": "r1", "violation": "solve_mismatch", "source": {}, "bundle": {}}
        rows = [{"rule": "r1", "result": "bug_found", "tokens_k": 0.0, "certificate": c1}]
        scored, _ = vs.score_submission(_submission(rows))
        assert scored["bugs_found"] == 0

    def test_new_submission_requires_bounded_submit_ledger(self, monkeypatch):
        monkeypatch.setattr(vs, "verify", lambda c, r=None: Verdict(True, "ok"))
        cert = {"rule": "r1", "source": {"n": 1}, "bundle": {}}
        sub = _submission(
            [_bug_row(cert)],
            submit_limit=100,
            submit_log=[{"attempt": 1, "accepted": True, "rule": "r1",
                         "reason": "ok", "certificate": cert}],
        )
        scored, report = vs.score_submission(sub)
        assert scored["bugs_found"] == 1
        assert "bounded submit command" in report[0]["provenance"]

    def test_final_answer_certificate_not_in_submit_ledger_is_rejected(self, monkeypatch):
        monkeypatch.setattr(vs, "verify", lambda c, r=None: Verdict(True, "ok"))
        submitted = {"rule": "r1", "source": {"n": 1}, "bundle": {}}
        only_in_prose = {"rule": "r2", "source": {"n": 2}, "bundle": {}}
        sub = _submission(
            [_bug_row(only_in_prose)],
            submit_limit=100,
            submit_log=[{"attempt": 1, "accepted": True, "rule": "r1",
                         "reason": "ok", "certificate": submitted}],
        )
        scored, _ = vs.score_submission(sub)
        assert scored["bugs_found"] == 0
        assert "bounded submit command" in scored["results"][0]["reject_reason"]

    def test_tampered_budget_log_fails_closed(self, monkeypatch):
        monkeypatch.setattr(vs, "verify", lambda c, r=None: Verdict(True, "ok"))
        cert = {"rule": "r1", "source": {"n": 1}, "bundle": {}}
        sub = _submission(
            [_bug_row(cert)],
            submit_limit=0,
            submit_log=[{"attempt": 1, "accepted": True, "rule": "r1",
                         "reason": "ok", "certificate": cert}],
        )
        scored, _ = vs.score_submission(sub)
        assert scored["bugs_found"] == 0
        assert "exceeds submit_limit" in scored["results"][0]["reject_reason"]

    def test_ledger_certificate_cannot_be_counted_under_another_row_rule(self, monkeypatch):
        monkeypatch.setattr(vs, "verify", lambda c, r=None: Verdict(True, "ok"))
        cert = {"rule": "r1", "source": {"n": 1}, "bundle": {}}
        forged_row = _bug_row(cert, rule="r2")
        sub = _submission(
            [forged_row], submit_limit=1,
            submit_log=[{"attempt": 1, "accepted": True, "rule": "r1",
                         "reason": "ok", "certificate": cert}],
        )
        scored, _ = vs.score_submission(sub)
        assert scored["bugs_found"] == 0
        assert "row rule" in scored["results"][0]["reject_reason"]

    def test_distinct_rule_dedup(self, monkeypatch):
        monkeypatch.setattr(vs, "verify", lambda c, r=None: Verdict(True, "ok"))
        cert = lambda rule, v: {"rule": rule, "violation": v, "source": {"r": rule}, "bundle": {}}
        sub = _submission([
            _bug_row(cert("r1", "solve_mismatch")),
            _bug_row(cert("r1", "unsound_extraction")),
            _bug_row(cert("r2", "solve_mismatch")),
        ])
        scored, _ = vs.score_submission(sub)
        assert scored["bugs_found"] == 2  # {r1, r2}, two certs on r1 collapse

    def test_rows_without_certificate_passthrough(self, monkeypatch):
        monkeypatch.setattr(vs, "verify", lambda c, r=None: Verdict(True, "ok"))
        sub = _submission([
            {"rule": "r1", "result": "no_certificate", "tokens_k": 5.0},
        ])
        scored, report = vs.score_submission(sub)
        assert scored["bugs_found"] == 0
        assert report == []

    def test_recomputes_tokens_from_usage_totals(self, monkeypatch):
        # Zero-trust tokens: the submission self-reports a bogus total_tokens_k, but carries
        # usage_totals — the scorer recomputes tokens from the 4-bucket primitive and ignores
        # the bogus figure (mirrors ignoring self-reported bugs_found).
        monkeypatch.setattr(vs, "verify", lambda c, r=None: Verdict(True, "ok"))
        sub = _submission(
            [_bug_row({"rule": "r1", "violation": "solve_mismatch", "source": {}, "bundle": {}})],
            total_tokens_k=0.001,  # bogus — must be ignored
            usage_totals={"input": 1_000_000, "output": 1_000_000,
                          "cache_read": 0, "cache_write": 0},
        )
        scored, _ = vs.score_submission(sub)
        assert scored["total_tokens_k"] == 2000.0
        assert scored["efficiency_bugs_per_ktok"] == round(1 / 2000.0, 4)
        # the primitive survives into the scored result (→ leaderboard entry)
        assert scored["usage_totals"]["input"] == 1_000_000

    def test_legacy_submission_without_usage_uses_self_reported(self, monkeypatch):
        # No usage_totals (legacy) → fall back to the self-reported figure.
        monkeypatch.setattr(vs, "verify", lambda c, r=None: Verdict(True, "ok"))
        sub = _submission(
            [_bug_row({"rule": "r1", "violation": "solve_mismatch", "source": {}, "bundle": {}})],
            total_tokens_k=50.0)
        scored, _ = vs.score_submission(sub)
        assert scored["total_tokens_k"] == 50.0

    def test_scored_is_results_schema_shaped(self, monkeypatch):
        monkeypatch.setattr(vs, "verify", lambda c, r=None: Verdict(True, "ok"))
        sub = _submission([
            _bug_row({"rule": "r1", "violation": "solve_mismatch", "source": {}, "bundle": {}}),
        ])
        scored, _ = vs.score_submission(sub)
        for field in ("model", "library_commit", "bugs_found",
                      "total_tokens_k", "efficiency_bugs_per_ktok",
                      "rules_tested", "results"):
            assert field in scored


# ── leaderboard entry ─────────────────────────────────────────────────────────

class TestLeaderboardEntry:
    def test_aggregate_only_no_certificates(self, monkeypatch):
        # The public entry must carry counts/tokens but NEVER the certificates or the
        # identities of the buggy rules — publishing those is a free answer key.
        monkeypatch.setattr(vs, "verify", lambda c, r=None: Verdict(True, "ok"))
        sub = _submission([
            _bug_row({"rule": "r1", "violation": "solve_mismatch",
                      "source": {"type": "A"}, "bundle": {"target": {"type": "B"}}, "note": "x"}),
        ])
        scored, _ = vs.score_submission(sub)
        entry = vs.leaderboard_entry(sub, scored)
        assert entry["placeholder"] is False
        assert entry["bugs_found"] == 1
        # no per-bug drilldown, no rule identities, no certificate fields leak out
        assert "bug_certificates" not in entry
        blob = json.dumps(entry)
        assert "r1" not in blob and "solve_mismatch" not in blob and "source" not in blob


# ── integration: real fixtures + pred ─────────────────────────────────────────

@pytest.mark.integration
class TestRealVerification:
    def _wrap(self, fixture_name: str) -> dict:
        cert = json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))
        return _submission([_bug_row(cert)])

    def test_genuine_bug_is_confirmed(self):
        # Accept path: a real reduction bug (weighted MIS -> IntegralFlowBundles) is
        # confirmed end-to-end and counted. The fixture is the answer key, so it lives in
        # the gitignored private dir; skip when absent (e.g. a fresh public clone).
        from benchmark.verify import PRIVATE_FIXTURES_DIR
        path = PRIVATE_FIXTURES_DIR / "genuine_bug_weighted_mis.json"
        if not path.exists():
            pytest.skip(f"private accept-path fixture absent: {path}")
        cert = json.loads(path.read_text(encoding="utf-8"))
        scored, report = vs.score_submission(_submission([_bug_row(cert)]))
        assert scored["bugs_found"] == 1
        assert report[0]["accepted"] is True

    def test_valid_bug_is_not_a_bug_under_roundtrip(self):
        # Re-classified: the old "valid_bug" fixture used a non-optimal target_config; the
        # round-trip recovers the optimum, so it is NOT a bug.
        scored, report = vs.score_submission(self._wrap("valid_bug.json"))
        assert scored["bugs_found"] == 0
        assert report[0]["accepted"] is False

    def test_wrong_target_rejected(self):
        scored, _ = vs.score_submission(self._wrap("wrong_target.json"))
        assert scored["bugs_found"] == 0

    def test_false_alarm_rejected(self):
        scored, _ = vs.score_submission(self._wrap("valid_solution_claimed_invalid.json"))
        assert scored["bugs_found"] == 0
