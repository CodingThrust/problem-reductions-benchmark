"""Entrypoint wiring tests for the standardized Top50 track."""
from __future__ import annotations

import json

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
    assert json.loads(output.read_text())["budget_contract_status"] == "pilot-unfrozen"


@pytest.mark.parametrize("forbidden", ["--backend", "--config", "--strategy-file"])
def test_standard_entrypoint_rejects_custom_harness_options(forbidden):
    with pytest.raises(SystemExit):
        run_top50.main(["--model", "fake/model", "--fake", forbidden, "custom"])
