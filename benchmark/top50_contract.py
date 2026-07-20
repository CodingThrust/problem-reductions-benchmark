"""Versioned private Top50 artifact validation, scoring, and public projection."""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
import os
import re
import shlex
from collections import Counter
from pathlib import Path
from typing import Callable

from benchmark.top50_budget import FROZEN_CONTRACT
from benchmark.usage import Usage, usage_as_dict, usage_from_dict
from benchmark.verify import Verdict, verify

CONTRACT_ID = FROZEN_CONTRACT["contract_id"]
AGENT_MODE = "standardized-model-api"
RUNNER_VERSION = "0.10.0"
EXPECTED_TRIAGE_BUDGET = FROZEN_CONTRACT["triage"]
EXPECTED_EPISODE_BUDGET = FROZEN_CONTRACT["episode"]
EXPECTED_SAFETY_CONTROLS = FROZEN_CONTRACT["safety_controls"]
EXPECTED_INFERENCE_PARAMETERS = FROZEN_CONTRACT["inference_parameters"]
EXPECTED_OBSERVATION = FROZEN_CONTRACT["observation"]
EXPECTED_SHORTLIST_SIZE = FROZEN_CONTRACT["shortlist_size"]
EXPECTED_HYPOTHESIS_CHARS = FROZEN_CONTRACT["hypothesis_chars"]
_REQUEST_ID = re.compile(r"[0-9a-f]{32}")
_COUNTERS = ("model_generations", "shell_actions", "pred_calls", "solve_calls")


def is_top50_submission(submission: object) -> bool:
    return isinstance(submission, dict) and str(
        submission.get("benchmark_contract", "")).startswith("top50-evidence/")


def expected_prompt_id() -> str:
    return hashlib.sha256(Path(__file__).with_name("top50_config.yaml").read_bytes()).hexdigest()


