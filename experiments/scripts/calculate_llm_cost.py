#!/usr/bin/env python3
"""Compatibility entrypoint that forwards to src/cost/calculate_llm_cost.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_main():
    repo_root = Path(__file__).resolve().parents[2]
    src_file = repo_root / "src" / "cost" / "calculate_llm_cost.py"
    spec = importlib.util.spec_from_file_location("ecoclaw_bench_cost", str(src_file))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {src_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main


if __name__ == "__main__":
    raise SystemExit(_load_main()())
