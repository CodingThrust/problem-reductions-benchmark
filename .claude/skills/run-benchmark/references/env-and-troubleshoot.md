# submission.env fields + failure decoding

Read on demand when filling `submission.env` or when a step fails. Source of truth for the
fields is `submission.env.example`; this is the operational digest.

## submission.env — required vs optional

Copy `submission.env.example` → `submission.env` (gitignored; holds your key). The runner reads
these as env vars (CLI flags would override, but the skill uses the env-file).

### Required for a real run
| Var | Meaning | If wrong/missing |
|---|---|---|
| `MODEL_NAME` | LiteLLM-routable model name (`anthropic/…`, `openai/…`, `openrouter/…`, `gemini/…`, or `openai/<m>` + `API_BASE`) | run hard-errors: "`--model (or env MODEL_NAME) is required`" |
| API key | `API_KEY` (generic) **or** a provider var (`OPENAI_API_KEY`/`ANTHROPIC_API_KEY`/`OPENROUTER_API_KEY`/`GEMINI_API_KEY`) — provider vars pass straight through to LiteLLM | not checked in Python; surfaces at the `model call` preflight as an auth error |

### Optional (defaults shown; uncomment only to change)
| Var | Default | Use |
|---|---|---|
| `API_BASE` | — | OpenAI-compatible endpoint (OpenRouter/gateway/vLLM/Azure) |
| `MODEL_KWARGS` | — | JSON object of extra litellm kwargs (`custom_llm_provider`, `api_version`, `extra_headers`…). Invalid JSON / non-object errors at startup |
| `MAX_TOKENS` | 8192 | per-call output ceiling |
| `MAX_RULES` | all | cap rules attempted — **smoke runs only**; omit for a ranked run (per-rule only) |
| `AGENT_MODE` | `per-rule` | `per-rule` (isolated session per rule, 35 steps each) or `whole-repo` (ONE session, 300 steps, the agent triages the rules itself) |
| `TRAJECTORY_DIR` | `OUTPUT`'s dir (`/out`) | where **whole-repo** persists the trajectory + the durable incremental cert log (`certs.txt`); the agent writes each certificate here the moment it finds it, so an early-stop/crash still leaves the found bugs on disk |
| `AGENT_CONFIG` / `AGENT_STRATEGY_FILE` | bundled | bring-your-own prompt; the files must be **mounted** into the container (`-v "$PWD/cfg:/cfg"`) and the path given as a container path |
| `SUBMITTED_BY` | — | your handle, recorded in the envelope |
| `EXPECTED_PRED_VERSION` / `EXPECTED_PRED_COMMIT` | baked | debugging only; `EXPECTED_PRED_VERSION=""` disables the version check |

`OUTPUT` (default `/out/submission.json`) is the stable "latest" pointer `prb submit` reads; every run **also** writes a versioned archive `submission-<model>-<timestamp>.json` beside it, so history isn't clobbered. `REPO_DIR` is container-internal and baked — don't set it.

Non-standard endpoint example:
```ini
MODEL_NAME=openai/my-model
API_BASE=https://my-gateway.example/v1
API_KEY=...
MODEL_KWARGS={"custom_llm_provider":"openai"}
```

## Preflight failure decoding

`make preflight` (or `<engine> run --env-file submission.env <image> --preflight`) runs three
checks and exits non-zero if any fail. It never raises — it prints `PASS`/`FAIL` + a detail.

| Check | FAIL means | Fix |
|---|---|---|
| **pred binary** | pred missing or version ≠ pinned | should always pass inside the image; a FAIL = broken/overridden image or a wrong `EXPECTED_PRED_VERSION`. Rebuild at the right `PR_REF` |
| **library rules** | no `.rs` rules under `REPO_DIR/src/rules` | source tree not copied / `REPO_DIR` overridden. Rebuild the image; don't set `REPO_DIR` |
| **model call** | the real error (spends ~$0.0001) — its detail names the exception type | **auth error** → bad/missing key; **connection error** → wrong `API_BASE`/endpoint; **routing/model-not-found** → wrong `MODEL_NAME`. Fix that line in `submission.env` and rerun preflight |

Only proceed to the full run when preflight prints `Preflight PASSED`.

## Other gotchas

- **Build exit 137** = OOM during the Rust compile → give the engine VM/host ≥8GB RAM (macOS
  Colima defaults to 2GB — see `engines.md`). LTO is already off and `CARGO_BUILD_JOBS=1` in the
  Dockerfile.
- **`docker run` "image not found"** at preflight/run → you built with a different `PR_REF` than
  you're running with. Build, preflight, and run must all use the **same** `PR_REF` (it drives
  both the build arg and the image tag).
- **`out/submission.json` owned by root** (rootful docker) → the container runs as root; `chown`
  it or use rootless podman with `--userns=keep-id` (the detect script sets this).
- **Build needs network** (clones problem-reductions from GitHub, apt/cargo/pip). The **run**
  needs network only to reach the model API; pred is a local self-contained binary.
- **Apple Silicon** builds an arm64 image natively — fine as long as you run it on the same Mac.
  Don't add `--platform=linux/amd64` unless the run target is amd64 (the emulated Rust build is
  brutally slow).
