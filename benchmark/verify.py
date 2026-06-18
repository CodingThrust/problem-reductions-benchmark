"""
Certificate verifier: independently re-validates counterexample certificates using only pred.

Usage:
    python -m benchmark.verify <certificate.json> [--repo-dir <path>]
    python -m benchmark.verify --calibrate           # run against fixtures, exit 0 iff all pass

A certificate is a JSON object describing a claimed bug in a reduction rule.
This verifier never trusts the AI's claim — it re-derives everything from pred.
"""

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "tests" / "fixtures"
PRED_BINARY = os.environ.get("PRED_BINARY", "pred")


FLOAT_TOLERANCE = 1e-6


@dataclass
class Verdict:
    accepted: bool
    reason: str
    details: dict = field(default_factory=dict)
    novelty: str | None = None  # "novel" | "known" | None (only set when accepted)

    def __str__(self) -> str:
        status = "ACCEPTED" if self.accepted else "REJECTED"
        return f"{status}: {self.reason}"


def _write_json(data: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _run_pred(args: list[str], stdin_file: str | None = None) -> tuple[int, str, str]:
    """
    Run pred with file-based input (avoids Windows stdin BOM/encoding issues).
    If stdin_file is given, replace the '-' placeholder in args with the file path.
    """
    cmd = [PRED_BINARY] + args
    if stdin_file:
        # Replace any "-" placeholder with the actual file path
        cmd = [stdin_file if a == "-" else a for a in cmd]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=30
    )
    return result.returncode, result.stdout, result.stderr


def _is_strictly_better(extracted: float, candidate: float, is_max: bool) -> bool:
    """True only if candidate is meaningfully better than extracted (beyond rounding noise)."""
    if is_max:
        return candidate - extracted > FLOAT_TOLERANCE
    return extracted - candidate > FLOAT_TOLERANCE


def _novelty_key(rule: str, source: dict) -> str:
    """Canonical key for deduplicating bugs: (rule, normalized_source)."""
    return json.dumps({"rule": rule, "source": _normalize(source)}, sort_keys=True)


def _normalize(data: dict) -> dict:
    """Normalize a JSON object for structural comparison (sort keys, stable types)."""
    if isinstance(data, dict):
        return {k: _normalize(v) for k, v in sorted(data.items())}
    if isinstance(data, list):
        return [_normalize(v) for v in data]
    return data


def _structures_match(a: dict, b: dict) -> bool:
    """True if two problem instances have the same type and structure (not raw text equality)."""
    return _normalize(a) == _normalize(b)


def verify(
    cert: dict,
    repo_dir: str | None = None,
    known_bugs: list[dict] | None = None,
) -> Verdict:
    """Re-validate a certificate via pred and determine novelty against a known-bugs ledger."""
    with tempfile.TemporaryDirectory() as tmpdir:
        verdict = _verify_in(cert, tmpdir)
    if verdict.accepted:
        key = _novelty_key(cert.get("rule", ""), cert.get("source", {}))
        known_keys = {_novelty_key(b.get("rule", ""), b.get("source", {})) for b in (known_bugs or [])}
        verdict.novelty = "known" if key in known_keys else "novel"
    return verdict


