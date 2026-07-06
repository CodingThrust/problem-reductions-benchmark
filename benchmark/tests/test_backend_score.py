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

    def test_test_submission_excluded_from_board(self, tmp_path):
        # A submission marked test=true is scored + stored, but kept off the public board.
        subs, results = tmp_path / "subs", tmp_path / "results"
        subs.mkdir()
        _write_submission(subs, "prod.json", [{"rule": "R", "result": "no_bug"}],
                          model="anthropic/prod")
        _write_submission(subs, "t.json", [{"rule": "R", "result": "no_bug"}],
                          model="anthropic/tester", test=True)
        summary = bs.process_local(str(subs), str(results))
        assert {s["status"] for s in summary} == {"FINISHED"}  # both scored
        board = json.loads((results / "leaderboard.json").read_text())
        models = {e["model"] for e in board}
        assert "anthropic/prod" in models
        assert "anthropic/tester" not in models  # test entry excluded


class TestPerSubmissionBoard:
    # R2 filenames are "<epoch_ms>-<uuid>.json" — the slug derives from them.
    F1 = "1783317767895-dc9b2aae-c838-4c15-a5aa-2f4a25f6f82c.json"
    F2 = "1783317781060-6fb1aeb1-da04-4e1b-b6c5-f0b53c0d29a3.json"

    def _score(self, tmp_path, files):
        subs, results = tmp_path / "subs", tmp_path / "results"
        subs.mkdir()
        for name, model, test in files:
            _write_submission(subs, name, [{"rule": "R", "result": "no_bug"}],
                              model=model, test=test)
        bs.process_local(str(subs), str(results))
        return results

    def test_slug_is_deterministic_and_tagged(self):
        scored = {"model": "anthropic/claude-sonnet-4-6"}
        stem = self.F1[:-5]
        slug = bs.board_slug(scored, stem)
        assert slug == bs.board_slug(scored, stem)                     # deterministic
        assert slug == "anthropic-claude-sonnet-4-6--20260706T060247--dc9b2aae"
        full = {**scored, "results": [], "bugs_found": 0, "rules_tested": 0,
                "total_cost_usd": 0, "total_tokens_k": 0,
                "efficiency_bugs_per_ktok": 0, "efficiency_bugs_per_dollar": 0}
        e = bs.board_entry(full, stem)
        assert e["timestamp"] == "20260706T060247" and e["submission_id"] == "dc9b2aae"

    def test_one_entry_file_per_nontest_submission(self, tmp_path):
        results = self._score(tmp_path, [
            (self.F1, "anthropic/claude-sonnet-4-6", False),
            (self.F2, "openai/gpt-5", False),
            ("1783317799999-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json", "anthropic/claude-sonnet-4-6", True),
        ])
        board_files = sorted(p.name for p in (results / "board").glob("*.json"))
        assert board_files == [
            "anthropic-claude-sonnet-4-6--20260706T060247--dc9b2aae.json",
            "openai-gpt-5--20260706T060301--6fb1aeb1.json",
        ]  # two non-test entries; the test submission produced no public file

    def test_write_board_entries_is_idempotent(self, tmp_path):
        results = self._score(tmp_path, [(self.F1, "anthropic/x", False)])
        before = {p.name for p in (results / "board").glob("*.json")}
        bs.write_board_entries(results, results / "board")
        after = {p.name for p in (results / "board").glob("*.json")}
        assert before == after and len(after) == 1

    def test_build_board_dedups_best_per_model(self, tmp_path):
        d = tmp_path / "entries"
        d.mkdir()
        (d / "m--t1--a.json").write_text(json.dumps(
            {"model": "m", "bugs_found": 1, "efficiency_bugs_per_ktok": 0.1}))
        (d / "m--t2--b.json").write_text(json.dumps(
            {"model": "m", "bugs_found": 3, "efficiency_bugs_per_ktok": 0.2}))
        (d / "n--t1--c.json").write_text(json.dumps(
            {"model": "n", "bugs_found": 2, "efficiency_bugs_per_ktok": 0.1}))
        board = bs.build_board(d)
        assert [(e["model"], e["bugs_found"]) for e in board] == [("m", 3), ("n", 2)]

    def test_main_build_board(self, tmp_path, monkeypatch):
        d = tmp_path / "entries"
        d.mkdir()
        (d / "m--t--a.json").write_text(json.dumps({"model": "m", "bugs_found": 1}))
        out = tmp_path / "results.json"
        monkeypatch.setattr("sys.argv", ["backend_score", "--build-board", str(d), str(out)])
        bs.main()
        assert json.loads(out.read_text())[0]["model"] == "m"


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
