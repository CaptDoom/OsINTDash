$ErrorActionPreference = 'Stop'

$projectPath = 'd:\NEWS PROJECT\globalive'

$startInfo = New-Object System.Diagnostics.ProcessStartInfo
$startInfo.FileName = 'cmd.exe'
$startInfo.WorkingDirectory = $projectPath
$startInfo.Arguments = '/c npm run dev'
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden

[void][System.Diagnostics.Process]::Start($startInfo)

$deadline = (Get-Date).AddSeconds(45)
while ((Get-Date) -lt $deadline) {
  try {
    $response = Invoke-WebRequest -Uri 'http://127.0.0.1:3000' -UseBasicParsing -TimeoutSec 2
    if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
      Start-Process 'http://127.0.0.1:3000'
      break
    }
  } catch {}
  Start-Sleep -Seconds 1
}
