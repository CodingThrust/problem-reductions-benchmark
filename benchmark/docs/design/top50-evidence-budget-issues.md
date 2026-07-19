# Top-50 Evidence-Budget Benchmark — Implementation Issue Drafts

Status: filed as GitHub issues #65–#68; implementation in progress  
Source design: [Top-50 Evidence-Budget Benchmark](top50-evidence-budget-benchmark.md)

## Agreed boundary

This batch implements one trusted-team, standardized Model API track in which a model commits
to its own Top50 and investigates every selected rule under identical logical budgets. It does
not implement multiple seeds, a Fixed Top50 diagnostic, a coding-agent/System Track, external
submission attestation, or ranking by elapsed time, tokens, or cost.

The four issues are deliberately coarse. Each delivers a complete component with one compound
behavioural acceptance suite; internal classes and substeps are not separate issues.

## Milestones and dependency graph

```text
M1 Enforceable evidence budget
└── I1 Evidence Budget Service
    └── M2 End-to-end Top50 benchmark
        └── I2 Self-selected Top50 Runner
            └── I3 vNext result and ranking contract
                └── M3 Calibration and release
                    └── I4 Calibrate and publish the frozen contract
```

I3 depends on I2 because its schema and scorer validate the actual Top50 run artifact. I4
depends on I1–I3 because calibration must exercise the same implementation that will be
released and must publish the selected values through the versioned result contract.

---

## I1 — Build the evaluation-owned Evidence Budget Service

### Background

The current benchmark exposes the real `pred` executable to the model and limits only a
run-wide submission count. One shell action can start hundreds of `pred` subprocesses, so turns
and shell commands do not bound how much target-system feedback a model receives. The
[Top-50 design](top50-evidence-budget-benchmark.md) instead gives every selected rule an
independent allowance for model generations, shell actions, `pred` calls, `solve` calls, and
two formal submissions.

The runner image currently executes as root and installs the real binary at
`/usr/local/bin/pred`. Replacing `PATH` alone is therefore not enforcement: a model-authored
script could find and execute the binary by absolute path. This issue owns the complete trusted
service and OS boundary that make the logical budget enforceable.

### Objective

Build one evaluation-owned Evidence Budget Service that atomically accounts for a single rule
episode, exposes `pred`-compatible and `submit` shims to an unprivileged agent shell, and keeps
the real `pred`, verifier, counters, and accepted-certificate ledger outside the agent's access.

Each rule receives exactly two non-transferable submission opportunities. Rejected, malformed,
duplicate, wrong-rule, and other model-caused attempts consume one; pure gateway, transport,
container, or verifier infrastructure errors do not. Retrying the same request after a lost
response must be idempotent.

### Interface (Input → Output)

- **Input:** episode ID, current rule ID, frozen budget contract
  `{M, E, P, P_solve, S=2, O}`, pinned real `pred` path, and certificate verifier.
- **Agent surface:** an unprivileged writable scratch directory; a `pred` shim supporting the
  documented CLI shape; `submit <certificate>`; and free status/help/version/cached-inventory
  operations.
- **Output:** immutable per-episode ledgers for model generations, shell actions, admitted
  `pred`/`solve` requests, submission attempts, idempotent responses, accepted certificate, and
  classified infrastructure errors.

### Technical recommendations

- Reuse the atomic file-spool, directory-FD, and append-only ledger patterns in
  `benchmark/submit_session.py` instead of introducing a network service.
- Add separate non-root container identities for model-authored shell actions and admitted
  `pred` subprocesses. The real binary is executable only by the oracle identity; both identities
  share only the episode scratch group. The privileged Python parent owns counters and spawning,
  but never runs model-parameterized `pred` as root.
- Reserve a counter under a lock before spawning a process. A dynamic `create`, `reduce`,
  `solve`, `evaluate`, or `extract` request consumes one `P`; `solve` additionally consumes one
  `P_solve`. Nonzero exits, invalid arguments, pathological model input, and target timeouts
  consume the reservation.
