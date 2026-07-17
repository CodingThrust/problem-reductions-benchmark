"""Tests for the whole-repo runner mode (one bounded-submit session for the library)."""

import subprocess

from benchmark import run_submission


class TestBuildSubmissionTotals:
    def test_explicit_session_totals_override_row_sums(self):
        # whole-repo rows carry 0 tokens; the session total comes in as an explicit arg.
        rows = [{"rule": "r1", "result": "bug_found", "tokens_k": 0.0}]
        sub = run_submission.build_submission(
            "m", rows, library_commit="c", total_tokens_k=42.0)
        assert sub["total_tokens_k"] == 42.0
        assert sub["efficiency_bugs_per_ktok"] == round(1 / 42.0, 4)


class TestCrashSalvage:
    def test_run_error_recorded_when_session_dies(self, monkeypatch):
        # A fatal session error must still produce a submission (partial salvage) tagged with
        # run_error — not crash and leave a stale submission.json.
        def dying_session(model, ctx, **kw):
            return {"tokens_k": 5.0, "usage": None,
                    "error": "APIError: quota exhausted"}
        monkeypatch.setattr(run_submission, "find_pred_binary", lambda: "pred")
        monkeypatch.setattr(run_submission, "verify_pred_version", lambda p: "1.2.3")
        monkeypatch.setattr(run_submission, "EnvContext",
                            lambda **kw: type("C", (), {"pred_version": "1.2.3", **kw})())
        monkeypatch.setattr("benchmark.run_mini.run_repo_session", dying_session)
        sub = run_submission.run("m", "/repo", library_commit="c")
        assert sub["run_error"] == "APIError: quota exhausted"
        assert sub["bugs_found"] == 0

    def test_no_run_error_key_on_clean_run(self):
        sub = run_submission.build_submission("m", [], library_commit="c")
        assert "run_error" not in sub

    def test_unprobed_submit_channel_is_not_a_clean_zero(self, monkeypatch, tmp_path):
        def clean_but_unprobed(model, ctx, **kw):
            return {"tokens_k": 1.0, "usage": None, "error": None}

        monkeypatch.setattr(run_submission, "find_pred_binary", lambda: "pred")
        monkeypatch.setattr(run_submission, "verify_pred_version", lambda p: "1.2.3")
        monkeypatch.setattr(run_submission, "EnvContext",
                            lambda **kw: type("C", (), {"pred_version": "1.2.3", **kw})())
        monkeypatch.setattr("benchmark.run_mini.run_repo_session", clean_but_unprobed)
        sub = run_submission.run("m", "/repo", library_commit="c",
                                 trajectory_dir=tmp_path / "logs")
        assert "submit channel was not successfully probed" in sub["run_error"]

    def test_successful_status_probe_allows_a_clean_zero(self, monkeypatch):
        def clean_and_probed(model, ctx, **kw):
            status = subprocess.run(["submit", "--status"], capture_output=True, text=True)
            assert status.returncode == 0
            return {"tokens_k": 1.0, "usage": None, "error": None}

        monkeypatch.setattr(run_submission, "find_pred_binary", lambda: "pred")
        monkeypatch.setattr(run_submission, "verify_pred_version", lambda p: "1.2.3")
        monkeypatch.setattr(run_submission, "EnvContext",
                            lambda **kw: type("C", (), {"pred_version": "1.2.3", **kw})())
        monkeypatch.setattr("benchmark.run_mini.run_repo_session", clean_and_probed)
        sub = run_submission.run("m", "/repo", library_commit="c")
        assert "run_error" not in sub


class TestRunWholeRepoWiring:
    def test_run_dispatches_to_repo_session(self, monkeypatch):
        # run() must call the single repository session and build its envelope.
        def fake_repo_session(model, ctx, **kw):
            kw["submit_session"].result_rows = lambda: [
                {"rule": "r1", "result": "bug_found", "tokens_k": 0.0,
                 "certificate": {"rule": "r1", "source": {}}}]
            return {"tokens_k": 30.0, "usage": None, "error": None}

        monkeypatch.setattr(run_submission, "find_pred_binary", lambda: "pred")
        monkeypatch.setattr(run_submission, "verify_pred_version", lambda p: "1.2.3")
        monkeypatch.setattr(run_submission, "EnvContext",
                            lambda **kw: type("C", (), {"pred_version": "1.2.3", **kw})())
        monkeypatch.setattr("benchmark.run_mini.run_repo_session", fake_repo_session)

        sub = run_submission.run("m", "/repo", library_commit="deadbeef")
        assert sub["total_tokens_k"] == 30.0
        assert sub["bugs_found"] == 1
        assert "trajectory" not in sub                   # logs live in trajectory_dir
        assert "trajectory" not in sub["results"][0]