def _verify_in(cert: dict, tmpdir: str) -> Verdict:
    source = cert.get("source")
    bundle = cert.get("bundle")
    violation = cert.get("violation")
    target_config = cert.get("target_config")
    claimed_source_solution = cert.get("claimed_source_solution")

    if not source or not bundle or not violation:
        return Verdict(False, "certificate missing required fields (source, bundle, violation)")

    claimed_target = bundle.get("target")
    claimed_source_in_bundle = bundle.get("source")

    if not claimed_target or not claimed_source_in_bundle:
        return Verdict(False, "bundle missing source or target fields")

    # Step 1: re-derive the bundle from source using pred reduce
    target_type = claimed_target.get("type")
    if not target_type:
        return Verdict(False, "bundle.target has no type field")

    source_file = os.path.join(tmpdir, "source.json")
    _write_json(source, source_file)

    rc, stdout, stderr = _run_pred(["reduce", "-", "--to", target_type, "--json"], stdin_file=source_file)
    if rc != 0:
        return Verdict(False, f"pred reduce failed: {stderr.strip()[:200]}")

    try:
        real_bundle = json.loads(stdout)
    except json.JSONDecodeError as e:
        return Verdict(False, f"pred reduce returned invalid JSON: {e}")

    # Step 2: confirm re-derived target matches claimed target structure
    real_target = real_bundle.get("target", {})
    if not _structures_match(real_target, claimed_target):
        return Verdict(
            False,
            "bundle target does not match what pred reduce actually produces",
            {"claimed_target": claimed_target, "real_target": real_target},
        )

    bundle_file = os.path.join(tmpdir, "bundle.json")
    _write_json(real_bundle, bundle_file)

    # Step 3-5: check the claimed violation
    if violation == "unsound_extraction":
        return _check_unsound_extraction(cert, source_file, bundle_file, tmpdir)
    elif violation == "incomplete_reduction":
        return _check_incomplete_reduction(cert, source_file, bundle_file)
    elif violation == "suboptimal_extraction":
        return _check_suboptimal_extraction(cert, source_file, bundle_file)
    elif violation == "solve_mismatch":
        return _check_solve_mismatch(cert, source_file, bundle_file)
    elif violation == "order_reversal":
        return _check_order_reversal(cert, source_file, bundle_file, tmpdir)
    else:
        return Verdict(False, f"unknown violation type: {violation!r}")


def _check_unsound_extraction(cert: dict, source_file: str, bundle_file: str, tmpdir: str) -> Verdict:
    """
    Unsound extraction: a valid target solution maps back to an INVALID source solution.

    Verifier re-derives the extracted solution via pred extract — never trusts
    the AI-supplied claimed_source_solution.
    """
    target_config = cert.get("target_config")

    if not target_config:
        return Verdict(False, "unsound_extraction certificate missing target_config")

    # Step 1: verify target_config is a valid target solution
    target_data = cert.get("bundle", {}).get("target")
    if target_data:
        target_file = os.path.join(tmpdir, "target.json")
        _write_json(target_data, target_file)
        rc, stdout, stderr = _run_pred(
            ["evaluate", "-", "--config", target_config, "--json"], stdin_file=target_file
        )
        if rc == 0:
            try:
                tgt_eval = json.loads(stdout)
                tgt_result = tgt_eval.get("result", "")
                if "None" in tgt_result or "false" in tgt_result.lower():
                    return Verdict(
                        False,
                        f"target_config {target_config!r} is not a valid target solution ({tgt_result}) — not useful evidence",
                        {"target_evaluation": tgt_result},
                    )
            except json.JSONDecodeError:
                pass

    # Step 2: run pred extract to get the real extracted source solution
    rc, stdout, stderr = _run_pred(
        ["extract", "-", "--config", target_config, "--json"], stdin_file=bundle_file
    )
    if rc != 0:
        return Verdict(False, f"pred extract failed: {stderr.strip()[:200]}")

    try:
        extract_result = json.loads(stdout)
    except json.JSONDecodeError as e:
        return Verdict(False, f"pred extract returned invalid JSON: {e}")

    extracted_solution = extract_result.get("solution")
    if extracted_solution is None:
        return Verdict(False, "pred extract returned no solution field")

    # Step 3: evaluate the extracted source solution — must be INVALID
    config_str = ",".join(str(x) for x in extracted_solution)
    rc, stdout, stderr = _run_pred(
        ["evaluate", "-", "--config", config_str, "--json"], stdin_file=source_file
    )
    if rc != 0:
        return Verdict(False, f"pred evaluate (extracted solution) failed: {stderr.strip()[:200]}")

    try:
        eval_result = json.loads(stdout)
    except json.JSONDecodeError as e:
        return Verdict(False, f"pred evaluate returned invalid JSON: {e}")

    result_value = eval_result.get("result", "")
    if "None" in result_value or "false" in result_value.lower():
        return Verdict(
            True,
            f"confirmed unsound extraction: extracted source solution {extracted_solution} is invalid ({result_value})",
            {"extracted_solution": extracted_solution, "evaluation": result_value},
        )
    else:
        return Verdict(
            False,
            f"extracted source solution is actually valid: {result_value} — not a bug",
            {"extracted_solution": extracted_solution, "evaluation": result_value},
        )


