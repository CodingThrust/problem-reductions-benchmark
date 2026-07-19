# Top-50 Evidence-Budget Benchmark — Product Design

Status: proposed for the next benchmark version  
Last updated: 2026-07-20

## Need

The current benchmark gives a model the complete `problem-reductions` repository and lets
it decide how broadly and deeply to search and when to stop. The runner limits submissions,
but it does not limit model turns, shell actions, or calls to `pred`. Consequently, two runs
may receive radically different amounts of feedback from the target system. Wall-clock time
is not a suitable correction: the model cannot budget against it reliably, and it also
contains provider latency, retries, network delay, host load, and harness overhead.

The next benchmark version must compare models under the same **logical opportunities to
obtain evidence**, rather than under the same elapsed time. A model first selects the 50
rules it considers most likely to contain bugs, then investigates every selected rule with
the same non-transferable per-rule budget. The primary result remains the number of distinct
rules for which the evaluation-owned verifier accepts a counterexample.

The benchmark is for researchers and developers comparing base models. It has one
standardized Model Track, using one reference harness and one frozen evaluation contract.
It intentionally does not compare rapidly changing coding-agent products or custom agent
systems.

Success means that:

- a longer-running session cannot obtain more target-system feedback than a shorter one;
- shell scripts, loops, and parallel subprocesses cannot bypass the `pred` or submission
  budgets;
- all 50 selected rules receive the same investigation allowance;
- infrastructure failures are distinguishable from model failures and never become clean
  zero scores;
- the score has a narrow, defensible interpretation: verified bug-finding yield under a
  fixed evidence budget.

## Prior art and landscape

The design follows a common pattern in agent evaluation: bound interaction opportunities
directly and retain timeouts only as safety mechanisms.

