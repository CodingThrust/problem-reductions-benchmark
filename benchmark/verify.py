"""
Certificate verifier: independently re-validates a claimed reduction bug using only pred.

A certificate names a source instance `a` and a reduction A→B. The reduction is correct
on `a` iff solving via the reduction recovers the true source answer:

    solve(a)  ==  solve(reduce(a))        # compare VALUES (opt) / feasibility (decision)

`pred solve <bundle>` already does the round-trip (solve the target, extract back to the
source, evaluate there), so its top-level evaluation is the source-space value. A mismatch
is a genuine bug (incomplete / unsound-at-optimum / suboptimal-at-optimum). We compare
*values*, never *which* solution — so multiple optima never cause a false mismatch.

Optionally, a certificate may carry a witness `target_config` (a specific target solution);
this lets us also catch extraction bugs on feasible solutions the solver wouldn't return.

Usage:
    python -m benchmark.verify <certificate.json> [--repo-dir <path>]
    python -m benchmark.verify --calibrate
"""

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

try:
    import resource  # POSIX-only; used to cap each pred child's CPU/memory/file size
except ImportError:  # pragma: no cover - non-POSIX (e.g. Windows)
    resource = None

FIXTURES_DIR = Path(__file__).parent / "tests" / "fixtures"
# Accept-path fixtures are the benchmark answer key (real reduction bugs); they are kept
# OUT of version control. Default to a gitignored private dir; override with an env var.
PRIVATE_FIXTURES_DIR = Path(
    os.environ.get("BENCHMARK_PRIVATE_FIXTURES", str(FIXTURES_DIR / "private")))
PRED_BINARY = os.environ.get("PRED_BINARY", "pred")

FLOAT_TOLERANCE = 1e-6
# Counterexamples are minimal witnesses — reject absurd inputs before spawning pred.
MAX_INPUT_BYTES = 256 * 1024
# Per-pred-call wall-clock; SOLVE_TIMEOUT is also handed to `pred solve --timeout`.
RUN_TIMEOUT = 30
SOLVE_TIMEOUT = 25

# OS resource caps applied to each pred child (best-effort, POSIX). The verifier runs our
# trusted pred binary on a submitter-supplied instance, so these bound a *pathological
# instance* — not a hostile binary. Network isolation is a deployment concern: run the
# scoring container/Job with no network (e.g. `docker run --network none`).
CPU_LIMIT_SECONDS = 60               # hard CPU-time cap (wall clock is RUN_TIMEOUT)
MEM_LIMIT_BYTES = 2 * 1024 ** 3      # address-space cap per call (2 GiB)
FSIZE_LIMIT_BYTES = 64 * 1024 ** 2   # cap any file pred writes (64 MiB)
MAX_OUTPUT_BYTES = 16 * 1024 ** 2    # truncate runaway stdout/stderr (16 MiB)


# ─── result types ─────────────────────────────────────────────────────────────

@dataclass
class Verdict:
    accepted: bool
    reason: str
    details: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{'ACCEPTED' if self.accepted else 'REJECTED'}: {self.reason}"


class PredError(Exception):
    """pred failed (non-zero exit, panic, bad JSON)."""


class Inconclusive(Exception):
    """Could not decide within resource limits (timeout / no solver succeeded).

    A timeout is not a proof, so the verifier rejects rather than guesses.
    """


def _num(s: str) -> float | None:
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s or "")
    return float(m.group()) if m else None


@dataclass
class Eval:
    """A pred result/evaluation parsed into a structured, kind-aware form.

    kind: "opt" (objective, Max/Min) | "sat" (decision, true/false) | "unknown".
    All the brittle string handling lives HERE — switch to structured pred fields later
    by changing only this parser.
    """
    kind: str
    feasible: bool
    value: float | None
    sense: str          # "max" | "min" | ""
    raw: str

    @classmethod
    def parse(cls, raw: str) -> "Eval":
        s = (raw or "").strip()
        low = s.lower()
        if low.startswith("max") or low.startswith("min"):
            sense = "max" if low.startswith("max") else "min"
            if "none" in low:                       # Max(None) → infeasible
                return cls("opt", False, None, sense, s)
            v = _num(s)
            return cls("opt", v is not None, v, sense, s)
        if "true" in low or "false" in low:         # Or(true)/Or(false) → decision
            return cls("sat", "true" in low, None, "", s)
        v = _num(s)
        if v is not None:
            return cls("opt", True, v, "", s)
        return cls("unknown", False, None, "", s)


