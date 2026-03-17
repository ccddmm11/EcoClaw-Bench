param(
  [string]$Model = "",
  [string]$Judge = "",
  [string]$Suite = "",
  [int]$Runs = 0,
  [double]$TimeoutMultiplier = 0
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptDir "common.ps1")
Import-DotEnv
Apply-EcoClawEnv

$ModelLike = if ($Model) { $Model } elseif ($env:ECOCLAW_MODEL) { $env:ECOCLAW_MODEL } else { "claude-sonnet-4" }
$JudgeLike = if ($Judge) { $Judge } elseif ($env:ECOCLAW_JUDGE) { $env:ECOCLAW_JUDGE } else { "claude-opus-4.1" }
$ResolvedModel = Resolve-ModelAlias $ModelLike
$ResolvedJudge = Resolve-ModelAlias $JudgeLike
$ResolvedSuite = if ($Suite) { $Suite } elseif ($env:ECOCLAW_SUITE) { $env:ECOCLAW_SUITE } else { "automated-only" }
$ResolvedRuns = if ($Runs -gt 0) { $Runs } elseif ($env:ECOCLAW_RUNS) { [int]$env:ECOCLAW_RUNS } else { 3 }
$ResolvedTimeout = if ($TimeoutMultiplier -gt 0) { $TimeoutMultiplier } elseif ($env:ECOCLAW_TIMEOUT_MULTIPLIER) { [double]$env:ECOCLAW_TIMEOUT_MULTIPLIER } else { 1.0 }

$OutputDir = "D:/EcoClaw-Bench/results/raw/pinchbench/baseline"
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

Set-Location D:/skill
uv run scripts/benchmark.py `
  --model $ResolvedModel `
  --judge $ResolvedJudge `
  --suite $ResolvedSuite `
  --runs $ResolvedRuns `
  --timeout-multiplier $ResolvedTimeout `
  --output-dir $OutputDir `
  --no-upload
