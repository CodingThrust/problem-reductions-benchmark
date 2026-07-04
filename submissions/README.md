# Submissions

Each model run is one JSON file committed here via **pull request**:

```
submissions/<your-handle>/<model>.json
```

## How to submit

1. **Produce the file.** Run the dockerized runner at the fixed $20 budget (see
   `../CONTRIBUTING.md`): `make run` → `out/submission.json`.
2. **Open a PR** adding it as `submissions/<your-handle>/<model>.json` — and nothing else
   (a PR that also changes code won't be auto-scored; keep submissions in their own PR).
   - An automated check validates the file against `benchmark/submission.schema.json`
     (structure only).
3. **A maintainer approves the scoring run.** Running `pred` on submitted input is the
   trust boundary, so it doesn't run automatically — a maintainer approves it on the PR.
4. **The verified result appears on the PR** (zero-trust `pred` re-check) and is a required
   check. **A maintainer reviews the result and merges.** You never merge a number nobody
   has seen.

## What happens after merge

Merge publishes the already-verified result: CI rebuilds the leaderboard from all merged
submissions (re-verifying with `pred` — deterministic, so it reproduces the number shown on
your PR) and publishes the static site to **GitHub Pages**. Your self-reported `bugs_found`
is ignored throughout; the score is the number of **distinct rules** with a `pred`-confirmed
bug.

## Notes

- `budget_cap` must be `20` to be ranked.
- Counterexamples must be minimal; the verifier sandboxes each `pred` call (CPU/memory/
  timeout) and rejects oversized sources.
- Re-submitting the same model is fine — the leaderboard keeps your best run.