def agrees(a: Eval, b: Eval) -> bool:
    """Kind-aware equivalence — the round-trip must preserve feasibility and (opt) value.

    Compares values, never solutions, so degeneracy (multiple optima) is irrelevant.
    """
    if a.feasible != b.feasible:
        return False
    if not a.feasible:
        return True
    if a.value is not None and b.value is not None:
        return abs(a.value - b.value) <= FLOAT_TOLERANCE
    return True   # both feasible decision problems, or values absent → consistent


def _strictly_worse(value: float, optimum: float, sense: str) -> bool:
    if sense == "min":
        return value - optimum > FLOAT_TOLERANCE
    return optimum - value > FLOAT_TOLERANCE       # default: maximization


def _cfg(solution) -> str:
    return ",".join(str(x) for x in solution)


# ─── the pred oracle ──────────────────────────────────────────────────────────

def _rlimit_preexec() -> None:  # pragma: no cover - runs in the forked child
    """Set resource limits in the child after fork, before exec. Best-effort per limit:
    a platform that rejects one (e.g. RLIMIT_AS on some macOS) just skips it."""
    for res_name, value in (("RLIMIT_CPU", CPU_LIMIT_SECONDS),
                            ("RLIMIT_AS", MEM_LIMIT_BYTES),
                            ("RLIMIT_FSIZE", FSIZE_LIMIT_BYTES)):
        res = getattr(resource, res_name, None)
        if res is None:
            continue
        try:
            resource.setrlimit(res, (value, value))
        except (ValueError, OSError):
            pass


class PredSolver:
    """Independent oracle over `pred`. reduce/evaluate/extract are direct, reduction-free
    primitives (the trust bedrock); solve centralises the brute-force/ILP strategy and
    timeout handling. All subprocess + resource handling lives here.
    """

    def __init__(self, tmpdir: str, binary: str = PRED_BINARY):
        self.tmpdir = tmpdir
        self.binary = binary
        self._n = 0

    def _write(self, data: dict) -> str:
        self._n += 1
        path = os.path.join(self.tmpdir, f"obj_{self._n}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return path

    def _run(self, args: list[str], stdin_file: str, timeout: int) -> tuple[int, str, str]:
        """Run pred with the '-' placeholder replaced by stdin_file. Own process group so
        a timeout kills the whole tree; CPU/memory/file-size capped via setrlimit (POSIX).
        Output is truncated to MAX_OUTPUT_BYTES. Raises subprocess.TimeoutExpired on timeout."""
        cmd = [self.binary] + [stdin_file if a == "-" else a for a in args]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True,
            preexec_fn=_rlimit_preexec if resource is not None else None,
        )
        try:
            out, err = proc.communicate(timeout=timeout)
            return proc.returncode, out[:MAX_OUTPUT_BYTES], err[:MAX_OUTPUT_BYTES]
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait()
            raise

    def reduce(self, source: dict, target_type: str) -> dict:
        f = self._write(source)
        rc, out, err = self._run(["reduce", "-", "--to", target_type, "--json"], f, RUN_TIMEOUT)
        if rc != 0:
            raise PredError(f"reduce failed: {err.strip()[:200]}")
        try:
            return json.loads(out)
        except json.JSONDecodeError as e:
            raise PredError(f"reduce returned invalid JSON: {e}")

    def evaluate(self, instance: dict, config: str) -> Eval:
        f = self._write(instance)
        rc, out, err = self._run(["evaluate", "-", "--config", config, "--json"], f, RUN_TIMEOUT)
        if rc != 0:
            raise PredError(f"evaluate failed: {err.strip()[:200]}")
        try:
            return Eval.parse(json.loads(out).get("result", ""))
        except json.JSONDecodeError as e:
            raise PredError(f"evaluate returned invalid JSON: {e}")

    def extract(self, bundle: dict, config: str) -> tuple[list | None, Eval]:
        f = self._write(bundle)
        rc, out, err = self._run(["extract", "-", "--config", config, "--json"], f, RUN_TIMEOUT)
        if rc != 0:
            raise PredError(f"extract failed: {err.strip()[:200]}")
        try:
            d = json.loads(out)
        except json.JSONDecodeError as e:
            raise PredError(f"extract returned invalid JSON: {e}")
        return d.get("solution"), Eval.parse(d.get("evaluation", ""))

    def _solve_with(self, instance: dict, solver: str | None) -> Eval | None:
        """One `pred solve` call. Returns Eval or None on failure/panic/timeout/bad-JSON."""
        f = self._write(instance)
        args = ["solve", "-", "--json", "--timeout", str(SOLVE_TIMEOUT)]
        if solver:
            args += ["--solver", solver]
        try:
            rc, out, _ = self._run(args, f, RUN_TIMEOUT)
        except subprocess.TimeoutExpired:
            return None
        if rc != 0:
            return None
        try:
            return Eval.parse(json.loads(out).get("evaluation", ""))
        except json.JSONDecodeError:
            return None

    def solve(self, instance: dict, *, independent: bool = False) -> Eval:
        """Optimal source-space evaluation.

        Default: pred's default solver (ILP/HiGHS, which auto-reduces to ILP) — fast, since
        pure enumeration is too slow for many problems. Falls back to brute-force if ILP has
        no path or fails (e.g. a panic in the auto-reduce chain). `independent=True` forces
        brute-force (used for the SOURCE of an *→ILP rule, where solving via ILP would use
        the very rule under test — self-verification). A clean result from either solver is
        trusted; if neither decides in time → Inconclusive (a timeout is not a proof)."""
        if not independent:
            ev = self._solve_with(instance, None)         # None → pred default solver (ilp)
            if ev is not None:
                return ev
        ev = self._solve_with(instance, "brute-force")
        if ev is not None:
            return ev
        raise Inconclusive("no solver decided within limits (ilp + brute-force failed/timed out)")


