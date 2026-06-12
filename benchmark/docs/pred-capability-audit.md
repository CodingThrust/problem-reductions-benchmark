# pred CLI Capability Audit

**Date:** 2026-06-12
**Repo:** `C:\Users\ASUS\Desktop\111\reduction\problem-reductions`
**Commit:** `aa2d1a10cffa434871d12a4d6f411147fb7e08a8`
**pred binary:** `C:\Users\ASUS\.cargo\bin\pred.exe`
**pred version:** 0.6.0

## Summary

8/8 capabilities verified.

## Required Capabilities for Bug Checker

| Capability | Status | Notes |
|------------|--------|-------|
| `create` | OK Available | Creates valid JSON with type and data |
| `reduce` | OK Available | Creates reduction bundle with source/target/path |
| `solve` | OK Available | Returns solution and evaluation |
| `solve_no_solution` | OK Available | Returns Or(false) for unsatisfiable |
| `evaluate_valid` | OK Available | Valid config returns Max(2) |
| `evaluate_invalid` | OK Available | Invalid config returns Max(None) |
| `extract` | OK Available | Lifts target-space config back to source-space solution |
| `round_trip` | OK Available | Round-trip completes: source solution + intermediate target solution |

## Test Details

### `create`

**Description:** Create MIS instance and output JSON

**Result:** PASS

Creates valid JSON with type and data

### `reduce`

**Description:** Reduce MIS to QUBO and output bundle

**Result:** PASS

Creates reduction bundle with source/target/path

### `solve`

**Description:** Solve MIS with brute-force and return solution + evaluation

**Result:** PASS

Returns solution and evaluation

### `solve_no_solution`

**Description:** Solve unsatisfiable instance returns Or(false)

**Result:** PASS

Returns Or(false) for unsatisfiable

### `evaluate_valid`

**Description:** Evaluate valid MIS config returns Max(2)

**Result:** PASS

Valid config returns Max(2)

### `evaluate_invalid`

**Description:** Evaluate invalid MIS config returns Max(None)

**Result:** PASS

Invalid config returns Max(None)

### `extract`

**Description:** Extract source-space solution from bundle + target config

**Result:** PASS

Lifts target-space config back to source-space solution

### `round_trip`

**Description:** MIS -> QUBO -> solve -> extract back to MIS

**Result:** PASS

Round-trip completes: source solution + intermediate target solution

## Upstream Issues Filed

None - all required capabilities are available in v0.6.0.

## Notes

- `pred extract` is available in v0.6.0 (added in #1060).
- `solve --solver brute-force` correctly returns `Or(false)` / `Max(None)` for unsatisfiable instances.
- `evaluate` distinguishes valid vs invalid configurations via `Max(value)` vs `Max(None)`.
- Built with `--no-default-features --features lp-solvers` on Windows (highs-sys requires CMake + VS2026).
