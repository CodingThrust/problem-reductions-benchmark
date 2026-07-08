#!/usr/bin/env python3
"""
Benchmark runner: AI explores problem-reductions rules via pred CLI and
emits counterexample certificates. Independent checker validates each certificate.
"""

import argparse
import json
import os
import re
from pathlib import Path

import yaml

from benchmark.env_context import EnvContext
from benchmark.env_setup import setup_env
from benchmark.usage import extract_usage, usage_as_dict
from benchmark.verify import count_bugs, verify

CONFIG_FILE = Path(__file__).parent / "config.yaml"
REPO_CONFIG_FILE = Path(__file__).parent / "config_repo.yaml"
SKIP_RULES = {"mod", "traits", "graph_helpers", "analysis", "cost", "registry", "graph"}

# Per-call output-token ceiling. The submitter can override via --max-tokens.
DEFAULT_MAX_TOKENS = 8192


def list_rules(repo_dir: str) -> list[str]:
    rules_dir = Path(repo_dir) / "src" / "rules"
    return [f.stem for f in sorted(rules_dir.glob("*.rs")) if f.stem not in SKIP_RULES]


_CERT_RE = re.compile(r"CERTIFICATE_START\s*\n(.*?)CERTIFICATE_END", re.DOTALL)


def _message_text(msg: dict) -> str:
    """Full searchable text of one message: its ``content`` PLUS ``reasoning_content``.

    Tool-calling models (e.g. Qwen via function-calling) leave ``content`` empty and put their
    prose — including any CERTIFICATE block they narrate — in ``reasoning_content``. Reading
    only ``content`` would miss those, so certificate parsing scans both channels."""
    parts = [msg.get("content") or "", msg.get("reasoning_content") or ""]
    return "\n".join(p for p in parts if p)


def parse_certificate(messages: list) -> dict | None:
    """Extract the last structured certificate JSON from agent message history."""
    for msg in reversed(messages):
        block = _CERT_RE.search(_message_text(msg))
        if block:
            try:
                return json.loads(block.group(1).strip())
            except json.JSONDecodeError:
                return None
    return None


