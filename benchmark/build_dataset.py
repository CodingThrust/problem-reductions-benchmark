"""
Build the benchmark task dataset from a pinned problem-reductions commit.

Each row is ONE reduction-rule file (the unit the agent is pointed at) frozen as a
self-contained task: the rule's Rust source snapshot plus the metadata parsed from
it (source/target problem types, overhead) and the library pin. The
dataset is the published benchmark — load_dataset() gives the full task set without
needing the (Rust) problem-reductions repo. Reproduction still needs `pred` built at
the same pin; that environment is shipped separately (see the dataset card).

Usage:
    python -m benchmark.build_dataset --repo-dir ../problem-reductions [--out dataset]
    python -m benchmark.build_dataset --repo-dir ../problem-reductions --pred <path>  # cross-check count

Outputs:
    <out>/problem_reductions_bugs.jsonl   one task per line
    <out>/README.md                       HF Dataset card (frontmatter + docs)
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Non-rule files in src/rules/: helpers / module glue / test scaffolding. These cannot rely
# on the "implements ReduceTo" check alone — e.g. traits.rs defines the ReductionResult trait
# and helper files reference it, so they pass the substring check but are not tasks.
NON_RULE_STEMS = {
    "mod", "traits", "graph_helpers", "analysis", "cost", "registry", "graph",
    "ilp_helpers", "test_helpers",
}

_SOURCE_RE = re.compile(r"type\s+Source\s*=\s*([^;]+);")
_TARGET_RE = re.compile(r"type\s+Target\s*=\s*([^;]+);")
_NUM_VARS_RE = re.compile(r'num_vars\s*=\s*"([^"]*)"')
_NUM_CONSTRAINTS_RE = re.compile(r'num_constraints\s*=\s*"([^"]*)"')


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _pin(repo: Path) -> tuple[str, str]:
    """Return (commit, tag-or-empty) for the repo's current HEAD."""
    commit = _git(repo, "rev-parse", "HEAD")
    try:
        tag = _git(repo, "describe", "--tags", "--exact-match")
    except subprocess.CalledProcessError:
        tag = ""
    return commit, tag


def parse_rule(stem: str, src: str, commit: str, tag: str) -> dict:
    sources = [s.strip() for s in _SOURCE_RE.findall(src)]
    targets = [t.strip() for t in _TARGET_RE.findall(src)]
    reductions = [{"source": s, "target": t} for s, t in zip(sources, targets)]
    nv = _NUM_VARS_RE.search(src)
    nc = _NUM_CONSTRAINTS_RE.search(src)
    return {
        "rule": stem,
        "source": sources[0] if sources else None,
        "target": targets[0] if targets else None,
        "reductions": reductions,
        "overhead_num_vars": nv.group(1) if nv else None,
        "overhead_num_constraints": nc.group(1) if nc else None,
        "rule_source": src,
        "rule_file": f"src/rules/{stem}.rs",
        "library_tag": tag,
        "library_commit": commit,
    }


def build_dataset(repo: Path) -> list[dict]:
    """Parse every reduction-rule file at the repo's current pin into a task row."""
    rules_dir = repo / "src" / "rules"
    if not rules_dir.is_dir():
        raise FileNotFoundError(f"no rules dir at {rules_dir}")
    commit, tag = _pin(repo)

    rows = []
    for path in sorted(rules_dir.glob("*.rs")):
        if path.stem in NON_RULE_STEMS:
            continue
        src = path.read_text(encoding="utf-8")
        # A reduction rule implements ReduceTo and declares Source/Target.
        if "impl ReduceTo" not in src and "ReductionResult" not in src:
            continue
        rows.append(parse_rule(path.stem, src, commit, tag))
    return rows


def _pred_reduction_count(pred: str) -> int | None:
    """num_rules reported by `pred list --rules --json`, for cross-checking."""
    try:
        out = subprocess.run([pred, "list", "--rules", "--json"],
                             capture_output=True, text=True, check=True).stdout
        return json.loads(out).get("num_rules")
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        return None


def _card(rows: list[dict], commit: str, tag: str) -> str:
    pin = tag or commit[:12]
    frontmatter = f"""---
license: mit
pretty_name: Problem-Reductions Bug-Finding Benchmark
tags:
- leaderboard
- benchmark
- bug-finding
- reductions
- reproducibility
size_categories:
- n<1K
configs:
- config_name: default
  data_files: problem_reductions_bugs.jsonl
---
"""
    body = f"""
# Problem-Reductions Bug-Finding Benchmark

The frozen task set for the [problem-reductions bug-finding benchmark](https://github.com/Ferrari-72/problem-reductions-benchmark).
Each row is one **reduction rule** A → B from
[problem-reductions](https://github.com/CodingThrust/problem-reductions) at **`{pin}`**.
The task: find a round-trip bug (reduce → solve → extract yields an invalid/suboptimal
source solution).

- **Tasks:** {len(rows)} reduction rules
- **Library pin:** `{tag or '(untagged)'}` — commit `{commit}`

## Fields

| field | meaning |
|-------|---------|
| `rule` | task id (rule source filename stem) |
| `source` / `target` | reduction's source and target problem types |
| `reductions` | all `(source, target)` impls in the file |
| `overhead_num_vars` / `overhead_num_constraints` | declared reduction overhead |
| `rule_source` | full Rust source snapshot (self-contained) |
| `rule_file` | path in the library |
| `library_tag` / `library_commit` | the pin |

## Reproduction

This dataset is the *task definition* only. Running the benchmark also needs the
`pred` CLI built from problem-reductions at the **same pin** (`{tag or commit[:12]}`) —
that execution environment is shipped separately (Docker image / prebuilt binary).
See the benchmark repo for the harness and the independent `pred`-based verifier.
"""
    return frontmatter + body


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the benchmark task dataset")
    parser.add_argument("--repo-dir", required=True, help="problem-reductions clone at the pinned commit")
    parser.add_argument("--out", default="dataset", help="output directory")
    parser.add_argument("--pred", default=None, help="optional pred binary to cross-check rule count")
    args = parser.parse_args()

    repo = Path(args.repo_dir).resolve()
    rows = build_dataset(repo)
    # rows already carry the pin (build_dataset called _pin); reuse it instead of re-spawning git.
    commit = rows[0]["library_commit"] if rows else _pin(repo)[0]
    tag = rows[0]["library_tag"] if rows else _pin(repo)[1]

    if args.pred:
        n = _pred_reduction_count(args.pred)
        if n is not None:
            print(f"cross-check: {len(rows)} rule files vs pred num_rules={n} "
                  f"({'files ≤ reductions, OK (multi-impl files)' if len(rows) <= n else 'WARNING: more files than reductions'})")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    jsonl = out / "problem_reductions_bugs.jsonl"
    with open(jsonl, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out / "README.md").write_text(_card(rows, commit, tag), encoding="utf-8")

    n_multi = sum(1 for r in rows if len(r["reductions"]) > 1)
    n_missing = sum(1 for r in rows if not r["source"] or not r["target"])
    print(f"Dataset built: {len(rows)} tasks → {jsonl}")
    print(f"  pin: {tag or '(untagged)'} {commit[:12]}")
    print(f"  multi-reduction files: {n_multi} | files missing source/target: {n_missing}")
    print(f"\nPush:  hf upload <user>/problem-reductions-benchmarks {out} . --type dataset")


if __name__ == "__main__":
    main()
