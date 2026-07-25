$ErrorActionPreference = 'Stop'

# Resolve project path from this script so launcher works even after moving folders.
$projectPath = Split-Path -Parent $MyInvocation.MyCommand.Path

function Get-EnvValue {
  param(
    [string]$Path,
    [string]$Key
  )

  if (-not (Test-Path $Path)) {
    return $null
  }

  foreach ($line in Get-Content -Path $Path) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
    if ($trimmed -match "^$([regex]::Escape($Key))=(.*)$") {
      return $matches[1].Trim()
    }
  }
  return $null
}

function Start-HiddenCmd {
  param(
    [string]$WorkingDirectory,
    [string]$Command
  )

  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = 'cmd.exe'
  $psi.WorkingDirectory = $WorkingDirectory
  $psi.Arguments = "/c $Command"
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
  [void][System.Diagnostics.Process]::Start($psi)
}

$envPath = Join-Path $projectPath '.env'
$llmProvider = (Get-EnvValue -Path $envPath -Key 'LLM_PROVIDER')
if (-not $llmProvider) { $llmProvider = 'ollama' }
$llmProvider = $llmProvider.ToLowerInvariant()

$llmModel = (Get-EnvValue -Path $envPath -Key 'LLM_MODEL')
if (-not $llmModel) { $llmModel = 'llama3.1:8b-instruct' }

$nodeModulesPath = Join-Path $projectPath 'node_modules'
if (-not (Test-Path $nodeModulesPath)) {
  Start-HiddenCmd -WorkingDirectory $projectPath -Command 'npm install'

  $installDeadline = (Get-Date).AddMinutes(6)
  while ((Get-Date) -lt $installDeadline) {
    if (Test-Path $nodeModulesPath) {
      break
    }
    Start-Sleep -Milliseconds 700
  }
}

if ($llmProvider -eq 'ollama') {
  $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
  if ($ollamaCmd) {
    # Start local Ollama daemon if not already available.
    try {
      Invoke-WebRequest -Uri 'http://127.0.0.1:11434/api/tags' -UseBasicParsing -TimeoutSec 2 | Out-Null
    } catch {
      Start-HiddenCmd -WorkingDirectory $projectPath -Command 'ollama serve'
    }

    # First-time setup: automatically pull configured model if missing.
    Start-HiddenCmd -WorkingDirectory $projectPath -Command "ollama pull $llmModel"
  }
}

# Start frontend + backend together.
Start-HiddenCmd -WorkingDirectory $projectPath -Command 'npm run dev'

$deadline = (Get-Date).AddSeconds(75)
while ((Get-Date) -lt $deadline) {
  try {
    $response = Invoke-WebRequest -Uri 'http://127.0.0.1:3000' -UseBasicParsing -TimeoutSec 2
    if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
      Start-Process 'http://127.0.0.1:3000'
      break
    }
  } catch {}
  Start-Sleep -Milliseconds 900
}
