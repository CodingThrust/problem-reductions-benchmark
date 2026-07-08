# Submissions

**Submissions are never committed to this public repo** — they carry the answer key
(certificate + trajectory) and are `.gitignore`d. This directory is only local scratch for
the self-run scoring path.

## How to submit (external)

Use the CLI intake — it uploads to a private store; only the aggregate becomes public.

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
them with `pred`, rebuilds the aggregate, and writes `site/results.json`. The files here
stay local; they never enter git.

## Notes

- Your self-reported `bugs_found` is advisory only; the score is the number of **distinct
  rules** with a `pred`-confirmed bug, recomputed by the backend (zero-trust).
- Re-submitting the same model is fine — the leaderboard keeps your best run.
