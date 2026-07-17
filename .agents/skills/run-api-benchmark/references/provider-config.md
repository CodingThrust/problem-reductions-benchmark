# API provider configuration

Use `submission.env.example` as the source of truth. Keep secrets in the gitignored
`submission.env`; never ask the caller to paste them into chat.

## Variables

| Variable | Purpose |
|---|---|
| `MODEL_NAME` | LiteLLM-routable provider/model identity |
| provider key or `API_KEY` | authentication |
| `API_BASE` | custom gateway or OpenAI-compatible endpoint |
| `MODEL_KWARGS` | JSON object for provider-specific LiteLLM arguments |
| `MAX_TOKENS` | per-response output ceiling, not a run/turn limit |
| `SUBMIT_LIMIT` | run-wide certificate attempts |
| `AGENT_CONFIG` | full prompt override |
| `AGENT_STRATEGY_FILE` | extra strategy injected into the shared prompt |
| `SUBMITTED_BY` | optional submitter metadata |

Do not use removed `AGENT_MODE`, `MAX_RULES`, or max-turn variables.

## Standard provider

Set `MODEL_NAME` and the provider's documented key variable, for example
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, or `GEMINI_API_KEY`.

## Custom endpoint

```ini
MODEL_NAME=openai/my-model
API_BASE=https://my-gateway.example/v1
API_KEY=...
MODEL_KWARGS={"custom_llm_provider":"openai"}
```

## Failure decoding

| Symptom | Action |
|---|---|
| `pred binary` or rule-source preflight fails | rebuild the image at the intended `PR_REF` |
| tiny model call fails | correct model routing, endpoint, key variable, or kwargs |
| build exits 137 | provision at least about 8 GB for the engine VM/host |
| `run_error` | preserve the partial submission and logs; do not call it a clean zero |
| submit channel not probed | treat as runner infrastructure failure |
