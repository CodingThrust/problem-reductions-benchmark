# Submissions

**Submissions are never committed to this public repo.** A submission carries the
certificate + trajectory — the answer key. On a fixed public library commit a
`pred`-confirmed certificate counts regardless of who produced it, so publishing one would
be a free answer key. Everything under `submissions/*.json` is `.gitignore`d.

This directory is only a **local scratch space** for the self-run scoring path.

## How to submit (external)

Use the CLI intake — it uploads over HTTPS to a private store (Cloudflare R2); only the
maintainer ever reads it back, and only the aggregate (counts, no rules/certs) becomes
public.

```bash
export PRB_SUBMIT_URL=<intake endpoint>   # from the maintainer
export PRB_API_KEY=<token>                 # from the maintainer
make run                                   # → out/submission.json
python -m benchmark.submit --predictions out/submission.json
```

Add `--test` to run an end-to-end check that is scored + stored privately but excluded from
the public leaderboard. See `intake/cloudflare-worker/README.md`.

## Self-run scoring (maintainer / local)

Drop scored submission files into this directory and run `make publish-local`: it scores
them with `pred`, rebuilds the aggregate, and writes `site/results.json` (guarded so no
answer-key field leaks). The files here stay local — they never enter git.

## Notes

- `budget_cap` must be `20` to be ranked.
- Your self-reported `bugs_found` is advisory only; the score is the number of **distinct
  rules** with a `pred`-confirmed bug, recomputed by the backend (zero-trust).
- Re-submitting the same model is fine — the leaderboard keeps your best run.
