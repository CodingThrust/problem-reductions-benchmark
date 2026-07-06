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


def _traj(cert: dict) -> list[dict]:
    """A minimal trajectory reproducing ``cert`` — satisfies the provenance gate."""
    return [{"role": "assistant",
             "content": "CERTIFICATE_START\n" + json.dumps(cert) + "\nCERTIFICATE_END"}]


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
        win_cert, lose_cert = cert("solve_mismatch"), cert("unsound_extraction")
        _write_submission(subs, "win.json",
                          [{"rule": "r1", "result": "bug_found", "cost": 1.0, "tokens_k": 10.0,
                            "certificate": win_cert, "trajectory": _traj(win_cert)}],
                          model="anthropic/winner")
        _write_submission(subs, "lose.json",
                          [{"rule": "r1", "result": "bug_found", "cost": 1.0, "tokens_k": 10.0,
                            "certificate": lose_cert, "trajectory": _traj(lose_cert)}],
                          model="anthropic/loser")
        bs.process_local(str(subs), str(results))
        board = json.loads((results / "leaderboard.json").read_text())
        assert [e["model"] for e in board] == ["anthropic/winner", "anthropic/loser"]
        assert board[0]["bugs_found"] == 1 and board[1]["bugs_found"] == 0
        assert all(e["budget_cap"] == 20 for e in board)

    def test_finds_nested_submissions(self, tmp_path):
        # Real layout: submissions/<handle>/<file>.json — must be found recursively.
        subs, results = tmp_path / "subs", tmp_path / "results"
        nested = subs / "submissions" / "alice"
        nested.mkdir(parents=True)
        _write_submission(nested, "run.json",
                          [{"rule": "r1", "result": "no_certificate", "cost": 1.0, "tokens_k": 10.0}])
        summary = bs.process_local(str(subs), str(results))
        assert len(summary) == 1 and summary[0]["status"] == "FINISHED"
        assert (nested / "run.status.json").exists()
        assert (results / "leaderboard.json").exists()

    def test_zero_submissions_writes_empty_board(self, tmp_path):
        subs, results = tmp_path / "subs", tmp_path / "results"
        subs.mkdir()
        summary = bs.process_local(str(subs), str(results))
        assert summary == []
        assert json.loads((results / "leaderboard.json").read_text()) == []

    def test_failed_submission_marked(self, tmp_path):
        subs, results = tmp_path / "subs", tmp_path / "results"
        subs.mkdir()
        (subs / "bad.json").write_text("{not valid json", encoding="utf-8")
        summary = bs.process_local(str(subs), str(results))
        assert summary[0]["status"] == "FAILED"
        status = json.loads((subs / "bad.status.json").read_text())
        assert status["status"] == "FAILED"

    def test_main_exits_nonzero_on_failure(self, tmp_path, monkeypatch):
        # A FAILED submission must make the CLI exit non-zero, so score-from-r2.yml
        # stops before archiving incoming/ → processed/ and the submission stays queued.
        subs, results = tmp_path / "subs", tmp_path / "results"
        subs.mkdir()
        (subs / "bad.json").write_text("{not valid json", encoding="utf-8")
        monkeypatch.setattr("sys.argv", ["backend_score", "--local", str(subs), str(results)])
        with pytest.raises(SystemExit) as ei:
            bs.main()
        assert ei.value.code == 1

    def test_main_exits_zero_when_all_scored(self, tmp_path, monkeypatch):
        # A clean batch (no FAILED) exits 0 so the workflow proceeds to archive + publish.
        subs, results = tmp_path / "subs", tmp_path / "results"
        subs.mkdir()
        _write_submission(subs, "a.json", [{"rule": "R", "result": "no_bug"}])
        monkeypatch.setattr("sys.argv", ["backend_score", "--local", str(subs), str(results)])
        bs.main()  # returns normally (no SystemExit) → exit 0


