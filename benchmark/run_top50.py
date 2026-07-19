#!/usr/bin/env python3
"""Production entrypoint for the standardized self-selected Top50 model track."""
from __future__ import annotations

import argparse
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


def pilot_contract() -> Top50Contract:
    """Return explicitly provisional values; issue #68 freezes the public contract."""
    return Top50Contract(
        triage=TriageBudget(
            model_generations=int(os.environ.get("PRB_TRIAGE_GENERATIONS", "8")),
            shell_actions=int(os.environ.get("PRB_TRIAGE_ACTIONS", "12"))),
        episode=EvidenceBudget(
            model_generations=int(os.environ.get("PRB_EPISODE_GENERATIONS", "10")),
            shell_actions=int(os.environ.get("PRB_EPISODE_ACTIONS", "12")),
            pred_calls=int(os.environ.get("PRB_PRED_CALLS", "24")),
            solve_calls=int(os.environ.get("PRB_SOLVE_CALLS", "10")),
            submit_attempts=2,
            max_output_chars=int(os.environ.get("PRB_MAX_OUTPUT_CHARS", "10000")),
            pred_timeout_seconds=int(os.environ.get("PRB_PRED_TIMEOUT_SECONDS", "300"))),
    )


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
    repo = Path(repo_dir).resolve()
    inventory = list_rules(repo)
    if len(inventory) < 50:
        raise ValueError(f"canonical inventory has only {len(inventory)} runnable rules")
    pred_binary = find_pred_binary()
    verify_pred_version(pred_binary)
    contract = pilot_contract()
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
            api_base=api_base, api_key=api_key, model_kwargs=model_kwargs)
    result = runner.run(model=model, repo_path=repo, inventory=inventory, output=output)
    result["library_commit"] = pinned_commit()
    result["budget_contract_status"] = "pilot-unfrozen"
    # Rewrite once with provenance added after the runner's final checkpoint.
    Path(output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Standardized Model API Top50 benchmark")
    parser.add_argument("--model", default=os.environ.get("MODEL_NAME"), required=False)
    parser.add_argument("--repo-dir", default=os.environ.get("REPO_DIR", "/app/pr-src"))
    parser.add_argument("--output", default=os.environ.get("OUTPUT", "/out/submission.json"))
    parser.add_argument("--api-base", default=os.environ.get("API_BASE"))
    parser.add_argument("--api-key", default=os.environ.get("API_KEY"))
    parser.add_argument("--model-kwargs", default=os.environ.get("MODEL_KWARGS"))
    parser.add_argument("--fake", action="store_true", default=bool(os.environ.get("FAKE")))
    args = parser.parse_args(argv)
    if not args.model:
        parser.error("--model (or MODEL_NAME) is required")
    kwargs = json.loads(args.model_kwargs) if args.model_kwargs else None
    if kwargs is not None and not isinstance(kwargs, dict):
        parser.error("--model-kwargs must be a JSON object")
    result = run(model=args.model, repo_dir=args.repo_dir, output=args.output,
                 fake=args.fake, api_base=args.api_base, api_key=args.api_key,
                 model_kwargs=kwargs)
    print(f"Top50 {result['status']} ({len(result['episodes'])}/50 episodes) → {args.output}")


if __name__ == "__main__":
    main()
