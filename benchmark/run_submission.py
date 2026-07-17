#!/usr/bin/env python3
"""
Submission runner — shared by Docker and lightweight local agent modes.

Given a model and an agent backend, run the bug-hunting agent across the reduction rules
and emit a single, rankable ``submission.json`` recording its submitted counterexamples.

The agent decides when its single whole-repository session is complete. A run-wide
counterexample submission limit (100 by default) bounds scored claims, not exploration.
Self-reported bug counts are advisory only — the backend
re-verifies every certificate with ``pred`` before anything reaches the leaderboard (see
benchmark/verify_submission.py).

Docker usage (all config — any provider — in submission.env, key never baked in):

    docker run --rm --env-file submission.env -v "$PWD/out:/out" \
        problem-reductions-runner:v0.6.0
    # → ./out/submission.json   (template: submission.env.example)

Env vars (CLI flags override): MODEL_NAME, the matching API key (generic API_KEY or a
provider var like OPENAI_API_KEY/ANTHROPIC_API_KEY), API_BASE, MODEL_KWARGS,
AGENT_BACKEND (mini-swe | claude-code | codex), AGENT_CONFIG,
AGENT_STRATEGY_FILE;
SUBMIT_LIMIT (default 100), FAKE (1 → no API/pred, used by tests). The claude-code backend authenticates via
CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY.
"""
import argparse
import json
import os
import re
from pathlib import Path

from benchmark.env_context import EnvContext
from benchmark.env_setup import (
    DEFAULT_REPO_URL,
    clone_or_verify_repo,
    find_pred_binary,
    pinned_commit,
    verify_pred_version,
)
from benchmark.submit_session import SubmissionSession
from benchmark.usage import Usage, usage_as_dict, usage_from_dict
from benchmark.verify import count_bugs

SCHEMA_VERSION = "2.1"
RUNNER_VERSION = "0.8.0"
BACKENDS = ("mini-swe", "claude-code", "codex")


def build_submission(
    model: str,
    rows: list[dict],
    *,
    library_commit: str,
    runner_version: str = RUNNER_VERSION,
    created_at: str | None = None,
    submitted_by: str | None = None,
    total_tokens_k: float | None = None,
    pred_version: str = "",
    usage_totals=None,
    run_error: str | None = None,
    submit_limit: int = 100,
    submit_attempts: list[dict] | None = None,
) -> dict:
    """Assemble the submission envelope from the runner's result rows.

    ``rules_tested`` is the number of distinct rules for which the agent submitted a
    structured certificate. It is a floor on exploration coverage because inspected clean
    rules do not produce rows. ``bugs_found`` is distinct rules with a confirmed bug.

    The 4-bucket token total is either passed in (``usage_totals`` — whole-repo, one
    session) and rides on the envelope as ``usage_totals``. ``total_tokens_k`` is derived
    from it when it carries counts, else the explicit session total is used.
    """
    bugs = count_bugs(rows)

    # 4-bucket token usage: the reproducible primitive.
    if usage_totals is None:
        usage_totals = Usage()
        for r in rows:
            usage_totals = usage_totals + usage_from_dict(r.get("usage"))
    elif isinstance(usage_totals, dict):
        usage_totals = usage_from_dict(usage_totals)

    if usage_totals.total_tokens:
        tokens_k = usage_totals.total_tokens / 1000
    else:
        tokens_k = total_tokens_k if total_tokens_k is not None else sum(r.get("tokens_k", 0.0) for r in rows)

    submit_attempts = submit_attempts or []
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "model": model,
        "library_commit": library_commit,
        "bugs_found": bugs,
        "total_tokens_k": round(tokens_k, 2),
        "efficiency_bugs_per_ktok": round(bugs / tokens_k, 4) if tokens_k else 0,
        "rules_tested": len({r.get("rule") for r in rows}),
        # Aggregate token totals — reproducible primitive behind total_tokens_k.
        "usage_totals": usage_as_dict(usage_totals),
        "results": rows,
        # Version/provenance stamp so a produced file self-identifies its run (no more
        # "everything overwrites one submission.json"). agent_mode + created_at + the
        # runner/pred/library pins together pin down exactly what produced this file.
        "runner_version": runner_version,
        "pred_version": pred_version,
        "agent_mode": "whole-repo",
        "created_at": created_at,
        "submitted_by": submitted_by,
        # Evaluation-owned command ledger.  Results are derived from this ledger; agent
        # prose and ad-hoc cert files are not accepted as alternate submission channels.
        "submit_limit": submit_limit,
        "submit_log": submit_attempts,
    }
    # Set only when the session died on a fatal error (quota/auth/network): the results are
    # the partial salvage, not a clean "0 bugs" completion. Keeps a crash from masquerading
    # as a finished run.
    if run_error is not None:
        envelope["run_error"] = run_error
    return envelope


