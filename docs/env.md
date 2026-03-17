# Environment Variables

Create a local `.env` file at repo root (do not commit it).

Use `.env.example` as template.

## Required

- `ECOCLAW_API_KEY`: your API key
- `ECOCLAW_BASE_URL`: your OpenAI-compatible gateway URL

Example from your setup:

```env
ECOCLAW_API_KEY=sk-xxx
ECOCLAW_BASE_URL=https://concept.dica.cc/llm
```

## Model Selection

Set model by alias or full model id:

```env
ECOCLAW_MODEL=gpt-5-mini
ECOCLAW_JUDGE=claude-opus-4.1
```

Supported aliases are defined in:

- `experiments/scripts/common.ps1`
- `experiments/configs/models/litellm_model_list.yaml`

## Optional

- `ECOCLAW_SUITE` (default `automated-only`)
- `ECOCLAW_RUNS` (default `3`)
- `ECOCLAW_TIMEOUT_MULTIPLIER` (default `1.0`)
