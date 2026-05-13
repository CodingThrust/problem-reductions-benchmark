#!/usr/bin/env python3
"""
Benchmark runner using mini-SWE-agent as the harness.

Instead of a single-shot API call, the agent runs a bash loop:
it reads the rule file, writes a test, runs `cargo test`, fixes
compile errors, and iterates — just like SWE-bench.
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
    """After agent run: check if a test_bug_* test now fails."""
    safe_name = rule_name.replace("-", "_")
    result = subprocess.run(
        ["cargo", "test", f"test_bug_{safe_name}", "--", "--nocapture"],
        cwd=repo_dir, capture_output=True, text=True, timeout=120
    )
    output = result.stdout + result.stderr
    if "error[" in output or "error: aborting" in output:
        return False  # compile error, not a real bug
    return result.returncode != 0


def restore_test_file(test_file: str, original: str) -> None:
    if test_file and original is not None:
        Path(test_file).write_text(original, encoding="utf-8")


def run_one(model_name: str, repo_dir: str, rule_name: str, cost_limit: float) -> dict:
    safe_name = rule_name.replace("-", "_")
    test_file = find_test_file(repo_dir, rule_name)
    original = Path(test_file).read_text(encoding="utf-8") if test_file else None

    config = yaml.safe_load(CONFIG_FILE.read_text())
    # Inject per-instance values into instance_template via extra_template_vars
    agent_cfg = config.get("agent", {})
    agent_cfg["cost_limit"] = cost_limit

    agent = DefaultAgent(
        LitellmModel(model_name=model_name),
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
        bug_found = check_bug_found(repo_dir, rule_name)
    except Exception as e:
        cost = agent.cost
        bug_found = False
        return {"rule": rule_name, "result": f"error: {e}", "cost": cost}
    finally:
        restore_test_file(test_file, original)

    return {
        "rule": rule_name,
        "result": "bug_found" if bug_found else "no_bug",
        "cost": cost,
        "steps": agent.n_calls,
    }


def list_rules(repo_dir: str) -> list[str]:
    rules_dir = Path(repo_dir) / "src" / "rules"
    return [f.stem for f in sorted(rules_dir.glob("*.rs")) if f.stem not in SKIP_RULES]


def main():
    parser = argparse.ArgumentParser(description="mini-SWE-agent benchmark for problem-reductions")
    parser.add_argument("--model", default="anthropic/claude-sonnet-4-6", help="LiteLLM model name")
    parser.add_argument("--budget", type=float, default=20.0, help="Total USD budget")
    parser.add_argument("--per-rule", type=float, default=0.5, help="Per-rule cost limit ($)")
    parser.add_argument("--rules", nargs="*", help="Specific rule names (default: all)")
    parser.add_argument("--output", default="results/results_mini.json")
    parser.add_argument("--repo-dir", help="Local problem-reductions clone (skips git clone)")
    args = parser.parse_args()

    def run(repo_dir: str):
        rules = args.rules if args.rules else list_rules(repo_dir)
        results, total_cost, bugs_found = [], 0.0, 0

        for rule_name in rules:
            remaining = args.budget - total_cost
            if remaining <= 0:
                print("Budget exhausted.")
                break
            limit = min(args.per_rule, remaining)
            print(f"  {rule_name} (limit ${limit:.2f})...", end=" ", flush=True)
            r = run_one(args.model, repo_dir, rule_name, limit)
            results.append(r)
            total_cost += r["cost"]
            status = "BUG FOUND" if r["result"] == "bug_found" else r["result"]
            print(f"{status} (${r['cost']:.4f}, {r.get('steps', '?')} steps)")
            if r["result"] == "bug_found":
                bugs_found += 1

        summary = {
            "model": args.model,
            "bugs_found": bugs_found,
            "total_cost_usd": round(total_cost, 6),
            "efficiency_bugs_per_dollar": round(bugs_found / total_cost, 4) if total_cost else 0,
            "rules_tested": len(results),
            "results": results,
        }
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\n{bugs_found} bugs | ${total_cost:.4f} | {summary['efficiency_bugs_per_dollar']:.2f} bugs/$")
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
