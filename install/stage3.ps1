# Stage 3: Ollama (local LLM server) + the analysis model.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File install\stage3.ps1
#   powershell ... -File install\stage3.ps1 -Model qwen2.5:7b-instruct-q4_K_M
#
# Safe to re-run: it restarts the server with OLLAMA_MODELS pointing at the data
# directory and skips a model that is already pulled.
param(
    [string]$Home  = $(if ($env:CALL_ANALYZER_HOME) { $env:CALL_ANALYZER_HOME } else { 'C:\cc' }),
    [string]$Model = 'qwen2.5:7b',
    [string]$OllamaHost = 'http://127.0.0.1:11434'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

$models    = Join-Path $Home 'models\ollama'
$ollamaDir = Join-Path $env:LOCALAPPDATA 'Programs\Ollama'
$ollama    = Join-Path $ollamaDir 'ollama.exe'
$log       = Join-Path $Home 'logs\stage3.log'

New-Item -ItemType Directory -Force -Path $models,"$Home\logs" | Out-Null

function Log([string]$m) {
    $line = "[{0:HH:mm:ss}] {1}" -f (Get-Date), $m
    Add-Content -Path $log -Value $line -Encoding UTF8
    Write-Host $line
}
function Run([string]$exe, [string[]]$argv, [string]$label) {
    Log "RUN   $label"
    $eap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $exe @argv 2>&1 | ForEach-Object { Write-Host $_ } } finally { $ErrorActionPreference = $eap }
    if ($LASTEXITCODE -ne 0) { throw "$label failed (exit $LASTEXITCODE)" }
}

Log '===== STAGE 3 START ====='
$env:OLLAMA_MODELS = $models
Log "models: $models"
Log "model:  $Model"

# ---------- install ----------
try {
    if (Test-Path $ollama) {
        Log "STEP install: already at $ollama"
    } else {
        $setup = Join-Path (Join-Path $Home 'installers') 'OllamaSetup.exe'
        New-Item -ItemType Directory -Force -Path (Split-Path $setup -Parent) | Out-Null
        if (-not (Test-Path $setup)) {
            Log 'GET   https://github.com/ollama/ollama/releases/latest/download/OllamaSetup.exe'
            $eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
            & curl.exe -L --fail -C - --retry 5 --retry-delay 3 --retry-all-errors `
                --connect-timeout 30 --speed-limit 1000000 --speed-time 120 `
                -o $setup 'https://github.com/ollama/ollama/releases/latest/download/OllamaSetup.exe'
            $rc = $LASTEXITCODE; $ErrorActionPreference = $eap
            if ($rc -ne 0) { throw "download failed: $rc" }
        }
        Log ("OK    OllamaSetup.exe ({0:N1} MB)" -f ((Get-Item $setup).Length / 1MB))
        Log 'installing (per-user, silent)'
        $p = Start-Process -FilePath $setup -Wait -PassThru -ArgumentList '/VERYSILENT', '/NORESTART'
        Log "installer exit: $($p.ExitCode)"
        if (-not (Test-Path $ollama)) { throw 'ollama.exe not found after install' }
    }
    Run $ollama @('--version') 'ollama --version'
    Log 'STEP install: OK'
} catch { Log "STEP install: FAIL - $($_.Exception.Message)"; throw }

# ---------- server with our model dir ----------
try {
    $running = Get-Process ollama -ErrorAction SilentlyContinue
    if ($running) {
        Log "stopping $($running.Count) ollama process(es) to apply OLLAMA_MODELS"
        $running | Stop-Process -Force
        Start-Sleep -Seconds 3
    }
    Log "starting ollama serve (OLLAMA_MODELS=$env:OLLAMA_MODELS)"
    Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden `
        -RedirectStandardOutput "$Home\logs\ollama.out.log" `
        -RedirectStandardError  "$Home\logs\ollama.err.log"
    $up = $false
    for ($i = 0; $i -lt 45; $i++) {
        Start-Sleep -Seconds 2
        try {
            $v = Invoke-RestMethod -Uri "$OllamaHost/api/version" -TimeoutSec 3
            if ($v.version) { $up = $true; Log "server up: $($v.version)"; break }
        } catch { }
    }
    if (-not $up) { throw 'ollama server did not start' }
    Log 'STEP server: OK'
} catch { Log "STEP server: FAIL - $($_.Exception.Message)"; throw }

# ---------- pull model ----------
try {
    $have = & $ollama list 2>$null | Select-String -SimpleMatch $Model
    if ($have) {
        Log "STEP pull: $Model already present"
    } else {
        Run $ollama @('pull', $Model) "ollama pull $Model"
    }
    Run $ollama @('list') 'ollama list'
    Log 'STEP pull: OK'
} catch { Log "STEP pull: FAIL - $($_.Exception.Message)"; throw }

# ---------- smoke test ----------
try {
    Log 'smoke test generation'
    $body = @{
        model   = $Model
        prompt  = 'Ответь одним предложением по-русски: что такое диаризация звонка?'
        stream  = $false
        options = @{ num_ctx = 2048 }
    } | ConvertTo-Json -Depth 5
    $r = Invoke-RestMethod -Uri "$OllamaHost/api/generate" -Method Post `
        -Body $body -ContentType 'application/json' -TimeoutSec 600
    Log ("model reply: " + ($r.response -replace "`r?`n", ' '))
    Log 'STEP smoke: OK'
} catch { Log "STEP smoke: FAIL - $($_.Exception.Message)"; throw }

Log '===== STAGE 3 DONE ====='