def validate_top50_submission(
    submission: object, canonical_inventory: set[str] | None = None
) -> list[str]:
    """Recompute structural rankability from private evaluation-owned ledgers."""
    if not isinstance(submission, dict):
        return ["submission is not a JSON object"]
    problems: list[str] = []
    for field in ("benchmark_contract", "model", "library_commit", "runner_version",
                  "pred_version", "agent_mode", "prompt_id", "contract", "shortlist",
                  "triage", "episodes", "status", "budget_contract_status",
                  "safety_controls", "inference_parameters", "observation_policy"):
        if field not in submission:
            problems.append(f"missing required field: {field}")
    if problems:
        return problems
    if submission["benchmark_contract"] != CONTRACT_ID:
        problems.append(f"unsupported benchmark_contract: {submission['benchmark_contract']!r}")
    if submission["agent_mode"] != AGENT_MODE:
        problems.append(f"agent_mode must be {AGENT_MODE!r}")
    if submission["runner_version"] != RUNNER_VERSION:
        problems.append(f"runner_version must be {RUNNER_VERSION!r}")
    if submission.get("budget_contract_status") != "frozen":
        problems.append("budget_contract_status must be 'frozen'")
    if submission.get("safety_controls") != EXPECTED_SAFETY_CONTROLS:
        problems.append("safety_controls differ from the released watchdog settings")
    if submission.get("status") != "completed" or submission.get("run_error"):
        problems.append("Top50 run is incomplete or has run_error")
    if submission.get("rankable") is not True:
        problems.append("runner did not certify the standardized rankable path")
    model = submission.get("model")
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9._:/-]{1,200}", model):
        problems.append("model identifier is not a bounded safe identifier")
    submitter = submission.get("submitted_by")
    if (submitter is not None and (not isinstance(submitter, str)
                                  or not re.fullmatch(r"[A-Za-z0-9_.@-]{1,100}", submitter))):
        problems.append("submitted_by is not a bounded safe identifier")
    if submission.get("inference_parameters") != EXPECTED_INFERENCE_PARAMETERS:
        problems.append("inference_parameters differ from the standardized model settings")
    if submission.get("observation_policy") != EXPECTED_OBSERVATION:
        problems.append("observation_policy differs from the versioned contract")
    if submission.get("prompt_id") != expected_prompt_id():
        problems.append("prompt_id does not match the frozen Top50 prompt")
    if "submit_limit" in submission or "submit_log" in submission:
        problems.append("Top50 artifacts cannot use a shared run-wide submit pool")

    contract = submission.get("contract")
    if not isinstance(contract, dict):
        return problems + ["contract must be an object"]
    triage_budget = contract.get("triage")
    episode_budget = contract.get("episode")
    observation_contract = contract.get("observation")
    if not isinstance(triage_budget, dict) or not isinstance(episode_budget, dict):
        return problems + ["contract triage and episode budgets must be objects"]
    if triage_budget != EXPECTED_TRIAGE_BUDGET:
        problems.append("triage budget differs from the versioned contract")
    if episode_budget != EXPECTED_EPISODE_BUDGET:
        problems.append("episode budget differs from the versioned contract")
    if observation_contract != EXPECTED_OBSERVATION:
        problems.append("observation contract differs from the versioned contract")
    if contract.get("shortlist_size") != EXPECTED_SHORTLIST_SIZE:
        problems.append(f"contract shortlist_size must be {EXPECTED_SHORTLIST_SIZE}")
    hypothesis_limit = contract.get("hypothesis_chars")
    if not _nonnegative_int(hypothesis_limit):
        problems.append("contract hypothesis_chars must be a non-negative integer")
        hypothesis_limit = -1
    elif hypothesis_limit != EXPECTED_HYPOTHESIS_CHARS:
        problems.append("contract hypothesis_chars differs from the versioned contract")
    if episode_budget.get("submit_attempts") != 2:
        problems.append("episode submit_attempts must be exactly 2")
    pred_limit = episode_budget.get("pred_calls")
    solve_limit = episode_budget.get("solve_calls")
    if not _nonnegative_int(pred_limit) or not _nonnegative_int(solve_limit):
        problems.append("pred_calls and solve_calls limits must be non-negative integers")
    elif solve_limit > pred_limit:
        problems.append("solve_calls limit exceeds pred_calls")

    shortlist = submission.get("shortlist")
    if not isinstance(shortlist, list) or len(shortlist) != EXPECTED_SHORTLIST_SIZE:
        return problems + [
            f"shortlist must contain exactly {EXPECTED_SHORTLIST_SIZE} entries"]
    rules: list[str] = []
    for index, entry in enumerate(shortlist):
        if not isinstance(entry, dict) or not isinstance(entry.get("rule"), str):
            problems.append(f"shortlist[{index}] must have a rule string")
            continue
        rules.append(entry["rule"])
        hypothesis = entry.get("hypothesis", "")
        if (set(entry) - {"rule", "hypothesis"} or not isinstance(hypothesis, str)
                or len(hypothesis) > hypothesis_limit):
            problems.append(f"shortlist[{index}] hypothesis schema is invalid")
    if len(set(rules)) != len(rules):
        problems.append("shortlist rules must be unique")
    if canonical_inventory is not None:
        unknown = sorted(set(rules) - canonical_inventory)
        if unknown:
            problems.append(f"shortlist contains non-canonical rules: {unknown[:3]}")

    triage = submission.get("triage")
    triage_ledger = triage.get("ledger") if isinstance(triage, dict) else None
    if not isinstance(triage_ledger, dict):
        problems.append("triage.ledger must be an object")
    else:
        if triage_ledger.get("budget") != triage_budget:
            problems.append("triage ledger budget differs from contract")
        _validate_observation_ledger(triage_ledger, "triage", problems)
        frozen = triage_ledger.get("shortlist")
        if frozen != shortlist:
            problems.append("top-level shortlist differs from frozen triage shortlist")
        _validate_status(triage_ledger.get("status"), triage_budget,
                         "triage", problems, counters=("model_generations", "shell_actions"))
        _validate_triage_events(triage_ledger, problems)

    episodes = submission.get("episodes")
    if not isinstance(episodes, list) or len(episodes) != 50:
        return problems + ["episodes must contain exactly 50 entries"]
    seen_request_ids: set[str] = set()
    for index, episode in enumerate(episodes):
        label = f"episodes[{index}]"
        if not isinstance(episode, dict):
            problems.append(f"{label} must be an object")
            continue
        expected_rule = rules[index] if index < len(rules) else None
        if episode.get("index") != index + 1 or episode.get("rule") != expected_rule:
            problems.append(f"{label} order/rule does not match frozen shortlist")
        expected_hypothesis = shortlist[index].get("hypothesis", "")
        if episode.get("hypothesis") != expected_hypothesis:
            problems.append(f"{label} hypothesis differs from frozen shortlist")
        if episode.get("status") == "run_error":
            problems.append(f"{label} has infrastructure run_error")
        ledger = episode.get("ledger")
        if not isinstance(ledger, dict):
            problems.append(f"{label}.ledger must be an object")
            continue
        if ledger.get("rule") != expected_rule or ledger.get("budget") != episode_budget:
            problems.append(f"{label} rule or budget differs from contract")
        messages = episode.get("messages")
        if not isinstance(messages, list) or len(messages) > 3 * episode_budget.get(
                "model_generations", 0) + 4:
            problems.append(f"{label} message history exceeds its logical bound")
        elif any(not isinstance(message, dict)
                 or len(str(message.get("content", ""))) > 200_000 for message in messages):
            problems.append(f"{label} contains an oversized message")
        _validate_status(ledger.get("status"), episode_budget, label, problems)
        _validate_episode_events(ledger, label, problems)
        _validate_observation_ledger(ledger, label, problems)
        _validate_pred_ledger(ledger, label, seen_request_ids, problems)
        _validate_submit_ledger(ledger, expected_rule, label, seen_request_ids, problems)
        accepted_attempts = [attempt for attempt in ledger.get("submit", [])
                             if isinstance(attempt, dict) and attempt.get("accepted") is True]
        expected_status = "bug_found" if accepted_attempts else "completed"
        expected_attempt = accepted_attempts[0].get("attempt") if accepted_attempts else None
        if (episode.get("status") != expected_status
                or episode.get("accepted_submit_attempt") != expected_attempt):
            problems.append(f"{label} outcome does not match its submit ledger")
    return list(dict.fromkeys(problems))


