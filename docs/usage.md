# Usage Guide

This repo supports two evaluation modes:

1. **Baseline**: OpenClaw behavior without EcoClaw optimization modules.
2. **EcoClaw**: OpenClaw behavior with EcoClaw optimization modules enabled.

Both runs should use:

- same model id
- same task suite
- same judge model
- same number of runs

Then compare `results/raw/*` with the provided comparison script.

## Run Commands (Linux)

```bash
./experiments/scripts/run_pinchbench_baseline.sh --suite task_00_sanity --runs 1
./experiments/scripts/run_pinchbench_ecoclaw.sh --suite task_00_sanity --runs 1
./experiments/scripts/compare_pinchbench_results.sh
```

Each run script now auto-generates a cost report in `results/reports/` and prints:

- per-model cost breakdown
- total cost (USD/CNY)

## Cost Report (CNY)

Use the dataset-agnostic cost calculator on any result JSON:

```bash
python ./src/cost/calculate_llm_cost.py \
  --input ./results/raw/pinchbench/baseline \
  --output ./results/reports/baseline_cost_cny.json \
  --cache-write-ttl 5m
```

Notes:

- Cost is computed from per-call `llm_calls` token fields.
- Supports mixed-model/router runs and aggregates by model + task.
- Supports OpenAI and Claude pricing.
- Claude cache write pricing supports `--cache-write-ttl {5m,1h}`.
- OpenAI `cache_write_tokens` is priced using the same rate as input tokens.
- Unknown models are excluded from `cost_cny` totals and listed in `unknown_models`.
