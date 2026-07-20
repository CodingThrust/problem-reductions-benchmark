# API provider configuration

Use `submission.env.example` as the source of truth. Keep secrets in the gitignored
`submission.env`; never ask the caller to paste them into chat.

This configuration is for the containerized model API runner.

## Variables

| Variable | Purpose |
|---|---|
| `MODEL_NAME` | LiteLLM-routable provider/model identity |
| provider key or `API_KEY` | authentication |
| `API_BASE` | custom gateway or OpenAI-compatible endpoint |
| `SUBMITTED_BY` | optional submitter metadata |

Prompts, inference settings, the execution harness, and logical budgets are benchmark-owned.

## Standard provider

Set `MODEL_NAME` and the provider's documented key variable, for example
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, or `GEMINI_API_KEY`.

## Custom endpoint

```ini
MODEL_NAME=openai/my-model
API_BASE=https://my-gateway.example/v1
API_KEY=...
```

## Failure decoding

| Symptom | Action |
|---|---|
| `pred binary` or rule-source preflight fails | rebuild the image at the intended `PR_REF` |
| tiny model call fails | correct model routing, endpoint, or key variable |
| build exits 137 | provision at least about 8 GB for the engine VM/host |
| `run_error` | preserve the partial submission; do not call it a clean zero |
