# EcoClaw-Bench

Benchmarking and reproducibility suite for **EcoClaw**.

This repository is the official place to:

- Install and run EcoClaw end-to-end
- Reproduce PinchBench baseline vs EcoClaw runs
- Store raw benchmark outputs and comparison reports
- Extend evaluation to additional datasets over time

## Scope

- Runtime under test: [EcoClaw](https://github.com/Xubqpanda/EcoClaw)
- Primary benchmark (phase 1): PinchBench-compatible `skill` tasks (recommended fork: [Xubqpanda/skill](https://github.com/Xubqpanda/skill))
- Evaluation goal: improve **token efficiency** while maintaining or improving task quality

## Repository Layout

```text
EcoClaw-Bench/
├── docs/
├── experiments/
│   ├── configs/
│   │   └── pinchbench/
│   └── scripts/
├── results/
│   ├── raw/
│   └── reports/
└── assets/
```

## Environment Setup

1. Copy `.env.example` to `.env`
2. Fill your API key and base URL

```bash
cp .env.example .env
```

Detailed variable reference: [docs/env.md](docs/env.md)

## Compatibility Notes

This repo uses a patched benchmark flow compared with upstream PinchBench scripts:

- Use the local/forked `skill` repo (set `ECOCLAW_SKILL_DIR` if needed).
- Baseline scripts support isolated parallel execution via `--parallel` / `ECOCLAW_PARALLEL`.
- Model aliases in experiment scripts are mapped to `dica/*` provider ids by default.

If your OpenClaw default model is not `dica/*`, prefer explicit full model ids in `.env`:

- `ECOCLAW_MODEL=dica/gpt-5-mini`
- `ECOCLAW_JUDGE=dica/gpt-5-nano`

This avoids silent fallback to other providers/models in mixed-provider OpenClaw configs.

## Quick Start (Linux)

1. Read [docs/install.md](docs/install.md)
2. Fill [`.env.example`](.env.example) fields in your local `.env`
3. Run baseline
4. Run EcoClaw-enabled
5. Compare outputs

Linux (bash):

- [experiments/scripts/run_pinchbench_baseline.sh](experiments/scripts/run_pinchbench_baseline.sh)
- [experiments/scripts/run_pinchbench_ecoclaw.sh](experiments/scripts/run_pinchbench_ecoclaw.sh)
- [experiments/scripts/compare_pinchbench_results.sh](experiments/scripts/compare_pinchbench_results.sh)
- [src/cost/calculate_llm_cost.py](src/cost/calculate_llm_cost.py)

Example:

```bash
./experiments/scripts/run_pinchbench_baseline.sh --suite all --parallel 4
```

Linux quick guide: [docs/linux.md](docs/linux.md)
