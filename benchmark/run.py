#!/usr/bin/env python3
"""
Minimal SWE-like benchmark for problem-reductions bug finding.
Pattern: rule file → AI → inject test → cargo test → record result.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import anthropic

REPO_URL = "https://github.com/CodingThrust/problem-reductions"

PRICING = {
    "claude-sonnet-4-6":        {"input": 3.0,  "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.8,  "output": 4.0},
    "claude-opus-4-7":           {"input": 15.0, "output": 75.0},
}

SYSTEM = """\
You are a Rust expert finding bugs in problem reduction rules.

Each rule implements two functions:
- reduce_to(): transforms source problem A into target problem B
- extract_solution(): maps a target solution back to source space

A rule is BUGGY if the round-trip fails: given A, reduce to B, solve B to get
solution s, extract s → A', but A' is not a valid solution to A.

Write a single #[test] function that:
1. Constructs a specific source problem instance
2. Calls reduce_to() to get the target problem
3. Constructs or derives a target solution (use a brute-force solver if available)
4. Calls extract_solution() with that solution
5. Asserts the extracted result is a valid solution to the source problem

If the rule has a bug, your test FAILS. If correct, it PASSES.
Output ONLY a Rust code block with the test function. No explanation.\
"""


def cost_usd(usage, model: str) -> float:
    p = PRICING.get(model, {"input": 3.0, "output": 15.0})
    return (usage.input_tokens * p["input"] + usage.output_tokens * p["output"]) / 1_000_000


def extract_rust_block(text: str) -> str | None:
    m = re.search(r"```rust\n(.*?)```", text, re.DOTALL)
    return m.group(1).strip() if m else None


def find_test_file(repo_dir: str, rule_name: str) -> Path | None:
    """Find the unit test file for a rule by scanning the rule file for #[path = ...]."""
    rule_path = Path(repo_dir) / "src" / "rules" / f"{rule_name}.rs"
    if not rule_path.exists():
        return None
    content = rule_path.read_text(encoding="utf-8")
    m = re.search(r'#\[path\s*=\s*"([^"]+)"\]', content)
    if m:
        rel = m.group(1)
        return (Path(repo_dir) / "src" / "rules" / rel).resolve()
    # Fallback: conventional location
    p = Path(repo_dir) / "src" / "unit_tests" / "rules" / f"{rule_name}.rs"
    return p if p.exists() else None


def run_cargo_test(repo_dir: str, test_name: str) -> tuple[bool, str]:
    """Returns (bug_found, output). bug_found=True means test failed (bug detected)."""
    result = subprocess.run(
        ["cargo", "test", test_name, "--", "--nocapture"],
        cwd=repo_dir, capture_output=True, text=True, timeout=180
    )
    output = result.stdout + result.stderr
    # Distinguish compile error from test failure
    if "error[" in output or "error: " in output:
        return False, "compile_error: " + output[:500]
    return result.returncode != 0, output[:500]


def benchmark_rule(client, model: str, repo_dir: str, rule_name: str) -> dict:
    rule_path = Path(repo_dir) / "src" / "rules" / f"{rule_name}.rs"
    rule_content = rule_path.read_text(encoding="utf-8")

    test_file = find_test_file(repo_dir, rule_name)
    test_content = test_file.read_text(encoding="utf-8") if test_file else "// no existing tests"

    safe_name = rule_name.replace("-", "_")
    user_msg = (
        f"Rule file `{rule_name}.rs`:\n```rust\n{rule_content[:5000]}\n```\n\n"
        f"Existing tests (for import patterns):\n```rust\n{test_content[:2000]}\n```\n\n"
        f"Write test `test_bug_{safe_name}` that probes for bugs in reduce_to() or extract_solution()."
    )

    resp = client.messages.create(
        model=model, max_tokens=1024,
        system=SYSTEM,
        messages=[{"role": "user", "content": user_msg}]
    )

    cost = cost_usd(resp.usage, model)
    test_code = extract_rust_block(resp.content[0].text)

    if not test_code:
        return {"rule": rule_name, "result": "parse_error", "cost": cost}

    if not test_file:
        return {"rule": rule_name, "result": "no_test_file", "cost": cost}

    original = test_file.read_text(encoding="utf-8")
    test_file.write_text(original + f"\n\n{test_code}\n", encoding="utf-8")

    test_name = f"test_bug_{safe_name}"
    try:
        bug_found, output = run_cargo_test(repo_dir, test_name)
    except subprocess.TimeoutExpired:
        test_file.write_text(original, encoding="utf-8")
        return {"rule": rule_name, "result": "timeout", "cost": cost}
    finally:
        test_file.write_text(original, encoding="utf-8")  # always restore

    return {
        "rule": rule_name,
        "result": "bug_found" if bug_found else "no_bug",
        "cost": cost,
        "cargo_output": output,
        "test_code": test_code,
    }


# Rules to skip (not reduction rules)
SKIP_RULES = {"mod", "traits", "graph_helpers", "analysis", "cost", "registry", "graph"}


def list_rules(repo_dir: str) -> list[str]:
    rules_dir = Path(repo_dir) / "src" / "rules"
    return [
        f.stem for f in sorted(rules_dir.glob("*.rs"))
        if f.stem not in SKIP_RULES
    ]


def main():
    parser = argparse.ArgumentParser(description="Problem-reductions bug-finding benchmark")
    parser.add_argument("--model", default="claude-sonnet-4-6")
    parser.add_argument("--budget", type=float, default=20.0, help="USD budget")
    parser.add_argument("--rules", nargs="*", help="specific rule names (default: all)")
    parser.add_argument("--output", default="results/results.json")
    parser.add_argument("--repo-dir", help="use existing local clone instead of cloning")
    args = parser.parse_args()

    client = anthropic.Anthropic()

    def run(repo_dir: str):
        rules = args.rules if args.rules else list_rules(repo_dir)
        results, total_cost, bugs_found = [], 0.0, 0

        for rule_name in rules:
            if total_cost >= args.budget:
                print("Budget exhausted.")
                break
            print(f"  {rule_name}...", end=" ", flush=True)
            r = benchmark_rule(client, args.model, repo_dir, rule_name)
            results.append(r)
            total_cost += r["cost"]
            status = "BUG FOUND" if r["result"] == "bug_found" else r["result"]
            print(f"{status} (${r['cost']:.4f})")
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
        print(f"\n{bugs_found} bugs found | ${total_cost:.4f} spent | "
              f"{summary['efficiency_bugs_per_dollar']:.2f} bugs/$")
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
