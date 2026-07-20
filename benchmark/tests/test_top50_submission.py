"""Acceptance tests for the private Top50 contract and aggregate-only public score."""
from __future__ import annotations

import copy
import json
import runpy
from pathlib import Path

import pytest

from benchmark.backend_score import _dedup_best, aggregate_leaderboard, score_one
from benchmark.submit import validate_submission
from benchmark.top50_contract import (
    AGENT_MODE,
    CONTRACT_ID,
    RUNNER_VERSION,
    expected_prompt_id,
    score_top50_submission,
    top50_public_entry,
    validate_top50_submission,
)
from benchmark.verify import Verdict


def _status(limit: int, used: int = 0) -> dict:
    return {"used": used, "limit": limit, "remaining": limit - used}


def _artifact(accepted_positions=(7, 18, 41)) -> dict:
    observation = {"policy_id": "terminal-diagnostics/v1", "preview_chars": 10_000,
                   "archive_chars": 1_048_576}
    triage_observation = {
        "observation_id": "shell-0001", "kind": "shell",
        "command": "commit-top50 shortlist.json", "policy_id": observation["policy_id"],
        "raw_log": "../observations/shell-0001.log", "returncode": 0,
        "timed_out": False, "original_chars": 16, "original_lines": 1,
        "preview_chars": 200, "archive_chars": 16, "preview_compacted": False,
        "archive_truncated": False,
    }
    triage_budget = {
        "model_generations": 8, "shell_actions": 12,
        "max_output_chars": 10_000, "command_timeout_seconds": 300,
    }
    episode_budget = {
        "model_generations": 10, "shell_actions": 12, "pred_calls": 24,
        "solve_calls": 10, "submit_attempts": 2, "max_output_chars": 10_000,
        "pred_timeout_seconds": 300,
    }
    shortlist = [{"rule": f"rule_{index:02d}", "hypothesis": f"risk-{index}"}
                 for index in range(1, 51)]
    triage_status = {"model_generations": _status(8, 1),
                     "shell_actions": _status(12, 1),
                     "pred_calls": _status(0), "solve_calls": _status(0)}
    triage = {
        "ledger": {
            "budget": triage_budget,
            "observation_policy": copy.deepcopy(observation),
            "observations": [triage_observation],
            "status": triage_status,
            "events": [
                {"sequence": 1, "type": "model_generation", "charged": True},
                {"sequence": 2, "type": "shell_action", "charged": True,
                 "command": "commit-top50 shortlist.json", "outcome": "completed",
                 "observation_id": "shell-0001"},
                {"type": "shortlist_commit", "accepted": True},
            ],
            "shortlist": copy.deepcopy(shortlist),
        },
        "messages": [], "tokens_k": 0.1,
        "usage": {"input": 100, "output": 10, "cache_read": 0, "cache_write": 0},
    }
    episodes = []
    request = 1
    for position, entry in enumerate(shortlist, 1):
        attempts = []
        submit_used = 0
        submit_remaining = 2
        status = "completed"
        if position in accepted_positions:
            attempts.append({
                "attempt": 1,
                "request_id": f"{request:032x}",
                "accepted": True,
                "rule": entry["rule"],
                "reason": "confirmed",
                "certificate": {"rule": entry["rule"], "source": {},
                                "bundle": {"target": {"type": "Target"}}},
            })
            request += 1
            submit_used, submit_remaining, status = 1, 0, "bug_found"
        ledger_status = {
            "model_generations": _status(10), "shell_actions": _status(12),
            "pred_calls": _status(24), "solve_calls": _status(10),
            "submit_attempts": {"used": submit_used, "limit": 2,
                                "remaining": submit_remaining},
        }
        episodes.append({
            "index": position, "rule": entry["rule"], "hypothesis": entry["hypothesis"],
            "status": status, "accepted_submit_attempt": 1 if attempts else None,
            "ledger": {"rule": entry["rule"], "budget": copy.deepcopy(episode_budget),
                       "observation_policy": copy.deepcopy(observation),
                       "observations": [],
                       "status": ledger_status, "pred": [], "submit": attempts,
                       "model_generations": [], "shell_actions": []},
            "messages": [], "tokens_k": 0.01,
            "usage": {"input": 10, "output": 1, "cache_read": 0, "cache_write": 0},
        })
    return {
        "benchmark_contract": CONTRACT_ID,
        "model": "provider/model",
        "library_commit": "a" * 40,
        "runner_version": RUNNER_VERSION,
        "pred_version": "0.6.0",
        "agent_mode": AGENT_MODE,
        "prompt_id": expected_prompt_id(),
        "budget_contract_status": "frozen",
        "safety_controls": {"model_timeout_seconds": 300, "model_retries": 2},
        "observation_policy": copy.deepcopy(observation),
        "status": "completed",
        "rankable": True,
        "inference_parameters": {"max_tokens": 8192, "timeout": 300, "num_retries": 2},
        "contract": {"triage": triage_budget, "episode": episode_budget,
                     "observation": copy.deepcopy(observation),
                     "shortlist_size": 50, "hypothesis_chars": 500},
        "shortlist": shortlist,
        "triage": triage,
        "episodes": episodes,
        "bugs_found": 999,
    }