- Give requests caller-stable idempotency keys and retain completed responses. Do not generate
  a new logical request ID merely because the client lost the first response.
- Refactor `SubmissionSession` into, or adapt it behind, a rule-scoped judge. Treat verifier
  exceptions as `infrastructure_error`, not ordinary rejection. Acceptance closes the rule and
  expires its unused submission attempt.
- Keep verifier-internal `pred` calls outside the agent's `P`/`P_solve` ledger.

These are recommendations; an alternative implementation is acceptable if it passes the same
privilege, concurrency, charging, and idempotency checks.

### Verification

Add one integration acceptance suite and run:

```bash
pytest -v benchmark/tests/test_evidence_budget.py
```

It must pass named cases demonstrating all of the following observable behaviour:

1. With `P=4` and `P_solve=1`, a model-authored loop that starts 20 sequential or concurrent
   `pred` processes receives exactly four admitted dynamic calls, at most one admitted solve,
   and `budget_exhausted` for the rest; the authoritative ledger contains the same counts.
2. Running the real binary's absolute path from the agent shell fails with a permission error,
   while the `pred` shim succeeds. This is the negative control proving `PATH` replacement is
   not the only protection.
3. Invalid JSON, invalid CLI arguments, nonzero `pred`, and a model-caused `pred` timeout consume
   admitted `P`; an injected gateway failure before admission consumes nothing.
4. Rule A can make two charged submissions and no third. Rule B still has two, proving the
   allowance is per rule rather than a shared run-wide pool. Acceptance on Rule B's first attempt
   closes Rule B and expires its second attempt.
5. Malformed, rejected, duplicate, and wrong-rule submissions consume `S`; an injected verifier
   exception consumes nothing and returns `infrastructure_error`.
6. Replaying one completed request ID after simulating a lost response returns the identical
   stored response without changing any counter. Two genuinely different concurrent request IDs
   can never overspend a counter.
7. Status/help/version/cached inventory probes do not change any charged counter.

As a regression check, also run:

```bash
pytest -v benchmark/tests/test_submit_session.py benchmark/tests/test_verify.py
```

Existing certificate acceptance/rejection behaviour must remain green. A test fixture that makes
the real binary executable by the agent must make the absolute-path negative control fail, proving
that the acceptance suite detects a broken privilege boundary.

### Out of scope

- Top50 selection and episode orchestration;
- choosing the public numeric values of `M`, `E`, `P`, `P_solve`, or output ceilings;
- protecting the runner from a malicious trusted team member or cryptographically attesting a
  remotely uploaded run; and
- changing `problem-reductions` or the `pred` CLI upstream.

---

## I2 — Implement the Self-selected Top50 Runner

### Background

The existing runner launches one unlimited whole-repository conversation. Models can inspect
different numbers of rules, spend different amounts of target-system feedback on each one, and
carry discoveries and accumulated context throughout the session. The
[Top-50 design](top50-evidence-budget-benchmark.md) replaces that with a bounded static triage
followed by 50 fresh, equal-budget rule episodes.

This issue owns the complete run workflow. A Top50 result is meaningful only if the shortlist is
frozen before dynamic experimentation, contains exactly 50 valid unique rules, and every rule
receives a clean context, clean scratch directory, and new Evidence Budget Service from I1.

### Objective

Replace the unlimited whole-repository Model API session with a two-phase `Top50Runner`:

1. a bounded source-only triage conversation that atomically commits an ordered Top50 with
   capped hypotheses; and
2. 50 sequential, isolated investigation episodes with identical non-transferable budgets.

Automatically inject authoritative remaining counters into every observation. Preserve partial
audit artifacts after a crash, but mark any unrecoverable provider, gateway, judge, or container
failure as `run_error` so it cannot be ranked as a clean completion.

