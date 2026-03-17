# Installation Guide

## 1) Clone required repositories

```bash
git clone https://github.com/Xubqpanda/EcoClaw.git D:/EcoClaw
git clone https://github.com/pinchbench/skill.git D:/skill
git clone https://github.com/Xubqpanda/EcoClaw-Bench.git D:/EcoClaw-Bench
```

## 2) Install prerequisites

- Python 3.10+
- `uv` package manager
- OpenClaw/EcoClaw runtime dependencies

PinchBench side:

```bash
cd D:/skill
uv sync
```

EcoClaw side:

```bash
cd D:/EcoClaw
npm install
```

## 3) Start OpenClaw/EcoClaw runtime

Use your normal OpenClaw startup command and ensure the agent endpoint is reachable before running benchmarks.

## 4) Configure environment

```bash
cd D:/EcoClaw-Bench
cp .env.example .env
```

Then edit `.env` and fill your values:

- `ECOCLAW_API_KEY`
- `ECOCLAW_BASE_URL`
- `ECOCLAW_MODEL`
- `ECOCLAW_JUDGE`

See [env.md](env.md) for details.

## 5) Run benchmark scripts

From `D:/EcoClaw-Bench`, execute scripts in `experiments/scripts/`.
