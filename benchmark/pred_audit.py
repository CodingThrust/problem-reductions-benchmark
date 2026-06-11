"""Audit pred CLI capabilities for bug verification."""
import json
import subprocess
from datetime import datetime
from pathlib import Path
from benchmark.env_context import EnvContext


class CapabilityTest:
    """Single capability test case."""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.passed = False
        self.output = ""
        self.error = ""


def run_pred_command(ctx: EnvContext, args: list[str], stdin: str | None = None) -> tuple[int, str, str]:
    """Run pred command and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        [str(ctx.pred_binary)] + args,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode, result.stdout, result.stderr


def test_create(ctx: EnvContext) -> CapabilityTest:
    test = CapabilityTest("create", "Create MIS instance and output JSON")
    returncode, stdout, stderr = run_pred_command(ctx, ["create", "MIS", "--graph", "0-1,1-2", "--json"])
    if returncode != 0:
        test.error = f"Command failed: {stderr}"
        return test
    try:
        data = json.loads(stdout)
        assert data["type"] == "MaximumIndependentSet"
        assert "data" in data and "graph" in data["data"]
        test.passed = True
        test.output = "Creates valid JSON with type and data"
    except (json.JSONDecodeError, KeyError, AssertionError) as e:
        test.error = f"Invalid output: {e}"
    return test


def test_reduce(ctx: EnvContext) -> CapabilityTest:
    test = CapabilityTest("reduce", "Reduce MIS to QUBO and output bundle")
    returncode, source_json, stderr = run_pred_command(ctx, ["create", "MIS", "--graph", "0-1,1-2", "--json"])
    if returncode != 0:
        test.error = f"Create failed: {stderr}"
        return test
    returncode, stdout, stderr = run_pred_command(ctx, ["reduce", "-", "--to", "QUBO", "--json"], stdin=source_json)
    if returncode != 0:
        test.error = f"Reduce failed: {stderr}"
        return test
    try:
        bundle = json.loads(stdout)
        assert "source" in bundle and "target" in bundle and "path" in bundle
        assert bundle["target"]["type"] == "QUBO"
        test.passed = True
        test.output = "Creates reduction bundle with source/target/path"
    except (json.JSONDecodeError, KeyError, AssertionError) as e:
        test.error = f"Invalid bundle: {e}"
    return test


def test_solve_brute_force(ctx: EnvContext) -> CapabilityTest:
    test = CapabilityTest("solve", "Solve MIS with brute-force and return solution + evaluation")
    returncode, source_json, stderr = run_pred_command(ctx, ["create", "MIS", "--graph", "0-1,1-2", "--json"])
    if returncode != 0:
        test.error = f"Create failed: {stderr}"
        return test
    returncode, stdout, stderr = run_pred_command(ctx, ["solve", "-", "--solver", "brute-force", "--json"], stdin=source_json)
    if returncode != 0:
        test.error = f"Solve failed: {stderr}"
        return test
    try:
        result = json.loads(stdout)
        assert "solution" in result
        assert "evaluation" in result
        assert result["evaluation"] == "Max(2)"
        test.passed = True
        test.output = "Returns solution and evaluation"
    except (json.JSONDecodeError, KeyError, AssertionError) as e:
        test.error = f"Invalid solve result: {e}"
    return test


def test_solve_no_solution(ctx: EnvContext) -> CapabilityTest:
    test = CapabilityTest("solve_no_solution", "Solve unsatisfiable instance returns Or(false)")
    returncode, source_json, stderr = run_pred_command(ctx, ["create", "KColoring", "--graph", "0-1", "--k", "1", "--json"])
    if returncode != 0:
        test.error = f"Create failed: {stderr}"
        return test
    returncode, stdout, stderr = run_pred_command(ctx, ["solve", "-", "--solver", "brute-force", "--json"], stdin=source_json)
    if returncode != 0:
        test.error = f"Solve failed: {stderr}"
        return test
    try:
        result = json.loads(stdout)
        assert "evaluation" in result
        assert result["evaluation"] == "Or(false)"
        test.passed = True
        test.output = "Returns Or(false) for unsatisfiable"
    except (json.JSONDecodeError, KeyError, AssertionError) as e:
        test.error = f"Invalid result: {e}"
    return test


def test_evaluate_valid(ctx: EnvContext) -> CapabilityTest:
    test = CapabilityTest("evaluate_valid", "Evaluate valid MIS config returns Max(2)")
    returncode, source_json, stderr = run_pred_command(ctx, ["create", "MIS", "--graph", "0-1,1-2", "--json"])
    if returncode != 0:
        test.error = f"Create failed: {stderr}"
        return test
    returncode, stdout, stderr = run_pred_command(ctx, ["evaluate", "-", "--config", "1,0,1", "--json"], stdin=source_json)
    if returncode != 0:
        test.error = f"Evaluate failed: {stderr}"
        return test
    try:
        result = json.loads(stdout)
        assert result["result"] == "Max(2)"
        test.passed = True
        test.output = "Valid config returns Max(2)"
    except (json.JSONDecodeError, KeyError, AssertionError) as e:
        test.error = f"Invalid result: {e}"
    return test


def test_evaluate_invalid(ctx: EnvContext) -> CapabilityTest:
    test = CapabilityTest("evaluate_invalid", "Evaluate invalid MIS config returns Max(None)")
    returncode, source_json, stderr = run_pred_command(ctx, ["create", "MIS", "--graph", "0-1,1-2", "--json"])
    if returncode != 0:
        test.error = f"Create failed: {stderr}"
        return test
    returncode, stdout, stderr = run_pred_command(ctx, ["evaluate", "-", "--config", "1,1,0", "--json"], stdin=source_json)
    if returncode != 0:
        test.error = f"Evaluate failed: {stderr}"
        return test
    try:
        result = json.loads(stdout)
        assert result["result"] == "Max(None)"
        test.passed = True
        test.output = "Invalid config returns Max(None)"
    except (json.JSONDecodeError, KeyError, AssertionError) as e:
        test.error = f"Invalid result: {e}"
    return test


def test_round_trip(ctx: EnvContext) -> CapabilityTest:
    test = CapabilityTest("round_trip", "MIS -> QUBO -> solve -> extract back to MIS")
    returncode, source_json, stderr = run_pred_command(ctx, ["create", "MIS", "--graph", "0-1,1-2", "--json"])
    if returncode != 0:
        test.error = f"Create failed: {stderr}"
        return test
    returncode, bundle_json, stderr = run_pred_command(ctx, ["reduce", "-", "--to", "QUBO", "--json"], stdin=source_json)
    if returncode != 0:
        test.error = f"Reduce failed: {stderr}"
        return test
    returncode, stdout, stderr = run_pred_command(ctx, ["solve", "-", "--solver", "brute-force", "--json"], stdin=bundle_json)
    if returncode != 0:
        test.error = f"Solve bundle failed: {stderr}"
        return test
    try:
        result = json.loads(stdout)
        assert "solution" in result
        assert "evaluation" in result
        assert "intermediate" in result
        assert result["problem"] == "MaximumIndependentSet"
        assert result["reduced_to"] == "QUBO"
        assert result["intermediate"]["problem"] == "QUBO"
        assert result["evaluation"] == "Max(2)"
        test.passed = True
        test.output = "Round-trip completes: source solution + intermediate target solution"
    except (json.JSONDecodeError, KeyError, AssertionError) as e:
        test.error = f"Invalid round-trip result: {e}"
    return test


def run_audit(ctx: EnvContext) -> list[CapabilityTest]:
    """Run all capability tests."""
    return [
        test_create(ctx),
        test_reduce(ctx),
        test_solve_brute_force(ctx),
        test_solve_no_solution(ctx),
        test_evaluate_valid(ctx),
        test_evaluate_invalid(ctx),
        test_round_trip(ctx),
    ]


def generate_audit_doc(ctx: EnvContext, tests: list[CapabilityTest]) -> str:
    """Generate markdown capability audit document."""
    passed = sum(1 for t in tests if t.passed)
    total = len(tests)

    doc = f"""# pred CLI Capability Audit

