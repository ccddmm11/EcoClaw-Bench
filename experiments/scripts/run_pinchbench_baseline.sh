#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

MODEL=""
JUDGE=""
SUITE=""
RUNS=""
TIMEOUT_MULTIPLIER=""
PARALLEL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL="${2:-}"; shift 2 ;;
    --judge) JUDGE="${2:-}"; shift 2 ;;
    --suite) SUITE="${2:-}"; shift 2 ;;
    --runs) RUNS="${2:-}"; shift 2 ;;
    --timeout-multiplier) TIMEOUT_MULTIPLIER="${2:-}"; shift 2 ;;
    --parallel) PARALLEL="${2:-}"; shift 2 ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

import_dotenv
apply_ecoclaw_env

MODEL_LIKE="${MODEL:-${ECOCLAW_MODEL:-claude-sonnet-4}}"
JUDGE_LIKE="${JUDGE:-${ECOCLAW_JUDGE:-claude-opus-4.1}}"
RESOLVED_MODEL="$(resolve_model_alias "${MODEL_LIKE}")"
RESOLVED_JUDGE="$(resolve_model_alias "${JUDGE_LIKE}")"
RESOLVED_SUITE="${SUITE:-${ECOCLAW_SUITE:-automated-only}}"
RESOLVED_RUNS="${RUNS:-${ECOCLAW_RUNS:-3}}"
RESOLVED_TIMEOUT="${TIMEOUT_MULTIPLIER:-${ECOCLAW_TIMEOUT_MULTIPLIER:-1.0}}"
RESOLVED_PARALLEL="${PARALLEL:-${ECOCLAW_PARALLEL:-1}}"

OUTPUT_DIR="${REPO_ROOT}/results/raw/pinchbench/baseline"
LOG_DIR="${REPO_ROOT}/log"
RUN_TAG="$(date +%Y%m%d_%H%M%S)"
RUN_LOG_FILE="${LOG_DIR}/pinchbench_baseline_${RUN_TAG}.log"
BENCHMARK_LOG_FILE="${LOG_DIR}/pinchbench_baseline_${RUN_TAG}_benchmark.log"
mkdir -p "${OUTPUT_DIR}" "${LOG_DIR}"

SKILL_DIR="$(resolve_skill_dir)"
cd "${SKILL_DIR}"
uv run scripts/benchmark.py \
  --model "${RESOLVED_MODEL}" \
  --judge "${RESOLVED_JUDGE}" \
  --suite "${RESOLVED_SUITE}" \
  --runs "${RESOLVED_RUNS}" \
  --parallel "${RESOLVED_PARALLEL}" \
  --timeout-multiplier "${RESOLVED_TIMEOUT}" \
  --output-dir "${OUTPUT_DIR}" \
  --no-upload \
  2>&1 | tee "${RUN_LOG_FILE}"

if [[ -f "${SKILL_DIR}/benchmark.log" ]]; then
  cp "${SKILL_DIR}/benchmark.log" "${BENCHMARK_LOG_FILE}"
fi

echo "Run log saved to: ${RUN_LOG_FILE}"
if [[ -f "${BENCHMARK_LOG_FILE}" ]]; then
  echo "Benchmark log saved to: ${BENCHMARK_LOG_FILE}"
fi

RESULT_JSON="$(latest_json_in_dir "${OUTPUT_DIR}" || true)"
if [[ -n "${RESULT_JSON}" ]]; then
  COST_REPORT_DIR="${REPO_ROOT}/results/reports"
  COST_REPORT_FILE="${COST_REPORT_DIR}/baseline_${RUN_TAG}_cost.json"
  mkdir -p "${COST_REPORT_DIR}"
  generate_cost_report_and_print_summary "${RESULT_JSON}" "${COST_REPORT_FILE}"
else
  echo "Cost report skipped: no result JSON found in ${OUTPUT_DIR}" >&2
fi