def _check_incomplete_reduction(cert: dict, source_file: str, bundle_file: str) -> Verdict:
    """
    Incomplete reduction: source has a valid solution but the reduction target has none.
    """
    # First confirm the source has a solution
    try:
        rc, stdout, stderr = _run_pred(["solve", "-", "--solver", "brute-force", "--json"], stdin_file=source_file)
    except subprocess.TimeoutExpired:
        return Verdict(False, "instance too large for solve-based verification: pred solve timed out on source")

    if rc != 0:
        return Verdict(False, f"pred solve (source) failed: {stderr.strip()[:200]}")

    try:
        source_solve = json.loads(stdout)
    except json.JSONDecodeError as e:
        return Verdict(False, f"pred solve (source) returned invalid JSON: {e}")

    source_eval = source_solve.get("evaluation", "")
    if "None" in source_eval or "false" in source_eval.lower():
        return Verdict(
            False,
            "source problem itself has no solution — incomplete_reduction requires source to be satisfiable",
            {"source_evaluation": source_eval},
        )

    # Now confirm the target (bundle) has no solution
    try:
        rc, stdout, stderr = _run_pred(["solve", "-", "--solver", "brute-force", "--json"], stdin_file=bundle_file)
    except subprocess.TimeoutExpired:
        return Verdict(False, "instance too large for solve-based verification: pred solve timed out on bundle")

    if rc != 0:
        return Verdict(False, f"pred solve (bundle) failed: {stderr.strip()[:200]}")

    try:
        bundle_solve = json.loads(stdout)
    except json.JSONDecodeError as e:
        return Verdict(False, f"pred solve (bundle) returned invalid JSON: {e}")

    target_eval = bundle_solve.get("evaluation", "")
    if "None" in target_eval or "false" in target_eval.lower():
        return Verdict(
            True,
            "confirmed incomplete reduction: source is satisfiable but target has no solution",
            {"source_evaluation": source_eval, "target_evaluation": target_eval},
        )
    else:
        return Verdict(
            False,
            f"target has a solution ({target_eval}) — no incomplete reduction",
            {"source_evaluation": source_eval, "target_evaluation": target_eval},
        )


def _parse_numeric_result(result_str: str) -> float | None:
    """Parse 'Max(2)' or 'Min(-14)' into a float. Returns None for None/false results."""
    import re
    m = re.search(r"[-+]?\d+(?:\.\d+)?", result_str)
    if m:
        return float(m.group())
    return None