# ─── verification ─────────────────────────────────────────────────────────────

def count_bugs(results: list[dict]) -> int:
    """Distinct rules with at least one confirmed bug (one rule = one bug)."""
    return len({r.get("rule") for r in results if r.get("result") == "bug_found"})


def _derive_label(src: Eval, bnd: Eval) -> str:
    if src.feasible and not bnd.feasible:
        return "feasibility_not_preserved"   # source solvable but round-trip yields no/invalid solution
    if not src.feasible and bnd.feasible:
        return "spurious_solution"           # round-trip claims a solution the source has none of
    return "optimum_not_preserved"           # both feasible, values differ


def _witness_check(solver: PredSolver, source: dict, bundle: dict,
                   config: str, source_opt: Eval) -> Verdict | None:
    """Optional: a specific target solution `config` exposes an extraction bug the
    round-trip (which only sees the solver's returned optimum) would miss.
    Returns a Verdict if it confirms a bug, else None."""
    target = bundle.get("target")
    if not target:
        return None
    try:
        tev = solver.evaluate(target, config)
        if not tev.feasible:
            return None  # not a valid target solution → not evidence
        sol, _ = solver.extract(bundle, config)
        if sol is None:
            return None
        sev = solver.evaluate(source, _cfg(sol))
    except PredError:
        return None

    # Soundness: a valid target solution must extract to a valid source solution.
    if not sev.feasible:
        return Verdict(
            True,
            f"confirmed unsound_extraction: valid target solution {config!r} extracts to "
            f"invalid source solution {sol} ({sev.raw})",
            {"witness": config, "extracted_solution": sol, "evaluation": sev.raw},
        )

    # Optimality: an *optimal* target solution must extract to an *optimal* source solution.
    if (source_opt.kind == "opt" and source_opt.feasible
            and sev.value is not None and source_opt.value is not None):
        try:
            tgt_opt = solver.solve(target)
        except (Inconclusive, PredError):
            return None
        is_c_optimal = (tgt_opt.feasible and tev.value is not None
                        and tgt_opt.value is not None
                        and abs(tev.value - tgt_opt.value) <= FLOAT_TOLERANCE)
        if is_c_optimal and _strictly_worse(sev.value, source_opt.value, source_opt.sense):
            return Verdict(
                True,
                f"confirmed suboptimal_extraction: optimal target solution {config!r} extracts "
                f"to suboptimal source value {sev.raw} vs optimum {source_opt.raw}",
                {"witness": config, "extracted_value": sev.raw, "source_optimum": source_opt.raw},
            )
    return None


