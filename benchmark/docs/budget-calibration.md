# Top50 budget calibration

Contract: `top50-evidence/v2` (`frozen`)

This is a human-reviewed, non-ranking bounded-prefix replay record from internally retained pilot trajectories. Raw trajectories remain private; this offline checker validates the checked-in record and release consistency, not the raw replay. It is not a public score, a multi-seed experiment, or a claim that elapsed time is model ability.

## Selected contract

The release freezes M=10 model generations, P=24 total `pred` calls, P_solve=10 solve calls, S=2 submit attempts, E=12 shell actions, and O=10000 automatically previewed characters per action. Triage is T=8 generations and E_t=12 source-only actions.

Terminal output uses `terminal-diagnostics/v1` with a 10000-character automatic preview and a bounded 1048576-character raw archive per command.

Model calls have a fixed 300-second watchdog and 2 transport retries. Command and `pred` watchdogs are also fixed safety controls. They are recorded but are outside the logical budget and never enter the score.

## Measured grid

| Model | M | P | Bugs | M cap rate | P cap rate | Tokens | Retries | Infra failures | Marginal yield |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| anthropic/claude-haiku-4-5 | 6 | 12 | 0 | 62% | 48% | 1423721 | unavailable | 0 | 0.000 |
| anthropic/claude-haiku-4-5 | 6 | 24 | 0 | 62% | 14% | 1615376 | unavailable | 0 | 0.000 |
| anthropic/claude-haiku-4-5 | 6 | 36 | 0 | 62% | 2% | 1807031 | unavailable | 0 | 0.000 |
| anthropic/claude-haiku-4-5 | 10 | 12 | 0 | 22% | 48% | 1587997 | unavailable | 0 | 0.000 |
| anthropic/claude-haiku-4-5 | 10 | 24 | 0 | 22% | 14% | 1779651 | unavailable | 0 | 0.000 |
| anthropic/claude-haiku-4-5 | 10 | 36 | 0 | 22% | 2% | 1971306 | unavailable | 0 | 0.000 |
| anthropic/claude-haiku-4-5 | 14 | 12 | 0 | 6% | 48% | 1752272 | unavailable | 0 | 0.000 |
| anthropic/claude-haiku-4-5 | 14 | 24 | 0 | 6% | 14% | 1943927 | unavailable | 0 | 0.000 |
| anthropic/claude-haiku-4-5 | 14 | 36 | 0 | 6% | 2% | 2135581 | unavailable | 0 | 0.000 |
| anthropic/claude-sonnet-5 | 6 | 12 | 0 | 62% | 48% | 6109805 | unavailable | 0 | 0.000 |
| anthropic/claude-sonnet-5 | 6 | 24 | 0 | 62% | 14% | 6932279 | unavailable | 0 | 0.000 |
| anthropic/claude-sonnet-5 | 6 | 36 | 0 | 62% | 2% | 7754753 | unavailable | 0 | 0.000 |
| anthropic/claude-sonnet-5 | 10 | 12 | 0 | 22% | 48% | 6814783 | unavailable | 0 | 0.000 |
| anthropic/claude-sonnet-5 | 10 | 24 | 1 | 22% | 14% | 7637256 | unavailable | 0 | 1.000 |
| anthropic/claude-sonnet-5 | 10 | 36 | 1 | 22% | 2% | 8459730 | unavailable | 0 | 0.000 |
| anthropic/claude-sonnet-5 | 14 | 12 | 0 | 6% | 48% | 7519760 | unavailable | 0 | 0.000 |
| anthropic/claude-sonnet-5 | 14 | 24 | 1 | 6% | 14% | 8342234 | unavailable | 0 | 0.000 |
| anthropic/claude-sonnet-5 | 14 | 36 | 1 | 6% | 2% | 9164707 | unavailable | 0 | 0.000 |

Token, cost, and elapsed-time fields are diagnostic references only. Missing provider cost or reliable wall-clock data is recorded as unavailable instead of imputed.

## Decision

M=10 and P=24 are the smallest tested values at which the replayed stronger pilot retains its verified find. Moving down to M=6 or P=12 loses that find and shows materially higher cap pressure; moving up to M=14 or P=36 adds usage without another verified bug. E=12 and P_solve=10 cover the observed command mix without exposing an unlimited path. S=2 follows the fixed per-rule retry policy. T=8 and E_t=12 bound source-only shortlist formation. Output and watchdog ceilings are safety controls selected above observed pilot maxima and do not affect ranking.

The public comparison is therefore one run at this single contract, ranked only by verified distinct-rule bugs. Fixed Top50, multiple seeds, a System Track, and a public budget grid remain out of scope.

## Provenance

- `32237111e946206e42bb7ee36c54c09eb1d5313c8fcd4d75c2f440e94bee96d4` — anthropic/claude-haiku-4-5, problem-reductions@aa2d1a10cffa434871d12a4d6f411147fb7e08a8, private legacy pilot trajectory; aggregate bounded-prefix replay retained here
- `ac475bb2cc7c6118f6405b63fc52703f4afc27588f0f4cd021d36328222316d3` — anthropic/claude-sonnet-5, problem-reductions@aa2d1a10cffa434871d12a4d6f411147fb7e08a8, private legacy pilot trajectory; aggregate bounded-prefix replay retained here
