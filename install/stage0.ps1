# Stage 0: portable binaries (Git, Miniconda, FFmpeg) + user environment.
# Everything installs into -Tools (default C:\tools), no administrator rights.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File install\stage0.ps1
#   powershell ... -File install\stage0.ps1 -Home D:\calls -Tools D:\tools
param(
    [string]$Home  = $(if ($env:CALL_ANALYZER_HOME) { $env:CALL_ANALYZER_HOME } else { 'C:\cc' }),
    [string]$Tools = 'C:\tools'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

$git    = Join-Path $Tools 'git\cmd\git.exe'
$conda  = Join-Path $Tools 'miniconda3\Scripts\conda.exe'
$ffmpeg = Join-Path $Tools 'ffmpeg\bin\ffmpeg.exe'
$log    = Join-Path $Home 'logs\stage0.log'

New-Item -ItemType Directory -Force -Path `
    "$Home\logs","$Home\installers","$Home\tmp","$Home\audio","$Home\out",
    "$Home\models\whisper","$Home\models\align","$Home\models\pyannote",
    "$Home\models\hf","$Home\models\torch","$Home\models\nltk","$Home\models\ollama" |
    Out-Null
New-Item -ItemType Directory -Force -Path $Tools | Out-Null

function Log([string]$m) {
    $line = "[{0:HH:mm:ss}] {1}" -f (Get-Date), $m
    Add-Content -Path $log -Value $line -Encoding UTF8
    Write-Host $line
}

function Dl([string]$url, [string]$out) {
    if ((Test-Path $out) -and ((Get-Item $out).Length -gt 1MB)) {
        Log ("SKIP  {0} ({1:N1} MB already)" -f (Split-Path $out -Leaf), ((Get-Item $out).Length/1MB))
        return
    }
    Log "GET   $url"
    & curl.exe -L --fail --retry 2 --retry-delay 2 --connect-timeout 20 `
        --speed-limit 20000 --speed-time 60 -sS -o $out $url
    if ($LASTEXITCODE -ne 0) { throw "curl failed ($LASTEXITCODE) for $url" }
    if (-not (Test-Path $out) -or (Get-Item $out).Length -lt 1MB) {
        throw "downloaded file too small: $out"
    }
    Log ("OK    {0} ({1:N1} MB)" -f (Split-Path $out -Leaf), ((Get-Item $out).Length/1MB))
}

function AddUserPath([string]$dir) {
    if (-not (Test-Path $dir)) { throw "path not found: $dir" }
    $cur = [Environment]::GetEnvironmentVariable('Path','User')
    if (-not $cur) { $cur = '' }
    $parts = $cur -split ';' | Where-Object { $_ -ne '' }
    if ($parts -notcontains $dir) {
        [Environment]::SetEnvironmentVariable('Path', ((@($parts) + $dir) -join ';'), 'User')
        Log "PATH+ $dir"
    } else {
        Log "PATH= $dir (already)"
    }
}

Log '===== STAGE 0 START ====='
Log "home:  $Home"
Log "tools: $Tools"

if ($Home -match '[^\x00-\x7F]') {
    Log 'WARN: the data path contains non-ASCII characters.'
    Log 'WARN: PyTorch/whisper and some model files fail on Cyrillic paths; prefer C:\cc.'
}
if ($Tools -match '[^\x00-\x7F]') {
    Log 'WARN: the tools path contains non-ASCII characters; prefer C:\tools.'
}

# ---------- 1. Downloads ----------
try {
    Dl 'https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe' "$Home\installers\miniconda3.exe"

    $rel   = Invoke-RestMethod -Uri 'https://api.github.com/repos/git-for-windows/git/releases/latest' -Headers @{ 'User-Agent' = 'pwsh' }
    $asset = $rel.assets | Where-Object { $_.name -like 'PortableGit-*-64-bit.7z.exe' } | Select-Object -First 1
    if (-not $asset) { throw 'PortableGit asset not found in latest release' }
    Log "git release: $($rel.tag_name) / $($asset.name)"
    Dl $asset.browser_download_url "$Home\installers\portablegit.exe"

    Dl 'https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip' "$Home\installers\ffmpeg.zip"
    Log 'STEP downloads: OK'
} catch { Log "STEP downloads: FAIL - $($_.Exception.Message)"; throw }