def _check_suboptimal_extraction(cert: dict, source_file: str, bundle_file: str) -> Verdict:
    """
    Suboptimal extraction: target's optimal solution maps to a non-optimal source solution,
    and the certificate provides a strictly better one.
    """
    target_config = cert.get("target_config")
    brute_force_solution = cert.get("brute_force_solution")

    if not target_config:
        return Verdict(False, "suboptimal_extraction certificate missing target_config")
    if not brute_force_solution:
        return Verdict(False, "suboptimal_extraction certificate missing brute_force_solution")

    # Extract the solution from the bundle
    rc, stdout, stderr = _run_pred(["extract", "-", "--config", target_config, "--json"], stdin_file=bundle_file)
    if rc != 0:
        return Verdict(False, f"pred extract failed: {stderr.strip()[:200]}")

    try:
        extract_result = json.loads(stdout)
    except json.JSONDecodeError as e:
        return Verdict(False, f"pred extract returned invalid JSON: {e}")

    source_eval_str = extract_result.get("evaluation", "")
    extracted_value = _parse_numeric_result(source_eval_str)

    # Evaluate the claimed better solution
    better_config = ",".join(str(x) for x in brute_force_solution)
    rc, stdout, stderr = _run_pred(
        ["evaluate", "-", "--config", better_config, "--json"], stdin_file=source_file
    )
    if rc != 0:
        return Verdict(False, f"pred evaluate (better solution) failed: {stderr.strip()[:200]}")

    try:
        better_eval_result = json.loads(stdout)
    except json.JSONDecodeError as e:
        return Verdict(False, f"pred evaluate returned invalid JSON: {e}")

    better_value = _parse_numeric_result(better_eval_result.get("result", ""))

    # For a maximization bug: better_value > extracted_value
    # For a minimization bug: better_value < extracted_value
    # We determine the objective direction from the result string prefix
    is_max = source_eval_str.lower().startswith("max")

    if extracted_value is None or better_value is None:
        return Verdict(
            False,
            f"could not parse numeric values: extracted={source_eval_str!r}, better={better_eval_result.get('result')!r}",
        )

    if _is_strictly_better(extracted_value, better_value, is_max):
        return Verdict(
            True,
            f"confirmed suboptimal extraction: extracted {extracted_value} but better solution achieves {better_value}",
            {"extracted_evaluation": source_eval_str, "better_evaluation": better_eval_result.get("result")},
        )
    else:
        return Verdict(
            False,
            f"extraction is already optimal: extracted={extracted_value}, claimed better={better_value}",
            {"extracted_evaluation": source_eval_str, "better_evaluation": better_eval_result.get("result")},
        )


def _check_solve_mismatch(cert: dict, source_file: str, bundle_file: str) -> Verdict:
    """
    Solve mismatch: pred solve on source and bundle return different evaluations.
    The verifier solves both sides itself — no target_config or solution data from the AI.
    """
    try:
        rc, stdout, stderr = _run_pred(["solve", "-", "--solver", "brute-force", "--json"], stdin_file=source_file)
    except subprocess.TimeoutExpired:
        return Verdict(False, "instance too large for solve-based verification: pred solve timed out on source")

    if rc != 0:
        return Verdict(False, f"pred solve (source) failed: {stderr.strip()[:200]}")
    try:
        source_solve = json.loads(stdout)
    except json.JSONDecodeError as e:
        return Verdict(False, f"pred solve (source) returned invalid JSON: {e}")

    try:
        rc, stdout, stderr = _run_pred(["solve", "-", "--solver", "brute-force", "--json"], stdin_file=bundle_file)
    except subprocess.TimeoutExpired:
        return Verdict(False, "instance too large for solve-based verification: pred solve timed out on bundle")

    if rc != 0:
        return Verdict(False, f"pred solve (bundle) failed: {stderr.strip()[:200]}")
    try:
        bundle_solve = json.loads(stdout)
    except json.JSONDecodeError as e:
        return Verdict(False, f"pred solve (bundle) returned invalid JSON: {e}")

    eval_source = source_solve.get("evaluation", "")
    eval_bundle = bundle_solve.get("evaluation", "")

    src_val = _parse_numeric_result(eval_source)
    bnd_val = _parse_numeric_result(eval_bundle)

    # Compare numerically if both parseable, else compare strings
    mismatch = False
    if src_val is not None and bnd_val is not None:
        mismatch = abs(src_val - bnd_val) > FLOAT_TOLERANCE
    else:
        mismatch = eval_source != eval_bundle

    if mismatch:
        return Verdict(
            True,
            f"confirmed solve_mismatch: source evaluation {eval_source!r} != bundle evaluation {eval_bundle!r}",
            {"source_evaluation": eval_source, "bundle_evaluation": eval_bundle},
        )
    else:
        return Verdict(
            False,
            f"evaluations match — no bug: source={eval_source!r}, bundle={eval_bundle!r}",
            {"source_evaluation": eval_source, "bundle_evaluation": eval_bundle},
        )


