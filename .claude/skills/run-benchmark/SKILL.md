---
name: run-benchmark
description: >-
  Run this repo's problem-reductions bug-finding benchmark end-to-end and produce
  out/submission.json: detect a container engine, build the runner image, configure
  submission.env, preflight, and run the budgeted agent. Works on macOS and Linux
  (Docker or rootless Podman). Use when asked to run, test, reproduce, or smoke-test the benchmark,
  or to generate a submission for a model. NOT for `make test-unit` / pytest.
---

# Run the benchmark → out/submission.json

The whole benchmark is **dockerized**: the `pred` binary (Rust, compiled from
problem-reductions at a pinned tag), the agent stack (mini-swe-agent + LiteLLM), and the
rule sources are all baked into one image. The host only needs a **container engine** and
**git** — no host-side Rust/pred/Python install. Your job here is to drive:
**detect engine → build image → configure `submission.env` → preflight → run**, ending at
`out/submission.json`. **First ask the user's goal** — run/test locally, or submit to the
official benchmark. Steps 1–5 are identical either way; only Step 6 differs, and only the
submit path needs intake secrets.

Drive this with the user's real output at each step; don't assume success. Run each command,
read what it printed, and branch. When a step needs installing software or changing the
system, run it **visibly** (one command at a time) so the user sees it — never silently.

## Step 0 — Ask the goal, and confirm prerequisites

First ask: **run/test locally**, or **submit to the official benchmark**? This only changes
Step 6 — don't ask for intake secrets unless they're submitting.

Confirm the user has:

- A **model API key** and its **price per 1M tokens** (input + output) — needed for *either*
  goal, since running the agent calls the model. Both *required*; a real run hard-fails
  without `PRICE_IN`/`PRICE_OUT` (there is no built-in price table).
- **Only if submitting**: `PRB_SUBMIT_URL` + `PRB_API_KEY`, from the maintainer.
- **git** and a **container engine** (checked next).