def _run_backend(backend: str, ctx, model: str, *, trajectory_dir=None,
                 api_base=None, max_tokens=None, config_path=None, strategy=None,
                 model_kwargs=None, api_key=None, submit_session=None) -> dict:
    """Dispatch one whole-repository session without a one-use wrapper hierarchy."""
    if backend == "claude-code":
        from benchmark.claude_code import run_repo_claude
        return run_repo_claude(
            model, ctx, trajectory_dir=trajectory_dir, config_path=config_path,
            strategy=strategy, api_key=api_key, submit_session=submit_session)
    if backend == "codex":
        from benchmark.codex_cli import run_repo_codex
        return run_repo_codex(
            model, ctx, trajectory_dir=trajectory_dir, config_path=config_path,
            strategy=strategy, api_key=api_key, submit_session=submit_session)

    from benchmark.run_mini import DEFAULT_MAX_TOKENS, run_repo_session
    return run_repo_session(
        model, ctx, api_base=api_base,
        max_tokens=max_tokens if max_tokens is not None else DEFAULT_MAX_TOKENS,
        trajectory_dir=trajectory_dir, config_path=config_path, strategy=strategy,
        model_kwargs=model_kwargs, api_key=api_key, submit_session=submit_session)


def run(
    model: str,
    repo_dir: str,
    *,
    fake: bool = False,
    library_commit: str | None = None,
    api_base: str | None = None,
    output: Path | None = None,
    created_at: str | None = None,
    submitted_by: str | None = None,
    max_tokens: int | None = None,
    config_path: str | Path | None = None,
    strategy: str | None = None,
    model_kwargs: dict | None = None,
    api_key: str | None = None,
    backend: str = "mini-swe",
    trajectory_dir: str | Path | None = None,
    submit_limit: int = 100,
) -> dict:
    """Run the full session for one model and return the submission dict.

    ``trajectory_dir`` is where the whole-repo agent's raw/final log is persisted
    (default: the output file's directory). ``output`` is the authoritative submission
    path; callers that want run history should give each run a distinct path.

    The runner always launches ONE session over the whole library. The agent enumerates,
    triages, and decides when to stop. ``backend`` selects the implementation: ``mini-swe``
    (default,
    litellm — any provider), ``claude-code`` (headless ``claude -p``), or ``codex``
    (headless ``codex exec``). CLI backends ignore
    ``api_base``/``model_kwargs``/``max_tokens`` — the CLI owns the API call.

    In ``fake`` mode no API key or pred binary is needed; it only smoke-tests wiring.
    """
    repo = Path(repo_dir)
    commit = library_commit or pinned_commit()

    if backend not in BACKENDS:
        raise ValueError(f"unknown backend: {backend!r} (expected one of {BACKENDS})")

    if fake:
        # EnvContext validates a real pred binary; fake mode only needs the fields consumed
        # by a backend, so a lightweight stand-in avoids API/pred setup.
        from types import SimpleNamespace
        ctx = SimpleNamespace(repo_path=repo, pred_binary=Path("pred"),
                              commit_hash=commit, pred_version="")
    else:
        pred_binary = find_pred_binary()
        pred_ver = verify_pred_version(pred_binary)  # fail fast if pred != pinned version
        ctx = EnvContext(repo_path=repo, pred_binary=pred_binary, commit_hash=commit,
                         pred_version=pred_ver)

    # Where to persist the whole-repo trajectory. An explicit
    # TRAJECTORY_DIR wins; otherwise default to the output file's directory (the mounted
    # /out). Exposed as a parameter so it shows up in the runner's config surface, not
    # hardcoded — a crash/early-stop still leaves the latest trajectory on disk.
    traj_dir = Path(trajectory_dir) if trajectory_dir else (
        Path(output).parent if output is not None else None)

    # One service spans the complete repository session.
    with SubmissionSession(limit=submit_limit) as submit_session:
        session = ({"tokens_k": 0.0, "usage": None, "error": None} if fake else
                   _run_backend(
                       backend, ctx, model, trajectory_dir=traj_dir,
                       api_base=api_base, max_tokens=max_tokens,
                       config_path=config_path, strategy=strategy,
                       model_kwargs=model_kwargs, api_key=api_key,
                       submit_session=submit_session))
        rows = submit_session.result_rows()
        total_tokens = session["tokens_k"]
        session_usage = session.get("usage")
        run_error = session.get("error")
        if not fake and not run_error and not submit_session.reachable:
            run_error = ("submit channel was not successfully probed; no status or submit "
                         "request reached the evaluation service")
            if traj_dir is not None:
                submit_session.preserve_artifacts(
                    Path(traj_dir) / "salvaged-agent-artifacts")
        if run_error:
            print(f"WARNING: session ended on error — salvaged partial results: {run_error}")
        submit_attempts = submit_session.attempts

    sub = build_submission(
        model, rows, library_commit=commit,
        created_at=created_at, submitted_by=submitted_by,
        total_tokens_k=total_tokens,
        pred_version=getattr(ctx, "pred_version", ""),
        usage_totals=session_usage,
        run_error=run_error,
        submit_limit=submit_limit, submit_attempts=submit_attempts,
    )

    if output is not None:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(sub, indent=2), encoding="utf-8")

    return sub


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def _load_env_file(path: str | Path) -> None:
    """Load a small Docker-compatible ``KEY=VALUE`` env file without overriding env."""
    path = Path(path)
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{line_no}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"{path}:{line_no}: invalid environment variable name {key!r}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def main(argv: list[str] | None = None) -> None:
    # Load the env file before computing argparse defaults. Ambient variables and explicit
    # CLI flags still win, matching Docker's practical configuration precedence.
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--env-file")
    pre_args, _ = pre_parser.parse_known_args(argv)
    if pre_args.env_file:
        try:
            _load_env_file(pre_args.env_file)
        except (OSError, ValueError) as e:
            pre_parser.error(str(e))

    parser = argparse.ArgumentParser(description="Bug-finding runner → submission.json")
    parser.add_argument("--env-file",
                        help="Load KEY=VALUE defaults from this file; ambient env and CLI flags win")
    parser.add_argument("--model", default=_env("MODEL_NAME"), help="LiteLLM model name (env MODEL_NAME)")
    parser.add_argument("--repo-dir", default=_env("REPO_DIR"),
                        help="problem-reductions source tree (env REPO_DIR)")
    parser.add_argument("--repo-ref", default=_env("REPO_REF"),
                        help="Clone/verify this git tag, branch, or commit before running. "
                             "Used by lightweight local runs (env REPO_REF).")
    parser.add_argument("--repo-url", default=_env("REPO_URL", DEFAULT_REPO_URL),
                        help="Repository URL used when --repo-dir does not exist (env REPO_URL)")
    parser.add_argument("--output", default=_env("OUTPUT"),
                        help="Authoritative submission path (env OUTPUT)")
    parser.add_argument("--trajectory-dir", default=_env("TRAJECTORY_DIR"),
                        help="Where to persist the whole-repo raw/final log "
                             "(env TRAJECTORY_DIR; default: the output file's directory).")
    parser.add_argument("--api-base", default=_env("API_BASE"), help="Custom API base (env API_BASE)")
    parser.add_argument("--api-key", default=_env("API_KEY"),
                        help="Generic API key, any provider (env API_KEY). Avoids needing the "
                             "provider-specific var; provider env vars (ANTHROPIC_API_KEY, …) still work.")
    parser.add_argument("--model-kwargs", default=_env("MODEL_KWARGS"),
                        help="JSON of extra litellm.completion kwargs for non-standard providers "
                             "(env MODEL_KWARGS), e.g. '{\"api_version\":\"2024-02-01\","
                             "\"custom_llm_provider\":\"openai\"}'.")
    parser.add_argument("--submitted-by", default=_env("SUBMITTED_BY"))
    parser.add_argument("--max-tokens", type=lambda v: int(v) if v else None,
                        default=_env("MAX_TOKENS"), help="Per-call output-token ceiling")
    parser.add_argument("--submit-limit", type=int,
                        default=int(_env("SUBMIT_LIMIT", "100")),
                        help="Run-wide counterexample submission attempts (env SUBMIT_LIMIT; "
                             "accepted and rejected submissions both consume one)")
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
                        help="No API/pred — backend wiring smoke run (mostly covered by tests)")
    parser.add_argument("--backend", choices=BACKENDS,
                        default=_env("AGENT_BACKEND", "mini-swe"),
                        help="Agent implementation (env AGENT_BACKEND). mini-swe: litellm, any "
                             "provider (default). claude-code: headless `claude -p`; auth via "
                             "Claude login/token. codex: headless `codex exec`; auth via Codex "
                             "login or OPENAI_API_KEY.")
    args = parser.parse_args(argv)

    if not args.model:
        parser.error("--model (or env MODEL_NAME) is required")

    library_commit = None
    if args.repo_ref:
        missing = [name for name, value in (
            ("--repo-dir", args.repo_dir),
            ("--output", args.output),
            ("--trajectory-dir", args.trajectory_dir),
        ) if not value]
        if missing:
            parser.error("local --repo-ref runs require explicit " + ", ".join(missing))
        try:
            library_commit = clone_or_verify_repo(args.repo_dir, args.repo_ref, args.repo_url)
        except (OSError, ValueError) as e:
            parser.error(str(e))
        print(f"Repository ready at {Path(args.repo_dir).resolve()} ({library_commit[:12]})")
    else:
        # Container defaults. The image also sets these variables, but retaining fallbacks
        # keeps the module directly runnable for build-time fake checks.
        args.repo_dir = args.repo_dir or "/app/pr-src"
        args.output = args.output or "/out/submission.json"

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

    if args.preflight:
        from benchmark.preflight import format_report, run_checks
        from benchmark.run_mini import DEFAULT_MAX_TOKENS
        print(f"Preflight for {args.model} (one tiny real call + pred/rules checks)...")
        results = run_checks(args.model, repo_dir=args.repo_dir, api_base=args.api_base,
                             api_key=args.api_key, model_kwargs=model_kwargs,
                             max_tokens=args.max_tokens or DEFAULT_MAX_TOKENS)
        raise SystemExit(0 if format_report(results) else 1)

    import datetime
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    backend_tag = f", {args.backend}" if args.backend != "mini-swe" else ""
    print(f"Running {args.model} (whole-repo{backend_tag}){' [FAKE]' if args.fake else ''}...")
    sub = run(
        args.model,
        args.repo_dir,
        fake=args.fake,
        library_commit=library_commit,
        api_base=args.api_base,
        output=Path(args.output),
        created_at=created_at,
        submitted_by=args.submitted_by,
        max_tokens=args.max_tokens,
        config_path=args.config,
        strategy=strategy,
        model_kwargs=model_kwargs,
        api_key=args.api_key,
        backend=args.backend,
        trajectory_dir=args.trajectory_dir,
        submit_limit=args.submit_limit,
    )
    print(f"\n{sub['bugs_found']} claimed bugs | {sub['total_tokens_k']:.1f}K tok | "
          f"{sub['rules_tested']} rules submitted")
    used, limit = len(sub["submit_log"]), sub["submit_limit"]
    print(f"Submit attempts: {used}/{limit} ({limit - used} remaining)")
    print(f"Submission → {args.output}")
    print("Self-reported counts are advisory; the backend re-verifies every certificate "
          "with pred before it counts.")


if __name__ == "__main__":
    main()