def _check_order_reversal(cert: dict, source_file: str, bundle_file: str, tmpdir: str) -> Verdict:
    """
    Order reversal: two target configs c_lo, c_hi where obj_B(c_lo) < obj_B(c_hi)
    but obj_A(extract(c_lo)) > obj_A(extract(c_hi)).
    Proves the reduction does not preserve ordering — solver-free.
    """
    c_lo = cert.get("target_config_lo")
    c_hi = cert.get("target_config_hi")

    if not c_lo:
        return Verdict(False, "order_reversal certificate missing target_config_lo")
    if not c_hi:
        return Verdict(False, "order_reversal certificate missing target_config_hi")

    # Extract source solutions for both configs
    rc, stdout, stderr = _run_pred(["extract", "-", "--config", c_lo, "--json"], stdin_file=bundle_file)
    if rc != 0:
        return Verdict(False, f"pred extract (c_lo) failed: {stderr.strip()[:200]}")
    try:
        extract_lo = json.loads(stdout)
    except json.JSONDecodeError as e:
        return Verdict(False, f"pred extract (c_lo) returned invalid JSON: {e}")

    rc, stdout, stderr = _run_pred(["extract", "-", "--config", c_hi, "--json"], stdin_file=bundle_file)
    if rc != 0:
        return Verdict(False, f"pred extract (c_hi) failed: {stderr.strip()[:200]}")
    try:
        extract_hi = json.loads(stdout)
    except json.JSONDecodeError as e:
        return Verdict(False, f"pred extract (c_hi) returned invalid JSON: {e}")

    obj_a_lo_str = extract_lo.get("evaluation", "")
    obj_a_hi_str = extract_hi.get("evaluation", "")
    obj_a_lo = _parse_numeric_result(obj_a_lo_str)
    obj_a_hi = _parse_numeric_result(obj_a_hi_str)

    # Evaluate target configs against the target problem
    target_data = cert.get("bundle", {}).get("target")
    if not target_data:
        return Verdict(False, "bundle has no target field")
    target_file = os.path.join(tmpdir, "target.json")
    _write_json(target_data, target_file)

    rc, stdout, stderr = _run_pred(["evaluate", "-", "--config", c_lo, "--json"], stdin_file=target_file)
    if rc != 0:
        return Verdict(False, f"pred evaluate target (c_lo) failed: {stderr.strip()[:200]}")
    try:
        obj_b_lo_str = json.loads(stdout).get("result", "")
    except json.JSONDecodeError as e:
        return Verdict(False, f"pred evaluate (c_lo) returned invalid JSON: {e}")

    rc, stdout, stderr = _run_pred(["evaluate", "-", "--config", c_hi, "--json"], stdin_file=target_file)
    if rc != 0:
        return Verdict(False, f"pred evaluate target (c_hi) failed: {stderr.strip()[:200]}")
    try:
        obj_b_hi_str = json.loads(stdout).get("result", "")
    except json.JSONDecodeError as e:
        return Verdict(False, f"pred evaluate (c_hi) returned invalid JSON: {e}")

    obj_b_lo = _parse_numeric_result(obj_b_lo_str)
    obj_b_hi = _parse_numeric_result(obj_b_hi_str)

    if obj_a_lo is None or obj_a_hi is None or obj_b_lo is None or obj_b_hi is None:
        return Verdict(False, f"could not parse numeric values: "
                       f"obj_B(c_lo)={obj_b_lo_str!r}, obj_B(c_hi)={obj_b_hi_str!r}, "
                       f"obj_A(c_lo)={obj_a_lo_str!r}, obj_A(c_hi)={obj_a_hi_str!r}")

    # Determine direction from c_hi extraction (assume maximization if starts with Max)
    is_max = obj_a_hi_str.lower().startswith("max")

    # c_lo must be strictly lower in target space
    b_lo_is_lower = _is_strictly_better(obj_b_lo, obj_b_hi, is_max)  # hi > lo by tolerance
    if not b_lo_is_lower:
        return Verdict(False,
            f"target values not strictly ordered: obj_B(c_lo)={obj_b_lo_str!r}, obj_B(c_hi)={obj_b_hi_str!r}",
            {"obj_b_lo": obj_b_lo_str, "obj_b_hi": obj_b_hi_str})

    # Order is reversed if extract(c_lo) is strictly better than extract(c_hi) in source space
    reversed_ = _is_strictly_better(obj_a_hi, obj_a_lo, is_max)  # lo > hi
    if reversed_:
        return Verdict(True,
            f"confirmed order_reversal: obj_B(c_lo)={obj_b_lo_str} < obj_B(c_hi)={obj_b_hi_str} "
            f"but obj_A(extract(c_lo))={obj_a_lo_str} > obj_A(extract(c_hi))={obj_a_hi_str}",
            {"obj_b_lo": obj_b_lo_str, "obj_b_hi": obj_b_hi_str,
             "obj_a_lo": obj_a_lo_str, "obj_a_hi": obj_a_hi_str})
    else:
        return Verdict(False,
            f"order preserved — no bug: obj_B(c_lo)={obj_b_lo_str}, obj_B(c_hi)={obj_b_hi_str}, "
            f"obj_A(extract(c_lo))={obj_a_lo_str}, obj_A(extract(c_hi))={obj_a_hi_str}",
            {"obj_b_lo": obj_b_lo_str, "obj_b_hi": obj_b_hi_str,
             "obj_a_lo": obj_a_lo_str, "obj_a_hi": obj_a_hi_str})