## Step 1 — Detect the engine

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/detect-engine.sh"
```

It's read-only. Parse the `KEY=VALUE` lines. `ENGINE` is `docker`, `podman`, or `none`. Also
note `RUN_FLAGS` (the exact `-v`/`--userns`/`--env-file` flags for *this* host),
`RAM_HINT`, and any `PROBLEM` line.

- **`ENGINE=none`** → install one. See `references/engines.md`, install the recommended engine
  for the OS **with the user watching**, then re-run detect. On a non-Unix shell (Windows
  native) the script says so: Windows is not supported — direct the user to WSL2 (a Linux
  distro) or any Linux host (a workstation or small cloud VM).
- **`ENGINE=docker` or `podman`** → continue below.
- **`RAM_HINT=low` or macOS** → the Rust build OOMs (exit 137) under ~8GB *engine-VM* RAM. On
  macOS/Colima bump the VM (`references/engines.md`) before building.

## Step 2 — Build the runner image

`PR_REF` (default `v0.6.0`) selects the library version and **must be identical for build,
preflight, and run** — otherwise `docker run` can't find the image tag. Building compiles pred
(a few minutes) and needs network (it clones problem-reductions from GitHub).

- **docker**: `make runner-build PR_REF=v0.6.0`
- **podman** (Makefile hardcodes `docker`): run the raw build, or `alias docker=podman` first:
  ```bash
  podman build -f docker/Dockerfile --target runner \
    --build-arg PR_REF=v0.6.0 -t problem-reductions-runner:v0.6.0 .
  ```

Build fails with **exit 137** = OOM → give the engine VM/host more RAM and rebuild.

## Step 3 — Configure submission.env

If `submission.env` is absent: `cp submission.env.example submission.env`. It's gitignored
(holds the key). Fill the **required** lines; leave the rest at their defaults. See
`references/env-and-troubleshoot.md` for the full field table and non-standard endpoints.

```ini
MODEL_NAME=openai/gpt-5.4    # any LiteLLM-routable name (anthropic/… openai/… openrouter/… gemini/…)
API_KEY=sk-...               # generic; or a provider var (OPENAI_API_KEY / ANTHROPIC_API_KEY / …)
PRICE_IN=3.0                 # USD / 1M input tokens  — REQUIRED
PRICE_OUT=15.0               # USD / 1M output tokens — REQUIRED
```

For a cheap smoke run (don't spend the full $20) add `MAX_RULES=1`. A **ranked** submission
must keep `BUDGET_USD=20` and omit `MAX_RULES`.

**Agent mode** (`AGENT_MODE`, default `per-rule`): `per-rule` runs one isolated agent session
per rule with the budget split evenly; `whole-repo` runs ONE session over the whole library
and lets the agent enumerate and triage the rules itself under a single budget. Both produce
the same `out/submission.json` and are scored identically — set `AGENT_MODE=whole-repo` to try
it. (`MAX_RULES` only applies to `per-rule`.) In `whole-repo`, the agent also writes each
certificate to `TRAJECTORY_DIR/certs.txt` (default `/out`) as it finds it, and the trajectory
is persisted every step — so an early-stop/crash still leaves the found bugs on disk.

**Output is versioned.** `out/submission.json` is the stable "latest" pointer; every run ALSO
writes a versioned archive `out/submission-<model>-<timestamp>.json` beside it, so runs don't
overwrite each other.

**Confirm the experiment parameters with the user — don't silently default them.** These
shape the result and the spend, so state the resolved set and get an explicit OK before
running: **mode** (`AGENT_MODE`), **budget** (a full ranked run at `BUDGET_USD=20`, or a
cheap smoke run via `MAX_RULES=1` / a smaller budget), and — only if they care —
`PER_RULE_BUDGET` and `MAX_TOKENS`. Ranked runs require `BUDGET_USD=20` and no `MAX_RULES`.

## Step 4 — Preflight (one tiny real API call, ~a fraction of a cent)

Always run this before the full run; it validates key/endpoint/price + pred/rules through the
exact batch code path and fails fast.

- **docker**: `make preflight`
- **podman/raw**: `<engine> run --rm --env-file submission.env problem-reductions-runner:v0.6.0 --preflight`

It prints three checks (`pred binary`, `library rules`, `model call`). If any **FAIL**, read
the detail and fix it — the `model call` line carries the real error (auth / endpoint / model
name / pricing). Decode table in `references/env-and-troubleshoot.md`. Do not proceed on a FAIL.

## Step 5 — Full run → out/submission.json

**Gate**: a full run spends real money and takes a while. Restate the resolved parameters
(model, mode, budget, any smoke caps) and get an explicit OK before you launch it.

- **docker**: `make run`
- **podman/raw**: use the `RUN_FLAGS` from Step 1, e.g.
  ```bash
  mkdir -p out
  podman run --rm <RUN_FLAGS> problem-reductions-runner:v0.6.0
  ```
  (rootless podman: `RUN_FLAGS` already includes `--userns=keep-id` and `:z` if needed, so
  `out/submission.json` comes back owned by you. Rootful docker writes it **root-owned** — the
  user may need `sudo`/`chown` to move it.)

The run needs network only to reach the model API. When it finishes, confirm
`out/submission.json` exists and report the result (bugs found, spend). 

## Step 6 — Hand back, or submit

`out/submission.json` now exists; report the result (bugs found, spend). Then branch on the
goal from Step 0:

- **Run/test locally** → done. `out/submission.json` is the deliverable; no secrets, no upload.
- **Submit to the official benchmark** → with `PRB_SUBMIT_URL` + `PRB_API_KEY` set, upload:
  ```bash
  python -m benchmark.submit --predictions out/submission.json
  #   --dry-run   validate locally without sending
  #   --test      scored + stored privately, kept off the public board
  ```
  The backend re-verifies every certificate with `pred`; only the aggregate becomes public.
  Details in `CONTRIBUTING.md`. Don't submit unless the user asked.

## Per-engine flag matrix (reference)

| Engine | Build | Run flags | out/ ownership |
|---|---|---|---|
| docker (rootful) | `make runner-build` | `-v "$PWD/out:/out" --env-file submission.env` | **root** (may need sudo) |
| podman (rootless) | raw `podman build …` | `-v "$PWD/out:/out[:z]" --userns=keep-id --env-file submission.env` | you |

`:z` on the podman mount only when SELinux is enforcing (detect-engine.sh decides). Deeper
per-platform engine setup lives in `references/engines.md`; env fields + failure decoding in
`references/env-and-troubleshoot.md` — read those on demand, not up front.
