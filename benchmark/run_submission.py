#!/usr/bin/env python3
"""
Submission runner — the dockerized entry point.

Given a model (LiteLLM name), a provider API key (via the standard env var, e.g.
ANTHROPIC_API_KEY / OPENAI_API_KEY), and a fixed USD budget (default $20), run the
bug-hunting agent across the reduction rules and emit a single, rankable
``submission.json`` recording the bugs (rule counterexamples) it claims.

Budget is enforced by the shared Scheduler (per-rule LiteLLM ``cost_limit`` + a hard
total cap). Self-reported bug counts are advisory only — the backend re-verifies every
certificate with ``pred`` before anything reaches the leaderboard (see
benchmark/verify_submission.py).

Docker usage (key passed at run time, never baked into the image):

    docker run --rm \
        -e MODEL_NAME=anthropic/claude-sonnet-4-6 \
        -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
        -e BUDGET_USD=20 \
        -v "$PWD/out:/out" \
        problem-reductions-runner:v0.6.0
    # → ./out/submission.json

Env vars (CLI flags override): MODEL_NAME, BUDGET_USD, PER_RULE_BUDGET, OUTPUT,
REPO_DIR, MAX_RULES, API_BASE, FAKE (1 → no API/pred, for smoke tests).
"""
import argparse
import json
import os
import tempfile
from pathlib import Path

from benchmark.env_context import EnvContext
from benchmark.env_setup import PINNED_COMMIT, find_pred_binary
from benchmark.run_mini import list_rules
from benchmark.runner import FakeRunner, MiniSweRunner
from benchmark.scheduler import Scheduler
from benchmark.verify import count_bugs

SCHEMA_VERSION = "1.0"
RUNNER_VERSION = "0.6.0"


def build_submission(
    model: str,
    rows: list[dict],
    *,
    budget_cap: float,
    library_commit: str,
    runner_version: str = RUNNER_VERSION,
    created_at: str | None = None,
    submitted_by: str | None = None,
    total_cost_usd: float | None = None,
) -> dict:
    """Assemble the submission envelope from the scheduler's per-rule result rows.

    ``rules_tested`` counts only rules actually attempted (skipped_budget rows don't
    count as "reached"); ``bugs_found`` is distinct rules with a confirmed bug.
    ``total_cost_usd`` defaults to the sum of row costs; pass the scheduler's tracked
    spend to get the budget-faithful figure (the cap is enforced there, not per-row).
    """
    attempted = [r for r in rows if r.get("result") != "skipped_budget"]
    bugs = count_bugs(rows)
    cost = total_cost_usd if total_cost_usd is not None else sum(r.get("cost", 0.0) for r in rows)
    tokens_k = sum(r.get("tokens_k", 0.0) for r in rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "model": model,
        "library_commit": library_commit,
        "budget_cap": budget_cap,
        "bugs_found": bugs,
        "total_cost_usd": round(cost, 6),
        "total_tokens_k": round(tokens_k, 2),
        "efficiency_bugs_per_ktok": round(bugs / tokens_k, 4) if tokens_k else 0,
        "efficiency_bugs_per_dollar": round(bugs / cost, 4) if cost else 0,
        "rules_tested": len(attempted),
        "results": rows,
        "runner_version": runner_version,
        "created_at": created_at,
        "submitted_by": submitted_by,
    }