# ─── Calibration mode ────────────────────────────────────────────────────────

FIXTURE_EXPECTATIONS = {
    "valid_bug.json": True,                       # genuine bug → accepted
    "wrong_target.json": False,                   # tampered bundle → rejected
    "valid_solution_claimed_invalid.json": False, # false alarm → rejected
}


def run_calibration() -> bool:
    """Run verifier against all fixtures. Return True iff every expectation is met."""
    all_passed = True
    print("Running verifier calibration...")
    print("-" * 60)

    for fixture_name, expected_accepted in FIXTURE_EXPECTATIONS.items():
        fixture_path = FIXTURES_DIR / fixture_name
        if not fixture_path.exists():
            print(f"MISSING  {fixture_name}")
            all_passed = False
            continue

        with open(fixture_path, encoding="utf-8") as f:
            cert = json.load(f)

        verdict = verify(cert)
        passed = verdict.accepted == expected_accepted

        status = "PASS" if passed else "FAIL"
        expected = "accepted" if expected_accepted else "rejected"
        got = "accepted" if verdict.accepted else "rejected"
        print(f"{status}  {fixture_name}")
        print(f"      expected={expected}, got={got}")
        print(f"      {verdict.reason}")
        print()

        if not passed:
            all_passed = False

    print("-" * 60)
    if all_passed:
        print("Calibration PASSED: all fixtures verified correctly.")
    else:
        print("Calibration FAILED: some fixtures did not verify as expected.")
    return all_passed


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m benchmark.verify <certificate.json>")
        print("       python -m benchmark.verify --calibrate")
        sys.exit(1)

    if sys.argv[1] == "--calibrate":
        ok = run_calibration()
        sys.exit(0 if ok else 1)

    cert_path = sys.argv[1]
    with open(cert_path, encoding="utf-8") as f:
        cert = json.load(f)

    verdict = verify(cert)
    print(verdict)
    if verdict.details:
        for k, v in verdict.details.items():
            print(f"  {k}: {v}")
    sys.exit(0 if verdict.accepted else 1)


if __name__ == "__main__":
    main()
