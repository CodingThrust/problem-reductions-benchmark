# Submitting a model run

The current public comparison accepts one execution protocol: **Standardized Model API / Self-selected Top50**. Its limits are built into the benchmark code and are not configurable.

```text
source-only triage → frozen Top50 → 50 isolated rule episodes → private submission
                                                                ↓
                                     independent pred verification → aggregate leaderboard
```

## 1. Configure the model API

Copy the internal template and set the model plus its provider credential:

```bash
cp submission.env.example submission.env
```

`MODEL_NAME`, `API_BASE`, and `API_KEY` identify the endpoint. The official path does not accept prompt files, strategy files, coding-agent backends, model kwargs, submit pools, or budget variables. This is intentional: two entries are comparable only if model access is the remaining meaningful variable.

## 2. Preflight and run

```bash
make runner-pull PR_REF=v0.6.0   # or make runner-build PR_REF=v0.6.0
make preflight
make run
```

Preflight first checks that no forbidden execution knob is present, verifies the pinned source and `pred`, and then makes one minimal model call. A rankable run uses:

- triage: 8 model generations, 12 source-only shell actions;
- 50 unique frozen rules;
- per rule: 10 model generations, 12 shell actions, 24 `pred` calls, at most 10 solves;
- exactly two charged submit attempts per rule;
- a 10,000-character deterministic terminal preview per action, with a read-only bounded raw log.

The runner owns these counters. A model receives authoritative remaining-budget state after each action, cannot transfer unused budget, and cannot invoke the verifier directly. Each rule starts with fresh model history and a fresh submission ledger.

The runner checkpoints partial artifacts for diagnosis, but only a completed 50-episode artifact produced through the protected Model API factory can be rankable. Hung child processes are killed by fixed watchdogs and recorded as model/infrastructure outcomes; changing wall-clock conditions never changes the named logical budget or score.

## 3. Validate and upload

```bash
python -m benchmark.submit --predictions out/<timestamp>/submission.json
```

The intake check is only a courtesy check. The private backend independently validates:

- prompt hash, runner mode, target commit, and `pred` version;
- the canonical 50-rule inventory and frozen order;
- triage and per-rule event/usage ledgers;
- exactly two submit opportunities per rule, including malformed or rejected calls;
- every accepted certificate with pinned `pred`.

Changing any embedded limit, claiming a custom or incomplete run, inventing a rule, or making the artifact disagree with its ledgers makes the result unrankable. Only a reproducible certificate accepted by the independent `pred` replay contributes to the score.

Only a compact aggregate is published. Private prompts, hypotheses, certificates, messages, rule identities, raw ledgers, credentials, and free text are excluded from the public projection.

## 4. Read the score

The Top50 table ranks `verified_bugs`: distinct shortlisted rules with a server-reproduced bug. `bugs_at_10`, `bugs_at_25`, first/second-attempt accepts, cap hits, tokens, cost, and elapsed time are diagnostics. They do not break ties.

The score should be read as joint **prioritization + bounded diagnosis + certificate construction** ability on the pinned library snapshot. A self-selected shortlist means it is not a pure diagnostic-only test.

## Release checks

Before changing benchmark limits or the target version, update the non-ranking calibration evidence and documentation in the same change. The repository commit identifies the resulting benchmark definition.

```bash
pytest -v -m "not integration"
python -m benchmark.calibrate_budget --check benchmark/docs/budget-calibration.json
python -m benchmark.verify --calibrate
```

The calibration checker proves that the selected limits match the built-in runtime values, every model covers the full candidate grid, smaller and larger `M`/`P` candidates surround the selection, and the Markdown report matches the machine-readable evidence.

## Historical tooling

`benchmark.run_submission`, `make run-local`, `benchmark/config.yaml`, and the coding-agent adapters reproduce the old `legacy-whole-repo` protocol only. Historical aggregates remain visible in their own site selector. They cannot be submitted or sorted into the Top50 table, and they are not a maintained System Track.

Repository policy also applies: never merge a PR until required CI is green and the user explicitly approves the merge.