def score_top50_submission(
    submission: dict, repo_dir: str | None = None,
    *, verifier: Callable[[dict], Verdict] = verify,
    canonical_inventory: set[str] | None = None,
) -> tuple[dict, list[dict]]:
    """Validate ledgers and independently re-verify accepted certificates."""
    inventory = canonical_inventory or load_canonical_inventory(repo_dir)
    problems = validate_top50_submission(submission, inventory)
    accepted_positions: list[int] = []
    report: list[dict] = []
    first_attempt = second_attempt = 0
    pred_calls = 0
    cap_hits: Counter[str] = Counter()
    if not problems:
        for position, episode in enumerate(submission["episodes"], 1):
            ledger = episode["ledger"]
            pred_calls += ledger["status"]["pred_calls"]["used"]
            for counter in _COUNTERS:
                status = ledger["status"][counter]
                if status["limit"] > 0 and status["used"] == status["limit"]:
                    cap_hits[counter] += 1
            for attempt in ledger["submit"]:
                if attempt.get("accepted") is not True:
                    continue
                cert = attempt.get("certificate")
                verdict = _call_verifier(verifier, cert, repo_dir)
                accepted = bool(verdict.accepted)
                report.append({"position": position, "rule": episode["rule"],
                               "violation": cert.get("violation") if isinstance(cert, dict) else None,
                               "attempt": attempt["attempt"],
                               "accepted": accepted, "reason": verdict.reason})
                if accepted:
                    accepted_positions.append(position)
                    if attempt["attempt"] == 1:
                        first_attempt += 1
                    else:
                        second_attempt += 1
                break
    distinct_positions = sorted(set(accepted_positions))
    bugs = len(distinct_positions)
    usage = _aggregate_usage(submission)
    scored = {
        "benchmark_contract": submission.get("benchmark_contract"),
        "model": submission.get("model", "unknown"),
        "library_commit": submission.get("library_commit", "unknown"),
        "runner_version": submission.get("runner_version"),
        "pred_version": submission.get("pred_version"),
        "agent_mode": submission.get("agent_mode"),
        "rankable": not problems,
        "rankability_errors": problems,
        "verified_bugs": bugs,
        "bugs_found": bugs,
        "bugs_at_10": sum(position <= 10 for position in distinct_positions),
        "bugs_at_25": sum(position <= 25 for position in distinct_positions),
        "bugs_at_50": bugs,
        "first_attempt_accepts": first_attempt,
        "second_attempt_accepts": second_attempt,
        "pred_calls_per_bug": round(pred_calls / bugs, 4) if bugs else None,
        "cap_hits": dict(cap_hits),
        "usage_totals": usage_as_dict(usage),
        "total_tokens_k": round(usage.total_tokens / 1000, 2),
        "artifact_sha256": hashlib.sha256(json.dumps(
            submission, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "verifier_report": copy.deepcopy(report),
    }
    for field in ("submitted_by", "created_at", "test", "run_error"):
        if field in submission:
            scored[field] = submission[field]
    return scored, report


def top50_public_entry(submission: dict, scored: dict) -> dict:
    """Project private scoring data to aggregate-only public fields."""
    return {
        "benchmark_contract": scored["benchmark_contract"],
        "model": scored["model"],
        "library_commit": scored["library_commit"],
        "runner_version": scored.get("runner_version"),
        "pred_version": scored.get("pred_version"),
        "agent_mode": scored.get("agent_mode"),
        "rankable": scored["rankable"],
        "bugs_found": scored["verified_bugs"],
        "rules_tested": 50,
        "bugs_at_10": scored["bugs_at_10"],
        "bugs_at_25": scored["bugs_at_25"],
        "bugs_at_50": scored["bugs_at_50"],
        "first_attempt_accepts": scored["first_attempt_accepts"],
        "second_attempt_accepts": scored["second_attempt_accepts"],
        "pred_calls_per_bug": scored["pred_calls_per_bug"],
        "cap_hits": scored["cap_hits"],
        "usage_totals": scored["usage_totals"],
        "total_tokens_k": scored["total_tokens_k"],
        "submitted_by": submission.get("submitted_by"),
        "placeholder": False,
    }


def _validate_status(status, budget, label, problems, counters=_COUNTERS) -> None:
    if not isinstance(status, dict):
        problems.append(f"{label} status must be an object")
        return
    for counter in counters:
        item = status.get(counter)
        limit = budget.get(counter)
        if not _nonnegative_int(limit):
            problems.append(f"{label} {counter} limit is not a non-negative integer")
            continue
        if not isinstance(item, dict) or item.get("limit") != limit:
            problems.append(f"{label} {counter} limit differs from contract")
            continue
        used, remaining = item.get("used"), item.get("remaining")
        if (not _nonnegative_int(used) or used > limit
                or remaining != limit - used):
            problems.append(f"{label} {counter} usage is inconsistent")
    if "submit_attempts" in status:
        item = status["submit_attempts"]
        if not isinstance(item, dict) or item.get("limit") != 2:
            problems.append(f"{label} submit limit must be 2")
        elif (not _nonnegative_int(item.get("used")) or item["used"] > 2
              or not _nonnegative_int(item.get("remaining"))):
            problems.append(f"{label} submit usage is inconsistent")


def _validate_triage_events(ledger: dict, problems: list[str]) -> None:
    events = ledger.get("events")
    if not isinstance(events, list):
        problems.append("triage events must be a list")
        return
    budget = ledger.get("budget", {})
    if len(events) > budget.get("model_generations", 0) + budget.get("shell_actions", 0) + 3:
        problems.append("triage event ledger exceeds its logical bound")
    commit_events = [event for event in events if isinstance(event, dict)
                     and event.get("type") == "shortlist_commit"
                     and event.get("accepted") is True]
    if len(commit_events) != 1:
        problems.append("triage must contain exactly one accepted shortlist_commit")
    sequenced = [event for event in events if isinstance(event, dict) and "sequence" in event]
    if [event.get("sequence") for event in sequenced] != list(range(1, len(sequenced) + 1)):
        problems.append("triage event sequence is inconsistent")
    if any("charged" in event and type(event["charged"]) is not bool for event in events
           if isinstance(event, dict)):
        problems.append("triage charged fields must be booleans")
    charged = Counter(event.get("type") for event in events
                      if isinstance(event, dict) and event.get("charged") is True)
    status = ledger.get("status", {})
    if charged["model_generation"] != status.get("model_generations", {}).get("used"):
        problems.append("triage model-generation ledger count is inconsistent")
    if charged["shell_action"] != status.get("shell_actions", {}).get("used"):
        problems.append("triage shell-action ledger count is inconsistent")


def _validate_pred_ledger(ledger, label, seen_ids, problems) -> None:
    records = ledger.get("pred")
    if not isinstance(records, list):
        problems.append(f"{label} pred ledger must be a list")
        return
    if len(records) > ledger.get("budget", {}).get("shell_actions", 0) + 1:
        problems.append(f"{label} pred ledger exceeds its logical bound")
    charged = solve = 0
    for sequence, record in enumerate(records, 1):
        if not isinstance(record, dict) or record.get("sequence") != sequence:
            problems.append(f"{label} pred sequence is inconsistent")
            continue
        request_id = record.get("request_id")
        if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
            problems.append(f"{label} pred request id is invalid")
        elif request_id in seen_ids:
            problems.append(f"{label} reuses a request id")
        else:
            seen_ids.add(request_id)
        if type(record.get("charged")) is not bool:
            problems.append(f"{label} pred charged field must be boolean")
        if record.get("charged") is True:
            charged += 1
            solve += record.get("command") == "solve"
    status = ledger.get("status", {})
    if charged != status.get("pred_calls", {}).get("used"):
        problems.append(f"{label} pred charged count differs from status")
    if solve != status.get("solve_calls", {}).get("used"):
        problems.append(f"{label} solve charged count differs from status")


def _validate_episode_events(ledger, label, problems) -> None:
    status = ledger.get("status", {})
    for field in ("model_generations", "shell_actions"):
        events = ledger.get(field)
        if not isinstance(events, list):
            problems.append(f"{label} {field} ledger must be a list")
            continue
        limit = ledger.get("budget", {}).get(field, 0)
        if len(events) > limit + 1:
            problems.append(f"{label} {field} ledger exceeds its logical bound")
        if any(type(event.get("charged")) is not bool for event in events
               if isinstance(event, dict)):
            problems.append(f"{label} {field} charged fields must be booleans")
        charged = sum(event.get("charged") is True for event in events
                      if isinstance(event, dict))
        if charged != status.get(field, {}).get("used"):
            problems.append(f"{label} {field} charged count differs from status")


def _validate_observation_ledger(ledger: dict, label: str, problems: list[str]) -> None:
    if ledger.get("observation_policy") != EXPECTED_OBSERVATION:
        problems.append(f"{label} observation policy differs from contract")
    records = ledger.get("observations")
    if not isinstance(records, list):
        problems.append(f"{label} observations must be a list")
        return
    shell_events = (ledger.get("events", []) if label == "triage"
                    else ledger.get("shell_actions", []))
    shell_events = [event for event in shell_events if isinstance(event, dict)
                    and (label != "triage" or event.get("type") == "shell_action")]
    pred_records = ledger.get("pred", []) if label != "triage" else []
    maximum = len(shell_events) + (len(pred_records) if isinstance(pred_records, list) else 0)
    if len(records) > maximum:
        problems.append(f"{label} observation ledger exceeds its logical bound")
    seen: set[str] = set()
    record_by_id: dict[str, dict] = {}
    required = {
        "observation_id", "kind", "command", "policy_id", "raw_log", "returncode",
        "timed_out", "original_chars", "original_lines", "preview_chars", "archive_chars",
        "preview_compacted", "archive_truncated",
    }
    for record in records:
        if not isinstance(record, dict) or set(record) != required:
            problems.append(f"{label} has malformed observation metadata")
            continue
        observation_id = record["observation_id"]
        if (not isinstance(observation_id, str)
                or not re.fullmatch(r"(?:shell-[0-9]{4}|pred-[0-9a-f]{32})", observation_id)):
            problems.append(f"{label} has invalid observation id")
            continue
        if observation_id in seen:
            problems.append(f"{label} has invalid observation id")
            continue
        seen.add(observation_id)
        record_by_id[observation_id] = record
        if record["policy_id"] != EXPECTED_OBSERVATION["policy_id"]:
            problems.append(f"{label} observation policy id is inconsistent")
        if (not _nonnegative_int(record["preview_chars"])
                or record["preview_chars"] > EXPECTED_OBSERVATION["preview_chars"]):
            problems.append(f"{label} observation preview exceeds contract")
        if (not _nonnegative_int(record["archive_chars"])
                or record["archive_chars"] > EXPECTED_OBSERVATION["archive_chars"]):
            problems.append(f"{label} observation archive exceeds contract")
        if not _nonnegative_int(record["original_chars"]):
            problems.append(f"{label} observation original size is invalid")
        if not _nonnegative_int(record["original_lines"]):
            problems.append(f"{label} observation original line count is invalid")
        if (record["kind"] not in ("shell", "pred")
                or not isinstance(record["command"], str)
                or type(record["returncode"]) is not int
                or not isinstance(record["timed_out"], bool)
                or not isinstance(record["preview_compacted"], bool)
                or not isinstance(record["archive_truncated"], bool)):
            problems.append(f"{label} observation fields have invalid types")
        expected_path = f"../observations/{observation_id}.log"
        if record["raw_log"] != expected_path:
            problems.append(f"{label} observation raw-log reference is invalid")

    referenced: list[str] = []
    for event in shell_events:
        observation_id = event.get("observation_id")
        record = record_by_id.get(observation_id)
        if record is None:
            problems.append(f"{label} shell event is missing its observation")
            continue
        referenced.append(observation_id)
        if record["kind"] != "shell" or record["command"] != event.get("command"):
            problems.append(f"{label} shell observation does not match its event")
        if not _observation_matches_outcome(record, event.get("outcome")):
            problems.append(f"{label} shell observation outcome is inconsistent")
    if isinstance(pred_records, list):
        for pred_record in pred_records:
            if not isinstance(pred_record, dict):
                continue
            embedded = pred_record.get("observation")
            requires_observation = pred_record.get("outcome") in {
                "completed", "nonzero_exit", "timeout"}
            if embedded is None:
                if requires_observation:
                    problems.append(f"{label} pred record is missing its observation")
                continue
            if not isinstance(embedded, dict):
                problems.append(f"{label} pred observation is malformed")
                continue
            observation_id = embedded.get("observation_id")
            referenced.append(observation_id)
            if (record_by_id.get(observation_id) != embedded
                    or embedded.get("kind") != "pred"
                    or not _pred_command_matches(embedded, pred_record)
                    or embedded.get("returncode") != pred_record.get("returncode")
                    or not _observation_matches_outcome(embedded, pred_record.get("outcome"))):
                problems.append(f"{label} pred observation does not match its record")
    if len(referenced) != len(set(referenced)) or set(referenced) != set(record_by_id):
        problems.append(f"{label} observation ledger is not bijective with action records")


def _observation_matches_outcome(record: dict, outcome: object) -> bool:
    return {
        "completed": record["returncode"] == 0 and record["timed_out"] is False,
        "nonzero_exit": record["returncode"] != 0 and record["timed_out"] is False,
        "timeout": record["returncode"] == 124 and record["timed_out"] is True,
        "budget_exhausted": record["returncode"] == 75 and record["timed_out"] is False,
    }.get(outcome, False)


def _pred_command_matches(observation: dict, pred_record: dict) -> bool:
    args = pred_record.get("args")
    return (isinstance(args, list) and all(isinstance(arg, str) for arg in args)
            and observation.get("command") == "pred " + shlex.join(args))


def _validate_submit_ledger(ledger, expected_rule, label, seen_ids, problems) -> None:
    attempts = ledger.get("submit")
    if not isinstance(attempts, list) or len(attempts) > 2:
        problems.append(f"{label} must have at most two submit attempts")
        return
    status = ledger.get("status", {}).get("submit_attempts", {})
    if status.get("used") != len(attempts):
        problems.append(f"{label} submit count differs from status")
    accepted = 0
    for number, attempt in enumerate(attempts, 1):
        if not isinstance(attempt, dict) or attempt.get("attempt") != number:
            problems.append(f"{label} submit attempt numbering is inconsistent")
            continue
        request_id = attempt.get("request_id")
        if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
            problems.append(f"{label} submit request id is invalid")
        elif request_id in seen_ids:
            problems.append(f"{label} reuses a request id")
        else:
            seen_ids.add(request_id)
        if type(attempt.get("accepted")) is not bool:
            problems.append(f"{label} submit accepted field must be boolean")
        if attempt.get("accepted") is True:
            accepted += 1
            certificate = attempt.get("certificate")
            if not isinstance(certificate, dict) or certificate.get("rule") != expected_rule:
                problems.append(f"{label} accepted certificate rule mismatch")
    if accepted > 1:
        problems.append(f"{label} has more than one accepted submit attempt")
    expected_remaining = 0 if accepted else 2 - len(attempts)
    if status.get("remaining") != expected_remaining:
        problems.append(f"{label} submit remaining count is inconsistent")


def load_canonical_inventory(repo_dir: str | None) -> set[str]:
    """Load source-rule identifiers from the pinned repository bundled with the image."""
    candidate = Path(repo_dir or os.environ.get("REPO_DIR", "/app/pr-src"))
    if not (candidate / "src" / "rules").is_dir():
        raise RuntimeError(f"pinned canonical rule inventory is unavailable under {candidate}")
    from benchmark.run_mini import list_rules
    return set(list_rules(candidate))


def _aggregate_usage(submission: dict) -> Usage:
    total = usage_from_dict(submission.get("triage", {}).get("usage"))
    for episode in submission.get("episodes", []):
        total = total + usage_from_dict(episode.get("usage"))
    return total


def _nonnegative_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _call_verifier(verifier, certificate, repo_dir):
    """Bind the verifier signature before execution; never retry an internal TypeError."""
    signature = inspect.signature(verifier)
    positional = [parameter for parameter in signature.parameters.values()
                  if parameter.kind in (parameter.POSITIONAL_ONLY,
                                        parameter.POSITIONAL_OR_KEYWORD)]
    variadic = any(parameter.kind == parameter.VAR_POSITIONAL
                   for parameter in signature.parameters.values())
    return verifier(certificate, repo_dir) if variadic or len(positional) >= 2 else verifier(certificate)
