"""Release-gate tests for frozen Top50 calibration evidence."""
from __future__ import annotations

import copy
import json
from dataclasses import asdict

from benchmark import calibrate_budget
from benchmark import run_top50


def _evidence() -> dict:
    return calibrate_budget.load_json(
        calibrate_budget.ROOT / "docs" / "budget-calibration.json")


def _contract() -> dict:
    return calibrate_budget.load_json(calibrate_budget.CONTRACT_PATH)


def test_checked_in_calibration_is_self_consistent():
    assert calibrate_budget.check(
        calibrate_budget.ROOT / "docs" / "budget-calibration.json") == []


def test_changed_selected_limit_is_rejected():
    evidence = _evidence()
    evidence["selected_contract"]["episode"]["pred_calls"] = 25
    assert "does not exactly match" in " ".join(
        calibrate_budget.validate_evidence(evidence, _contract()))


def test_deleted_grid_candidate_is_rejected():
    evidence = _evidence()
    evidence["observations"] = [item for item in evidence["observations"]
                                if not (item["model"] == "anthropic/claude-haiku-4-5"
                                        and item["candidate"]["model_generations"] == 6
                                        and item["candidate"]["pred_calls"] == 12)]
    assert "complete candidate grid" in " ".join(
        calibrate_budget.validate_evidence(evidence, _contract()))


def test_report_mismatch_is_rejected(tmp_path, monkeypatch):
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(_evidence()))
    report = tmp_path / "report.md"
    report.write_text("stale")
    monkeypatch.setattr(calibrate_budget, "REPORT_PATH", report)
    assert "does not match" in " ".join(calibrate_budget.check(evidence_path))


def test_smaller_and_larger_candidates_are_required():
    evidence = copy.deepcopy(_evidence())
    evidence["observations"] = [item for item in evidence["observations"]
                                if item["candidate"]["model_generations"] >= 10]
    assert "selected M is not surrounded" in " ".join(
        calibrate_budget.validate_evidence(evidence, _contract()))


def test_observation_must_link_to_source_and_have_numeric_counts():
    evidence = _evidence()
    evidence["observations"][0]["token_reference"] = "0" * 64
    evidence["observations"][1]["cap_hits"]["pred_calls"] = -1
    errors = " ".join(calibrate_budget.validate_evidence(evidence, _contract()))
    assert "not linked to a declared source" in errors
    assert "cap_hits is invalid" in errors


def test_development_artifact_observation_is_derived_offline(tmp_path):
    artifact = {
        "calibration_status": "non-ranking-development",
        "model": "internal/model", "library_commit": "abc",
        "contract": {"episode": {"model_generations": 6, "pred_calls": 12}},
        "episodes": [{
            "status": "bug_found",
            "usage": {"input": 10, "output": 2, "cache_read": 3, "cache_write": 1},
            "ledger": {
                "status": {"model_generations": {"used": 6, "limit": 6},
                           "pred_calls": {"used": 5, "limit": 12}},
                "submit": [{"attempt": 2}], "shell_actions": [{}, {}], "pred": [{}] * 5,
            },
        }],
    }
    path = tmp_path / "pilot.json"
    path.write_text(json.dumps(artifact))
    observation = calibrate_budget.observation_from_artifact(path)
    assert observation["verified_bugs"] == 1
    assert observation["cap_hits"] == {"model_generations": 1, "pred_calls": 0}
    assert observation["usage"]["total_tokens"] == 16
    assert observation["retries"] is None


def test_frozen_contract_is_referenced_across_release_surfaces():
    contract = _contract()
    runtime = asdict(run_top50.frozen_contract())
    assert runtime == {key: contract[key] for key in
                       ("triage", "episode", "shortlist_size", "hypothesis_chars")}
    root = calibrate_budget.ROOT.parent
    for relative in (
        "README.md", "CONTRIBUTING.md", "benchmark/top50_config.yaml",
        "benchmark/top50_submission.schema.json", "benchmark/top50_results.schema.json",
        "docker/Dockerfile", "site/index.html", ".github/workflows/ci.yml",
    ):
        text = (root / relative).read_text(encoding="utf-8")
        if relative.endswith("ci.yml"):
            assert "benchmark.calibrate_budget" in text
        else:
            assert contract["contract_id"] in text
