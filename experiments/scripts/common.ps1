function Import-DotEnv {
  param(
    [string]$EnvPath = "D:/EcoClaw-Bench/.env"
  )

  if (!(Test-Path $EnvPath)) {
    return
  }

  Get-Content $EnvPath | ForEach-Object {
    $line = $_.Trim()
    if ($line.Length -eq 0) { return }
    if ($line.StartsWith("#")) { return }
    $parts = $line.Split("=", 2)
    if ($parts.Count -ne 2) { return }
    $key = $parts[0].Trim()
    $value = $parts[1].Trim()
    [Environment]::SetEnvironmentVariable($key, $value, "Process")
  }
}

function Resolve-ModelAlias {
  param(
    [Parameter(Mandatory = $true)][string]$ModelLike
  )

  if ($ModelLike.Contains("/")) {
    return $ModelLike
  }

  $map = @{
    "gpt-oss-20b" = "openai/gpt-oss-20b"
    "gpt-oss-120b" = "openai/gpt-oss-120b"
    "gpt-5-nano" = "openai/gpt-5-nano"
    "gpt-5-mini" = "openai/gpt-5-mini"
    "gpt-5" = "openai/gpt-5"
    "gpt-5-chat" = "openai/gpt-5-chat"
    "gpt-4.1-nano" = "openai/gpt-4.1-nano"
    "gpt-4.1-mini" = "openai/gpt-4.1-mini"
    "gpt-4.1" = "openai/gpt-4.1"
    "gpt-4o-mini" = "openai/gpt-4o-mini"
    "gpt-4o" = "openai/gpt-4o"
    "o1" = "openai/o1"
    "o1-mini" = "openai/o1-mini"
    "o1-pro" = "openai/o1-pro"
    "o3-mini" = "openai/o3-mini"
    "o3" = "openai/o3"
    "o4-mini" = "openai/o4-mini"
    "claude-3.5-sonnet" = "openrouter/anthropic/claude-3.5-sonnet"
    "claude-3.5-haiku" = "openrouter/anthropic/claude-3.5-haiku"
    "claude-3.7-sonnet" = "openrouter/anthropic/claude-3.7-sonnet"
    "claude-sonnet-4" = "openrouter/anthropic/claude-sonnet-4"
    "claude-opus-4.1" = "openrouter/anthropic/claude-opus-4.1"
    "claude-haiku-4.5" = "openrouter/anthropic/claude-haiku-4.5"
  }

  if ($map.ContainsKey($ModelLike)) {
    return $map[$ModelLike]
  }

  throw "Unknown model alias: $ModelLike"
}

function Apply-EcoClawEnv {
  # Map your local gateway credentials to common provider env vars
  if ($env:ECOCLAW_API_KEY) {
    $env:OPENAI_API_KEY = $env:ECOCLAW_API_KEY
    $env:OPENROUTER_API_KEY = $env:ECOCLAW_API_KEY
  }
  if ($env:ECOCLAW_BASE_URL) {
    $env:OPENAI_BASE_URL = $env:ECOCLAW_BASE_URL
    $env:OPENROUTER_BASE_URL = $env:ECOCLAW_BASE_URL
  }
}
