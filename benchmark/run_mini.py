#!/usr/bin/env python3
"""
Benchmark runner: AI explores problem-reductions rules via pred CLI and
emits counterexample certificates. Independent checker validates each certificate.
"""

import argparse
import json
from pathlib import Path

import yaml

from benchmark.env_context import EnvContext
from benchmark.env_setup import setup_env
from benchmark.verify import count_bugs, verify

CONFIG_FILE = Path(__file__).parent / "config.yaml"
SKIP_RULES = {"mod", "traits", "graph_helpers", "analysis", "cost", "registry", "graph"}

# Rough average cost per 1K tokens (used as fallback when model doesn't report usage)
AVG_COST_PER_KTOK = 6.0


def list_rules(repo_dir: str) -> list[str]:
    rules_dir = Path(repo_dir) / "src" / "rules"
    return [f.stem for f in sorted(rules_dir.glob("*.rs")) if f.stem not in SKIP_RULES]


def parse_certificate(messages: list) -> dict | None:
    """Extract structured certificate JSON from agent message history."""
    import re
    for msg in reversed(messages):
        content = msg.get("content", "")
        block = re.search(r"CERTIFICATE_START\s*\n(.*?)CERTIFICATE_END", content, re.DOTALL)
        if block:
            try:
                return json.loads(block.group(1).strip())
            except json.JSONDecodeError:
                return None
    return None


def extract_total_tokens(messages: list) -> int:
    total = 0
    for msg in messages:
        resp = msg.get("extra", {}).get("response")
        if resp and hasattr(resp, "usage"):
            total += getattr(resp.usage, "total_tokens", 0)
    return total


def save_trajectory(messages: list, path: Path) -> None:
    """Save agent message history as JSONL — one JSON object per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps({"role": msg.get("role", ""), "content": msg.get("content", "")}) + "\n")


def run_one(
    model_name: str,
    ctx: EnvContext,
    rule_name: str,
    cost_limit: float,
    api_base: str | None = None,
    trajectory_dir: Path | None = None,
) -> dict:
    """Run one bug-hunting session for a single rule. Returns a result dict."""
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.local import LocalEnvironment
    from minisweagent.models.litellm_model import LitellmModel

    safe_name = rule_name.replace("-", "_")
    rule_file = ctx.repo_path / "src" / "rules" / f"{rule_name}.rs"

    config = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
    agent_cfg = config.get("agent", {})
    agent_cfg["cost_limit"] = cost_limit

    model_kwargs = {"model_name": model_name}
    if api_base:
        model_kwargs["api_base"] = api_base

    agent = DefaultAgent(
        LitellmModel(**model_kwargs),
        LocalEnvironment(),
        **agent_cfg,
    )
    agent.extra_template_vars = {
        "repo_dir": str(ctx.repo_path),
        "rule_name": rule_name,
        "safe_name": safe_name,
        "rule_file": str(rule_file),
        "commit_hash": ctx.commit_hash[:7],
        "cost_limit": cost_limit,
    }

    cert = None
    try:
        agent.run(task=rule_name)
        cost = agent.cost
        total_tokens = extract_total_tokens(agent.messages)
        tokens_k = round(total_tokens / 1000, 2) if total_tokens else round(cost / AVG_COST_PER_KTOK * 1000, 2)
        cert = parse_certificate(agent.messages)
        if trajectory_dir is not None:
            safe_model = model_name.replace("/", "_").replace(":", "_")
            save_trajectory(agent.messages, Path(trajectory_dir) / f"{safe_model}_{rule_name}.jsonl")
    except Exception as e:
        return {
            "rule": rule_name,
            "result": f"error: {e}",
            "cost": getattr(agent, "cost", 0.0),
            "tokens_k": 0,
        }

    if cert is None:
        return {
            "rule": rule_name,
            "result": "no_certificate",
            "cost": cost,
            "tokens_k": tokens_k,
            "steps": agent.n_calls,
        }

    # Ensure the certificate carries the rule name
    cert.setdefault("rule", rule_name)

    # Independent verification — never trust the AI's claim
    verdict = verify(cert)

    if not verdict.accepted:
        return {
            "rule": rule_name,
            "result": "rejected",
            "cost": cost,
            "tokens_k": tokens_k,
            "steps": agent.n_calls,
            "reject_reason": verdict.reason,
            "certificate": cert,
        }

    # A confirmed certificate on a fixed library commit is a bug, full stop —
    # novelty against external trackers is not part of the score.
    return {
        "rule": rule_name,
        "result": "bug_found",
        "cost": cost,
        "tokens_k": tokens_k,
        "steps": agent.n_calls,
        "certificate": cert,
        "verify_details": verdict.details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="pred-based bug-finding benchmark")
    parser.add_argument("--model", default="anthropic/claude-sonnet-4-6", help="LiteLLM model name")
    parser.add_argument("--api-base", default=None, help="Custom API base URL")
    parser.add_argument("--budget", type=float, default=20.0, help="Total USD budget")
    parser.add_argument("--per-rule", type=float, default=0.5, help="Per-rule cost limit ($)")
    parser.add_argument("--rules", nargs="*", help="Specific rule names (default: all)")
    parser.add_argument("--output", default="results/results_mini.json")
    parser.add_argument("--trajectory-dir", default=None, help="Directory to save per-rule JSONL trajectories")
    parser.add_argument("--repo-dir", required=True, help="Local problem-reductions clone (pinned commit required)")
    args = parser.parse_args()

    ctx = setup_env(args.repo_dir)
    rules = args.rules if args.rules else list_rules(str(ctx.repo_path))

    results, total_cost, total_tokens_k = [], 0.0, 0.0

    for rule_name in rules:
        remaining = args.budget - total_cost
        if remaining <= 0:
            print("Budget exhausted.")
            break
        limit = min(args.per_rule, remaining)
        print(f"  {rule_name} (limit ${limit:.2f})...", end=" ", flush=True)

        r = run_one(args.model, ctx, rule_name, limit, api_base=args.api_base,
                    trajectory_dir=Path(args.trajectory_dir) if args.trajectory_dir else None)
        results.append(r)
        total_cost += r.get("cost", 0)
        total_tokens_k += r.get("tokens_k", 0)

        status = "BUG FOUND" if r["result"] == "bug_found" else r["result"]
        print(f"{status} (${r.get('cost', 0):.4f}, {r.get('tokens_k', 0):.1f}K tok)")

    bugs_found = count_bugs(results)  # one rule = one bug
    efficiency_per_ktok = round(bugs_found / total_tokens_k, 4) if total_tokens_k else 0
    summary = {
        "model": args.model,
        "library_commit": ctx.commit_hash,
        "bugs_found": bugs_found,
        "total_cost_usd": round(total_cost, 6),
        "total_tokens_k": round(total_tokens_k, 2),
        "efficiency_bugs_per_ktok": efficiency_per_ktok,
        "efficiency_bugs_per_dollar": round(bugs_found / total_cost, 4) if total_cost else 0,
        "rules_tested": len(results),
        "results": results,
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n{bugs_found} bugs | ${total_cost:.4f} | {efficiency_per_ktok:.4f} bugs/Ktok")
    print(f"Results → {args.output}")


if __name__ == "__main__":
    main()