def parse_all_certificates(messages: list) -> list[dict]:
    """Every certificate block in the trajectory (a whole-repo run emits many), de-duplicated
    by (rule, source) in first-seen order. Unparseable blocks are skipped."""
    seen, out = set(), []
    for msg in messages:
        for block in _CERT_RE.finditer(_message_text(msg)):
            try:
                cert = json.loads(block.group(1).strip())
            except json.JSONDecodeError:
                continue
            key = (cert.get("rule"), json.dumps(cert.get("source"), sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            out.append(cert)
    return out


def extract_total_tokens(messages: list) -> int:
    total = 0
    for msg in messages:
        resp = msg.get("extra", {}).get("response")
        if resp and hasattr(resp, "usage"):
            total += getattr(resp.usage, "total_tokens", 0)
    return total


def save_trajectory(messages: list, path: Path) -> None:
    """Save agent message history as JSONL — one JSON object per line. ``content`` folds in
    ``reasoning_content`` so a cert a tool-calling model narrated there is preserved."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps({"role": msg.get("role", ""), "content": _message_text(msg)}) + "\n")


def _session_usage(agent):
    """Session token usage summed from the trajectory. Returns (tokens_k, usage)."""
    usage = extract_usage(agent.messages)
    total_tokens = usage.total_tokens or extract_total_tokens(agent.messages)
    tokens_k = round(total_tokens / 1000, 2)
    return tokens_k, usage


def _trajectory(agent) -> list[dict]:
    """The agent's own message history — provenance proof carried on cert-bearing rows.

    ``content`` folds in ``reasoning_content`` so the backend (which re-parses + provenance-
    checks certificates against the stored ``content``) also sees certs a tool-calling model
    narrated in its reasoning channel rather than in ``content``."""
    return [{"role": m.get("role", ""), "content": _message_text(m)} for m in agent.messages]


def _build_model(model_name: str, api_base: str | None, max_tokens: int,
                 model_kwargs: dict | None = None, api_key: str | None = None,
                 observation_template: str | None = None,
                 format_error_template: str | None = None):
    """A LitellmModel configured for this benchmark's runs.

    Everything that configures the API call flows through ``model_kwargs`` (forwarded to
    litellm.completion) — these are NOT top-level config fields in mini-swe-agent v2 and
    would otherwise be silently dropped. ``model_kwargs`` is the open-ended escape hatch for
    non-standard providers (Azure ``api_version``, OpenRouter / vLLM ``custom_llm_provider``,
    ``extra_headers``, ``temperature``, …); ``api_base``/``api_key``/``max_tokens`` are
    convenience shortcuts that merge into it (explicit shortcuts win on conflict).

    ``observation_template`` / ``format_error_template`` are LitellmModel CONFIG fields (not
    model_kwargs). They MUST be passed here or mini-swe-agent falls back to its default
    observation_template — which does NOT truncate output, so a big command result (e.g.
    ``pred list --rules --json``) floods the context and is re-sent every step. Our config's
    ``model:`` section carries the truncating templates; the caller threads them through."""
    from minisweagent.models.litellm_model import LitellmModel

    mk: dict = dict(model_kwargs or {})  # arbitrary passthrough for non-standard providers
    if max_tokens:
        mk["max_tokens"] = max_tokens  # per-call output ceiling
    if api_base:
        mk["api_base"] = api_base
    if api_key:
        mk["api_key"] = api_key  # generic key — no provider-specific env var name needed
    # Only pass templates we actually have, so an absent one keeps mini-swe's default.
    cfg = {}
    if observation_template is not None:
        cfg["observation_template"] = observation_template
    if format_error_template is not None:
        cfg["format_error_template"] = format_error_template
    return LitellmModel(model_name=model_name, model_kwargs=mk, **cfg)


def _load_agent_config(config_path: str | Path | None, default_file: Path,
                       strategy: str | None) -> tuple[dict, dict, str]:
    """Resolve the prompt config + strategy hints shared by both session runners.

    Returns (agent_cfg, model_cfg, strategy). ``cost_limit`` is force-disabled HERE — one
    place — so mini-swe-agent's default ($3) can never bound a run, including under a
    user-supplied AGENT_CONFIG; the config's step_limit is the only run bound."""
    cfg_file = Path(config_path) if config_path else default_file
    config = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
    if strategy is None:
        strat_file = os.environ.get("AGENT_STRATEGY_FILE")
        strategy = Path(strat_file).read_text(encoding="utf-8") if strat_file else ""
    agent_cfg = config.get("agent", {})
    agent_cfg["cost_limit"] = 0
    model_cfg = config.get("model", {}) or {}  # observation/format templates live here
    return agent_cfg, model_cfg, strategy


def run_one(
    model_name: str,
    ctx: EnvContext,
    rule_name: str,
    api_base: str | None = None,
    trajectory_dir: Path | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    config_path: str | Path | None = None,
    strategy: str | None = None,
    model_kwargs: dict | None = None,
    api_key: str | None = None,
) -> dict:
    """Run one bug-hunting session for a single rule. Returns a result dict.

    ``config_path`` overrides the bundled prompt config (hand-editable / mountable without a
    rebuild); ``strategy`` is extra free-form bug-hunting guidance injected into the prompt's
    reserved ``{{strategy}}`` slot (or read from env AGENT_STRATEGY_FILE if not passed).
    """
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.local import LocalEnvironment

    safe_name = rule_name.replace("-", "_")
    rule_file = ctx.repo_path / "src" / "rules" / f"{rule_name}.rs"

    agent_cfg, model_cfg, strategy = _load_agent_config(config_path, CONFIG_FILE, strategy)

    agent = DefaultAgent(
        _build_model(model_name, api_base, max_tokens,
                     model_kwargs=model_kwargs, api_key=api_key,
                     observation_template=model_cfg.get("observation_template"),
                     format_error_template=model_cfg.get("format_error_template")),
        LocalEnvironment(),
        **agent_cfg,
    )
    agent.extra_template_vars = {
        "repo_dir": str(ctx.repo_path),
        "rule_name": rule_name,
        "safe_name": safe_name,
        "rule_file": str(rule_file),
        "commit_hash": ctx.commit_hash[:7],
        "strategy": strategy,
    }

    cert = None
    try:
        agent.run(task=rule_name)
        tokens_k, usage = _session_usage(agent)
        usage_row = usage_as_dict(usage)
        trajectory = _trajectory(agent)
        cert = parse_certificate(agent.messages)
        if trajectory_dir is not None:
            safe_model = model_name.replace("/", "_").replace(":", "_")
            save_trajectory(agent.messages, Path(trajectory_dir) / f"{safe_model}_{rule_name}.jsonl")
    except Exception as e:
        return {
            "rule": rule_name,
            "result": f"error: {e}",
            "tokens_k": 0,
        }

    if cert is None:
        return {
            "rule": rule_name,
            "result": "no_certificate",
            "tokens_k": tokens_k,
            "steps": agent.n_calls,
            "usage": usage_row,
        }

    # Ensure the certificate carries the rule name
    cert.setdefault("rule", rule_name)

    # Independent verification — never trust the AI's claim
    verdict = verify(cert)

    if not verdict.accepted:
        return {
            "rule": rule_name,
            "result": "rejected",
            "tokens_k": tokens_k,
            "steps": agent.n_calls,
            "reject_reason": verdict.reason,
            "certificate": cert,
            "trajectory": trajectory,
            "usage": usage_row,
        }

    # A confirmed certificate on a fixed library commit is a bug, full stop —
    # novelty against external trackers is not part of the score.
    return {
        "rule": rule_name,
        "result": "bug_found",
        "tokens_k": tokens_k,
        "steps": agent.n_calls,
        "certificate": cert,
        "trajectory": trajectory,
        "verify_details": verdict.details,
        "usage": usage_row,
    }


def run_repo_session(
    model_name: str,
    ctx: EnvContext,
    *,
    api_base: str | None = None,
    trajectory_dir: Path | None = None,
    certs_path: Path | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    config_path: str | Path | None = None,
    strategy: str | None = None,
    model_kwargs: dict | None = None,
    api_key: str | None = None,
) -> dict:
    """One WHOLE-REPO bug-hunting session: the agent gets the entire library + pred, chooses
    which rules to probe within the config's ``step_limit``, and emits a certificate per bug.
    Returns ``{"rows": [...], "tokens_k": float, ...}`` — one result row per distinct emitted
    certificate, each re-verified with pred and carrying the shared session trajectory for
    provenance. Contrast with ``run_one`` (one isolated rule/session).

    Durability: the agent is prompted to append each certificate to ``certs_path`` the moment
    it finds it (the {{certs_file}} slot), so bugs survive an early stop; certificates are
    harvested from BOTH the trajectory and that file (deduped). ``output_path`` is pointed at
    ``trajectory_dir`` so mini-swe-agent persists the full trajectory to disk after every
    step (crash-proof) rather than only at the end.
    """
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.local import LocalEnvironment

    agent_cfg, model_cfg, strategy = _load_agent_config(config_path, REPO_CONFIG_FILE, strategy)

    safe_model = model_name.replace("/", "_").replace(":", "_")
    # Per-step trajectory persistence (mini-swe-agent saves config.output_path after every
    # step): point it at trajectory_dir so a mid-run crash still leaves the latest trajectory
    # on disk. Was previously unset (None → nothing written).
    if trajectory_dir is not None:
        Path(trajectory_dir).mkdir(parents=True, exist_ok=True)
        agent_cfg["output_path"] = str(Path(trajectory_dir) / f"{safe_model}_whole-repo.step.json")

    # The durable incremental cert log the prompt appends to — start each run from empty so it
    # only ever holds THIS run's bugs.
    if certs_path is not None:
        certs_path = Path(certs_path)
        certs_path.parent.mkdir(parents=True, exist_ok=True)
        certs_path.write_text("", encoding="utf-8")

    agent = DefaultAgent(
        _build_model(model_name, api_base, max_tokens,
                     model_kwargs=model_kwargs, api_key=api_key,
                     observation_template=model_cfg.get("observation_template"),
                     format_error_template=model_cfg.get("format_error_template")),
        LocalEnvironment(),
        **agent_cfg,
    )
    agent.extra_template_vars = {
        "repo_dir": str(ctx.repo_path),
        "commit_hash": ctx.commit_hash[:7],
        "strategy": strategy,
        "certs_file": str(certs_path) if certs_path is not None else "/tmp/certs.txt",
    }

    # Crash-safe: a fatal model error (quota/auth/network) is NOT a clean LimitsExceeded stop
    # — it propagates out of agent.run. Catch it so we still salvage whatever the agent found
    # before dying (from its messages + the durable certs.txt) and emit a fresh submission,
    # instead of the run crashing and leaving a STALE submission.json on disk.
    run_error = None
    try:
        agent.run(task="find-bugs")
    except Exception as e:  # noqa: BLE001 — any failure still salvages partial results
        run_error = f"{type(e).__name__}: {e}"

    tokens_k, usage = _session_usage(agent)
    trajectory = _trajectory(agent)
    if trajectory_dir is not None:
        save_trajectory(agent.messages, Path(trajectory_dir) / f"{safe_model}_whole-repo.jsonl")

    # Harvest from the trajectory AND the durable incremental log (parse_all_certificates
    # dedups by rule+source across both), so a cert the agent wrote to disk still counts even
    # if it never made it into the parsed messages.
    sources = list(agent.messages)
    if certs_path is not None and certs_path.exists():
        sources.append({"content": certs_path.read_text(encoding="utf-8")})
    rows = _rows_from_certificates(parse_all_certificates(sources))
    # The whole session is ONE trajectory shared by every bug — return it once for the
    # envelope (build_submission) instead of copying it onto each row. ``usage`` is the
    # session-level 4-bucket token total (per-rule rows carry their own; whole-repo rows
    # don't). ``error`` is set when the session died on a fatal error (the partial results
    # are still valid).
    return {"rows": rows, "tokens_k": tokens_k, "trajectory": trajectory,
            "usage": usage, "error": run_error}


def _rows_from_certificates(certs: list[dict]) -> list[dict]:
    """Verify each certificate with pred and build one result row per cert. bug_found when
    pred confirms, else rejected. No per-row trajectory — a whole-repo run's provenance
    trajectory lives once at the envelope level (submission["trajectory"])."""
    rows = []
    for cert in certs:
        verdict = verify(cert)
        row = {
            "rule": cert.get("rule"),
            "result": "bug_found" if verdict.accepted else "rejected",
            "tokens_k": 0.0,    # session tokens live on the submission envelope, not per row
            "certificate": cert,
        }
        if verdict.accepted:
            row["verify_details"] = verdict.details
        else:
            row["reject_reason"] = verdict.reason
        rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="pred-based bug-finding benchmark")
    parser.add_argument("--model", default="anthropic/claude-sonnet-4-6", help="LiteLLM model name")
    parser.add_argument("--api-base", default=None, help="Custom API base URL")
    parser.add_argument("--rules", nargs="*", help="Specific rule names (default: all)")
    parser.add_argument("--output", default="results/results_mini.json")
    parser.add_argument("--trajectory-dir", default=None, help="Directory to save per-rule JSONL trajectories")
    parser.add_argument("--repo-dir", required=True, help="Local problem-reductions clone (pinned commit required)")
    args = parser.parse_args()

    ctx = setup_env(args.repo_dir)
    rules = args.rules if args.rules else list_rules(str(ctx.repo_path))

    results, total_tokens_k = [], 0.0

    for rule_name in rules:
        print(f"  {rule_name}...", end=" ", flush=True)

        r = run_one(args.model, ctx, rule_name, api_base=args.api_base,
                    trajectory_dir=Path(args.trajectory_dir) if args.trajectory_dir else None)
        results.append(r)
        total_tokens_k += r.get("tokens_k", 0)

        status = "BUG FOUND" if r["result"] == "bug_found" else r["result"]
        print(f"{status} ({r.get('tokens_k', 0):.1f}K tok)")

    bugs_found = count_bugs(results)  # one rule = one bug
    efficiency_per_ktok = round(bugs_found / total_tokens_k, 4) if total_tokens_k else 0
    summary = {
        "model": args.model,
        "library_commit": ctx.commit_hash,
        "bugs_found": bugs_found,
        "total_tokens_k": round(total_tokens_k, 2),
        "efficiency_bugs_per_ktok": efficiency_per_ktok,
        "rules_tested": len(results),
        "results": results,
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n{bugs_found} bugs | {total_tokens_k:.1f}K tok | {efficiency_per_ktok:.4f} bugs/Ktok")
    print(f"Results → {args.output}")


if __name__ == "__main__":
    main()
