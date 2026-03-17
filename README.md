# EcoClaw-Bench

Benchmarking and reproducibility suite for **EcoClaw**.

This repository is the official place to:

- Install and run EcoClaw end-to-end
- Reproduce PinchBench baseline vs EcoClaw runs
- Store raw benchmark outputs and comparison reports
- Extend evaluation to additional datasets over time

## Scope

- Runtime under test: [EcoClaw](https://github.com/Xubqpanda/EcoClaw)
- Primary benchmark (phase 1): [PinchBench skill/tasks](https://github.com/pinchbench/skill)
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

## Quick Start

1. Read [docs/install.md](docs/install.md)
2. Fill [`.env.example`](.env.example) fields in your local `.env`
3. Run baseline: [experiments/scripts/run_pinchbench_baseline.ps1](experiments/scripts/run_pinchbench_baseline.ps1)
4. Run EcoClaw-enabled: [experiments/scripts/run_pinchbench_ecoclaw.ps1](experiments/scripts/run_pinchbench_ecoclaw.ps1)
5. Compare outputs: [experiments/scripts/compare_pinchbench_results.ps1](experiments/scripts/compare_pinchbench_results.ps1)
