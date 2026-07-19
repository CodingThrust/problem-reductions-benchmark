---
name: submit-benchmark-result
description: Validate and upload an existing problem-reductions benchmark submission. Use when the user wants to check, test-upload, or officially submit a submission.json. If no result exists yet, route the request to $run-benchmark.
---

# Submit a benchmark result

Follow this workflow for an existing `submission.json`. Do not run a model or create a
submission in this skill.

## 1. Find and validate the file

Ask for the path only if the user did not provide it. If no result exists, use
`$run-benchmark` instead.

Treat the file as confidential. Do not print certificates, trajectories, source instances,
submit-log contents, or credentials. Do not edit the submission.

Run:

```bash
python3 -m benchmark.submit --predictions <submission.json> --dry-run
```

Stop if validation fails. Otherwise report only:

- absolute path;
- `model` and `library_commit`;
- claimed bugs, `total_tokens_k`, and number of submit attempts;
- `run_error`, if present.

## 2. Choose the outcome

Use the user's stated choice. If they did not choose, ask:

1. Validate only — keep the file local.
2. Test upload — process it privately without publishing it.
3. Official submission — eligible for the public leaderboard after maintainer review.

Stop after validation for option 1. Use `--test` for option 2. Use no mode flag for option 3.
Do not officially submit a result containing `run_error`; offer options 1 or 2 instead.

## 3. Confirm the upload

Immediately before uploading, show:

- endpoint hostname;
- absolute file path;
- model and claimed bug count;
- TEST or OFFICIAL mode.

State that the private submission will leave the machine, then obtain explicit confirmation.

## 4. Log in and upload once

Use the official intake:

```bash
export PRB_SUBMIT_URL=https://intake.prb-bench.workers.dev/submit
PRB_ACCESS_APP="${PRB_SUBMIT_URL%/submit}"
PRB_ACCESS_TOKEN="$(cloudflared access login --no-verbose --auto-close "$PRB_ACCESS_APP")" \
  python3 -m benchmark.submit --predictions <submission.json> --test
```

Remove `--test` for an official submission. Require `cloudflared`; the login opens GitHub in
the user's browser. Keep token acquisition inside command substitution. Never print or ask
for the token, and never substitute a GitHub PAT, `gh auth token`, or `GITHUB_TOKEN`.

Upload only once. Do not retry a timeout because the first request may already be queued.
For HTTP 401/403, re-authenticate; for 413 or 429, stop and report the error.

## 5. Report the result

On success, report the `submission_id`, model, mode, and endpoint. Say that `accepted` means
queued privately, not scored or published. Keep the local submission until scoring is
confirmed.