def _accept(cert, repo_dir=None):
    return Verdict(True, "reverified")


def test_valid_artifact_recomputes_score_and_prefix_metrics():
    submission = _artifact()
    assert validate_top50_submission(submission) == []
    scored, report = score_top50_submission(
        submission, verifier=_accept,
        canonical_inventory={entry["rule"] for entry in submission["shortlist"]})

    assert scored["rankable"] is True
    assert scored["verified_bugs"] == 3
    assert scored["bugs_at_10"] == 1
    assert scored["bugs_at_25"] == 2
    assert scored["bugs_at_50"] == 3
    assert scored["first_attempt_accepts"] == 3
    assert len(report) == 3
    assert submission["bugs_found"] == 999
    assert validate_submission(submission) == []


@pytest.mark.parametrize("mutation", [
    lambda sub: sub["triage"]["ledger"]["observations"].clear(),
    lambda sub: sub["triage"]["ledger"]["events"][1].pop("observation_id"),
    lambda sub: sub["triage"]["ledger"]["observations"][0].update(command="forged"),
    lambda sub: sub["triage"]["ledger"]["observations"].append({
        **sub["triage"]["ledger"]["observations"][0],
        "observation_id": "shell-9999",
        "raw_log": "../observations/shell-9999.log",
    }),
])
def test_observation_ledger_must_be_bijective_with_actions(mutation):
    submission = _artifact()
    mutation(submission)
    errors = " ".join(validate_top50_submission(submission))
    assert "observation" in errors and errors


@pytest.mark.parametrize("mutation", [
    lambda record: record.update(returncode=9),
    lambda record: record.update(timed_out=True),
    lambda record: record.update(observation_id=[]),
])
def test_shell_observation_exact_metadata_is_checked_without_crashing(mutation):
    submission = _artifact()
    record = submission["triage"]["ledger"]["observations"][0]
    mutation(record)
    errors = " ".join(validate_top50_submission(submission))
    assert "observation" in errors and errors


def test_pred_observation_command_and_outcome_are_derived_from_action_record():
    submission = _artifact()
    ledger = submission["episodes"][0]["ledger"]
    request_id = "f" * 32
    observation = {
        "observation_id": f"pred-{request_id}", "kind": "pred",
        "command": "pred create X", "policy_id": "terminal-diagnostics/v1",
        "raw_log": f"../observations/pred-{request_id}.log", "returncode": 0,
        "timed_out": False, "original_chars": 10, "original_lines": 1,
        "preview_chars": 200, "archive_chars": 10, "preview_compacted": False,
        "archive_truncated": False,
    }
    ledger["status"]["pred_calls"] = _status(24, 1)
    ledger["pred"] = [{
        "sequence": 1, "request_id": request_id, "args": ["create", "X"],
        "command": "create", "charged": True, "outcome": "completed", "returncode": 0,
        "budget": copy.deepcopy(ledger["status"]),
        "observation": copy.deepcopy(observation),
    }]
    ledger["observations"].append(copy.deepcopy(observation))
    assert validate_top50_submission(submission) == []

    ledger["pred"][0]["observation"]["command"] = "pred forged"
    ledger["observations"][0]["command"] = "pred forged"
    assert "pred observation does not match" in " ".join(
        validate_top50_submission(submission))

    ledger["pred"][0]["observation"]["command"] = "pred create X"
    ledger["observations"][0]["command"] = "pred create X"
    ledger["pred"][0]["observation"]["timed_out"] = True
    ledger["observations"][0]["timed_out"] = True
    assert "pred observation does not match" in " ".join(
        validate_top50_submission(submission))


@pytest.mark.parametrize("mutate, expected", [
    (lambda sub: sub.update(submit_limit=100), "shared run-wide submit pool"),
    (lambda sub: sub["episodes"].pop(), "exactly 50"),
    (lambda sub: sub["episodes"].__setitem__(1, copy.deepcopy(sub["episodes"][0])),
     "order/rule"),
    (lambda sub: sub["episodes"][0]["ledger"]["status"]["pred_calls"].update(used=25),
     "usage is inconsistent"),
    (lambda sub: sub["episodes"][0]["ledger"]["budget"].update(pred_calls=23),
     "budget differs"),
    (lambda sub: sub["contract"]["episode"].update(pred_calls=1000000),
     "versioned contract"),
    (lambda sub: sub.update(agent_mode="codex"), "agent_mode"),
    (lambda sub: sub.update(prompt_id="custom"), "prompt_id"),
    (lambda sub: sub.pop("inference_parameters"), "inference_parameters"),
    (lambda sub: sub["observation_policy"].update(policy_id="custom"),
     "observation_policy"),
    (lambda sub: sub.update(status="run_error", run_error="provider failed"), "incomplete"),
])
def test_rankability_negative_controls(mutate, expected):
    submission = _artifact(accepted_positions=())
    mutate(submission)
    assert any(expected in problem for problem in validate_top50_submission(submission))


