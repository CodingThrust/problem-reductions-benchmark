#!/usr/bin/env python3
"""Production entrypoint for the standardized self-selected Top50 model track."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path

from benchmark.env_setup import find_pred_binary, pinned_commit, verify_pred_version
from benchmark.evidence_budget import EvidenceBudget
from benchmark.run_mini import list_rules
from benchmark.top50_runner import (
    PhaseResult,
    Top50Contract,
    Top50Runner,
    TriageBudget,
    build_rankable_runner,
)
from benchmark.top50_contract import (
    AGENT_MODE,
    CONTRACT_ID,
    RUNNER_VERSION,
    EXPECTED_EPISODE_BUDGET,
    EXPECTED_INFERENCE_PARAMETERS,
    EXPECTED_SAFETY_CONTROLS,
    EXPECTED_HYPOTHESIS_CHARS,
    EXPECTED_SHORTLIST_SIZE,
    EXPECTED_TRIAGE_BUDGET,
    expected_prompt_id,
)


FORBIDDEN_RANKABLE_ENV = (
    "AGENT_BACKEND", "AGENT_CONFIG", "AGENT_STRATEGY_FILE", "SUBMIT_LIMIT",
    "PRB_TRIAGE_GENERATIONS", "PRB_TRIAGE_ACTIONS", "PRB_EPISODE_GENERATIONS",
    "PRB_EPISODE_ACTIONS", "PRB_PRED_CALLS", "PRB_SOLVE_CALLS",
    "PRB_MAX_OUTPUT_CHARS", "PRB_PRED_TIMEOUT_SECONDS",
    "EXPECTED_PRED_COMMIT", "EXPECTED_PRED_VERSION",
)
TRUSTED_PRED_PATH = Path("/usr/local/libexec/prb/pred")


def verify_rankable_source(repo: Path, expected_commit: str) -> str:
    """Verify the baked target marker and every source-rule byte before model access."""
    marker = repo / ".prb-pinned-commit"
    manifest = repo / ".prb-source-manifest"
    try:
        actual_commit = marker.read_text(encoding="utf-8").strip()
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ValueError(f"rankable source is missing its baked provenance: {error}") from error
    if actual_commit != expected_commit:
        raise ValueError("rankable source commit marker differs from the image pin")
    expected_files: set[str] = set()
    for line in lines:
        try:
            digest, relative = line.split(maxsplit=1)
        except ValueError as error:
            raise ValueError("rankable source manifest is malformed") from error
        relative = relative.lstrip("*")
        candidate = (repo / relative).resolve()
        if not candidate.is_relative_to(repo) or not candidate.is_file():
            raise ValueError(f"rankable source manifest has invalid path: {relative}")
        if hashlib.sha256(candidate.read_bytes()).hexdigest() != digest:
            raise ValueError(f"rankable source differs from the image: {relative}")
        expected_files.add(relative)
    actual_files = {path.relative_to(repo).as_posix()
                    for path in (repo / "src").rglob("*") if path.is_file()}
    if not expected_files or actual_files != expected_files:
        raise ValueError("rankable source-rule inventory differs from the baked manifest")
    return actual_commit


def verify_rankable_pred_path(binary: Path) -> None:
    if binary.resolve() != TRUSTED_PRED_PATH:
        raise ValueError(f"rankable runs require the image-owned pred at {TRUSTED_PRED_PATH}")


def frozen_contract() -> Top50Contract:
    """Return the immutable logical budget for the released rankable track."""
    return Top50Contract(
        triage=TriageBudget(**EXPECTED_TRIAGE_BUDGET),
        episode=EvidenceBudget(**EXPECTED_EPISODE_BUDGET),
        shortlist_size=EXPECTED_SHORTLIST_SIZE,
        hypothesis_chars=EXPECTED_HYPOTHESIS_CHARS,
    )


def validate_rankable_settings(*, model_kwargs: dict | None = None) -> None:
    """Reject every knob that could change the frozen rankable execution path."""
    present = [name for name in FORBIDDEN_RANKABLE_ENV if name in os.environ]
    if present:
        raise ValueError(f"rankable Top50 runs reject custom setting(s): {', '.join(present)}")
    if model_kwargs is not None:
        raise ValueError("rankable Top50 runs do not accept custom model kwargs")


class _FakeExecutor:
    def run_triage(self, session, *, repo_path, inventory, model):
        payload = session.workdir / "shortlist.json"
        payload.write_text(json.dumps(list(inventory[:50])), encoding="utf-8")
        session.commit_file(str(payload))
        return PhaseResult(messages=[])

    def run_episode(self, session, **kwargs):
        return PhaseResult(messages=[])


def run(*, model: str, repo_dir: str | Path, output: str | Path,
        fake: bool = False, api_base: str | None = None, api_key: str | None = None,
        model_kwargs: dict | None = None) -> dict:
    if not fake:
        validate_rankable_settings(model_kwargs=model_kwargs)
    repo = Path(repo_dir).resolve()
    expected_commit = pinned_commit()
    actual_commit = expected_commit if fake else verify_rankable_source(repo, expected_commit)
    inventory = list_rules(repo)
    if len(inventory) < 50:
        raise ValueError(f"canonical inventory has only {len(inventory)} runnable rules")
    pred_binary = find_pred_binary()
    if not fake:
        verify_rankable_pred_path(pred_binary)
    pred_version = verify_pred_version(pred_binary)
    contract = frozen_contract()
    if fake:
        runner = Top50Runner(
            executor=_FakeExecutor(), contract=contract, pred_binary=pred_binary)
    else:
        runner = build_rankable_runner(
            contract=contract, pred_binary=pred_binary,
            agent_uid=int(os.environ["PRB_AGENT_UID"]),
            agent_gid=int(os.environ["PRB_AGENT_GID"]),
            oracle_uid=int(os.environ["PRB_ORACLE_UID"]),
            oracle_gid=int(os.environ["PRB_ORACLE_GID"]),
            evidence_gid=int(os.environ["PRB_EVIDENCE_GID"]),
            api_base=api_base, api_key=api_key)
    metadata = {
        "benchmark_contract": CONTRACT_ID,
        "library_commit": actual_commit,
        "runner_version": RUNNER_VERSION,
        "pred_version": pred_version,
        "agent_mode": AGENT_MODE,
        "prompt_id": expected_prompt_id(),
        "budget_contract_status": "frozen",
        "safety_controls": EXPECTED_SAFETY_CONTROLS,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "inference_parameters": EXPECTED_INFERENCE_PARAMETERS,
    }
    return runner.run(model=model, repo_path=repo, inventory=inventory,
                      output=output, metadata=metadata)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Standardized Model API Top50 benchmark")
    parser.add_argument("--model", default=os.environ.get("MODEL_NAME"), required=False)
    parser.add_argument("--repo-dir", default=os.environ.get("REPO_DIR", "/app/pr-src"))
    parser.add_argument("--output", default=os.environ.get("OUTPUT", "/out/submission.json"))
    parser.add_argument("--api-base", default=os.environ.get("API_BASE"))
    parser.add_argument("--api-key", default=os.environ.get("API_KEY"))
    parser.add_argument("--model-kwargs", default=os.environ.get("MODEL_KWARGS"))
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--fake", action="store_true", default=bool(os.environ.get("FAKE")))
    args = parser.parse_args(argv)
    if not args.model:
        parser.error("--model (or MODEL_NAME) is required")
    kwargs = json.loads(args.model_kwargs) if args.model_kwargs else None
    if kwargs is not None and not isinstance(kwargs, dict):
        parser.error("--model-kwargs must be a JSON object")
    if args.preflight:
        from benchmark.preflight import format_report, run_checks
        try:
            validate_rankable_settings(model_kwargs=kwargs)
        except ValueError as error:
            parser.error(str(error))
        try:
            verify_rankable_source(Path(args.repo_dir).resolve(), pinned_commit())
            verify_rankable_pred_path(find_pred_binary())
        except (OSError, ValueError, RuntimeError) as error:
            parser.error(str(error))
        checks = run_checks(args.model, repo_dir=args.repo_dir, api_base=args.api_base,
                            api_key=args.api_key, model_kwargs=None)
        raise SystemExit(0 if format_report(checks) else 1)
    result = run(model=args.model, repo_dir=args.repo_dir, output=args.output,
                 fake=args.fake, api_base=args.api_base, api_key=args.api_key,
                 model_kwargs=kwargs)
    print(f"Top50 {result['status']} ({len(result['episodes'])}/50 episodes) → {args.output}")


if __name__ == "__main__":
    main()
