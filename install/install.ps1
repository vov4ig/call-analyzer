# One-shot installer for Windows 10/11, no administrator rights needed.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File install\install.ps1
#
#   # custom locations and an older GPU
#   powershell ... -File install\install.ps1 -Home D:\calls -Tools D:\tools -Torch cu121
#
#   # pyannote weights from public mirrors instead of the gated HF endpoint
#   powershell ... -File install\install.ps1 -Mirror
#
# Total download: ~9 GB (models) + ~3 GB (CUDA wheels). Expect 30-90 minutes.
param(
    [string]$Home  = $(if ($env:CALL_ANALYZER_HOME) { $env:CALL_ANALYZER_HOME } else { 'C:\cc' }),
    [string]$Tools = 'C:\tools',
    [string]$Env   = 'wx',
    [ValidateSet('cu128','cu126','cu121','cu118')]
    [string]$Torch = 'cu128',
    [string]$Model = 'qwen2.5:7b',
    [string]$HfToken = $env:HF_TOKEN,
    [switch]$Mirror,
    [switch]$SkipOllama,
    [switch]$SkipSmoke
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$py   = Join-Path $Tools "miniconda3\envs\$Env\python.exe"
$log  = Join-Path $Home 'logs\install.log'

New-Item -ItemType Directory -Force -Path (Join-Path $Home 'logs') | Out-Null

function Step([string]$name) {
    $line = ("[{0:HH:mm:ss}] ===== {1} =====" -f (Get-Date), $name)
    Add-Content -Path $log -Value $line -Encoding UTF8
    Write-Host ''
    Write-Host $line -ForegroundColor Cyan
}

function Invoke-Script([string]$script, [string[]]$extra) {
    $path = Join-Path $PSScriptRoot $script
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $path @extra
    if ($LASTEXITCODE -ne 0) { throw "$script failed (exit $LASTEXITCODE)" }
}

if ($PSVersionTable.PSVersion.Major -lt 5) {
    throw 'PowerShell 5.1 or newer is required'
}

Write-Host 'Call analyzer - offline ASR + diarization + local LLM' -ForegroundColor Green
Write-Host "  data:  $Home"
Write-Host "  tools: $Tools"
Write-Host "  torch: $Torch"
Write-Host "  model: $Model"
Write-Host "  log:   $log"

# ---------- 0. binaries ----------
Step '0/6 binaries'
Invoke-Script 'stage0.ps1' @('-Home', $Home, '-Tools', $Tools)

# ---------- 1. python env ----------
Step '1/6 python env + torch + whisperX'
$stage1Args = @('-Home', $Home, '-Tools', $Tools, '-Env', $Env, '-Torch', $Torch)
Invoke-Script 'stage1.ps1' $stage1Args

# ---------- 2. torch.load compat shim ----------
Step '2/6 pyannote/torch compatibility shim'
$site = (& $py -c "import site; print(site.getsitepackages()[0])").Trim()
if (-not (Test-Path $site)) { throw "site-packages not found: $site" }
Copy-Item (Join-Path $root 'shim\sitecustomize.py') (Join-Path $site 'sitecustomize.py') -Force
Write-Host "  installed -> $site\sitecustomize.py"
& $py -c "import sitecustomize; print('  shim loads OK')"
if ($LASTEXITCODE -ne 0) { throw 'sitecustomize import failed' }

# ---------- 3. models ----------
Step '3/6 models (~9 GB)'
$modelArgs = @('--home', $Home)
if ($Mirror)        { $modelArgs += '--mirror' }
elseif ($HfToken)   { $modelArgs += @('--token', $HfToken) }
& $py (Join-Path $PSScriptRoot 'stage2_models.py') @modelArgs
if ($LASTEXITCODE -ne 0) { throw "model download failed (exit $LASTEXITCODE)" }

# ---------- 4. ollama ----------
if ($SkipOllama) {
    Step '4/6 ollama (skipped)'
} else {
    Step '4/6 ollama + analysis model'
    Invoke-Script 'stage3.ps1' @('-Home', $Home, '-Model', $Model)
}

# ---------- 5. smoke test ----------
if ($SkipSmoke) {
    Step '5/6 smoke test (skipped)'
} else {
    Step '5/6 smoke test'
    try {
        Invoke-Script 'make_test_audio.ps1' @('-Home', $Home, '-Tools', $Tools)
        $env:CALL_ANALYZER_HOME = $Home
        & $py (Join-Path $PSScriptRoot 'stage2_test.py')
        if ($LASTEXITCODE -ne 0) { throw 'pipeline smoke test failed' }
    } catch {
        Write-Host "  WARN: $($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host '  Installation finished, but the smoke test failed. Run verify.ps1 -Full later.'
    }
}

# ---------- 6. verify ----------
Step '6/6 self-check'
$verifyArgs = @('-Home', $Home, '-Python', $py)
$verify = Join-Path $root 'verify.ps1'
if (Test-Path $verify) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $verify @verifyArgs
}

Write-Host ''
Write-Host 'Installed. Start the web UI with:' -ForegroundColor Green
Write-Host "  $(Join-Path $root 'web.bat')"
Write-Host "  $(Join-Path $root 'web.bat') --host 0.0.0.0 --token SECRET   # LAN + password"
