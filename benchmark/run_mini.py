#!/usr/bin/env python3
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
"""
Benchmark runner using mini-SWE-agent as the harness.

Properties (GiggleLiu 2026-05-13):
- Energy efficiency: metric is bugs per 1K tokens (not bugs per dollar)
- Verifiable bug report: agent outputs structured report when bug found
- Sustainable bug pool: skip rules with no unit test file
- Not a previously known case: check GitHub issues before recording
"""

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

import yaml
from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.litellm_model import LitellmModel

REPO_URL = "https://github.com/CodingThrust/problem-reductions"
CONFIG_FILE = Path(__file__).parent / "config.yaml"
SKIP_RULES = {"mod", "traits", "graph_helpers", "analysis", "cost", "registry", "graph"}

# Tokens per dollar (used to estimate token count from cost)
# claude-sonnet-4-6: input $3/MTok, output $15/MTok — rough average ~$6/MTok
AVG_COST_PER_MTOK = 6.0


def find_test_file(repo_dir: str, rule_name: str) -> str:
    rule_path = Path(repo_dir) / "src" / "rules" / f"{rule_name}.rs"
    if not rule_path.exists():
        return ""
    content = rule_path.read_text(encoding="utf-8")
    m = re.search(r'#\[path\s*=\s*"([^"]+)"\]', content)
    if m:
        return str((Path(repo_dir) / "src" / "rules" / m.group(1)).resolve())
    fallback = Path(repo_dir) / "src" / "unit_tests" / "rules" / f"{rule_name}.rs"
    return str(fallback) if fallback.exists() else ""


def check_bug_found(repo_dir: str, rule_name: str) -> bool:
    safe_name = rule_name.replace("-", "_")
    result = subprocess.run(
        ["cargo", "test", f"test_bug_{safe_name}", "--", "--nocapture"],
        cwd=repo_dir, capture_output=True, text=True, timeout=120
    )
    output = result.stdout + result.stderr
    if "error[" in output or "error: aborting" in output:
        return False
    return result.returncode != 0


def parse_bug_report(messages: list) -> dict | None:
    """Extract structured bug report from agent message history."""
    for msg in reversed(messages):
        content = msg.get("content", "")
        if "BUG_REPORT_START" in content:
            block = re.search(r"BUG_REPORT_START\n(.*?)BUG_REPORT_END", content, re.DOTALL)
            if block:
                report = {}
                for line in block.group(1).strip().splitlines():
                    if ": " in line:
                        k, v = line.split(": ", 1)
                        report[k.strip()] = v.strip()
                return report
    return None


def is_known_issue(rule_name: str) -> bool:
    """Check if a bug for this rule is already reported on GitHub."""
    result = subprocess.run(
        ["gh", "issue", "list", "--repo", "CodingThrust/problem-reductions",
         "--search", rule_name, "--json", "title", "--limit", "5"],
        capture_output=True, text=True, timeout=15
    )
    if result.returncode != 0:
        return False  # can't check, assume novel
    issues = json.loads(result.stdout or "[]")
    return any(rule_name.replace("_", "-") in i["title"].lower() or
               rule_name in i["title"].lower() for i in issues)


def restore_test_file(test_file: str, original: str | None) -> None:
    if test_file and original is not None:
        Path(test_file).write_text(original, encoding="utf-8")


def extract_total_tokens(messages: list) -> int:
    """Sum actual token usage from all model responses."""
    total = 0
    for msg in messages:
        resp = msg.get("extra", {}).get("response")
        if resp and hasattr(resp, "usage"):
            total += getattr(resp.usage, "total_tokens", 0)
    return total


def run_one(model_name: str, repo_dir: str, rule_name: str, cost_limit: float, api_base: str | None = None) -> dict:
    safe_name = rule_name.replace("-", "_")
    test_file = find_test_file(repo_dir, rule_name)
    original = Path(test_file).read_text(encoding="utf-8") if test_file else None

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
        "repo_dir": repo_dir,
        "rule_name": rule_name,
        "safe_name": safe_name,
        "test_file": test_file or "(none — rule has no unit test file)",
        "cost_limit": cost_limit,
    }

    try:
        agent.run(task=rule_name)
        cost = agent.cost
        total_tokens = extract_total_tokens(agent.messages)
        tokens_k = round(total_tokens / 1000, 2) if total_tokens else round(cost / AVG_COST_PER_MTOK * 1000, 2)
        bug_found = check_bug_found(repo_dir, rule_name)
        bug_report = parse_bug_report(agent.messages) if bug_found else None
        known = is_known_issue(rule_name) if bug_found else False
    except Exception as e:
        restore_test_file(test_file, original)
        return {"rule": rule_name, "result": f"error: {e}", "cost": agent.cost, "tokens_k": 0}
    finally:
        restore_test_file(test_file, original)

    result = "known_issue" if (bug_found and known) else ("bug_found" if bug_found else "no_bug")
    return {
        "rule": rule_name,
        "result": result,
        "cost": cost,
        "tokens_k": tokens_k,
        "steps": agent.n_calls,
        "bug_report": bug_report,
    }


def list_rules(repo_dir: str) -> list[str]:
    rules_dir = Path(repo_dir) / "src" / "rules"
    return [f.stem for f in sorted(rules_dir.glob("*.rs")) if f.stem not in SKIP_RULES]


def main():
    parser = argparse.ArgumentParser(description="mini-SWE-agent benchmark for problem-reductions")
    parser.add_argument("--model", default="anthropic/claude-sonnet-4-6", help="LiteLLM model name")
    parser.add_argument("--api-base", default=None, help="Custom API base URL (e.g. https://api.openai-hk.com/v1)")
    parser.add_argument("--budget", type=float, default=20.0, help="Total USD budget")
    parser.add_argument("--per-rule", type=float, default=0.5, help="Per-rule cost limit ($)")
    parser.add_argument("--rules", nargs="*", help="Specific rule names (default: all)")
    parser.add_argument("--output", default="results/results_mini.json")
    parser.add_argument("--repo-dir", help="Local problem-reductions clone (skips git clone)")
    args = parser.parse_args()

    def run(repo_dir: str):
        rules = args.rules if args.rules else list_rules(repo_dir)
        results, total_cost, total_tokens_k, bugs_found = [], 0.0, 0.0, 0

        for rule_name in rules:
            remaining = args.budget - total_cost
            if remaining <= 0:
                print("Budget exhausted.")
                break
            limit = min(args.per_rule, remaining)
            print(f"  {rule_name} (limit ${limit:.2f})...", end=" ", flush=True)
            r = run_one(args.model, repo_dir, rule_name, limit, api_base=args.api_base)
            results.append(r)
            total_cost += r["cost"]
            total_tokens_k += r.get("tokens_k", 0)
            is_new_bug = r["result"] == "bug_found"
            status = "BUG FOUND" if is_new_bug else r["result"]
            print(f"{status} (${r['cost']:.4f}, {r.get('tokens_k', 0):.1f}K tok, {r.get('steps', '?')} steps)")
            if is_new_bug:
                bugs_found += 1

        efficiency_per_ktok = round(bugs_found / total_tokens_k, 4) if total_tokens_k else 0
        summary = {
            "model": args.model,
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

    if args.repo_dir:
        run(args.repo_dir)
    else:
        with tempfile.TemporaryDirectory() as tmpdir:
            print(f"Cloning {REPO_URL}...")
            subprocess.run(["git", "clone", "--depth=1", REPO_URL, tmpdir], check=True)
            run(tmpdir)


if __name__ == "__main__":
    main()
