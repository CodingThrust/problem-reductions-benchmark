"""
Tests for benchmark/backend_score.py — the local scoring queue.

Mechanics tests use certificate-free submissions (no pred needed). One integration test
wraps the real valid_bug fixture to prove end-to-end scoring writes a ranked entry.
"""
import json
from pathlib import Path

import pytest

from benchmark import backend_score as bs

FIXTURES = Path(__file__).parent / "fixtures"


def _write_submission(subs_dir: Path, name: str, results, **over) -> Path:
    sub = {
        "schema_version": "1.0",
        "model": over.pop("model", "anthropic/test"),
        "library_commit": "deadbeef",
        "budget_cap": 20,
        "bugs_found": over.pop("bugs_found", 0),
        "total_cost_usd": 2.0,
        "total_tokens_k": 50.0,
        "rules_tested": len(results),
        "results": results,
    }
    sub.update(over)
    p = subs_dir / name
    p.write_text(json.dumps(sub), encoding="utf-8")
    return p


class TestProcessLocal:
    def test_no_cert_submission_scores_zero(self, tmp_path):
        subs, results = tmp_path / "subs", tmp_path / "results"
        subs.mkdir()
        _write_submission(subs, "a.json",
                          [{"rule": "r1", "result": "no_certificate", "cost": 1.0, "tokens_k": 10.0}])
        summary = bs.process_local(str(subs), str(results))
        assert summary[0]["status"] == "FINISHED"
        assert summary[0]["bugs_found"] == 0
        assert (results / "a.json").exists()
        assert (subs / "a.status.json").exists()

    def test_status_transitions_to_finished(self, tmp_path):
        subs, results = tmp_path / "subs", tmp_path / "results"
        subs.mkdir()
        _write_submission(subs, "a.json",
                          [{"rule": "r1", "result": "no_certificate", "cost": 1.0, "tokens_k": 10.0}])
        bs.process_local(str(subs), str(results))
        status = json.loads((subs / "a.status.json").read_text())
        assert status["status"] == "FINISHED"

    def test_idempotent_skips_finished(self, tmp_path):
        subs, results = tmp_path / "subs", tmp_path / "results"
        subs.mkdir()
        _write_submission(subs, "a.json",
                          [{"rule": "r1", "result": "no_certificate", "cost": 1.0, "tokens_k": 10.0}])
        bs.process_local(str(subs), str(results))
        again = bs.process_local(str(subs), str(results))
        assert again == []  # nothing left pending

    def test_leaderboard_aggregated_and_ranked(self, tmp_path, monkeypatch):
        # Two models scored via a monkeypatched verifier: only solve_mismatch confirms.
        from benchmark.verify import Verdict
        import benchmark.verify_submission as vs
        monkeypatch.setattr(vs, "verify",
                            lambda c, r=None: Verdict(c.get("violation") == "solve_mismatch", "x"))
        subs, results = tmp_path / "subs", tmp_path / "results"
        subs.mkdir()
        cert = lambda v: {"rule": "r1", "violation": v, "source": {}, "bundle": {}}
        _write_submission(subs, "win.json",
                          [{"rule": "r1", "result": "bug_found", "cost": 1.0, "tokens_k": 10.0,
                            "certificate": cert("solve_mismatch")}],
                          model="anthropic/winner")
        _write_submission(subs, "lose.json",
                          [{"rule": "r1", "result": "bug_found", "cost": 1.0, "tokens_k": 10.0,
                            "certificate": cert("unsound_extraction")}],
                          model="anthropic/loser")
        bs.process_local(str(subs), str(results))
        board = json.loads((results / "leaderboard.json").read_text())
        assert [e["model"] for e in board] == ["anthropic/winner", "anthropic/loser"]
        assert board[0]["bugs_found"] == 1 and board[1]["bugs_found"] == 0
        assert all(e["budget_cap"] == 20 for e in board)

    def test_failed_submission_marked(self, tmp_path):
        subs, results = tmp_path / "subs", tmp_path / "results"
        subs.mkdir()
        (subs / "bad.json").write_text("{not valid json", encoding="utf-8")
        summary = bs.process_local(str(subs), str(results))
        assert summary[0]["status"] == "FAILED"
        status = json.loads((subs / "bad.status.json").read_text())
        assert status["status"] == "FAILED"


@pytest.mark.integration
class TestRealFixture:
    def test_valid_bug_end_to_end(self, tmp_path):
        subs, results = tmp_path / "subs", tmp_path / "results"
        subs.mkdir()
        cert = json.loads((FIXTURES / "valid_bug.json").read_text(encoding="utf-8"))
        _write_submission(subs, "real.json",
                          [{"rule": cert.get("rule", "r"), "result": "bug_found",
                            "cost": 1.0, "tokens_k": 10.0, "certificate": cert}],
                          model="anthropic/real")
        bs.process_local(str(subs), str(results))
        board = json.loads((results / "leaderboard.json").read_text())
        assert board[0]["model"] == "anthropic/real"
        assert board[0]["bugs_found"] == 1