class TestWebhook:
    def _payload(self, scope="repo.content", repo="org/subs", rtype="dataset"):
        return {"event": {"action": "update", "scope": scope},
                "repo": {"type": rtype, "name": repo},
                "webhook": {"id": "w", "version": 3}}

    def test_parse_payload(self):
        info = bs.parse_webhook_payload(self._payload())
        assert info == {"repo_id": "org/subs", "repo_type": "dataset",
                        "action": "update", "scope": "repo.content"}

    def test_content_change_triggers_scoring(self, monkeypatch):
        called = {}
        monkeypatch.setattr(bs, "process_hf",
                            lambda subs, results, repo_dir=None, token=None:
                            called.update(subs=subs, results=results) or [{"status": "FINISHED"}])
        out = bs.process_webhook(self._payload(repo="org/subs"),
                                 results_repo="org/results")
        assert out == [{"status": "FINISHED"}]
        assert called == {"subs": "org/subs", "results": "org/results"}

    def test_discussion_event_ignored(self, monkeypatch):
        monkeypatch.setattr(bs, "process_hf",
                            lambda *a, **k: pytest.fail("should not score on discussion"))
        assert bs.process_webhook(self._payload(scope="discussion"),
                                  results_repo="org/results") == []

    def test_secret_mismatch_raises(self):
        with pytest.raises(PermissionError):
            bs.process_webhook(self._payload(), results_repo="org/results",
                               expected_secret="s3cret", provided_secret="wrong")

    def test_missing_results_repo_raises(self, monkeypatch):
        monkeypatch.delenv("RESULTS_REPO", raising=False)
        with pytest.raises(RuntimeError):
            bs.process_webhook(self._payload(), results_repo=None)

    def test_payload_from_env(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK_PAYLOAD", json.dumps(self._payload(repo="org/s")))
        monkeypatch.setenv("RESULTS_REPO", "org/r")
        monkeypatch.setattr(bs, "process_hf",
                            lambda subs, results, repo_dir=None, token=None:
                            [{"subs": subs, "results": results}])
        out = bs.process_webhook()
        assert out == [{"subs": "org/s", "results": "org/r"}]


@pytest.mark.integration
class TestRealFixture:
    def test_genuine_bug_end_to_end_scores_via_real_pred(self, tmp_path):
        # End-to-end through real pred: a genuine reduction bug is verified and ranked.
        # The fixture is the answer key (gitignored private dir); skip when absent.
        from benchmark.verify import PRIVATE_FIXTURES_DIR
        path = PRIVATE_FIXTURES_DIR / "genuine_bug_weighted_mis.json"
        if not path.exists():
            pytest.skip(f"private accept-path fixture absent: {path}")
        subs, results = tmp_path / "subs", tmp_path / "results"
        subs.mkdir()
        cert = json.loads(path.read_text(encoding="utf-8"))
        _write_submission(subs, "real.json",
                          [{"rule": cert.get("rule", "r"), "result": "bug_found",
                            "cost": 1.0, "tokens_k": 10.0, "certificate": cert,
                            "trajectory": _traj(cert)}],
                          model="anthropic/real")
        bs.process_local(str(subs), str(results))
        board = json.loads((results / "leaderboard.json").read_text())
        assert board[0]["model"] == "anthropic/real"
        assert board[0]["bugs_found"] == 1

    def test_non_bug_fixture_scores_zero(self, tmp_path):
        # The re-classified valid_bug fixture is not a bug under the round-trip contract.
        subs, results = tmp_path / "subs", tmp_path / "results"
        subs.mkdir()
        cert = json.loads((FIXTURES / "valid_bug.json").read_text(encoding="utf-8"))
        _write_submission(subs, "real.json",
                          [{"rule": cert.get("rule", "r"), "result": "bug_found",
                            "cost": 1.0, "tokens_k": 10.0, "certificate": cert}],
                          model="anthropic/real")
        bs.process_local(str(subs), str(results))
        board = json.loads((results / "leaderboard.json").read_text())
        assert board[0]["bugs_found"] == 0
