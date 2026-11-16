# Self-check for the offline call-analysis setup.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File verify.ps1
#   powershell ... -File verify.ps1 -Full        # also runs a real call + LLM report
#   powershell ... -File verify.ps1 -Home D:\calls -Tools D:\tools
param(
    [string]$Home   = $(if ($env:CALL_ANALYZER_HOME) { $env:CALL_ANALYZER_HOME } else { 'C:\cc' }),
    [string]$Tools  = 'C:\tools',
    [string]$Python = $(if ($env:CALL_ANALYZER_PY) { $env:CALL_ANALYZER_PY } else { '' }),
    [string]$Env    = 'wx',
    [string]$Model  = 'qwen2.5:7b',
    [string]$OllamaHost = 'http://127.0.0.1:11434',
    [switch]$Full
)

$ErrorActionPreference = 'Continue'
if (-not $Python) { $Python = Join-Path $Tools "miniconda3\envs\$Env\python.exe" }

$here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$ok = 0; $bad = 0

function Head($t) { "" ; "=== $t ===" }
function Good($t) { $script:ok++; "  [OK]   $t" }
function Bad($t)  { $script:bad++; "  [FAIL] $t" }

"home:   $Home"
"python: $Python"

Head 'binaries'
foreach ($p in @((Join-Path $Tools 'ffmpeg\bin\ffmpeg.exe'),
                 (Join-Path $Tools 'git\cmd\git.exe'),
                 $Python,
                 (Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'))) {
    if (Test-Path $p) { Good $p } else { Bad "missing $p" }
}
$gitExe = Join-Path $Tools 'git\cmd\git.exe'
if (Test-Path $gitExe) { & $gitExe --version 2>$null | ForEach-Object { "  $_" } }

Head 'python / torch / cuda'
if (-not (Test-Path $Python)) { Bad "python not found: $Python" } else {
$code = @'
import torch, ctranslate2, numpy, transformers, pyannote.audio as pa, whisperx
print("torch", torch.__version__, "cuda", torch.version.cuda, "available", torch.cuda.is_available())
print("gpu", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none",
      "cap", torch.cuda.get_device_capability(0) if torch.cuda.is_available() else "none")
print("ct2", ctranslate2.__version__, "cuda devices", ctranslate2.get_cuda_device_count())
print("pyannote", pa.__version__, "numpy", numpy.__version__, "transformers", transformers.__version__)
print("torch.load compat shim:", getattr(torch.load, "_wx_compat", False))
'@
# PS 5.1 mangles quotes in native args, so run the snippet from a file
$codeFile = Join-Path ([IO.Path]::GetTempPath()) 'cc_verify_env.py'
[IO.File]::WriteAllText($codeFile, $code, (New-Object Text.UTF8Encoding($false)))
$out = & $Python $codeFile 2>&1 | Where-Object { $_ -notmatch 'UserWarning|warnings.warn|torchaudio|speechbrain|Reproducibility|allow_tf32|Redirects' }
$out | ForEach-Object { "  $_" }
if (($out -join ' ') -match 'available True') { Good 'CUDA works' } else { Bad 'CUDA not available' }
if (($out -join ' ') -match 'compat shim: True') { Good 'torch.load shim active' } else { Bad 'torch.load shim missing (pyannote may fail)' }
}

Head 'models'
$models = Join-Path $Home 'models'
$files = [ordered]@{
    'whisper large-v3' = @((Join-Path $models 'whisper\models--Systran--faster-whisper-large-v3\snapshots'), 3087284237)
    'pyannote seg.bin' = @((Join-Path $models 'pyannote\seg.bin'), 5905440)
    'pyannote emb.bin' = @((Join-Path $models 'pyannote\emb.bin'), 26645418)
    'pyannote config'  = @((Join-Path $models 'pyannote\config.yaml'), 0)
    'align en (.pth)'  = @((Join-Path $models 'align_en\wav2vec2_fairseq_base_ls960_asr_ls960.pth'), 377664473)
}
foreach ($k in $files.Keys) {
    $p = $files[$k][0]; $want = $files[$k][1]
    if ($p -like '*snapshots') {
        $hit = Get-ChildItem $p -Recurse -Filter 'model.bin' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit -and $hit.Length -eq $want) { Good "$k ($([math]::Round($hit.Length/1GB,2)) GB)" }
        else { Bad "$k (found: $(if ($hit) { $hit.Length } else { 'nothing' }))" }
    } elseif (Test-Path $p) {
        $len = (Get-Item $p).Length
        if ($want -eq 0 -or $len -eq $want) { Good "$k ($([math]::Round($len/1MB,1)) MB)" }
        else { Bad "$k size $len != $want" }
    } else { Bad "missing $p" }
}
$ruHit = Get-ChildItem (Join-Path $models 'align') -Recurse -Filter 'pytorch_model.bin' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($ruHit -and $ruHit.Length -eq 1262093719) { Good 'align ru (1.26 GB)' } else { Bad 'align ru model missing' }

Head 'environment'
foreach ($n in 'CALL_ANALYZER_HOME', 'HF_HOME', 'TORCH_HOME', 'NLTK_DATA', 'OLLAMA_MODELS') {
    $v = [Environment]::GetEnvironmentVariable($n, 'User')
    if ($v) { Good "$n = $v" } else { Bad "$n is not set (stage0.ps1 does this)" }
}

Head 'ollama'
try {
    $v = Invoke-RestMethod -Uri "$OllamaHost/api/version" -TimeoutSec 5
    Good "server $($v.version)"
    $tags = Invoke-RestMethod -Uri "$OllamaHost/api/tags" -TimeoutSec 10
    $q = $tags.models | Where-Object { $_.name -like "$Model*" }
    if ($q) { Good "model $Model ($([math]::Round($q.size/1GB,2)) GB)" } else { Bad "$Model not pulled" }
} catch { Bad "ollama not reachable: $($_.Exception.Message)" }

if ($Full) {
    Head 'end-to-end transcription (test audio)'
    $audio = Join-Path $Home 'audio\test_mix.wav'
    if (Test-Path $audio) {
        $out = & $Python (Join-Path $here 'run_call.py') $audio --lang en 2>&1
        if (($out -join "`n") -match 'RUN_CALL_DONE') {
            Good 'transcription finished'
            ($out | Where-Object { $_ -match '^\[' }).ForEach({ "  $_" })
        } else { Bad 'transcription failed'; $out | Select-Object -Last 15 | ForEach-Object { "  $_" } }

        Head 'LLM analysis'
        $json = [IO.Path]::ChangeExtension($audio, '.json')
        $out = & $Python (Join-Path $here 'analyze_call.py') $json 2>&1
        if (($out -join "`n") -match 'ANALYZE_CALL_DONE') { Good 'report written' }
        else { Bad 'analysis failed'; $out | Select-Object -Last 15 | ForEach-Object { "  $_" } }
    } else { Bad "test audio missing ($audio)" }
}

""
"RESULT: $ok ok, $bad failed"
if ($bad -gt 0) { exit 1 }