### Interface (Input → Output)

- **Input:** pinned repository and canonical rule inventory; standardized model client and
  inference settings; triage contract `{T, E_t, O_t}`; per-rule contract
  `{M, E, P, P_solve, S=2, O}`; and the I1 service factory.
- **Triage output:** immutable ordered JSON artifact containing exactly 50 unique canonical rule
  IDs and optional size-capped hypotheses, plus its generation/action ledger.
- **Run output:** one Top50 run record containing the shortlist, triage ledger, exactly 50 ordered
  episode records, usage metadata, completion status, and any classified infrastructure error.

### Technical recommendations

- Keep `mini-swe-agent`/LiteLLM as the sole rankable reference harness. Remove the forced
  unlimited `step_limit=0` behaviour and account for usable responses and executed shell actions
  explicitly.
- Provide triage with a runner-cached rule inventory and read-only source tree. Do not expose the
  dynamic `pred` or `submit` shims until after the shortlist passes validation.
- Commit the shortlist through an evaluation-owned command or spool rather than scraping final
  prose. Reject lists with the wrong count, duplicates, unknown IDs, oversized hypotheses, or a
  second commit attempt.
- Start each rule with a fresh model message history, fresh scratch directory, and fresh I1
  service. Pass only the current rule ID and its capped triage hypothesis; transfer no generated
  files or model-authored memory.
- Execute the 50 episodes sequentially in the first release. This avoids provider throttling and
  host contention becoming rule-order-dependent. Because budgets are isolated, parallelism may be
  revisited later without changing the public contract.
- Provider 429/5xx/disconnects before a usable response follow one frozen retry policy and do not
  consume `T`/`M`. A usable but malformed model action consumes its generation. An executed shell
  action consumes `E_t`/`E`, including nonzero exit and model-caused timeout.
- End a rule immediately after acceptance or budget exhaustion. Do not transfer unused allowance.

### Verification

Add a deterministic fake-model end-to-end acceptance suite and run:

```bash
pytest -v benchmark/tests/test_top50_runner.py
```

The suite must pass named scenarios demonstrating:

1. A fake triage response commits 50 known unique rule IDs; the runner then creates exactly 50
   episode records in the frozen order, each starting with identical full counters and an empty
   conversation/scratch state.
2. The fake model writes a sentinel file and message in episode 1; episode 2 cannot observe
   either. Spending all `pred` and submit allowance in episode 1 leaves episode 2's counters full.
3. Dynamic `pred` and `submit` attempts during triage are unavailable and do not produce target
   feedback. A shortlist with 49 entries, 51 entries, a duplicate, an unknown rule, or an
   oversized hypothesis is rejected before any investigation episode starts. These are the
   negative controls for shortlist enforcement.
4. Every model observation contains authoritative `rule i/50`, generation, shell, `pred`,
   `solve`, and `submit` usage/limit values matching the underlying ledger.
5. Acceptance in an episode stops that episode without a further model call. Exhausting any hard
   counter ends only that rule and does not start an extra action.
6. An injected 429 before a usable response retries without consuming a model generation; a
   malformed usable response consumes one. Exhausted infrastructure retries produce `run_error`
   and an unrankable record rather than a clean 0-bug run.
7. The rankable execution path rejects `claude-code`, `codex`, a custom strategy file, or a custom
   prompt config. The same adapters may remain callable in an explicitly unranked development
   mode.

Also run the existing no-API runner tests:

```bash
pytest -v benchmark/tests/test_run_mini.py benchmark/tests/test_run_submission.py benchmark/tests/test_whole_repo.py
```

Update or replace whole-repository expectations so they assert the versioned historical path,
not the new rankable path. A fake model that tries to return a second shortlist or reuse one
episode's workspace must make the new suite fail.

### Dependencies

Depends on I1, the evaluation-owned Evidence Budget Service.

### Out of scope

