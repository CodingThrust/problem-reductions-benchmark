"""Unit tests for the Codex headless backend (no real Codex invocation)."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmark import run_submission
from benchmark.codex_cli import _build_command, _child_env, parse_stream, run_repo_codex
from benchmark.run_submission import _load_env_file
from benchmark.submit_session import SubmissionSession


def _event(event: dict) -> str:
    return json.dumps(event)


def _ctx(tmp_path: Path) -> SimpleNamespace:
    repo = tmp_path / "repo"
    (repo / "src" / "rules").mkdir(parents=True)
    pred = tmp_path / "pred"
    pred.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    pred.chmod(0o755)
    return SimpleNamespace(repo_path=repo, pred_binary=pred, commit_hash="deadbeefcafe")


def _stub_codex(tmp_path: Path, events: list[dict], exit_code: int = 0) -> str:
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text("\n".join(_event(e) for e in events) + "\n", encoding="utf-8")
    script = tmp_path / "codex-stub"
    script.write_text(f'#!/bin/sh\ncat "{transcript}"\nexit {exit_code}\n', encoding="utf-8")
    script.chmod(0o755)
    return str(script)


class TestCommand:
    def test_noninteractive_is_ephemeral_and_controlled(self):
        cmd = _build_command("codex", "do work", "openai/gpt-5.4")
        assert cmd[:2] == ["codex", "exec"]
        assert "--json" in cmd
        assert "--ephemeral" in cmd
        assert cmd[cmd.index("--sandbox") + 1] == "workspace-write"
        assert "--ignore-user-config" in cmd
        assert "--ignore-rules" in cmd
        assert "--skip-git-repo-check" in cmd
        assert cmd[cmd.index("--model") + 1] == "gpt-5.4"
        assert cmd[-1] == "do work"

    def test_generic_api_key_uses_codex_cli_variable(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert _child_env(_ctx(tmp_path), "secret")["OPENAI_API_KEY"] == "secret"


class TestParseStream:
    def test_trajectory_steps_and_disjoint_usage(self):
        parsed = parse_stream([
            _event({"type": "thread.started", "thread_id": "thread-1"}),
            _event({"type": "item.completed", "item": {
                "id": "i1", "type": "command_execution", "command": "pred list",
                "aggregated_output": "rule-a\n", "status": "completed"}}),
            _event({"type": "item.completed", "item": {
                "id": "i2", "type": "agent_message", "text": "done"}}),
            _event({"type": "turn.completed", "usage": {
                "input_tokens": 24763, "cached_input_tokens": 24448,
                "output_tokens": 122, "reasoning_output_tokens": 7}}),
        ])
        assert parsed["thread_id"] == "thread-1"
        assert parsed["steps"] == 1
        assert [m["role"] for m in parsed["trajectory"]] == ["tool", "assistant"]
        assert parsed["usage"].input_tokens == 315
        assert parsed["usage"].cache_read_tokens == 24448
        assert parsed["usage"].output_tokens == 122
        assert parsed["usage"].total_tokens == 24885

    def test_skips_non_json_output(self):
        assert parse_stream(["warning", "", "42"])["trajectory"] == []


class TestCodexRuns:
    def test_uses_submit_workspace_as_sandbox_root(self, tmp_path):
        transcript = tmp_path / "codex.jsonl"
        transcript.write_text(_event({"type": "turn.completed", "usage": {}}) + "\n")
        stub = tmp_path / "codex-stub"
        stub.write_text(
            f'#!/bin/sh\npwd > workspace.txt\nsubmit --status > status.txt\n'
            f'cat "{transcript}"\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)

        with SubmissionSession() as submit_session:
            session = run_repo_codex("gpt-5.4", _ctx(tmp_path), codex_bin=str(stub),
                                     strategy="", submit_session=submit_session)
            assert Path(submit_session.workdir / "workspace.txt").read_text().strip() == str(
                submit_session.workdir.resolve())
            assert "0/100 used" in (submit_session.workdir / "status.txt").read_text()
            assert submit_session.reachable
            assert session["error"] is None

    def test_missing_cli(self, tmp_path):
        session = run_repo_codex("gpt-5.4", _ctx(tmp_path),
                                 codex_bin=str(tmp_path / "missing"), strategy="")
        assert session["error"].startswith("codex CLI not found")

    def test_partial_output_does_not_hide_cli_failure(self, tmp_path):
        stub = _stub_codex(tmp_path, [
            {"type": "item.completed", "item": {
                "type": "agent_message", "text": "partial work"}},
        ], exit_code=7)
        session = run_repo_codex("gpt-5.4", _ctx(tmp_path), codex_bin=stub, strategy="")
        assert session["error"].startswith("codex exited 7")
        assert "trajectory" not in session

    def test_turn_failed_event_is_an_error_even_on_zero_exit(self, tmp_path):
        stub = _stub_codex(tmp_path, [
            {"type": "turn.failed", "error": {"message": "quota exhausted"}},
        ])
        session = run_repo_codex("gpt-5.4", _ctx(tmp_path), codex_bin=stub, strategy="")
        assert session["error"].startswith("codex turn.failed")

    def test_whole_repo_persists_single_raw_stream(self, tmp_path):
        stub = _stub_codex(tmp_path, [
            {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
            {"type": "turn.completed", "usage": {
                "input_tokens": 30, "cached_input_tokens": 10, "output_tokens": 5}},
        ])
        submit_session = SimpleNamespace(limit=100)
        session = run_repo_codex("openai/gpt-5.4", _ctx(tmp_path), codex_bin=stub,
                                 strategy="", trajectory_dir=tmp_path / "out",
                                 submit_session=submit_session)
        assert session["usage"].total_tokens == 35
        assert (tmp_path / "out" / "openai_gpt-5.4_whole-repo.stream.jsonl").exists()
        assert not (tmp_path / "out" / "openai_gpt-5.4_whole-repo.jsonl").exists()


class TestWiring:
    @pytest.mark.parametrize("backend", ["codex", "claude-code"])
    def test_container_mode_rejects_cli_backend(self, backend, monkeypatch, capsys):
        monkeypatch.delenv("REPO_REF", raising=False)
        with pytest.raises(SystemExit):
            run_submission.main(["--model", "test-model", "--backend", backend])
        assert "make run-local" in capsys.readouterr().err

    def test_env_file_preserves_ambient_values(self, tmp_path, monkeypatch):
        env_file = tmp_path / "submission.env"
        env_file.write_text("# comment\nMODEL_NAME='from-file'\nAGENT_BACKEND=codex\n",
                            encoding="utf-8")
        monkeypatch.setenv("MODEL_NAME", "ambient")
        monkeypatch.delenv("AGENT_BACKEND", raising=False)
        _load_env_file(env_file)
        assert os.environ["MODEL_NAME"] == "ambient"
        assert os.environ["AGENT_BACKEND"] == "codex"

    def test_local_clone_is_forwarded_to_existing_runner(self, tmp_path, monkeypatch):
        seen = {}

        def fake_run(model, repo_dir, **kwargs):
            seen.update(model=model, repo_dir=repo_dir, **kwargs)
            return {"bugs_found": 0, "total_tokens_k": 0.0, "rules_tested": 0,
                    "submit_log": [], "submit_limit": kwargs["submit_limit"]}

        monkeypatch.setattr(run_submission, "clone_or_verify_repo",
                            lambda repo, ref, url: "a" * 40)
        monkeypatch.setattr(run_submission, "run", fake_run)
        run_submission.main([
            "--model", "gpt-5.4", "--repo-dir", str(tmp_path / "repo"),
            "--repo-ref", "v0.6.0", "--backend", "codex",
            "--host-cli",
            "--output", str(tmp_path / "submission.json"),
            "--trajectory-dir", str(tmp_path / "logs"),
        ])
        assert seen["backend"] == "codex"
        assert seen["library_commit"] == "a" * 40

    def test_local_clone_requires_separate_paths(self, monkeypatch):
        monkeypatch.delenv("OUTPUT", raising=False)
        monkeypatch.delenv("TRAJECTORY_DIR", raising=False)
        with pytest.raises(SystemExit):
            run_submission.main([
                "--model", "gpt-5.4", "--repo-ref", "v0.6.0",
                "--repo-dir", "/repo",
            ])

    def test_whole_repo_dispatches_to_codex(self, monkeypatch):
        called = {}

        def fake_repo(model, ctx, **kwargs):
            called["model"] = model
            return {"tokens_k": 1.0, "usage": None, "error": None}

        class FakeSubmissionSession:
            def __init__(self, limit):
                self.limit, self.attempts, self.reachable = limit, [], True

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def result_rows(self):
                return []

        monkeypatch.setattr(run_submission, "SubmissionSession", FakeSubmissionSession)
        monkeypatch.setattr(run_submission, "find_pred_binary", lambda: "pred")
        monkeypatch.setattr(run_submission, "verify_pred_version", lambda p: "1.2.3")
        monkeypatch.setattr(run_submission, "EnvContext",
                            lambda **kw: type("C", (), {"pred_version": "1.2.3", **kw})())
        monkeypatch.setattr("benchmark.codex_cli.run_repo_codex", fake_repo)
        sub = run_submission.run("gpt-5.4", "/repo", library_commit="abc",
                                 backend="codex")
        assert called["model"] == "gpt-5.4"
        assert sub["total_tokens_k"] == 1.0
