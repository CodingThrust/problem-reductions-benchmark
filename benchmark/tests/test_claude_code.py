"""Unit tests for the claude-code backend (no network, no real claude CLI, no pred)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmark import run_submission
from benchmark.claude_code import _build_command, parse_stream, run_repo_claude
from benchmark.run_submission import run
from benchmark.submit_session import SubmissionSession

CERT_TEXT = (
    "Found it.\nCERTIFICATE_START\n"
    '{"rule": "foo", "source": {"type": "MIS"}, "note": "mismatch"}\n'
    "CERTIFICATE_END"
)


def _assistant(msg_id: str, text: str, usage: dict | None = None) -> dict:
    return {"type": "assistant",
            "message": {"id": msg_id, "role": "assistant",
                        "content": [{"type": "text", "text": text}],
                        "usage": usage}}


def _result(num_turns: int = 3, usage: dict | None = None) -> dict:
    return {"type": "result", "subtype": "success", "num_turns": num_turns,
            "usage": usage or {}, "result": "done"}


USAGE_1 = {"input_tokens": 100, "output_tokens": 50,
           "cache_read_input_tokens": 10, "cache_creation_input_tokens": 5}
USAGE_2 = {"input_tokens": 200, "output_tokens": 80,
           "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


# ── parse_stream ──────────────────────────────────────────────────────────────

class TestParseStream:
    def test_trajectory_usage_and_steps(self):
        lines = [json.dumps(e) for e in [
            {"type": "system", "subtype": "init"},
            _assistant("msg_1", "looking", USAGE_1),
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
            _assistant("msg_2", CERT_TEXT, USAGE_2),
            _result(num_turns=2),
        ]]
        parsed = parse_stream(lines)
        assert [r["role"] for r in parsed["trajectory"]] == ["assistant", "user", "assistant"]
        assert "CERTIFICATE_START" in parsed["trajectory"][-1]["content"]
        assert parsed["usage"].input_tokens == 300
        assert parsed["usage"].output_tokens == 130
        assert parsed["usage"].cache_read_tokens == 10
        assert parsed["usage"].cache_write_tokens == 5
        assert parsed["steps"] == 2

    def test_usage_dedupes_by_message_id(self):
        # The same API message can surface in several stream events; count it once.
        lines = [json.dumps(_assistant("msg_1", "a", USAGE_1)),
                 json.dumps(_assistant("msg_1", "b", USAGE_1))]
        assert parse_stream(lines)["usage"].input_tokens == 100

    def test_falls_back_to_result_usage(self):
        lines = [json.dumps(_assistant("msg_1", "no usage carried")),
                 json.dumps(_result(num_turns=1, usage=USAGE_1))]
        assert parse_stream(lines)["usage"].total_tokens == 165

    def test_result_usage_overrides_per_message_snapshots(self):
        # Assistant events carry message_start usage snapshots (output ≈ a few tokens);
        # the result event's cumulative usage is authoritative when present.
        snapshot = {"input_tokens": 240, "output_tokens": 3}  # start-of-message snapshot
        final = {"input_tokens": 240, "output_tokens": 9238,
                 "cache_read_input_tokens": 1674661, "cache_creation_input_tokens": 66677}
        lines = [json.dumps(_assistant("msg_1", "text", snapshot)),
                 json.dumps(_result(num_turns=40, usage=final))]
        usage = parse_stream(lines)["usage"]
        assert usage.output_tokens == 9238
        assert usage.cache_read_tokens == 1674661

    def test_skips_garbage_lines(self):
        lines = ["not json", "", "42", json.dumps(_result(num_turns=1))]
        parsed = parse_stream(lines)
        assert parsed["trajectory"] == []
        assert parsed["steps"] == 1

    def test_tool_use_and_tool_result_blocks_rendered(self):
        event = {"type": "assistant", "message": {"id": "m", "role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "pred list"}}]}}
        parsed = parse_stream([json.dumps(event)])
        assert "pred list" in parsed["trajectory"][0]["content"]


# ── _build_command ────────────────────────────────────────────────────────────

class TestBuildCommand:
    def test_strips_litellm_provider_prefix(self):
        cmd = _build_command("claude", "sys", "task", "anthropic/claude-opus-4-8", "Bash")
        assert cmd[cmd.index("--model") + 1] == "claude-opus-4-8"

    def test_no_turn_cap_by_default(self):
        # claude -p self-terminates; the backend passes no --max-turns.
        cmd = _build_command("claude", "sys", "task", "claude-sonnet-5", "Bash")
        assert cmd[cmd.index("--model") + 1] == "claude-sonnet-5"
        assert "--max-turns" not in cmd
        assert "--output-format" in cmd and "stream-json" in cmd

    def test_toolset_is_restricted_not_just_allowed(self):
        # --allowedTools alone does not remove Write/Edit/Task; --tools is what sandboxes the
        # session. Assert the restrictive flag is present and space-separated, MCP is off.
        cmd = _build_command("claude", "sys", "task", "claude-sonnet-5",
                             "Bash,Read,Grep,Glob")
        assert cmd[cmd.index("--tools") + 1] == "Bash Read Grep Glob"
        assert cmd[cmd.index("--allowedTools") + 1] == "Bash,Read,Grep,Glob"
        assert "--strict-mcp-config" in cmd


def _ctx(tmp_path: Path) -> SimpleNamespace:
    repo = tmp_path / "repo"
    (repo / "src" / "rules").mkdir(parents=True)
    pred = tmp_path / "pred"
    pred.write_text("#!/bin/sh\nexit 0\n")
    pred.chmod(0o755)
    return SimpleNamespace(repo_path=repo, pred_binary=pred, commit_hash="deadbeefcafe")


def _stub_claude(tmp_path: Path, events: list[dict], exit_code: int = 0) -> str:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    script = tmp_path / "claude-stub"
    script.write_text(f'#!/bin/sh\ncat "{transcript}"\nexit {exit_code}\n', encoding="utf-8")
    script.chmod(0o755)
    return str(script)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("AGENT_STRATEGY_FILE", "CLAUDE_CODE_TOOLS", "CLAUDE_BIN",
                "CLAUDE_CODE_SESSION_TIMEOUT"):
        monkeypatch.delenv(var, raising=False)


# ── backend selection ─────────────────────────────────────────────────────────

class TestBackendSelection:
    def test_unknown_backend_rejected(self, tmp_path):
        (tmp_path / "src" / "rules").mkdir(parents=True)
        with pytest.raises(ValueError, match="unknown backend"):
            run("m", str(tmp_path), fake=True, backend="opencode",
                library_commit="abc123", output=tmp_path / "out.json")

    def test_whole_repo_dispatches_to_claude_backend(self, monkeypatch):
        # The selected backend must call run_repo_claude, not mini-swe.
        def fake_repo_claude(model, ctx, **kw):
            kw["submit_session"].result_rows = lambda: [
                {"rule": "r1", "result": "bug_found", "tokens_k": 0.0,
                 "certificate": {"rule": "r1", "source": {}}}]
            return {"tokens_k": 12.0, "usage": None, "error": None}

        monkeypatch.setattr(run_submission, "find_pred_binary", lambda: "pred")
        monkeypatch.setattr(run_submission, "verify_pred_version", lambda p: "1.2.3")
        monkeypatch.setattr(run_submission, "EnvContext",
                            lambda **kw: type("C", (), {"pred_version": "1.2.3", **kw})())
        monkeypatch.setattr("benchmark.run_mini.run_repo_session",
                            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("mini-swe used")))
        monkeypatch.setattr("benchmark.claude_code.run_repo_claude", fake_repo_claude)

        sub = run_submission.run("claude-opus-4-8", "/repo", library_commit="deadbeef",
                                 backend="claude-code")
        assert sub["total_tokens_k"] == 12.0
        assert sub["bugs_found"] == 1
        assert "trajectory" not in sub


# ── run_repo_claude (stubbed CLI) ─────────────────────────────────────────────

class TestRunRepoClaude:
    def test_uses_shared_workspace_and_submit_channel(self, tmp_path):
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(json.dumps(_result(num_turns=1)) + "\n", encoding="utf-8")
        stub = tmp_path / "claude-stub"
        stub.write_text(
            f'#!/bin/sh\npwd > workspace.txt\nsubmit --status > status.txt\n'
            f'cat "{transcript}"\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)

        with SubmissionSession() as submit_session:
            session = run_repo_claude(
                "claude-haiku-4-5", _ctx(tmp_path), claude_bin=str(stub), strategy="",
                submit_session=submit_session)
            assert (submit_session.workdir / "workspace.txt").read_text().strip() == str(
                submit_session.workdir.resolve())
            assert "0/100 used" in (submit_session.workdir / "status.txt").read_text()
            assert submit_session.reachable
            assert session["error"] is None

    def test_session_usage_and_single_raw_stream(self, tmp_path):
        stub = _stub_claude(tmp_path, [
            _assistant("m1", CERT_TEXT, USAGE_1),
            _result(num_turns=5),
        ])
        submit_session = SimpleNamespace(limit=100)
        traj_dir = tmp_path / "out"
        session = run_repo_claude("anthropic/claude-haiku-4-5", _ctx(tmp_path),
                                  claude_bin=stub, strategy="",
                                  trajectory_dir=traj_dir,
                                  submit_session=submit_session)
        assert session["error"] is None
        assert session["usage"].input_tokens == 100
        assert (traj_dir / "anthropic_claude-haiku-4-5_whole-repo.stream.jsonl").exists()
        assert not (traj_dir / "anthropic_claude-haiku-4-5_whole-repo.jsonl").exists()

    def test_cli_failure_reports_error_and_salvages(self, tmp_path):
        stub = _stub_claude(tmp_path, [_assistant("m1", "partial work", USAGE_1)], exit_code=7)
        log_dir = tmp_path / "logs"
        session = run_repo_claude("claude-haiku-4-5", _ctx(tmp_path),
                                  claude_bin=stub, strategy="", trajectory_dir=log_dir)
        assert "claude exited 7" in session["error"]
        assert "partial work" in (
            log_dir / "claude-haiku-4-5_whole-repo.stream.jsonl").read_text()
        assert session["usage"].input_tokens == 100

    def test_missing_cli(self, tmp_path):
        session = run_repo_claude("claude-haiku-4-5", _ctx(tmp_path),
                                  claude_bin=str(tmp_path / "nope"), strategy="")
        assert "not found" in session["error"]
        assert "rows" not in session