def verify(cert: dict, repo_dir: str | None = None) -> Verdict:
    """Re-validate a certificate deterministically via pred. Never trusts the AI's claim.

    Core check: solve(source) vs solve(reduce(source)) — a value/feasibility mismatch is a
    genuine bug. Plus an optional witness check when the certificate carries a target_config.
    """
    source = cert.get("source")
    if not source:
        return Verdict(False, "certificate missing 'source'")

    target_type = ((cert.get("bundle") or {}).get("target") or {}).get("type") \
        or cert.get("target_type")
    if not target_type:
        return Verdict(False, "certificate missing target type (bundle.target.type)")

    if len(json.dumps(source)) > MAX_INPUT_BYTES:
        return Verdict(False, f"source instance too large (> {MAX_INPUT_BYTES} bytes) — "
                              "counterexamples must be minimal")

    with tempfile.TemporaryDirectory() as tmp:
        solver = PredSolver(tmp)

        # Re-derive the bundle from source ourselves (the agent's bundle is never trusted).
        try:
            bundle = solver.reduce(source, target_type)
        except PredError as e:
            return Verdict(False, f"pred reduce failed: {e}")

        # Core round-trip check. The SOURCE of an *→ILP rule must be solved independently
        # of ILP (else we'd verify the rule with itself); the target side is unaffected
        # (solving B via B→ILP doesn't use the rule A→B under test).
        rule_targets_ilp = str(target_type).upper().startswith("ILP")
        try:
            src = solver.solve(source, independent=rule_targets_ilp)
            bnd = solver.solve(bundle)
        except Inconclusive as e:
            return Verdict(False, f"inconclusive — {e}")
        except PredError as e:
            return Verdict(False, f"pred solve failed: {e}")

        if not agrees(src, bnd):
            label = _derive_label(src, bnd)
            return Verdict(
                True,
                f"confirmed {label}: source solves to {src.raw} but solving via the "
                f"reduction yields {bnd.raw}",
                {"source_evaluation": src.raw, "roundtrip_evaluation": bnd.raw, "label": label},
            )

        # Optional witness check (closes the all-feasible-solutions / degenerate-optimum gap).
        config = cert.get("target_config")
        if config:
            w = _witness_check(solver, source, bundle, config, src)
            if w is not None:
                return w

        return Verdict(
            False,
            f"reduction recovers the source answer (direct {src.raw} == round-trip {bnd.raw}) "
            "— no bug",
            {"source_evaluation": src.raw, "roundtrip_evaluation": bnd.raw},
        )


# ─── Calibration ──────────────────────────────────────────────────────────────

# Reject path — published, safe fixtures (none is a real bug). NOTE: valid_bug.json was
# re-classified to REJECTED — the round-trip recovers the optimum (Max(2)); its old
# "suboptimal" claim used a NON-optimal target_config, not a real reduction bug.
FIXTURE_EXPECTATIONS = {
    "valid_bug.json": False,
    "wrong_target.json": False,
    "valid_solution_claimed_invalid.json": False,
}


def _private_accept_fixtures() -> list[Path]:
    """Accept-path fixtures (real bugs) live in the gitignored private dir, so the answer
    key is never published. Calibration runs them when present, skips them when absent."""
    if not PRIVATE_FIXTURES_DIR.is_dir():
        return []
    return sorted(PRIVATE_FIXTURES_DIR.glob("genuine_bug_*.json"))


def run_calibration() -> bool:
    all_passed = True
    print("Running verifier calibration...")
    print("-" * 60)

    cases = [(FIXTURES_DIR / name, expected)
             for name, expected in FIXTURE_EXPECTATIONS.items()]
    accept = _private_accept_fixtures()
    cases += [(p, True) for p in accept]
    if not accept:
        print("NOTE  no private accept-path fixtures found "
              f"({PRIVATE_FIXTURES_DIR}) — reject path only\n")

    for fixture_path, expected in cases:
        if not fixture_path.exists():
            print(f"MISSING  {fixture_path.name}")
            all_passed = False
            continue
        cert = json.loads(fixture_path.read_text(encoding="utf-8"))
        verdict = verify(cert)
        passed = verdict.accepted == expected
        print(f"{'PASS' if passed else 'FAIL'}  {fixture_path.name}")
        print(f"      expected={'accepted' if expected else 'rejected'}, "
              f"got={'accepted' if verdict.accepted else 'rejected'}")
        print(f"      {verdict.reason}\n")
        all_passed = all_passed and passed
    print("-" * 60)
    print("Calibration PASSED" if all_passed else "Calibration FAILED")
    return all_passed


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m benchmark.verify <certificate.json>")
        print("       python -m benchmark.verify --calibrate")
        sys.exit(1)
    if sys.argv[1] == "--calibrate":
        sys.exit(0 if run_calibration() else 1)
    cert = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    verdict = verify(cert)
    print(verdict)
    for k, v in verdict.details.items():
        print(f"  {k}: {v}")
    sys.exit(0 if verdict.accepted else 1)


if __name__ == "__main__":
    main()
