"""
Build results/index.json from all results/*.json files.

The index is what leaderboard/index.html fetches to render the leaderboard.
Each entry is a summary row; per-rule details (including certificates) are in the full results file.

Usage:
    python -m benchmark.build_index [--results-dir results] [--output results/index.json]
"""

import argparse
import json
import sys
from pathlib import Path


def build_index(results_dir: Path, schema_path: Path | None = None) -> list[dict]:
    """Read all results JSON files, validate if schema provided, return index entries."""
    entries = []

    for path in sorted(results_dir.glob("*.json")):
        if path.name == "index.json":
            continue

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  skip {path.name}: {e}", file=sys.stderr)
            continue

        if schema_path:
            err = _validate(data, schema_path, path.name)
            if err:
                print(f"  skip {path.name}: schema error: {err}", file=sys.stderr)
                continue

        # Per-bug certificate list for the leaderboard drilldown
        safe_model = data["model"].replace("/", "_").replace(":", "_")
        bug_certs = []
        for r in data.get("results", []):
            if r.get("result") != "bug_found" or not r.get("certificate"):
                continue
            rule = r["rule"]
            traj_rel = f"trajectories/{safe_model}_{rule}.jsonl"
            traj_abs = results_dir / traj_rel
            bug_certs.append({
                "rule": rule,
                "violation": r.get("certificate", {}).get("violation"),
                "note": r.get("certificate", {}).get("note", ""),
                "source_type": r.get("certificate", {}).get("source", {}).get("type"),
                "target_type": r.get("certificate", {}).get("bundle", {}).get("target", {}).get("type"),
                "trajectory_file": traj_rel if traj_abs.exists() else None,
            })

        entry = {
            "model": data["model"],
            "library_commit": data.get("library_commit", "unknown")[:7],
            "bugs_found": data["bugs_found"],
            "total_cost_usd": data["total_cost_usd"],
            "total_tokens_k": data["total_tokens_k"],
            "efficiency_bugs_per_ktok": data["efficiency_bugs_per_ktok"],
            "efficiency_bugs_per_dollar": data["efficiency_bugs_per_dollar"],
            "rules_tested": data["rules_tested"],
            "error_count": sum(1 for r in data.get("results", []) if str(r.get("result", "")).startswith("error:")),
            "skip_count": sum(1 for r in data.get("results", []) if r.get("result") == "skipped_budget"),
            "results_file": path.name,
            "bug_certificates": bug_certs,
        }
        entries.append(entry)

    # Sort by primary fair metric (bugs/Ktok) descending
    entries.sort(key=lambda e: e["efficiency_bugs_per_ktok"], reverse=True)
    return entries


def _validate(data: dict, schema_path: Path, filename: str) -> str | None:
    """Minimal JSON schema validation without jsonschema dependency."""
    required = ["model", "library_commit", "bugs_found", "total_cost_usd",
                "total_tokens_k", "efficiency_bugs_per_ktok", "efficiency_bugs_per_dollar",
                "rules_tested", "results"]
    for field in required:
        if field not in data:
            return f"missing required field: {field!r}"
    if not isinstance(data["results"], list):
        return "results must be an array"
    for i, r in enumerate(data["results"]):
        for req in ["rule", "result", "cost", "tokens_k"]:
            if req not in r:
                return f"results[{i}] missing required field: {req!r}"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Build leaderboard index from results files")
    parser.add_argument("--results-dir", default="results", help="Directory containing results JSON files")
    parser.add_argument("--output", default="results/index.json", help="Output index file path")
    parser.add_argument("--validate", action="store_true", help="Validate each file against results.schema.json")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    if not results_dir.exists():
        print(f"Results directory not found: {results_dir}")
        sys.exit(1)

    schema_path = None
    if args.validate:
        schema_path = Path(__file__).parent / "results.schema.json"
        if not schema_path.exists():
            print(f"Schema not found: {schema_path}")
            sys.exit(1)

    entries = build_index(results_dir, schema_path)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(entries, indent=2), encoding="utf-8")

    print(f"Index built: {len(entries)} entries → {out}")
    for i, e in enumerate(entries):
        print(f"  #{i+1} {e['model']} — {e['bugs_found']} bugs, {e['efficiency_bugs_per_ktok']:.4f} bugs/Ktok")


if __name__ == "__main__":
    main()