def run(
    model: str,
    repo_dir: str,
    *,
    budget: float = 20.0,
    per_rule_budget: float = 0.5,
    fake: bool = False,
    fake_result: str = "no_certificate",
    fake_cost: float = 0.01,
    max_rules: int | None = None,
    library_commit: str | None = None,
    api_base: str | None = None,
    output: Path | None = None,
    created_at: str | None = None,
    submitted_by: str | None = None,
) -> dict:
    """Run the full budgeted session for one model and return the submission dict.

    In ``fake`` mode no API key or pred binary is needed (FakeRunner) — used by tests
    and for smoke-running the container wiring.
    """
    repo = Path(repo_dir)
    commit = library_commit or PINNED_COMMIT

    if fake:
        # EnvContext validates a real pred binary; in fake mode the scheduler only ever
        # reads ctx.commit_hash, so a lightweight stand-in is enough (no API/pred needed).
        from types import SimpleNamespace
        ctx = SimpleNamespace(repo_path=repo, pred_binary=Path("pred"), commit_hash=commit)
        runner = FakeRunner(cost_per_rule=fake_cost, result=fake_result)
    else:
        ctx = EnvContext(repo_path=repo, pred_binary=find_pred_binary(), commit_hash=commit)
        runner = MiniSweRunner(api_base=api_base)

    rules = list_rules(str(repo))
    if max_rules is not None:
        rules = rules[:max_rules]

    with tempfile.TemporaryDirectory() as tmp:
        scheduler = Scheduler(
            runner=runner,
            models=[model],
            rules=rules,
            total_budget=budget,
            per_rule_budget=per_rule_budget,
            results_dir=Path(tmp) / "results",
            checkpoint_path=Path(tmp) / "checkpoint.json",
            ctx=ctx,
            resume=False,
            parallelism=1,
        )
        completed = scheduler.run_all()
        spent = scheduler._spent.get(model)

    rows = completed[model]
    sub = build_submission(
        model, rows, budget_cap=budget, library_commit=commit,
        created_at=created_at, submitted_by=submitted_by, total_cost_usd=spent,
    )

    if output is not None:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(sub, indent=2), encoding="utf-8")

    return sub


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def main() -> None:
    parser = argparse.ArgumentParser(description="Budgeted bug-finding runner → submission.json")
    parser.add_argument("--model", default=_env("MODEL_NAME"), help="LiteLLM model name (env MODEL_NAME)")
    parser.add_argument("--budget", type=float, default=float(_env("BUDGET_USD", "20") or 20),
                        help="Total USD budget (env BUDGET_USD, default 20)")
    parser.add_argument("--per-rule", type=float, default=float(_env("PER_RULE_BUDGET", "0.5") or 0.5),
                        help="Per-rule cost cap (env PER_RULE_BUDGET)")
    parser.add_argument("--repo-dir", default=_env("REPO_DIR", "/app/pr-src"),
                        help="problem-reductions source tree (env REPO_DIR)")
    parser.add_argument("--output", default=_env("OUTPUT", "/out/submission.json"),
                        help="Where to write submission.json (env OUTPUT)")
    parser.add_argument("--api-base", default=_env("API_BASE"), help="Custom API base (env API_BASE)")
    parser.add_argument("--max-rules", type=lambda v: int(v) if v else None,
                        default=_env("MAX_RULES"), help="Cap rules attempted (smoke runs)")
    parser.add_argument("--submitted-by", default=_env("SUBMITTED_BY"))
    parser.add_argument("--fake", action="store_true", default=bool(_env("FAKE")),
                        help="No API/pred — FakeRunner smoke test")
    args = parser.parse_args()

    if not args.model:
        parser.error("--model (or env MODEL_NAME) is required")

    import datetime
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    print(f"Running {args.model} at ${args.budget:.0f} budget "
          f"(per-rule ${args.per_rule:.2f}){' [FAKE]' if args.fake else ''}...")
    sub = run(
        args.model,
        args.repo_dir,
        budget=args.budget,
        per_rule_budget=args.per_rule,
        fake=args.fake,
        max_rules=args.max_rules,
        api_base=args.api_base,
        output=Path(args.output),
        created_at=created_at,
        submitted_by=args.submitted_by,
    )
    print(f"\n{sub['bugs_found']} claimed bugs | ${sub['total_cost_usd']:.4f} | "
          f"{sub['rules_tested']} rules attempted")
    print(f"Submission → {args.output}")
    print("Self-reported counts are advisory; the backend re-verifies every certificate "
          "with pred before it counts.")


if __name__ == "__main__":
    main()
