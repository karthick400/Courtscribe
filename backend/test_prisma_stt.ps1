param(
    [string] $AudioFile,

    [ValidateRange(1, 3600)]
    [int] $RecordSeconds = 10,

    [string] $LanguageCode = "en-IN",
    [string] $Model = "prisma-v3.0",

    [string] $SpeakerConfigFile,
    [string] $OutputJson
)

$ErrorActionPreference = "Stop"
$envFile = Join-Path $PSScriptRoot ".env"
$recordedFile = $null

if (-not (Test-Path -LiteralPath $envFile)) {
    throw "Missing $envFile. Add gnaniAPI=<your-api-key> to the file."
}

$apiLine = Get-Content -LiteralPath $envFile |
    Where-Object { $_ -match "^\s*gnaniAPI\s*=" } |
    Select-Object -First 1

if (-not $apiLine -or $apiLine -notmatch "^\s*gnaniAPI\s*=\s*[`"']?([^`"'\s]+)") {
    throw "gnaniAPI is missing or invalid in $envFile."
}

$apiKey = $Matches[1]

if ($SpeakerConfigFile) {
    if (-not (Test-Path -LiteralPath $SpeakerConfigFile -PathType Leaf)) {
        throw "Speaker configuration file not found: $SpeakerConfigFile"
    }

    try {
        $speakerConfig = Get-Content -LiteralPath $SpeakerConfigFile -Raw | ConvertFrom-Json
    }
    catch {
        throw "Speaker configuration is not valid JSON: $SpeakerConfigFile"
    }
}
else {
    $speakerConfig = [pscustomobject]@{
        speakers = @(
            [pscustomobject]@{ id = "judge-1"; role = "Judge"; name = "" },
            [pscustomobject]@{ id = "complainant-counsel-1"; role = "Complainant's Counsel"; name = "" },
            [pscustomobject]@{ id = "defendant-counsel-1"; role = "Defendant's Counsel"; name = "" },
            [pscustomobject]@{ id = "witness-1"; role = "Witness"; name = "" },
            [pscustomobject]@{ id = "accused-1"; role = "Accused"; name = "" }
        )
    }
}

if (-not $speakerConfig.speakers -or $speakerConfig.speakers.Count -eq 0) {
    throw "Speaker configuration must contain at least one speaker."
}

$allowedRoles = @("Judge", "Complainant's Counsel", "Defendant's Counsel", "Witness", "Accused")
$speakerIds = @{}
$speakerNames = @()
foreach ($speaker in $speakerConfig.speakers) {
    if ($speaker.role -notin $allowedRoles) {
        throw "Unsupported speaker role '$($speaker.role)'."
    }
    if ([string]::IsNullOrWhiteSpace($speaker.id) -or $speakerIds.ContainsKey($speaker.id)) {
        throw "Speaker IDs must be present and unique."
    }
    $speakerIds[$speaker.id] = $true
    if (-not [string]::IsNullOrWhiteSpace($speaker.name)) {
        $speakerNames += $speaker.name
    }
}

if ([string]::IsNullOrWhiteSpace($AudioFile)) {
    $recordedFile = Join-Path ([System.IO.Path]::GetTempPath()) "courtscribe-$([guid]::NewGuid()).wav"
    $python = Join-Path $PSScriptRoot "..\court\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        $python = "python"
    }

    & $python (Join-Path $PSScriptRoot "mic_test") `
        --output $recordedFile `
        --seconds $RecordSeconds
    if ($LASTEXITCODE -ne 0) {
        throw "Microphone recording failed with exit code $LASTEXITCODE."
    }
    $AudioFile = $recordedFile
}

if (-not (Test-Path -LiteralPath $AudioFile -PathType Leaf)) {
    throw "Audio file not found: $AudioFile"
}

$transcript = & curl.exe --fail-with-body --silent --show-error --request POST `
    "https://api.vachana.ai/stt/v3" `
    --header "X-API-Key-ID: $apiKey" `
    --form "audio_file=@$AudioFile" `
    --form "language_code=$LanguageCode" `
    --form "model=$Model" `
    --form "format=transcribe" `
    --form "speaker_names=$($speakerNames | ConvertTo-Json -Compress)"

if ($LASTEXITCODE -ne 0) {
    throw "Prisma STT request failed with exit code $LASTEXITCODE."
}

Write-Output "`nTranscript:"
$transcriptText = $transcript -join [Environment]::NewLine
Write-Output $transcriptText

try {
    $apiResponse = $transcriptText | ConvertFrom-Json
    $transcriptText = $apiResponse.transcript
}
catch {
    $apiResponse = [pscustomobject]@{
        success = $false
        transcript = $transcriptText
    }
}

$structuredTranscript = [pscustomobject]@{
    speakers = @($speakerConfig.speakers)
    segments = @(
        [pscustomobject]@{
            id = "segment-1"
            speakerId = $null
            role = $null
            name = $null
            text = $transcriptText
        }
    )
    transcript = $transcriptText
    apiResponse = $apiResponse
}

if ($OutputJson) {
    $structuredTranscript | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $OutputJson -Encoding utf8
    Write-Output "Structured transcript saved to $OutputJson"
}

if ($recordedFile -and (Test-Path -LiteralPath $recordedFile)) {
    Remove-Item -LiteralPath $recordedFile -Force
}
