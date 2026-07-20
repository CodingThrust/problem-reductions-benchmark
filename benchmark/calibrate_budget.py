"""Offline validation and rendering for the frozen Top50 budget evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from benchmark.top50_budget import FROZEN_CONTRACT
from benchmark.usage import usage_from_dict

ROOT = Path(__file__).resolve().parent
CONTRACT_PATH = ROOT / "top50_budget.json"
REPORT_PATH = ROOT / "docs" / "budget-calibration.md"
REQUIRED_OBSERVATION_FIELDS = {
    "candidate", "model", "target", "verified_bugs", "cap_hits", "usage",
    "retries", "infrastructure_failures", "token_reference", "cost_reference",
    "time_reference", "marginal_yield",
}


def load_json(path: str | Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def validate_evidence(evidence: dict, contract: dict) -> list[str]:
    errors: list[str] = []
    if evidence.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if evidence.get("ranking_status") != "non-ranking-development":
        errors.append("calibration evidence must be explicitly non-ranking")
    if evidence.get("selected_contract") != contract:
        errors.append("selected_contract does not exactly match top50_budget.json")
    sources = evidence.get("sources")
    source_index: dict[tuple[str, str], str] = {}
    if not isinstance(sources, list) or not sources:
        errors.append("sources must be a non-empty list")
    else:
        for index, source in enumerate(sources):
            if not isinstance(source, dict):
                errors.append(f"sources[{index}] must be an object")
                continue
            model, target, digest = (source.get("model"), source.get("target"),
                                     source.get("artifact_sha256"))
            if (not isinstance(model, str) or not isinstance(target, str)
                    or not isinstance(digest, str) or len(digest) != 64
                    or any(char not in "0123456789abcdef" for char in digest)):
                errors.append(f"sources[{index}] has invalid provenance")
                continue
            source_index[(model, target)] = digest

    observations = evidence.get("observations")
    if not isinstance(observations, list) or not observations:
        return errors + ["observations must be a non-empty list"]
    candidates: set[tuple[int, int]] = set()
    model_candidates: dict[str, set[tuple[int, int]]] = {}
    seen_rows: set[tuple[str, int, int]] = set()
    for index, observation in enumerate(observations):
        if not isinstance(observation, dict):
            errors.append(f"observations[{index}] must be an object")
            continue
        missing = REQUIRED_OBSERVATION_FIELDS - set(observation)
        if missing:
            errors.append(f"observations[{index}] missing {sorted(missing)}")
            continue
        candidate = observation["candidate"]
        if not isinstance(candidate, dict):
            errors.append(f"observations[{index}].candidate must be an object")
            continue
        m, p = candidate.get("model_generations"), candidate.get("pred_calls")
        if (not isinstance(m, int) or isinstance(m, bool) or m <= 0
                or not isinstance(p, int) or isinstance(p, bool) or p <= 0):
            errors.append(f"observations[{index}] has invalid M/P candidate")
            continue
        model, target = observation.get("model"), observation.get("target")
        if not isinstance(model, str) or not isinstance(target, str):
            errors.append(f"observations[{index}] has invalid model/target")
            continue
        row = (model, m, p)
        if row in seen_rows:
            errors.append(f"observations[{index}] duplicates model/M/P")
        seen_rows.add(row)
        candidates.add((m, p))
        model_candidates.setdefault(model, set()).add((m, p))
        digest = source_index.get((model, target))
        if digest is None or observation.get("token_reference") != digest:
            errors.append(f"observations[{index}] is not linked to a declared source")
        for field in ("verified_bugs", "infrastructure_failures"):
            value = observation.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                errors.append(f"observations[{index}].{field} must be non-negative integer")
        retries = observation.get("retries")
        if retries is not None and (not isinstance(retries, int)
                                    or isinstance(retries, bool) or retries < 0):
            errors.append(f"observations[{index}].retries must be non-negative or null")
        cap_hits, usage = observation.get("cap_hits"), observation.get("usage")
        if not isinstance(cap_hits, dict) or any(
                not isinstance(cap_hits.get(name), int) or isinstance(cap_hits.get(name), bool)
                or not 0 <= cap_hits[name] <= contract["shortlist_size"]
                for name in ("model_generations", "pred_calls")):
            errors.append(f"observations[{index}].cap_hits is invalid")
        if not isinstance(usage, dict) or any(
                not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0
                for value in usage.values()):
            errors.append(f"observations[{index}].usage is invalid")
        marginal = observation.get("marginal_yield")
        if not isinstance(marginal, (int, float)) or isinstance(marginal, bool):
            errors.append(f"observations[{index}].marginal_yield must be numeric")
    selected_m = contract["episode"]["model_generations"]
    selected_p = contract["episode"]["pred_calls"]
    ms = {m for m, _ in candidates}
    ps = {p for _, p in candidates}
    if not any(m < selected_m for m in ms) or not any(m > selected_m for m in ms):
        errors.append("selected M is not surrounded by smaller and larger candidates")
    if not any(p < selected_p for p in ps) or not any(p > selected_p for p in ps):
        errors.append("selected P is not surrounded by smaller and larger candidates")
    expected_grid = {(m, p) for m in ms for p in ps}
    for model, actual in model_candidates.items():
        if actual != expected_grid:
            errors.append(f"model {model!r} does not cover the complete candidate grid")
    if not evidence.get("selection_rationale"):
        errors.append("selection_rationale is required")
    return errors


def render_report(evidence: dict) -> str:
    contract = evidence["selected_contract"]
    episode = contract["episode"]
    safety = contract["safety_controls"]
    lines = [
        "# Top50 budget calibration",
        "",
        f"Contract: `{contract['contract_id']}` (`{contract['status']}`)",
        "",
        "This is a human-reviewed, non-ranking bounded-prefix replay record from internally "
        "retained pilot trajectories. Raw trajectories remain private; this offline checker "
        "validates the checked-in record and release consistency, not the raw replay. It is not "
        "a public score, a multi-seed experiment, or a claim that elapsed time is model ability.",
        "",
        "## Selected contract",
        "",
        f"The release freezes M={episode['model_generations']} model generations, "
        f"P={episode['pred_calls']} total `pred` calls, "
        f"P_solve={episode['solve_calls']} solve calls, S={episode['submit_attempts']} "
        f"submit attempts, E={episode['shell_actions']} shell actions, and "
        f"O={episode['max_output_chars']} observed characters per rule. Triage is "
        f"T={contract['triage']['model_generations']} generations and "
        f"E_t={contract['triage']['shell_actions']} source-only actions.",
        "",
        f"Model calls have a fixed {safety['model_timeout_seconds']}-second watchdog and "
        f"{safety['model_retries']} transport retries. Command and `pred` watchdogs are also "
        "fixed safety controls. They are recorded but are outside the logical budget and never "
        "enter the score.",
        "",
        "## Measured grid",
        "",
        "| Model | M | P | Bugs | M cap rate | P cap rate | Tokens | Retries | Infra failures | Marginal yield |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    shortlist_size = contract["shortlist_size"]
    for item in evidence["observations"]:
        c = item["candidate"]
        retries = item["retries"] if item["retries"] is not None else "unavailable"
        lines.append(
            f"| {item['model']} | {c['model_generations']} | {c['pred_calls']} | "
            f"{item['verified_bugs']} | "
            f"{item['cap_hits']['model_generations'] / shortlist_size:.0%} | "
            f"{item['cap_hits']['pred_calls'] / shortlist_size:.0%} | "
            f"{item['usage']['total_tokens']} | "
            f"{retries} | {item['infrastructure_failures']} | "
            f"{item['marginal_yield']:.3f} |")
    lines += [
        "",
        "Token, cost, and elapsed-time fields are diagnostic references only. Missing provider "
        "cost or reliable wall-clock data is recorded as unavailable instead of imputed.",
        "",
        "## Decision",
        "",
        evidence["selection_rationale"],
        "",
        "The public comparison is therefore one run at this single contract, ranked only by "
        "verified distinct-rule bugs. Fixed Top50, multiple seeds, a System Track, and a public "
        "budget grid remain out of scope.",
        "",
        "## Provenance",
        "",
    ]
    for source in evidence["sources"]:
        lines.append(f"- `{source['artifact_sha256']}` — {source['model']}, "
                     f"{source['target']}, {source['method']}")
    return "\n".join(lines) + "\n"


def observation_from_artifact(path: str | Path) -> dict:
    """Extract one non-ranking grid observation from a completed development artifact."""
    artifact_path = Path(path)
    raw = artifact_path.read_bytes()
    artifact = json.loads(raw)
    if not isinstance(artifact, dict):
        raise ValueError(f"{path} must contain a JSON object")
    episode_budget = artifact.get("contract", {}).get("episode", {})
    episodes = artifact.get("episodes")
    if (artifact.get("calibration_status") != "non-ranking-development"
            or not isinstance(episodes, list)
            or not all(key in episode_budget for key in ("model_generations", "pred_calls"))):
        raise ValueError(f"{path} is not a completed non-ranking calibration artifact")
    cap_hits = {"model_generations": 0, "pred_calls": 0}
    total_tokens = infrastructure_failures = 0
    bugs = 0
    for episode in episodes:
        if not isinstance(episode, dict):
            continue
        bugs += episode.get("status") == "bug_found"
        infrastructure_failures += episode.get("status") == "run_error"
        ledger = episode.get("ledger", {})
        for counter in cap_hits:
            state = ledger.get("status", {}).get(counter, {})
            cap_hits[counter] += (state.get("limit", 0) > 0
                                  and state.get("used") == state.get("limit"))
        total_tokens += usage_from_dict(episode.get("usage") or {}).total_tokens
    digest = hashlib.sha256(raw).hexdigest()
    return {
        "candidate": {"model_generations": episode_budget["model_generations"],
                      "pred_calls": episode_budget["pred_calls"]},
        "model": artifact.get("model", "unknown"),
        "target": artifact.get("library_commit", "unknown"),
        "verified_bugs": bugs,
        "cap_hits": cap_hits,
        "usage": {"total_tokens": total_tokens,
                  "shell_actions": sum(len(e.get("ledger", {}).get("shell_actions", []))
                                       for e in episodes if isinstance(e, dict)),
                  "pred_calls": sum(len(e.get("ledger", {}).get("pred", []))
                                    for e in episodes if isinstance(e, dict))},
        "retries": artifact.get("transport_retries"),
        "infrastructure_failures": infrastructure_failures,
        "token_reference": digest,
        "cost_reference": artifact.get("cost_reference", "unavailable; not imputed"),
        "time_reference": artifact.get("time_reference", "unavailable; not imputed"),
        "marginal_yield": 0.0,
    }


def check(path: str | Path) -> list[str]:
    evidence = load_json(path)
    contract = FROZEN_CONTRACT
    errors = validate_evidence(evidence, contract)
    for schema_name in ("top50_submission.schema.json", "top50_results.schema.json"):
        schema = load_json(ROOT / schema_name)
        schema_contract = schema.get("properties", {}).get("benchmark_contract", {}).get("const")
        if schema_contract != contract["contract_id"]:
            errors.append(f"{schema_name} does not name the frozen contract")
    submission_schema = load_json(ROOT / "top50_submission.schema.json")
    properties = submission_schema.get("properties", {})
    expected_artifact_contract = {key: contract[key] for key in
                                  ("triage", "episode", "shortlist_size",
                                   "hypothesis_chars")}
    if properties.get("contract", {}).get("const") != expected_artifact_contract:
        errors.append("top50_submission.schema.json has stale logical limits")
    if properties.get("safety_controls", {}).get("const") != contract["safety_controls"]:
        errors.append("top50_submission.schema.json has stale safety controls")
    if properties.get("inference_parameters", {}).get("const") != contract[
            "inference_parameters"]:
        errors.append("top50_submission.schema.json has stale inference parameters")
    if not errors and REPORT_PATH.read_text(encoding="utf-8") != render_report(evidence):
        errors.append("budget-calibration.md does not match the machine-readable evidence")
    return errors


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Validate frozen Top50 calibration evidence")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", metavar="EVIDENCE_JSON")
    action.add_argument("--summarize", nargs="+", metavar="DEV_ARTIFACT")
    args = parser.parse_args(argv)
    if args.summarize:
        print(json.dumps([observation_from_artifact(path) for path in args.summarize], indent=2))
        return
    errors = check(args.check)
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        raise SystemExit(1)
    print(f"PASS: calibration evidence matches {FROZEN_CONTRACT['contract_id']}")


if __name__ == "__main__":
    main()