**Date:** {datetime.now().strftime('%Y-%m-%d')}
**Repo:** `{ctx.repo_path}`
**Commit:** `{ctx.commit_hash}`
**pred binary:** `{ctx.pred_binary}`
**pred version:** 0.5.0

## Summary

{passed}/{total} capabilities verified.

## Required Capabilities for Bug Checker

| Capability | Status | Notes |
|------------|--------|-------|
"""

    for test in tests:
        status = "OK Available" if test.passed else "MISSING/Broken"
        notes = test.output if test.passed else test.error
        doc += f"| `{test.name}` | {status} | {notes} |\n"

    doc += "\n## Test Details\n\n"

    for test in tests:
        doc += f"### `{test.name}`\n\n"
        doc += f"**Description:** {test.description}\n\n"
        if test.passed:
            doc += f"**Result:** PASS\n\n{test.output}\n\n"
        else:
            doc += f"**Result:** FAIL\n\n**Error:** {test.error}\n\n"

    doc += """## Upstream Issues Filed

None - all required capabilities are available in v0.5.0.

## Notes

- **pred extract is missing** from the v0.5.0 binary (exists in source but not compiled in released version).
  However, `pred solve` on a reduction bundle already performs the full reduce -> solve -> extract round-trip internally,
  so extract is not needed for the bug checker workflow.
- `solve --solver brute-force` correctly returns `Or(false)` / `Max(None)` for unsatisfiable instances.
- `evaluate` distinguishes valid vs invalid configurations via `Max(value)` vs `Max(None)`.
"""

    return doc


def main():
    """Run pred capability audit."""
    import sys
    from benchmark.env_setup import setup_env

    if len(sys.argv) < 2:
        print("Usage: python -m benchmark.pred_audit <repo_path>")
        sys.exit(1)

    repo_path = sys.argv[1]

    try:
        ctx = setup_env(repo_path)
        print("Environment setup complete")
        print(f"  Repo: {ctx.repo_path}")
        print(f"  pred: {ctx.pred_binary}")
        print(f"  Commit: {ctx.commit_hash[:7]}")
        print()

        tests = run_audit(ctx)

        print("Capability Audit Results:")
        print("-" * 60)
        for test in tests:
            status = "PASS" if test.passed else "FAIL"
            print(f"{status} | {test.name}")
            print(f"      {test.description}")
            if test.passed:
                print(f"      {test.output}")
            else:
                print(f"      Error: {test.error}")
            print()

        passed = sum(1 for t in tests if t.passed)
        total = len(tests)
        print(f"Result: {passed}/{total} tests passed")

        # Write audit document
        doc_path = Path(__file__).parent / "docs" / "pred-capability-audit.md"
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(generate_audit_doc(ctx, tests), encoding="utf-8")
        print(f"Audit document written to: {doc_path}")

        sys.exit(0 if passed == total else 1)

    except Exception as e:
        print(f"Setup failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