- Fixed Top50 evaluation;
- multiple seeds or aggregate confidence intervals;
- parallel rule episodes;
- custom agent systems and coding-agent CLI ranking; and
- selecting final public budget values.

---

## I3 — Define and enforce the vNext result and ranking contract

### Background

The current submission schema describes one whole-repository session with a shared
`submit_limit`, a flat `submit_log`, `agent_mode: whole-repo`, and token efficiency as a
tie-breaker. Those fields cannot prove that a valid Top50 was frozen or that 50 rules received
identical non-transferable budgets.

The new private submission artifact must carry the complete evaluation-owned triage and episode
ledgers so the backend can recompute rankability and score. The public leaderboard must continue
to expose aggregate data only; rule identities, hypotheses, certificates, and trajectories remain
private answer-key material.

### Objective

Introduce a versioned Top50 submission and scoring contract, make the backend independently
validate its shortlist and all 50 episode ledgers, re-verify every accepted certificate, and rank
only clean Standardized Model Track results by distinct accepted rules.

Historical unlimited whole-repository and coding-agent results must remain viewable under their
original contract but must never be mixed into the Top50 ranking. Equal verified bug counts remain
ties; elapsed time, token usage, cost, and bugs/Ktok are informational only.

### Interface (Input → Output)

- **Input:** I2 Top50 run record, evaluation-owned ledgers, model/provider and exposed inference
  parameters, pinned repository/`pred`/runner identifiers, and accepted certificates.
- **Private output:** schema-valid versioned submission containing the complete budget vector,
  ordered Top50, triage ledger, exactly 50 rule-bound episode ledgers, usage, errors, and
  certificates.
- **Scored output:** backend-derived `VerifiedBugs@Top50[T,E_t,M,E,P,P_solve,S=2,O]`, Bugs@10/25/50,
  first-versus-second-attempt acceptance, calls per accepted bug, cap-hit diagnostics, and
  rankability verdict.
- **Public output:** aggregate leaderboard entry with model and contract provenance, headline
  verified bug count, and non-ranking efficiency fields; no answer-key data.

### Technical recommendations

- Add an explicit benchmark-contract identifier instead of inferring semantics from optional
  legacy fields. Keep a deliberate legacy parser for historical data rather than making the new
  schema accept ambiguous mixtures.
- Validate that the shortlist has exactly 50 canonical unique IDs, episode order matches it,
  every configured limit is identical, each usage count is within its limit, `P_solve <= P`,
  `S=2` per rule, and request IDs/attempt numbering are internally consistent.
- Derive result rows only from evaluation-owned accepted submit records for the matching current
  rule. Re-run `verify()` on every accepted certificate; ignore self-reported `bugs_found`.
- Treat missing episodes, infrastructure errors, CLI/custom-harness provenance, custom prompts,
  or inconsistent ledgers as unrankable. Preserve the private artifact for debugging.
- Version the public board by benchmark contract. Remove token efficiency from sort keys for the
  new contract while leaving old-board rendering intact.
- The operators are a trusted internal team. Do not add remote-run attestation, signatures, or
  external-user authentication to this issue.

### Verification

Run the contract and scorer acceptance suites:

```bash
pytest -v benchmark/tests/test_top50_submission.py benchmark/tests/test_verify_submission.py benchmark/tests/test_backend_score.py
```

They must use a schema-valid synthetic Top50 artifact and demonstrate:

1. The valid artifact has exactly 50 frozen rules and 50 matching episode ledgers; the backend
   ignores a deliberately false self-reported score and returns the distinct count implied by
   accepted, re-verified certificates.
2. Two accepted certificates for one rule count once. Accepted rules at shortlist positions 7,
   18, and 41 produce Bugs@10 = 1, Bugs@25 = 2, and Bugs@50 = 3.
