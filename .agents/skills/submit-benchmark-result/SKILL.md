---
name: submit-benchmark-result
description: Validate and upload an existing problem-reductions benchmark submission through the private intake. Use when a user wants to submit, upload, publish, dry-run, or test a submission.json they already produced, especially when the user is not a repository maintainer. Handles local validation, test-versus-official intent, intake authentication, upload confirmation, and submission ID reporting; routes missing results to run-benchmark instead of running a model itself.
---

# Submit a benchmark result

Submit an existing `submission.json` without requiring GitHub repository, R2, Worker, or
Actions access. Treat the file as confidential because it contains certificates and the
submit ledger.

## 1. Locate the authoritative result

Ask for the submission path only when it was not supplied. Accept the authoritative output
from either benchmark backend; do not reconstruct it from logs or edit its metrics,
certificates, `library_commit`, or ledger.

If no result exists and the user wants to run the benchmark, invoke `$run-benchmark`. Do not
choose a model/backend or start a paid run inside this skill.

Read the current round from `README.md` and the current structure from
`benchmark/submission.schema.json`. Inspect only summary fields; never print certificates,
source instances, submit-log contents, trajectories, or credentials. Report:

- model and `library_commit`;
- claimed bugs, token total, and submit-attempt count;
- `run_error`, when present;
- absolute submission path.

Run the repository client as a local courtesy check:

```bash
python3 -m benchmark.submit --predictions <submission.json> --dry-run
```

Stop on failure. Do not repair an invalid result by hand; send it back to the producing run.

## 2. Choose the outcome

Ask only when the user has not already chosen:

> What should happen to this result?
>
> 1. Keep it local after validation.
> 2. Upload an intake test that is scored privately and never reaches the leaderboard.
> 3. Upload an official submission.

For option 1, report validation and stop. For option 2, use `--test`. For option 3, never
use `--test`. A non-test result carrying `run_error` is not a clean official submission;
offer local-only or test upload instead.

## 3. Authenticate without exposing credentials

Require `PRB_SUBMIT_URL` from the repository documentation or maintainer. Never ask the user
to paste any token into chat, print an environment variable, or commit credentials.

Prefer GitHub-backed Cloudflare Access when the deployed client documents
`PRB_ACCESS_TOKEN`:

1. Require `cloudflared` locally.
2. Obtain the application-scoped token only inside the confirmed upload command, for example
   `PRB_ACCESS_TOKEN="$(cloudflared access token -app=<application-url>)" <submit-command>`.
   `cloudflared` opens the configured GitHub login in a browser when needed.
3. Never run `cloudflared access login` or `cloudflared access token` standalone because
   some versions print the JWT. Pass it through the client-supported environment variable
   for that one process only; do not display, persist, or put it in a command-line flag.

Do not substitute `gh auth token`, a GitHub personal access token, or `GITHUB_TOKEN`; the
intake must never receive the user's general GitHub credential.

If the deployed client does not yet support Access, use the legacy `PRB_API_KEY` path only
when a credential is already configured locally and the maintainer confirms that endpoint
mode is enabled. If it is absent, stop and report that self-service authentication is not
deployed. Ask the maintainer to enable GitHub Access or issue an out-of-band per-user or
one-time intake credential; never request a shared long-lived key in chat.

## 4. Confirm and upload once

Before the external write, show the endpoint hostname, absolute file path, model, claimed
bug count, and test/official mode. State that the private certificate payload will leave the
machine, then obtain explicit confirmation.

Use the repository client so validation and test marking stay consistent:

```bash
python3 -m benchmark.submit --predictions <submission.json> --test  # private test
python3 -m benchmark.submit --predictions <submission.json>         # official
```

Do not retry an ambiguous timeout automatically: the first request may already have reached
R2 and a retry can create a duplicate. On HTTP 401/403, re-authenticate or report the missing
Access deployment. On HTTP 413, do not trim the evidence; report the size limit. On HTTP 429,
stop and report the rate limit.

## 5. Report the handoff

On success, report the returned `submission_id`, model, mode, and endpoint hostname. Make it
clear that `accepted` means privately queued, not scored or published. The submitter does not
trigger Actions, inspect R2, or open the leaderboard PR; those are maintainer responsibilities.

Never claim a score until the maintainer confirms the private scorer finished. Preserve the
local submission and logs until that confirmation.
