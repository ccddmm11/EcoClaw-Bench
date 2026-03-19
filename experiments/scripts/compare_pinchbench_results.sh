#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

BASELINE_DIR="${REPO_ROOT}/results/raw/pinchbench/baseline"
ECOCLAW_DIR="${REPO_ROOT}/results/raw/pinchbench/ecoclaw"
REPORT_PATH="${REPO_ROOT}/results/reports/pinchbench_comparison.json"
LOG_DIR="${REPO_ROOT}/log"
RUN_TAG="$(date +%Y%m%d_%H%M%S)"
RUN_LOG_FILE="${LOG_DIR}/pinchbench_compare_${RUN_TAG}.log"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --baseline-dir) BASELINE_DIR="${2:-}"; shift 2 ;;
    --ecoclaw-dir) ECOCLAW_DIR="${2:-}"; shift 2 ;;
    --report-path) REPORT_PATH="${2:-}"; shift 2 ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

mkdir -p "$(dirname "${REPORT_PATH}")" "${LOG_DIR}"

python - <<'PY' "${BASELINE_DIR}" "${ECOCLAW_DIR}" "${REPORT_PATH}" "${REPO_ROOT}/src/cost/calculate_llm_cost.py" 2>&1 | tee "${RUN_LOG_FILE}"
import json
import importlib.util
import pathlib
import statistics
import sys

baseline_dir = pathlib.Path(sys.argv[1])
ecoclaw_dir = pathlib.Path(sys.argv[2])
report_path = pathlib.Path(sys.argv[3])
cost_script_path = pathlib.Path(sys.argv[4])

def latest_json(path: pathlib.Path) -> pathlib.Path:
    files = sorted(path.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No JSON files found in {path}")
    return files[0]

def mean_score(payload: dict) -> float:
    vals = [float(t["grading"]["mean"]) for t in payload["tasks"]]
    return statistics.mean(vals) if vals else 0.0

def build_cost_report(input_file: pathlib.Path, payload: dict) -> dict:
    spec = importlib.util.spec_from_file_location("calculate_llm_cost", str(cost_script_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load cost script: {cost_script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_report(input_file, payload)

baseline_file = latest_json(baseline_dir)
ecoclaw_file = latest_json(ecoclaw_dir)

baseline = json.loads(baseline_file.read_text(encoding="utf-8"))
ecoclaw = json.loads(ecoclaw_file.read_text(encoding="utf-8"))
baseline_cost = build_cost_report(baseline_file, baseline)
ecoclaw_cost = build_cost_report(ecoclaw_file, ecoclaw)

baseline_mean = mean_score(baseline)
ecoclaw_mean = mean_score(ecoclaw)

report = {
    "baseline_file": str(baseline_file),
    "ecoclaw_file": str(ecoclaw_file),
    "baseline": {
        "mean_score": round(baseline_mean, 6),
        "total_tokens": baseline["efficiency"].get("total_tokens"),
        "total_cost_usd": baseline["efficiency"].get("total_cost_usd"),
        "total_cost_cny": baseline_cost["totals"].get("cost_cny"),
        "score_per_1k_tokens": baseline["efficiency"].get("score_per_1k_tokens"),
        "score_per_dollar": baseline["efficiency"].get("score_per_dollar"),
    },
    "ecoclaw": {
        "mean_score": round(ecoclaw_mean, 6),
        "total_tokens": ecoclaw["efficiency"].get("total_tokens"),
        "total_cost_usd": ecoclaw["efficiency"].get("total_cost_usd"),
        "total_cost_cny": ecoclaw_cost["totals"].get("cost_cny"),
        "score_per_1k_tokens": ecoclaw["efficiency"].get("score_per_1k_tokens"),
        "score_per_dollar": ecoclaw["efficiency"].get("score_per_dollar"),
    },
    "deltas": {
        "mean_score": ecoclaw_mean - baseline_mean,
        "total_tokens": ecoclaw["efficiency"].get("total_tokens", 0) - baseline["efficiency"].get("total_tokens", 0),
        "total_cost_usd": ecoclaw["efficiency"].get("total_cost_usd", 0.0) - baseline["efficiency"].get("total_cost_usd", 0.0),
        "total_cost_cny": ecoclaw_cost["totals"].get("cost_cny", 0.0) - baseline_cost["totals"].get("cost_cny", 0.0),
    },
    "cost_reports": {
        "baseline": baseline_cost,
        "ecoclaw": ecoclaw_cost,
    },
}

report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(f"Comparison report written to: {report_path}")
PY

echo "Compare log saved to: ${RUN_LOG_FILE}"