3. A certificate for a rule outside the shortlist, a certificate submitted from another rule's
   episode, a third attempt, a shared `100`-attempt pool, duplicate/missing episode, over-limit
   counter, changed budget, or forged request ordering makes the run unrankable. These are the
   negative controls proving the scorer does not trust the envelope.
4. A pure `run_error` or unrecoverable infrastructure episode is preserved privately but excluded
   from ranking. A rejected model submission remains a clean charged attempt and does not make the
   run an infrastructure failure.
5. A `codex`/`claude-code` agent mode or custom prompt provenance is excluded from the Top50 board.
   A canonical API harness run is accepted.
6. Public output contains no rule ID, hypothesis, certificate, source instance, target config,
   submit reason, or trajectory. The aggregate guard fails if any such answer-key field is added.
7. For equal verified bug counts, changing tokens, cost, or elapsed time does not change rank
   order; the UI represents the entries as tied. A larger verified count ranks first.
8. One legacy v0.6 whole-repository fixture remains readable in its historical view but cannot
   enter or replace an entry in the Top50 board.

Also run:

```bash
python .github/scripts/check_aggregate.py site/results.json
```

against a generated development board. It must print no errors for the aggregate-only fixture;
injecting one `rule` or `certificate` field must make the guard exit nonzero.

### Dependencies

Depends on I2 so the contract validates the real `Top50Runner` artifact rather than a parallel
invented format.

### Out of scope

- cryptographic attestation or accepting untrusted external runs;
- multiple-seed aggregation, best-of-N, or confidence intervals;
- Fixed Top50 scores;
- ranking custom agent systems; and
- declaring unaccepted rules bug-free or reporting Top50 precision/recall.

---

## I4 — Calibrate and publish the first frozen Top50 budget contract

### Background

I1–I3 make logical budgets enforceable and scoreable, but the public values for triage turns,
per-rule model turns, shell actions, `pred`, `solve`, and output ceilings should not be chosen by
intuition. The [Top-50 design](top50-evidence-budget-benchmark.md) calls for one internal
pre-release calibration over development data, followed by one frozen budget for all formal runs.

The calibration grid is a development procedure, not a public multi-budget leaderboard and not a
multiple-seed requirement. The released benchmark runs each model once at the selected contract.

### Objective

Provide a reproducible internal calibration workflow, execute it on an explicitly non-ranking
development target, publish the measured trade-offs and chosen knee, then freeze those values
through configuration, prompts, preflight, schema, documentation, CI, and leaderboard display for
the next benchmark version.

This issue is the release gate: after it lands, no rankable run can request unlimited or custom
budgets, and all user-facing documentation tells the same Self-selected Top50 story.

### Interface (Input → Output)

- **Input:** development target identifier; a small named set of internal model runs; candidate
  contracts such as `M in {6,10,14}` and `P in {8,16,32}`; I1–I3 artifacts; and measured token,
  cost, elapsed-time, cap-hit, retry, and accepted-bug data.
- **Calibration output:** machine-readable observations plus a checked-in Markdown report showing
  yield, cap-hit rates, marginal gain, infrastructure errors, and the reason for selecting each
  public limit.
- **Release output:** one immutable named budget contract referenced by runner defaults, prompt,
  result schema, scorer, docs, preflight, image/version metadata, CI, and leaderboard.

### Technical recommendations

- Add a small analysis command that consumes completed development artifacts. Do not embed a
  supposedly objective automatic knee detector; show the raw table/curves and record the team's
  explicit selection rationale.
- Use an older library snapshot or mark every pilot artifact non-ranking. A pilot model set and
  candidate grid may be small; do not introduce a repeated-seed requirement.
- Start from the design's candidate coordinates, then adjust only when cap-hit or failure data
  shows that one counter binds pathologically. Record every tested contract, including failed
  runs, instead of publishing only the winner.
- Replace current `step_limit: 0`, `SUBMIT_LIMIT=100`, whole-repository prompt language, custom
  strategy hooks on the rankable path, and token-efficiency tie-breaking. Keep process/pred/model
  watchdogs as fixed safety controls.
