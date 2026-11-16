# Build a synthetic 2-speaker test call with Windows SAPI + ffmpeg.
# No microphone needed, works offline (Zira/David voices ship with Windows).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File install\make_test_audio.ps1
#   powershell ... -File install\make_test_audio.ps1 -Home D:\calls -Tools D:\tools
param(
    [string]$Home  = $(if ($env:CALL_ANALYZER_HOME) { $env:CALL_ANALYZER_HOME } else { 'C:\cc' }),
    [string]$Tools = 'C:\tools'
)

$ErrorActionPreference = 'Stop'
$Audio = Join-Path $Home 'audio'
$ff    = Join-Path $Tools 'ffmpeg\bin\ffmpeg.exe'

if (-not (Test-Path $ff)) { throw "ffmpeg not found at $ff - run stage0.ps1 first" }
New-Item -ItemType Directory -Force -Path $Audio | Out-Null

Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Rate = 0

# Prefer Zira; on non-English Windows fall back to any installed voice.
try { $s.SelectVoice('Microsoft Zira Desktop') }
catch {
    $any = $s.GetInstalledVoices() | Where-Object { $_.Enabled } | Select-Object -First 1
    if (-not $any) { throw 'no SAPI voices installed' }
    $s.SelectVoice($any.VoiceInfo.Name)
    "voice fallback: $($any.VoiceInfo.Name)"
}
"voice: $($s.Voice.Name)"

$lines = @(
  @{ f = 'a1.wav'; t = 'Hello, thank you for calling our support line. My name is Anna. How can I help you today?' },
  @{ f = 'b1.wav'; t = 'Hi Anna. I have been trying to pay with my card for two days, and the payment keeps failing.' },
  @{ f = 'a2.wav'; t = 'I am sorry about that. Could you please tell me the last four digits of your card?' },
  @{ f = 'b2.wav'; t = 'Yes, of course. The last four digits are four four one seven. It is a visa card.' }
)
foreach ($l in $lines) {
    $s.SetOutputToWaveFile((Join-Path $Audio $l.f))
    $s.Speak($l.t)
}
$s.Dispose()

# Shift the "client" lines down in pitch so diarization has two clear speakers.
foreach ($n in 'b1','b2') {
    & $ff -y -hide_banner -loglevel error -i "$Audio\$n.wav" `
        -af "asetrate=22050*0.80,aresample=22050,atempo=1.25" "$Audio\${n}_low.wav"
}

& $ff -y -hide_banner -loglevel error `
    -i "$Audio\a1.wav" -i "$Audio\b1_low.wav" -i "$Audio\a2.wav" -i "$Audio\b2_low.wav" `
    -filter_complex "[0:a][1:a][2:a][3:a]concat=n=4:v=0:a=1[out]" -map "[out]" `
    -ar 16000 -ac 1 "$Audio\test_mix.wav"

& $ff -hide_banner -i "$Audio\test_mix.wav" 2>&1 | Select-String 'Duration|Stream'
Get-Item "$Audio\test_mix.wav" | Select-Object Name, @{n='MB'; e = { [math]::Round($_.Length / 1MB, 2) } }