# ---------- 2. PortableGit ----------
try {
    if (Test-Path $git) {
        Log 'STEP git: already installed'
    } else {
        Log "extracting PortableGit -> $Tools\git"
        $p = Start-Process -FilePath "$Home\installers\portablegit.exe" `
            -ArgumentList '-y', "-o$Tools\git" -Wait -PassThru
        if ($p.ExitCode -ne 0) { throw "7z SFX exit code $($p.ExitCode)" }
        Log 'STEP git: extracted'
    }
    & $git --version
} catch { Log "STEP git: FAIL - $($_.Exception.Message)"; throw }

# ---------- 3. Miniconda ----------
try {
    if (Test-Path $conda) {
        Log 'STEP miniconda: already installed'
    } else {
        Log "installing Miniconda -> $Tools\miniconda3 (silent, JustMe)"
        $p = Start-Process -FilePath "$Home\installers\miniconda3.exe" -Wait -PassThru -ArgumentList @(
            '/InstallationType=JustMe','/RegisterPython=0','/AddToPath=0','/S',"/D=$Tools\miniconda3"
        )
        if ($p.ExitCode -ne 0) { throw "miniconda exit code $($p.ExitCode)" }
        Log 'STEP miniconda: installed'
    }
    & $conda --version
} catch { Log "STEP miniconda: FAIL - $($_.Exception.Message)"; throw }

# ---------- 4. FFmpeg ----------
try {
    if (Test-Path $ffmpeg) {
        Log 'STEP ffmpeg: already installed'
    } else {
        Log "extracting ffmpeg -> $Tools\ffmpeg"
        $tmp = "$Home\tmp\ffmpeg_x"
        if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
        Expand-Archive -Path "$Home\installers\ffmpeg.zip" -DestinationPath $tmp -Force
        $src = Get-ChildItem $tmp -Directory | Select-Object -First 1
        Move-Item $src.FullName (Join-Path $Tools 'ffmpeg')
        Remove-Item $tmp -Recurse -Force
        Log 'STEP ffmpeg: extracted'
    }
    & $ffmpeg -hide_banner -version | Select-Object -First 1
} catch { Log "STEP ffmpeg: FAIL - $($_.Exception.Message)"; throw }

# ---------- 5. conda config ----------
try {
    $condarc = @"
channels:
  - conda-forge
channel_priority: strict
auto_activate_base: false
show_channel_urls: true
"@
    Set-Content -Path "$env:USERPROFILE\.condarc" -Value $condarc -Encoding ASCII
    & $conda config --show channels
    & $conda init powershell | Out-String | ForEach-Object { Log $_ }
    Log 'STEP conda-config: OK'
} catch { Log "STEP conda-config: FAIL - $($_.Exception.Message)"; throw }

# ---------- 6. PATH + env ----------
try {
    AddUserPath (Join-Path $Tools 'git\cmd')
    AddUserPath (Join-Path $Tools 'ffmpeg\bin')
    AddUserPath (Join-Path $Tools 'miniconda3')
    AddUserPath (Join-Path $Tools 'miniconda3\Scripts')
    AddUserPath (Join-Path $Tools 'miniconda3\condabin')

    $envs = @{
        CALL_ANALYZER_HOME = $Home
        HF_HOME            = "$Home\models\hf"
        TORCH_HOME         = "$Home\models\torch"
        NLTK_DATA          = "$Home\models\nltk"
        OLLAMA_MODELS      = "$Home\models\ollama"
        TMP                = "$Home\tmp"
        TEMP               = "$Home\tmp"
    }
    foreach ($k in $envs.Keys) {
        [Environment]::SetEnvironmentVariable($k, $envs[$k], 'User')
        Log "ENV   $k = $($envs[$k])"
    }
    $env:CALL_ANALYZER_HOME = $Home
    Log 'STEP env: OK'
} catch { Log "STEP env: FAIL - $($_.Exception.Message)"; throw }

# ---------- 7. git config ----------
try {
    & $git config --global core.longpaths true
    & $git config --global core.autocrlf false
    Log 'STEP git-config: OK'
} catch { Log "STEP git-config: FAIL - $($_.Exception.Message)"; throw }

Log '===== STAGE 0 DONE ====='