- Update `README.md`, `CONTRIBUTING.md`, `submission.env.example`, `benchmark/config.yaml`, runner
  help, preflight, Docker image metadata, schema docs, and the public site together.
- After release, reconcile stale issues #2, #4, and #42 by linking the new implementation and
  stating precisely which old recommendations were implemented, deferred, or dropped. Closing or
  editing those issues remains a maintainer action after the implementation is merged.

### Verification

The implementation PR must include the calibration inputs/report and pass:

```bash
pytest -v -m "not integration"
```

followed in the pinned runner image by:

```bash
python -m benchmark.verify --calibrate
```

A reviewer must also be able to check the committed evidence offline in a few seconds:

```bash
python -m benchmark.calibrate_budget \
  --check benchmark/docs/budget-calibration.json
```

It must print `PASS: calibration evidence matches <contract-id>` and exit 0. Changing any
selected limit, deleting a tested candidate, or making the Markdown report disagree with the
machine-readable evidence must make the same command exit nonzero and identify the mismatch.

The release evidence must additionally demonstrate:

1. The calibration report lists every tested contract and, for each, model/target provenance,
   verified bugs, cap-hit counts, usage, retries, infrastructure failures, token/cost/time
   references, and marginal yield. The chosen values exactly match the checked-in named contract.
2. At least one smaller and one larger candidate surround the selected `M` and `P` values, so the
   report demonstrates a trade-off rather than merely documenting a predetermined number.
3. Starting a rankable run with any unlimited counter, `S != 2`, custom budget, custom prompt,
   custom strategy, or coding-agent backend fails preflight before a scored model generation.
   These are the release negative controls.
4. A standard fake Top50 run completes with exactly 50 episodes and emits a schema-valid artifact
   carrying the frozen contract ID. Changing one embedded limit makes backend validation fail.
5. A forced hung model call and a forced hung `pred` child are killed by watchdogs and recorded as
   infrastructure/model outcomes according to the design; changing the watchdog duration does not
   change the named logical budget or score formula.
6. The README and leaderboard describe the primary metric as verified distinct bugs at the frozen
   Top50 evidence budget. They do not present time, cost, tokens, Fixed Top50, multiple seeds, or a
   System Track as primary ranking dimensions.
7. Historical whole-repository results remain visible under their old contract and cannot be
   compared or sorted into the new Top50 table.

Before merging the release, run the repository's required CI and calibration workflows. A PR must
not be merged until CI is green and the user has explicitly approved the merge.

### Dependencies

Depends on I1, I2, and I3.

### Out of scope

- a public budget grid or bugs-versus-budget leaderboard;
- multiple seeds and confidence intervals;
- Fixed Top50 diagnostics;
- System Track/coding-agent ranking;
- external contributor submission; and
- choosing future benchmark versions' budgets without a new calibration and version bump.

## Coverage scan

| Design area | Covered by |
|---|---|
| Logical resource model and charging semantics | I1 |
| Privilege boundary and `pred` bypass prevention | I1 |
| Per-rule two-attempt submission judge | I1 |
| Static triage and atomic Top50 freeze | I2 |
| Fresh equal-budget rule episodes and counter visibility | I2 |
| Provider/infrastructure failure semantics | I1, I2, I3 |
| Versioned private result and backend re-verification | I3 |
| Headline score, diagnostics, privacy, and historical separation | I3 |
| Internal pilot and public budget freeze | I4 |
| Prompt, preflight, docs, CI, and release migration | I4 |
| Multiple seeds, Fixed Top50, System Track | explicitly out of scope in all relevant issues |

The final scan found no uncovered first-release area from the source design. Cross-cutting
security, privacy, migration, observability, reliability, negative controls, and stale-issue
reconciliation are assigned above rather than split into additional issues.