def test_wrong_episode_certificate_and_third_attempt_are_unrankable():
    submission = _artifact(accepted_positions=(1,))
    attempt = submission["episodes"][0]["ledger"]["submit"][0]
    attempt["certificate"]["rule"] = "rule_02"
    assert any("certificate rule mismatch" in p
               for p in validate_top50_submission(submission))

    submission = _artifact(accepted_positions=())
    episode = submission["episodes"][0]
    episode["ledger"]["submit"] = [
        {"attempt": n, "request_id": f"{n:032x}", "accepted": False, "reason": "no"}
        for n in range(1, 4)]
    episode["ledger"]["status"]["submit_attempts"] = {"used": 3, "limit": 2, "remaining": 0}
    assert any("at most two" in p for p in validate_top50_submission(submission))


def test_strict_boolean_commit_and_canonical_inventory_controls():
    submission = _artifact(accepted_positions=(1,))
    submission["episodes"][0]["ledger"]["submit"][0]["accepted"] = "false"
    assert any("accepted field must be boolean" in p
               for p in validate_top50_submission(submission))

    submission = _artifact(accepted_positions=())
    submission["triage"]["ledger"]["events"] = submission["triage"]["ledger"]["events"][:2]
    assert any("exactly one accepted shortlist_commit" in p
               for p in validate_top50_submission(submission))

    submission = _artifact(accepted_positions=())
    canonical = {entry["rule"] for entry in submission["shortlist"]} - {"rule_50"}
    assert any("non-canonical" in p
               for p in validate_top50_submission(submission, canonical))


def test_public_projection_contains_no_answer_key_material():
    submission = _artifact()
    scored, _ = score_top50_submission(
        submission, verifier=_accept,
        canonical_inventory={entry["rule"] for entry in submission["shortlist"]})
    public = top50_public_entry(submission, scored)
    raw = json.dumps(public)

    for forbidden in ("rule_", "hypothesis", "certificate", '"source"', '"bundle"',
                      "submit_log", "submit_attempt", "trajectory", "reason"):
        assert forbidden not in raw
    assert public["bugs_found"] == 3

    guard = runpy.run_path(str(
        Path(__file__).parents[2] / ".github" / "scripts" / "check_aggregate.py"))["check"]
    # The actual egress guard accepts the generated projection, not merely this key test.
    # Use pytest's temporary path in the dedicated backend test below for file-based checks.
    assert callable(guard)


def test_top50_ties_ignore_tokens_and_efficiency_and_legacy_is_separate():
    entries = [
        {"benchmark_contract": CONTRACT_ID, "model": "a", "bugs_found": 2,
         "total_tokens_k": 1, "efficiency_bugs_per_ktok": 999},
        {"benchmark_contract": CONTRACT_ID, "model": "b", "bugs_found": 2,
         "total_tokens_k": 1000, "efficiency_bugs_per_ktok": 0.001},
        {"model": "a", "bugs_found": 9, "efficiency_bugs_per_ktok": 9},
    ]
    board = _dedup_best(entries)
    assert len(board) == 3
    top50 = [entry for entry in board if entry.get("benchmark_contract") == CONTRACT_ID]
    assert [entry["model"] for entry in top50] == ["a", "b"]


def test_backend_official_path_keeps_private_detail_and_publishes_aggregate(tmp_path):
    submission = _artifact(accepted_positions=())
    repo = tmp_path / "repo"
    rules = repo / "src" / "rules"
    rules.mkdir(parents=True)
    for entry in submission["shortlist"]:
        (rules / f"{entry['rule']}.rs").write_text("// fixture")
    source = tmp_path / "submission.json"
    source.write_text(json.dumps(submission))
    results = tmp_path / "results"

    public = score_one(
        source, results, repo_dir=str(repo), official=True,
        expected_commit=submission["library_commit"])
    board = aggregate_leaderboard(results)
    private = json.loads((results / "submission.json").read_text())

    assert private["artifact_sha256"] and "episodes" not in private
    assert public["benchmark_contract"] == CONTRACT_ID
    assert board == [public]
    assert "episodes" not in public and "shortlist" not in public
    guard = runpy.run_path(str(
        Path(__file__).parents[2] / ".github" / "scripts" / "check_aggregate.py"))["check"]
    public_file = tmp_path / "public.json"
    public_file.write_text(json.dumps(public))
    assert guard(public_file) == []


def test_site_keeps_contracts_separate_and_top50_ties_ignore_efficiency():
    site = (Path(__file__).parents[2] / "site" / "index.html").read_text()
    assert 'id="lb-track"' in site
    assert 'top50=track.startsWith("top50-evidence/")' in site
    assert "top50?String(a.model).localeCompare" in site