- [METR's time-horizon methodology](https://metr.org/time-horizons/) notes that exact agent
  runtime varies with the inference provider and agent setup and is not itself the reported
  capability measure.
- [Inspect](https://inspect.aisi.org.uk/agent-custom.html) exposes separate limits for
  messages, tokens, turns, time, working time, and cost. Its working-time accounting excludes
  delays such as rate-limit waiting, illustrating why resource dimensions should not be
  collapsed into elapsed time.
- [InterCode](https://proceedings.neurips.cc/paper_files/paper/2023/file/4b175d846fb008d540d233c188379ff9-Paper-Datasets_and_Benchmarks.pdf)
  bounds interaction turns and observation size. [OSWorld](https://github.com/xlang-ai/OSWorld)
  and [OpenCUA](https://github.com/xlang-ai/OpenCUA) likewise publish results at explicit
  action-step budgets.
- [tau2-bench](https://github.com/sierra-research/tau2-bench/blob/main/docs/cli-reference.md)
  separates maximum steps, maximum errors, retries, trials, and an optional wall-clock
  timeout instead of treating time as the sole budget.
- [SWE-agent](https://swe-agent.com/latest/usage/competitive_runs/) recommends a per-instance
  cost or turn limit because otherwise individual instances can consume unbounded resources.
- [AI Agents That Matter](https://arxiv.org/abs/2407.01502) argues that agent results should
  expose capability and resource use jointly, including Pareto-style comparisons, instead
  of hiding unequal evaluation cost.
- Systems such as [AlphaCode 2](https://deepmind.google/AlphaCode2_Tech_Report.pdf) separate
  large internal search from a small external submission allowance. This motivates separate
  ledgers for `pred` feedback and formal bug submissions.

Existing repository components provide useful building blocks:

- `SubmissionSession` already owns an evaluation-side, atomic, append-only submission
  ledger and an agent-facing file-spool transport.
- `verify.py` already applies payload, CPU, memory, output, and solver watchdog limits.
- API and CLI runners already share prompts and normalized result packaging.

The repository does not yet provide an evaluation-owned `pred` gateway, per-rule budgets,
isolated per-rule episodes, shortlist freezing, or a distinction between verifier
infrastructure errors and rejected model submissions. Those gaps belong to this benchmark.

## Features

### Selected for the first release

| Feature | User value | Rough implementation effort |
|---|---|---:|
| Static Top-50 triage and shortlist freezing | Measures risk prioritization without allowing dynamic experiments before commitment | 1–2 days |
| Fifty isolated per-rule investigation episodes | Gives every selected rule the same opportunity and removes order and long-context effects | 3–5 days |
| Evaluation-owned `pred`/`solve` gateway | Makes all target-system feedback countable and prevents script/parallelism bypasses | 2–4 days |
| Two submissions **per selected rule** | Allows one formal attempt and one feedback-driven correction without creating an unbounded proof loop | 1–2 days |
| Visible remaining-budget counters | Lets the model plan in logical units it can observe | about 1 day |
| Single-run scoring and audit schema | Produces one comparable, re-verifiable result under a named budget contract | 2–3 days |
| Internal budget-calibration pilot | Selects defensible turn and `pred` limits before the public contract is frozen | 2–4 days plus inference cost |

The estimates are implementation-order estimates, not elapsed calendar commitments. The
existing submission and verification machinery is reused where possible.

### Deferred

#### Multiple seeds

The first release records any available seed and sampling parameters but does not require
repeated runs, compute means or confidence intervals, or rank aggregate results. This can be
added after the single-run contract and its operating cost are understood.

#### Fixed Top-50 diagnostic

A future diagnostic may give every model the same 50 rules to isolate investigation ability
from risk-ranking ability. It is not required for the main benchmark, which deliberately
measures the combined workflow, and is deferred to keep the first release small.

### Dropped

#### System Track

There is no leaderboard for Codex CLI, Claude Code, custom prompts, memory systems,
subagents, or other agent products. These systems change too quickly, attract less interest
for this benchmark, and confound model capability with orchestration. Existing CLI adapters
may remain useful for development, but their runs are not rankable in this track.

#### Wall-clock, token, cost, or best-of-N ranking

Wall-clock time remains a watchdog and an auxiliary measurement. Tokens and monetary cost
are reported when available. None is a primary score or tie-breaker, and ties in verified
bug count remain ties. The first release has no best-of-N selection.

## Evaluation contract

### Track

The benchmark has one rankable track: **Standardized Model Track**. Rankable runs use the
same:

- reference API harness and harness version;
- system and task prompts;
- pinned library repository and `pred` version;
- rule inventory;
- tool surface and network policy;
- triage and per-rule budgets;
- maximum model output and observation sizes;
- reasoning/sampling configuration, to the extent the model API exposes it; and
- submission verifier.

The result identifies the model, provider/endpoint class, runner version, library commit,
budget-contract version, and exposed inference parameters. A custom coding-agent harness is
outside this track even if it uses the same underlying model.

Rankable runs are produced and submitted only by the trusted internal team. The first release
does not accept untrusted external runs and therefore does not build remote execution or
cryptographic runner attestation. The backend still re-verifies bugs and ledger consistency,
but the operator trust boundary is explicit.

### Phase 0: preflight

Before any scored model generation, the runner checks the pinned repository, rule inventory,
`pred` gateway, and submission judge. Status and version probes are cached and free. If any
required service is unavailable, the run ends as `run_error` and is not rankable.

### Phase 1: static triage

The model receives the pinned repository and a canonical, cached inventory of all runnable
rules. It may inspect source code with ordinary read-only shell tools under a fixed triage
generation/action/observation budget. Dynamic `pred` operations and `submit` are unavailable
during this phase.

Before the triage budget expires, the model must commit an ordered list of exactly 50 unique,
valid rule identifiers. It may attach a short, size-capped risk hypothesis for each rule.
The runner validates and freezes this artifact. The list cannot change after investigation
begins.

Failure to produce 50 valid unique rules is an invalid run, not a 0-bug result. This avoids
silently comparing a 50-rule run with a smaller task.

The order is retained for audit and future diagnostics such as Bugs@10 and Bugs@25, but the
first-release headline score uses all 50 rules.

### Phase 2: isolated investigation

The runner creates one fresh episode for each frozen rule. Every episode has:

- a fresh model conversation and writable scratch directory;
- the same pinned read-only repository;
- the current rule identifier and its capped triage hypothesis;
- the same per-rule model-generation, shell-action, `pred`, `solve`, observation, and
  submission limits; and
- a submission judge that accepts certificates only for the current rule.

No conversation, scratch files, remaining budget, or model-authored memory transfers between
rules. Unused allowance is not transferable. The episodes may execute sequentially or in
parallel as an implementation detail, provided the model and infrastructure configuration is
identical and each ledger remains isolated.

An accepted certificate ends that rule's episode immediately. Exhausting a counter leaves
the episode as completed without an accepted bug; it does not borrow from another rule.

### Logical resource vector

The frozen public contract is named by a vector rather than a time allowance:

`Top50[T, E_t, M, E, P, P_solve, S=2, O]`

where:

- `T` is the number of usable model generations in static triage;
- `E_t` is the number of executed triage shell actions;
- `M` is the number of usable model generations per selected rule;
- `E` is the number of executed shell actions per selected rule;
- `P` is the number of agent-initiated dynamic `pred` invocations per selected rule;
- `P_solve` is the subset of `P` that may invoke `pred solve`;
- `S` is the number of submission opportunities per selected rule, fixed initially at 2;
  and
- `O` contains fixed observation, command-output, and payload-size ceilings.

`S=2` is a per-rule ledger. With 50 selected rules, a run can therefore make at most 100
charged submissions, but there is no run-wide pool from which one rule can take another
rule's unused attempts. Once a rule is accepted, its unused submission opportunity expires.

The exact values of `T`, `E_t`, `M`, `E`, `P`, `P_solve`, and `O` are selected by the internal
calibration pilot and then frozen with the benchmark version.

Every observation includes a compact machine-generated status block, for example:

```text
rule 17/50: maximum_independent_set_to_clique
model generations: 6/10
shell actions: 6/10
pred calls: 13/24
solve calls: 5/10
submit attempts: 1/2
```

This is the model's planning signal. Wall-clock time is not.

## Modules

### 1. Inventory and Triage Controller

Purpose: provide a canonical rule inventory, run static analysis, validate the shortlist,
and freeze the ordered Top50 artifact.

Interface:

- input: pinned repository metadata and triage budget contract;
- output: an immutable ordered list of 50 rule IDs plus capped hypotheses and a triage
  ledger;
- dependencies: reference model harness, read-only repository, budget controller.

### 2. Episode Runner

Purpose: create 50 fresh, equal-budget investigation episodes and combine their immutable
ledgers without sharing model state or resources.

Interface:

- input: frozen Top50 artifact and per-rule budget contract;
- output: 50 episode records with completion, accepted bug, or infrastructure-error status;
- dependencies: model harness, `PredGateway`, `SubmitJudge`, observation renderer.

### 3. Pred Gateway

Purpose: be the only executable route to dynamic `pred` behavior and atomically enforce `P`
and `P_solve` for the current episode.

Interface:

- agent command: a `pred`-compatible shim;
- runner API: status, execute, ledger, and health operations;
- output: idempotent response plus updated per-rule counters;
- dependencies: hidden real `pred` binary, pinned execution environment, file-spool or RPC
  transport.

### 4. Submit Judge

Purpose: enforce exactly two non-transferable formal attempts for the current rule and
retain the verifier-authoritative certificate ledger.

Interface:

- agent command: `submit <certificate>` and free `submit --status`;
- runner API: status, verify, ledger, and health operations;
- output: accepted, rejected, exhausted, or infrastructure-error response;
- dependencies: existing certificate verifier and hidden internal `pred` access.

### 5. Scoring and Calibration

Purpose: validate rankability, compute the single-run headline score, expose audit metrics,
and determine the fixed public budgets before release.

Interface:

- input: triage ledger, 50 episode ledgers, inference usage, and run metadata;
- output: versioned submission artifact and internal pilot analysis;
- dependencies: schema validator and backend re-verifier.

## Technical approaches

### Inventory and triage

**Chosen: one bounded static-analysis session followed by an atomic shortlist commit.** This
directly measures the desired prioritization behavior while preventing the model from using
dynamic fuzzing to decide what belongs in the Top50.

Alternatives considered:

- Ask the model to score every rule independently. This is easier to normalize per rule but
  converts strategic prioritization into 290+ expensive microtasks and loses cross-rule
  comparison in one context.
- Use a benchmark-authored heuristic to prefilter candidates. This is cheaper but removes a
  central model ability from the task and risks encoding maintainer bias.

### Episode execution

**Chosen: 50 fresh, equal-budget, per-rule episodes.** This ensures that all shortlisted
rules receive the same opportunity and that later rules are not disadvantaged by context
growth or earlier spending.

Alternatives considered:

- One global investigation session with a run-wide pool. This measures adaptive allocation,
  but recreates unequal rule coverage and makes scores difficult to interpret.
- A global pool plus a per-rule ceiling. This blocks extreme concentration but still makes a
  miss ambiguous: the model may be weak on the rule or simply have allocated less budget.

### Pred enforcement

**Chosen: an evaluation-owned shim and service modeled on `SubmissionSession`.** The real
binary is not placed directly on the agent's `PATH`; the shim atomically reserves budget
before spawning it. All agent shells, Python programs, loops, and parallel subprocesses
share the same per-episode ledger.

Alternatives considered:

- Infer calls from shell commands or trajectories. One shell command can start hundreds of
  subprocesses, so this is neither complete nor enforceable.
- Expose the real binary and ask the agent to self-report. This is unauditable and permits
  accidental bypasses.

Simple invocation counting is preferred over weighted command costs. Each agent-initiated
`create`, `reduce`, `solve`, `evaluate`, or `extract` consumes one `P`; `solve` also consumes
one `P_solve`. Cached inventory, version, help, and status operations are free. `pred` calls
made internally by the submission verifier do not consume the agent's investigation budget.

Model-authored automation remains allowed: writing a fuzzer or batch script is useful agent
behavior. Its actual gateway invocations are charged individually, so automation is an
ability rather than a loophole.

### Submission enforcement

**Chosen: adapt the existing evaluation-owned ledger from run-wide accounting to one ledger
per rule.** A rejected, malformed, duplicate, wrong-rule, or model-caused verifier timeout
consumes one of that rule's two opportunities. `submit --status` is free. Acceptance closes
the episode.

Pure verifier, gateway, container, or transport failures do not consume an opportunity.
Requests carry stable idempotency keys; retrying the same request after a lost response
returns the stored result instead of charging again. An unrecoverable judge failure marks the
run unrankable rather than converting the episode into a rejection.

Alternatives considered:

- Keep one shared 100-attempt run pool. Although its total matches 50 × 2, it lets one rule
  consume attempts intended for another and violates the equal-opportunity contract.
- Allow only one attempt. This measures first-shot rigor, but provides no bounded opportunity
  to use verifier feedback to correct a nearly valid certificate.
- Allow attempts until acceptance. This rewards persistence with unequal external feedback
  and recreates the original bias.

### Scoring

**Chosen: count distinct accepted rules under the complete frozen budget vector.** The
headline metric is:

`VerifiedBugs@Top50[T,E_t,M,E,P,P_solve,S=2,O]`

It is the number from 0 to 50 of selected rules with at least one certificate accepted by the
evaluation-owned verifier. It is reported for one run in the first release. Equal counts are
ties.

Audit and efficiency fields include:

- ordered Top50 and hypotheses;
- Bugs@10, Bugs@25, and Bugs@50 along the frozen shortlist order;
- accepted on submission 1 versus submission 2;
- `pred` and `solve` calls per accepted bug;
- cap-hit counts for each logical resource;
- token usage, monetary cost when available, and elapsed time; and
- all infrastructure retries and errors.

These fields explain a run but do not override the headline ranking.

Because there is no exhaustive ground truth for which rules are buggy, the benchmark must
not call unaccepted rules clean or report Top50 precision/recall. It measures verified bug
discovery yield, not complete bug prevalence.

### Internal budget calibration

**Chosen: run a small pre-release budget grid on development data, then freeze one contract.**
Candidate values may start around `M ∈ {6, 10, 14}` and `P ∈ {8, 16, 32}`, with a separate
`P_solve` ceiling. These are pilot coordinates, not proposed public values.

The pilot uses an older library snapshot or other non-ranking development target and a small
set of available models. It examines:

- verified bugs as each budget increases;
- the fraction of episodes hitting each cap;
- marginal bugs gained per additional generation or `pred` call;
- infrastructure failure rate; and
- whether one counter consistently binds long before the others.

The selected contract should sit near the useful-yield knee: large enough that most episodes
can conduct a meaningful experiment, but before extra calls mostly add cost. The chosen
values and rationale are published and versioned. Formal leaderboard runs execute only that
one frozen budget, not the grid. The pilot does not introduce a multi-seed requirement.

## Counter and error semantics

Counters are charged for opportunities controlled by the model and retried for failures
controlled by the evaluation infrastructure.

| Event | Counter effect | Result semantics |
|---|---|---|
| Provider returns a usable model response | consume one `T` or `M` | response may lead to an action |
| Model emits invalid action/format | consume one `T` or `M`; no shell action if none executes | model failure |
| API 429/5xx/disconnect before any usable response | no model-generation charge; fixed infrastructure retry policy | unrecoverable exhaustion makes run unrankable |
| Shell command starts | consume one `E_t` or `E` | nonzero exit and model-caused timeout still consume |
| Dynamic `pred` request is admitted | atomically consume one `P`; `solve` also consumes `P_solve` | invalid arguments, bad model-authored input, nonzero exit, and target timeout consume |
| Gateway fails before admitting the request | no charge; retry idempotently | unrecoverable failure makes run unrankable |
| Submission is admitted for verification | atomically consume one of the current rule's two `S` | malformed, rejected, duplicate, and wrong-rule attempts consume |
| Judge infrastructure fails | no `S` charge; retry the same request idempotently | unrecoverable failure makes run unrankable |
| Status/help/version/cached inventory probe | free | rate-limited separately if abuse protection is needed |

A reservation is written before the underlying process starts so parallel requests cannot
overspend. Every ledger record includes episode ID, rule ID, request ID, command class,
counter values before and after, outcome class, and timestamps for auditing. Timestamps are
not part of the score.

## Quality requirements

### Fairness and reproducibility

- Budget values, prompts, schemas, repository commit, `pred`, harness, and verifier are
  immutable within a benchmark version.
- All 50 per-rule episodes receive identical non-transferable limits.
- The real `pred` binary and verifier-only interfaces are inaccessible to the agent.
- Network access from the agent environment is disabled; provider access remains in the
  runner.
- The execution image, host class, watchdogs, memory limits, and file/output ceilings are
  fixed for official runs and recorded where relevant.

### Reliability

- Budget and result ledgers are evaluation-owned, append-only, thread-safe, and idempotent.
- Crashes preserve accepted certificates and audit logs, but any incomplete infrastructure
  run is marked unrankable rather than published as a clean partial score.
- Free preflight probes must succeed before scored generations begin.
- Watchdogs remain on model calls, commands, `pred`, verification, and the complete run to
  terminate wedged processes; they protect the service and do not define capability.

### Compatibility

- The agent-facing `pred` shim preserves the documented CLI shape wherever possible so
  existing prompts and model-authored scripts continue to work.
- Result schema changes are tied to a benchmark-version bump. Older whole-repository
  submissions remain readable as historical results but are not mixed into the new ranking.

## What the benchmark can claim

Under a standardized harness and fixed logical budget, the score reflects a combination of:

- static risk ranking across a large rule library;
- source-code understanding;
- high-information experiment design;
- counterexample construction and minimization;
- interpretation of bounded `pred` and verifier feedback;
- disciplined use of limited formal submissions; and
- end-to-end reliability in producing verifier-accepted evidence.

It does not isolate pure model reasoning from all provider implementation details, equalize
hidden inference FLOPs, measure complete recall over all bugs, compare custom agent systems,
or rank network and runtime speed.

## Acceptance criteria

The first release is ready when automated tests demonstrate that:

1. triage cannot call dynamic `pred` or `submit`, and only an exact valid Top50 can start
   investigation;
2. all 50 episodes start with identical counters and isolated model/scratch state;
3. direct shell use, Python subprocesses, loops, and concurrent requests cannot exceed `P`
   or `P_solve`;
4. no rule can make more than two charged submissions or use another rule's unused attempt;
5. accepted certificates close their episode and score at most one bug per rule;
6. model-caused invalid operations charge the documented counters;
7. injected provider, gateway, transport, and verifier infrastructure failures follow the
   documented no-charge/idempotent behavior and make unrecoverable runs unrankable;
8. every observation exposes authoritative remaining counters;
9. the backend recomputes the headline score only from accepted evaluation-owned ledgers;
   and
10. the published result identifies the complete frozen budget contract and necessary
    provenance.

## Open questions before implementation

These are intentionally resolved by implementation discovery or the calibration pilot, not
by arbitrary values in the design:

- final numeric values for `T`, `E_t`, `M`, `E`, `P`, `P_solve`, and `O`;
- the smallest stable inference-parameter contract shared by supported model APIs;
- migration and display treatment for historical unlimited whole-repository results.
