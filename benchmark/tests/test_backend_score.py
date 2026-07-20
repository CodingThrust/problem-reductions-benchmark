"""Tests for the private scoring queue and aggregate leaderboard projection."""
import json
from pathlib import Path

import pytest

from benchmark import backend_score as bs
from benchmark.tests.test_top50_submission import _artifact


def _write_submission(directory: Path, name: str, *, model="anthropic/test",
                      test=False, created_at=None) -> tuple[Path, dict]:
    artifact = _artifact(accepted_positions=())
    artifact["model"] = model
    if test:
        artifact["test"] = True
    if created_at:
        artifact["created_at"] = created_at
    path = directory / name
    path.write_text(json.dumps(artifact), encoding="utf-8")
    return path, artifact


def _repo(root: Path, artifact: dict) -> Path:
    repo = root / "repo"
    rules = repo / "src" / "rules"
    rules.mkdir(parents=True)
    for entry in artifact["shortlist"]:
        (rules / f"{entry['rule']}.rs").write_text("// fixture")
    return repo


def _scored(model="m", bugs=0, **extra) -> dict:
    return {
        "model": model, "library_commit": "c", "runner_version": "0.11.0",
        "pred_version": "0.6.0", "rankable": True, "rankability_errors": [],
        "verified_bugs": bugs, "bugs_found": bugs, "bugs_at_10": bugs,
        "bugs_at_25": bugs, "bugs_at_50": bugs, "first_attempt_accepts": bugs,
        "second_attempt_accepts": 0, "pred_calls_per_bug": None, "cap_hits": {},
        "usage_totals": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        "total_tokens_k": 0, "artifact_sha256": "a" * 64, "verifier_report": [],
        **extra,
    }


class TestProcessLocal:
    def test_valid_submission_scores_and_is_idempotent(self, tmp_path):
        subs, results = tmp_path / "subs", tmp_path / "results"
        subs.mkdir()
        _, artifact = _write_submission(subs, "a.json")
        repo = _repo(tmp_path, artifact)

        summary = bs.process_local(str(subs), str(results), str(repo))
        assert summary == [{"submission": "a.json", "status": "FINISHED",
                            "model": "anthropic/test", "bugs_found": 0}]
        assert bs.process_local(str(subs), str(results), str(repo)) == []
        assert json.loads((results / "leaderboard.json").read_text())[0]["bugs_found"] == 0

    def test_finds_nested_submissions(self, tmp_path):
        nested = tmp_path / "subs" / "alice"
        nested.mkdir(parents=True)
        _, artifact = _write_submission(nested, "run.json")
        repo = _repo(tmp_path, artifact)
        summary = bs.process_local(str(tmp_path / "subs"), str(tmp_path / "results"), str(repo))
        assert summary[0]["status"] == "FINISHED"
        assert (nested / "run.status.json").exists()

    def test_invalid_json_is_permanent(self, tmp_path):
        subs = tmp_path / "subs"
        subs.mkdir()
        (subs / "bad.json").write_text("{not json")
        summary = bs.process_local(str(subs), str(tmp_path / "results"), str(tmp_path / "repo"))
        assert summary[0]["status"] == "FAILED"
        assert summary[0]["retryable"] is False

    def test_official_gate_rejects_wrong_commit(self, tmp_path):
        subs = tmp_path / "subs"
        subs.mkdir()
        _, artifact = _write_submission(subs, "wrong.json")
        repo = _repo(tmp_path, artifact)
        summary = bs.process_local(
            str(subs), str(tmp_path / "results"), str(repo),
            official=True, expected_commit="expected")
        assert summary[0]["retryable"] is False
        assert "library_commit" in summary[0]["error"]

    def test_official_gate_accepts_current_protocol(self, tmp_path):
        subs = tmp_path / "subs"
        subs.mkdir()
        _, artifact = _write_submission(subs, "ok.json")
        repo = _repo(tmp_path, artifact)
        summary = bs.process_local(
            str(subs), str(tmp_path / "results"), str(repo),
            official=True, expected_commit=artifact["library_commit"])
        assert summary[0]["status"] == "FINISHED"

    def test_retryable_scorer_failure_exits_nonzero(self, tmp_path, monkeypatch):
        subs = tmp_path / "subs"
        subs.mkdir()
        _, artifact = _write_submission(subs, "a.json")
        repo = _repo(tmp_path, artifact)
        monkeypatch.setattr(bs, "score_top50_submission",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("pred crashed")))
        monkeypatch.setattr("sys.argv", ["backend_score", "--local", str(subs),
                                        str(tmp_path / "results"), "--repo-dir", str(repo)])
        with pytest.raises(SystemExit) as error:
            bs.main()
        assert error.value.code == 1

    def test_test_submission_is_not_published(self, tmp_path):
        subs = tmp_path / "subs"
        subs.mkdir()
        _, artifact = _write_submission(subs, "test.json", test=True)
        repo = _repo(tmp_path, artifact)
        bs.process_local(str(subs), str(tmp_path / "results"), str(repo))
        assert json.loads((tmp_path / "results" / "leaderboard.json").read_text()) == []
        assert list((tmp_path / "results" / "board").glob("*.json")) == []


class TestPerSubmissionBoard:
    F1 = "1783317767895-dc9b2aae-c838-4c15-a5aa-2f4a25f6f82c.json"
    F2 = "1783317781060-6fb1aeb1-da04-4e1b-b6c5-f0b53c0d29a3.json"

    def test_slug_is_deterministic_and_tagged(self):
        scored = _scored("anthropic/claude-sonnet-4-6")
        stem = self.F1[:-5]
        assert bs.board_slug(scored, stem) == (
            "anthropic-claude-sonnet-4-6--20260706T060247--dc9b2aae")
        entry = bs.board_entry(scored, stem)
        assert entry["timestamp"] == "20260706T060247"
        assert entry["submission_id"] == "dc9b2aae"

    def test_build_board_keeps_best_run_per_model_without_efficiency_tiebreak(self, tmp_path):
        entries = tmp_path / "entries"
        entries.mkdir()
        (entries / "m1.json").write_text(json.dumps({"model": "m", "bugs_found": 1}))
        (entries / "m2.json").write_text(json.dumps({"model": "m", "bugs_found": 3}))
        (entries / "n.json").write_text(json.dumps({"model": "n", "bugs_found": 2}))
        board = bs.build_board(entries)
        assert [(entry["model"], entry["bugs_found"]) for entry in board] == [
            ("m", 3), ("n", 2)]

    def test_board_entry_preserves_submitter_and_created_at(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        scored = _scored("anthropic/x", submitted_by="alice",
                         created_at="2026-07-18T12:34:56Z")
        (results / self.F1).write_text(json.dumps(scored))
        bs.write_board_entries(results, results / "board")
        entry = json.loads(next((results / "board").glob("*.json")).read_text())
        assert entry["submitted_by"] == "alice"
        assert entry["timestamp"] == "20260718T123456"
