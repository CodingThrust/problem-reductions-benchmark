"""Entrypoint wiring tests for the standardized Top50 track."""
from __future__ import annotations

import json
import hashlib

import pytest

from benchmark import run_top50


def test_fake_entrypoint_uses_50_isolated_episodes(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    rules = repo / "src" / "rules"
    rules.mkdir(parents=True)
    for index in range(55):
        (rules / f"rule_{index:02d}.rs").write_text("// rule")
    pred = tmp_path / "pred"
    pred.write_text("#!/bin/sh\necho pred 0.6.0\n")
    pred.chmod(0o700)
    monkeypatch.setattr(run_top50, "find_pred_binary", lambda: pred)
    monkeypatch.setattr(run_top50, "verify_pred_version", lambda binary: "0.6.0")
    output = tmp_path / "top50.json"

    result = run_top50.run(
        model="fake/model", repo_dir=repo, output=output, fake=True)

    assert len(result["shortlist"]) == 50
    assert len(result["episodes"]) == 50
    assert result["rankable"] is False
    artifact = json.loads(output.read_text())
    assert artifact["budget_contract_status"] == "frozen"
    assert artifact["benchmark_contract"] == "top50-evidence/v2"


@pytest.mark.parametrize("forbidden", ["--backend", "--config", "--strategy-file"])
def test_standard_entrypoint_rejects_custom_harness_options(forbidden):
    with pytest.raises(SystemExit):
        run_top50.main(["--model", "fake/model", "--fake", forbidden, "custom"])


@pytest.mark.parametrize("name", run_top50.FORBIDDEN_RANKABLE_ENV)
def test_rankable_preflight_rejects_custom_execution_before_model_call(name, monkeypatch):
    monkeypatch.setenv(name, "custom")
    with pytest.raises(ValueError, match="reject custom"):
        run_top50.validate_rankable_settings()


def test_rankable_contract_cannot_be_changed_by_budget_environment(monkeypatch):
    monkeypatch.setenv("PRB_PRED_CALLS", "999")
    assert run_top50.frozen_contract().episode.pred_calls == 24


def test_even_empty_custom_model_kwargs_are_rejected(monkeypatch):
    for name in run_top50.FORBIDDEN_RANKABLE_ENV:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="custom model kwargs"):
        run_top50.validate_rankable_settings(model_kwargs={})


def test_top50_executor_keeps_finite_agent_step_guard():
    from benchmark.top50_runner import MiniSwePhaseExecutor
    executor = MiniSwePhaseExecutor()
    assert executor.agent_config["step_limit"] == 10


def test_rankable_source_manifest_detects_modified_rule(tmp_path):
    repo = tmp_path / "repo"
    rule = repo / "src" / "rules" / "r.rs"
    rule.parent.mkdir(parents=True)
    rule.write_text("original")
    (repo / ".prb-pinned-commit").write_text("abc\n")
    digest = hashlib.sha256(rule.read_bytes()).hexdigest()
    (repo / ".prb-source-manifest").write_text(f"{digest}  src/rules/r.rs\n")
    assert run_top50.verify_rankable_source(repo, "abc") == "abc"

    rule.write_text("modified")
    with pytest.raises(ValueError, match="differs from the image"):
        run_top50.verify_rankable_source(repo, "abc")
