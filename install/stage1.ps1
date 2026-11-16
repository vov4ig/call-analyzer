# Stage 1: conda env `wx` + PyTorch CUDA + whisperX + pinned pipeline deps.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File install\stage1.ps1
#   powershell ... -File install\stage1.ps1 -Torch cu121   # older GPUs
#   powershell ... -File install\stage1.ps1 -Editable      # dev install of the fork
param(
    [string]$Home  = $(if ($env:CALL_ANALYZER_HOME) { $env:CALL_ANALYZER_HOME } else { 'C:\cc' }),
    [string]$Tools = 'C:\tools',
    [string]$Env   = 'wx',
    [ValidateSet('cu128','cu126','cu121','cu118')]
    [string]$Torch = 'cu128',
    [switch]$Editable
)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

$repoRoot = Split-Path $PSScriptRoot -Parent
$conda    = Join-Path $Tools 'miniconda3\Scripts\conda.exe'
$py       = Join-Path $Tools "miniconda3\envs\$Env\python.exe"
$git      = Join-Path $Tools 'git\cmd\git.exe'
$fork     = Join-Path $Home 'repo\whisperX-offline'
$log      = Join-Path $Home 'logs\stage1.log'

New-Item -ItemType Directory -Force -Path "$Home\logs","$Home\tmp" | Out-Null

function Log([string]$m) {
    $line = "[{0:HH:mm:ss}] {1}" -f (Get-Date), $m
    Add-Content -Path $log -Value $line -Encoding UTF8
    Write-Host $line
}
function Run([string]$exe, [string[]]$argv, [string]$label) {
    Log "RUN   $label"
    $eap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $exe @argv } finally { $ErrorActionPreference = $eap }
    if ($LASTEXITCODE -ne 0) { throw "$label failed with exit code $LASTEXITCODE" }
}
function Py([string]$code, [string]$label) {
    Log "PY    $label"
    $eap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $py -c $code } finally { $ErrorActionPreference = $eap }
    if ($LASTEXITCODE -ne 0) { throw "$label failed (exit $LASTEXITCODE)" }
}

if (-not (Test-Path $conda)) { throw "Miniconda not found at $conda - run stage0.ps1 first" }
if (-not (Test-Path $git))   { throw "Git not found at $git - run stage0.ps1 first" }

$env:Path = (@(
    (Join-Path $Tools 'git\cmd'),
    (Join-Path $Tools 'miniconda3'),
    (Join-Path $Tools 'miniconda3\Scripts'),
    (Join-Path $Tools 'miniconda3\condabin'),
    (Join-Path $Tools 'ffmpeg\bin')
) -join ';') + ';' + $env:Path
$env:CALL_ANALYZER_HOME   = $Home
$env:HF_HOME              = "$Home\models\hf"
$env:TORCH_HOME           = "$Home\models\torch"
$env:NLTK_DATA            = "$Home\models\nltk"
$env:PIP_CACHE_DIR        = "$Home\tmp\pipcache"
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:PYTHONUTF8           = '1'

Log '===== STAGE 1 START ====='
Log "home: $Home | env: $Env | torch: $Torch"

# ---------- 1. conda env ----------
try {
    if (Test-Path $py) {
        Log "STEP env: $Env already exists"
    } else {
        Run $conda @('create','-y','-n',$Env,'--override-channels','-c','conda-forge','python=3.11','pip') "conda create $Env"
    }
    & $py --version
    Log 'STEP env: OK'
} catch { Log "STEP env: FAIL - $($_.Exception.Message)"; throw }

# ---------- 2. pip bootstrap ----------
try {
    Run $py @('-m','pip','install','--upgrade','pip','setuptools','wheel') 'pip bootstrap'
    Log 'STEP pip: OK'
} catch { Log "STEP pip: FAIL - $($_.Exception.Message)"; throw }

# ---------- 3. torch ----------
try {
    $eap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $py -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)" 2>&1 | Out-Null
    $torchOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $eap
    if (-not $torchOk) {
        Run $py @('-m','pip','install','torch==2.8.0','torchaudio==2.8.0',
                  '--index-url',"https://download.pytorch.org/whl/$Torch") "pip torch $Torch"
    } else {
        Log "STEP torch: already working (CUDA $Torch not reinstalled)"
    }
    Py "import torch; print('torch', torch.__version__); print('cuda', torch.version.cuda); print('avail', torch.cuda.is_available()); print('gpu', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none'); print('cap', torch.cuda.get_device_capability(0) if torch.cuda.is_available() else 'none')" 'torch selftest'
    Log 'STEP torch: OK'
} catch { Log "STEP torch: FAIL - $($_.Exception.Message)"; throw }

# ---------- 4. whisperX fork ----------
try {
    if ($Editable) {
        if (Test-Path (Join-Path $fork '.git')) {
            Log 'STEP clone: repo exists'
            & $git -C $fork fetch --all --tags
            if ($LASTEXITCODE -ne 0) { throw 'git fetch failed' }
        } else {
            Run $git @('clone','--branch','offline-alignment',
                'https://github.com/JayDHulster/whisperX-offline.git', $fork) 'git clone'
        }
        & $git -C $fork log -1 --oneline
        Push-Location $fork
        try { Run $py @('-m','pip','install','-e','.') 'pip install -e whisperx' }
        finally { Pop-Location }
        Log 'STEP whisperx: editable OK'
    } else {
        Log 'STEP whisperx: installed from requirements.txt'
    }
} catch { Log "STEP whisperx: FAIL - $($_.Exception.Message)"; throw }

# ---------- 5. pinned dependency set ----------
try {
    $req = Join-Path $repoRoot 'requirements.txt'
    if (-not (Test-Path $req)) { throw "missing $req" }
    # keep the pinned torch build: requirements.txt resolves it from the same index
    Run $py @('-m','pip','install','-r',$req) 'pip install -r requirements.txt'
    Log 'STEP deps: OK'
} catch { Log "STEP deps: FAIL - $($_.Exception.Message)"; throw }

# ---------- 6. smoke test ----------
try {
    Py "import whisperx, pyannote.audio, ctranslate2, transformers, numpy, torch; print('whisperx', whisperx.__version__ if hasattr(whisperx,'__version__') else 'ok'); print('pyannote', pyannote.audio.__version__); print('ct2', ctranslate2.__version__); print('numpy', numpy.__version__); print('torch', torch.__version__); print('cuda devices', ctranslate2.get_cuda_device_count())" 'imports'
    Log 'STEP smoke: OK'
} catch { Log "STEP smoke: FAIL - $($_.Exception.Message)"; throw }

Log '===== STAGE 1 DONE ====='
