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

Docker usage (all config — any provider — in submission.env, key never baked in):

    docker run --rm --env-file submission.env -v "$PWD/out:/out" \
        problem-reductions-runner:v0.6.0
    # → ./out/submission.json   (template: submission.env.example)

Env vars (CLI flags override): MODEL_NAME, the matching API key (generic API_KEY or a
provider var like OPENAI_API_KEY/ANTHROPIC_API_KEY), PRICE_IN/PRICE_OUT (required),
API_BASE, MODEL_KWARGS, BUDGET_USD, PER_RULE_BUDGET, MAX_RULES, AGENT_CONFIG,
AGENT_STRATEGY_FILE; FAKE (1 → no API/pred, used by tests).
"""
import argparse
import json
import os
import re
import tempfile
from pathlib import Path

from benchmark.cost import Usage, price_as_dict, usage_as_dict, usage_from_dict
from benchmark.env_context import EnvContext
from benchmark.env_setup import find_pred_binary, pinned_commit, verify_pred_version
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
    total_tokens_k: float | None = None,
    trajectory: list[dict] | None = None,
    pred_version: str = "",
    price=None,
    usage_totals=None,
    agent_mode: str | None = None,
    run_error: str | None = None,
) -> dict:
    """Assemble the submission envelope from the runner's result rows.

    ``rules_tested`` is the number of DISTINCT rules with a result (skipped_budget rows
    don't count as "reached"). For per-rule that is the rules attempted; for whole-repo it
    is the distinct rules the agent emitted a certificate for — a floor, since rules the
    agent probed but found clean aren't represented as rows. ``bugs_found`` is distinct
    rules with a confirmed bug.

    Cost is metered from tokens, not hand-reported: when ``price`` (the submitter's declared
    per-token rate) is given, ``total_cost_usd`` is DERIVED as ``price × token_usage`` — the
    same authoritative figure the backend recomputes (benchmark/verify_submission.py), so the
    self-reported total is never trusted. The 4-bucket token total is either passed in
    (``usage_totals`` — whole-repo, one session) or summed from each row's ``usage`` block
    (per-rule). It rides on the envelope as ``usage_totals`` (the reproducible primitive) next
    to the ``prices`` snapshot, so any reader can recompute spend under other prices. Without a
    ``price`` (e.g. FAKE mode) it falls back to explicit session totals or the row-sum.
    """
    attempted = [r for r in rows if r.get("result") != "skipped_budget"]
    bugs = count_bugs(rows)

    # 4-bucket token usage: the reproducible primitive the backend re-prices.
    if usage_totals is None:
        usage_totals = Usage()
        for r in rows:
            usage_totals = usage_totals + usage_from_dict(r.get("usage"))
    elif isinstance(usage_totals, dict):
        usage_totals = usage_from_dict(usage_totals)

    if price is not None:
        cost = price.cost(usage_totals)
        tokens_k = usage_totals.total_tokens / 1000
    else:
        cost = total_cost_usd if total_cost_usd is not None else sum(r.get("cost", 0.0) for r in rows)
        tokens_k = total_tokens_k if total_tokens_k is not None else sum(r.get("tokens_k", 0.0) for r in rows)

    envelope = {
        "schema_version": SCHEMA_VERSION,
        "model": model,
        "library_commit": library_commit,
        "budget_cap": budget_cap,
        "bugs_found": bugs,
        "total_cost_usd": round(cost, 6),
        "total_tokens_k": round(tokens_k, 2),
        "efficiency_bugs_per_ktok": round(bugs / tokens_k, 4) if tokens_k else 0,
        "efficiency_bugs_per_dollar": round(bugs / cost, 4) if cost else 0,
        "rules_tested": len({r.get("rule") for r in attempted}),
        # Aggregate token totals + the declared price snapshot — the backend re-meters cost
        # from these (zero-trust); dated by created_at, recomputable under any price table.
        "usage_totals": usage_as_dict(usage_totals),
        "prices": price_as_dict(price) if price is not None else None,
        "results": rows,
        # Version/provenance stamp so a produced file self-identifies its run (no more
        # "everything overwrites one submission.json"). agent_mode + created_at + the
        # runner/pred/library pins together pin down exactly what produced this file.
        "runner_version": runner_version,
        "pred_version": pred_version,
        "agent_mode": agent_mode,
        "created_at": created_at,
        "submitted_by": submitted_by,
    }
    # Set only when the session died on a fatal error (quota/auth/network): the results are
    # the partial salvage, not a clean "0 bugs" completion. Keeps a crash from masquerading
    # as a finished run.
    if run_error is not None:
        envelope["run_error"] = run_error
    # whole-repo: the one shared session log, stored once here (not copied onto each row).
    if trajectory is not None:
        envelope["trajectory"] = trajectory
    return envelope


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
    price=None,
    max_tokens: int | None = None,
    safety_margin: float = 1.0,
    config_path: str | Path | None = None,
    strategy: str | None = None,
    model_kwargs: dict | None = None,
    api_key: str | None = None,
    mode: str = "per-rule",
    trajectory_dir: str | Path | None = None,
) -> dict:
    """Run the full budgeted session for one model and return the submission dict.

    ``trajectory_dir`` is where the whole-repo agent's trajectory + the durable incremental
    cert log are persisted (default: the output file's directory). Beside the stable
    ``output`` a versioned archive (``submission-<model>-<timestamp>.json``) is also written
    so successive runs don't all overwrite one submission.json.

    ``mode`` selects the runner: ``per-rule`` (default) schedules one isolated agent session
    per rule under a shared budget; ``whole-repo`` runs ONE session over the whole library —
    the agent enumerates and triages the rules itself and emits a certificate per bug.

    ``price`` is the submitter's per-token rate (benchmark.cost.Price); with it, spend is
    recomputed from token usage so the budget is a hard cap. ``safety_margin`` is held back
    from the budget so the boundary-crossing call still lands under it.

    In ``fake`` mode no API key or pred binary is needed (FakeRunner) — used by tests
    and for smoke-running the container wiring; ``fake`` always uses the per-rule path.
    """
    repo = Path(repo_dir)
    commit = library_commit or pinned_commit()

    if fake:
        # EnvContext validates a real pred binary; in fake mode the scheduler only ever
        # reads ctx.commit_hash, so a lightweight stand-in is enough (no API/pred needed).
        from types import SimpleNamespace
        ctx = SimpleNamespace(repo_path=repo, pred_binary=Path("pred"),
                              commit_hash=commit, pred_version="")
        runner = FakeRunner(cost_per_rule=fake_cost, result=fake_result)
    else:
        pred_binary = find_pred_binary()
        pred_ver = verify_pred_version(pred_binary)  # fail fast if pred != pinned version
        ctx = EnvContext(repo_path=repo, pred_binary=pred_binary, commit_hash=commit,
                         pred_version=pred_ver)
        # Only the per-rule path uses this, but the constructor just stores kwargs (no I/O),
        # so building it unconditionally keeps `runner` assigned in exactly one place.
        runner = MiniSweRunner(api_base=api_base, price=price, max_tokens=max_tokens,
                               config_path=config_path, strategy=strategy,
                               model_kwargs=model_kwargs, api_key=api_key)

    # Where to persist the whole-repo trajectory + durable cert log. An explicit
    # TRAJECTORY_DIR wins; otherwise default to the output file's directory (the mounted
    # /out). Exposed as a parameter so it shows up in the runner's config surface, not
    # hardcoded — a crash/early-stop then still leaves the found bugs on disk.
    traj_dir = Path(trajectory_dir) if trajectory_dir else (
        Path(output).parent if output is not None else None)

    if mode == "whole-repo" and not fake:
        from benchmark.run_mini import run_repo_session
        out_dir = traj_dir
        session = run_repo_session(
            model, ctx, cost_limit=max(budget - safety_margin, 0.0),
            api_base=api_base, price=price, max_tokens=max_tokens,
            trajectory_dir=out_dir,
            certs_path=(out_dir / "certs.txt") if out_dir is not None else None,
            config_path=config_path, strategy=strategy,
            model_kwargs=model_kwargs, api_key=api_key,
        )
        rows, total_cost, total_tokens = session["rows"], session["cost"], session["tokens_k"]
        session_trajectory = session["trajectory"]
        session_usage = session.get("usage")  # session-level 4-bucket total to re-price
        run_error = session.get("error")       # set if the session died on a fatal error
        if run_error:
            print(f"WARNING: session ended on error — salvaged partial results: {run_error}")
    else:
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
                safety_margin=safety_margin,
            )
            completed = scheduler.run_all()
            spent = scheduler._spent.get(model)
        # per-rule totals: cost is the scheduler's tracked spend; tokens sum from the rows.
        # Per-rule rows carry their own trajectories AND their own 4-bucket ``usage``, so the
        # envelope aggregates usage from the rows (session_usage=None).
        rows, total_cost, total_tokens = completed[model], spent, None
        session_trajectory = None
        session_usage = None
        # per-rule already isolates each rule's failure into an "error:" row (run_one), so the
        # session as a whole never dies — no envelope-level error to record.
        run_error = None

    sub = build_submission(
        model, rows, budget_cap=budget, library_commit=commit,
        created_at=created_at, submitted_by=submitted_by,
        total_cost_usd=total_cost, total_tokens_k=total_tokens, trajectory=session_trajectory,
        pred_version=getattr(ctx, "pred_version", ""),
        price=price, usage_totals=session_usage,
        agent_mode=mode, run_error=run_error,
    )

    if output is not None:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(sub, indent=2)
        output.write_text(blob, encoding="utf-8")
        # ALSO write a versioned archive copy so runs don't clobber each other: the stable
        # `output` is the "latest" pointer (what `prb submit` reads); the archive keeps history.
        archive = output.with_name(_versioned_name(output, model, created_at))
        if archive.name != output.name:
            archive.write_text(blob, encoding="utf-8")

    return sub


def _versioned_name(output: Path, model: str, created_at: str | None) -> str:
    """Archive filename that encodes the run: ``<stem>-<model>-<timestamp><suffix>``.

    ``model`` is made filesystem-safe; ``timestamp`` is the compact UTC created_at
    (digits + 'T'). Keeps each run's output as a distinct, self-identifying file next to
    the stable ``output`` pointer."""
    label = model.replace("/", "_").replace(":", "_")
    stamp = re.sub(r"[^0-9T]", "", created_at)[:15] if created_at else "unknown"
    return f"{output.stem}-{label}-{stamp}{output.suffix}"


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def _float_env(name: str) -> float | None:
    v = os.environ.get(name)
    return float(v) if v else None


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
                        help="Stable 'latest' submission path (env OUTPUT). A versioned archive "
                             "copy (submission-<label>-<timestamp>.json) is also written beside it.")
    parser.add_argument("--trajectory-dir", default=_env("TRAJECTORY_DIR"),
                        help="Where to persist the whole-repo trajectory + durable cert log "
                             "(env TRAJECTORY_DIR; default: the output file's directory).")
    parser.add_argument("--api-base", default=_env("API_BASE"), help="Custom API base (env API_BASE)")
    parser.add_argument("--api-key", default=_env("API_KEY"),
                        help="Generic API key, any provider (env API_KEY). Avoids needing the "
                             "provider-specific var; provider env vars (ANTHROPIC_API_KEY, …) still work.")
    parser.add_argument("--model-kwargs", default=_env("MODEL_KWARGS"),
                        help="JSON of extra litellm.completion kwargs for non-standard providers "
                             "(env MODEL_KWARGS), e.g. '{\"api_version\":\"2024-02-01\","
                             "\"custom_llm_provider\":\"openai\"}'.")
    parser.add_argument("--max-rules", type=lambda v: int(v) if v else None,
                        default=_env("MAX_RULES"), help="Cap rules attempted (smoke runs)")
    parser.add_argument("--submitted-by", default=_env("SUBMITTED_BY"))
    # Submitter-supplied price (USD / 1M tokens). You pay the bill, so you set the rate; we
    # recompute spend from token usage instead of trusting the gateway. Omit to use a built-in
    # default for known models (see benchmark/cost.py), or the gateway figure if unknown.
    parser.add_argument("--price-in", type=float, default=_float_env("PRICE_IN"),
                        help="USD per 1M input tokens (env PRICE_IN)")
    parser.add_argument("--price-out", type=float, default=_float_env("PRICE_OUT"),
                        help="USD per 1M output tokens (env PRICE_OUT)")
    parser.add_argument("--price-cache-read", type=float, default=_float_env("PRICE_CACHE_READ"),
                        help="USD per 1M cache-read tokens (env PRICE_CACHE_READ)")
    parser.add_argument("--price-cache-write", type=float, default=_float_env("PRICE_CACHE_WRITE"),
                        help="USD per 1M cache-write tokens (env PRICE_CACHE_WRITE)")
    parser.add_argument("--max-tokens", type=lambda v: int(v) if v else None,
                        default=_env("MAX_TOKENS"), help="Per-call output-token ceiling")
    parser.add_argument("--safety-margin", type=float,
                        default=float(_env("SAFETY_MARGIN", "1.0") or 1.0),
                        help="USD held back from the budget as overshoot headroom (default 1)")
    parser.add_argument("--expected-pred-version", default=_env("EXPECTED_PRED_VERSION"),
                        help="Require this pred version (default: pinned; empty string disables)")
    parser.add_argument("--expected-pred-commit", default=_env("EXPECTED_PRED_COMMIT"),
                        help="Library commit to record/verify (default: pinned for this image)")
    # Agent prompt/strategy hook — hand-editable without rebuilding the image. Mount your own
    # config.yaml (full prompt) and/or a strategy file (extra bug-hunting hints injected into
    # the {{strategy}} slot of the system prompt). See benchmark/config.yaml.
    parser.add_argument("--config", default=_env("AGENT_CONFIG"),
                        help="Path to an agent config.yaml (env AGENT_CONFIG; default: bundled)")
    parser.add_argument("--strategy-file", default=_env("AGENT_STRATEGY_FILE"),
                        help="File of extra strategy hints injected into the prompt (env AGENT_STRATEGY_FILE)")
    parser.add_argument("--preflight", action="store_true", default=bool(_env("PREFLIGHT")),
                        help="Validate the config with one tiny real API call + pred/rules "
                             "checks, then exit (run this before the full batch).")
    parser.add_argument("--fake", action="store_true", default=bool(_env("FAKE")),
                        help="No API/pred — FakeRunner wiring run (mostly covered by tests)")
    parser.add_argument("--mode", choices=("per-rule", "whole-repo"),
                        default=_env("AGENT_MODE", "per-rule"),
                        help="per-rule: one isolated agent session per rule (default). "
                             "whole-repo: ONE session over the whole library, the agent picks "
                             "which rules to probe (env AGENT_MODE).")
    args = parser.parse_args()

    if not args.model:
        parser.error("--model (or env MODEL_NAME) is required")

    # verify_pred_version()/pinned_commit() read these env vars; surface the flags through them.
    if args.expected_pred_version is not None:
        os.environ["EXPECTED_PRED_VERSION"] = args.expected_pred_version
    if args.expected_pred_commit:
        os.environ["EXPECTED_PRED_COMMIT"] = args.expected_pred_commit

    # Read the strategy hints file once (so a bad path fails fast, before any API spend).
    strategy = None
    if args.strategy_file:
        strategy = Path(args.strategy_file).read_text(encoding="utf-8")

    model_kwargs = None
    if args.model_kwargs:
        try:
            model_kwargs = json.loads(args.model_kwargs)  # fail fast on malformed JSON
        except json.JSONDecodeError as e:
            parser.error(f"--model-kwargs is not valid JSON: {e}")
        if not isinstance(model_kwargs, dict):
            parser.error("--model-kwargs must be a JSON object")

    # Price is always submitter-supplied — there is no built-in table (a stale default would
    # silently mis-meter the $20 cap). A real run REQUIRES --price-in and --price-out.
    from benchmark.cost import Price
    price = None
    if args.price_in is not None and args.price_out is not None:
        price = Price(args.price_in, args.price_out,
                      args.price_cache_read or 0.0, args.price_cache_write or 0.0)
    elif args.price_in is not None or args.price_out is not None:
        parser.error("--price-in and --price-out must be given together")
    if price is None and not args.fake:
        parser.error("--price-in and --price-out (env PRICE_IN/PRICE_OUT) are required: "
                     "spend is metered as token_usage × your price, so you must declare it "
                     "(USD / 1M tokens). There is no built-in price table.")

    if args.preflight:
        from benchmark.preflight import format_report, run_checks
        from benchmark.run_mini import DEFAULT_MAX_TOKENS
        print(f"Preflight for {args.model} (one tiny real call + pred/rules checks)...")
        results = run_checks(args.model, repo_dir=args.repo_dir, api_base=args.api_base,
                             api_key=args.api_key, model_kwargs=model_kwargs,
                             max_tokens=args.max_tokens or DEFAULT_MAX_TOKENS, price=price)
        raise SystemExit(0 if format_report(results) else 1)

    import datetime
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    detail = f"per-rule ${args.per_rule:.2f}" if args.mode == "per-rule" else "whole-repo"
    print(f"Running {args.model} at ${args.budget:.0f} budget "
          f"({detail}){' [FAKE]' if args.fake else ''}...")
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
        price=price,
        max_tokens=args.max_tokens,
        safety_margin=args.safety_margin,
        config_path=args.config,
        strategy=strategy,
        model_kwargs=model_kwargs,
        api_key=args.api_key,
        mode=args.mode,
        trajectory_dir=args.trajectory_dir,
    )
    print(f"\n{sub['bugs_found']} claimed bugs | ${sub['total_cost_usd']:.4f} | "
          f"{sub['rules_tested']} rules attempted")
    archive = _versioned_name(Path(args.output), args.model, created_at)
    print(f"Submission → {args.output}  (archive: {archive})")
    print("Self-reported counts are advisory; the backend re-verifies every certificate "
          "with pred before it counts.")


if __name__ == "__main__":
    main()
