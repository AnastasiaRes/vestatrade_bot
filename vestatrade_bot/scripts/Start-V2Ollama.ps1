<#
Run the ordinary widget with local Ollama and V2-first ownership on Windows.
The public safety gates remain active; a rejected V2 turn falls back to Legacy.
#>
[CmdletBinding()]
param(
    [int]$Port = $(if ($env:PORT) { [int]$env:PORT } else { 8010 }),
    [switch]$SkipPreparation
)

$ErrorActionPreference = 'Stop'
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $ProjectDir

$PythonBin = if ($env:PYTHON_BIN) {
    $env:PYTHON_BIN
} else {
    Join-Path $ProjectDir '.venv\Scripts\python.exe'
}
if (-not (Test-Path $PythonBin)) {
    throw 'Python environment is missing. Create .venv and install requirements.txt first.'
}

$env:LLM_PROVIDER = 'ollama'
$env:OLLAMA_BASE_URL = if ($env:OLLAMA_BASE_URL) { $env:OLLAMA_BASE_URL } else { 'http://127.0.0.1:11434' }
$env:OLLAMA_MODEL = if ($env:OLLAMA_MODEL) { $env:OLLAMA_MODEL } else { 'qwen3-vl:8b-instruct' }
$env:OLLAMA_MODEL_STRONG = if ($env:OLLAMA_MODEL_STRONG) { $env:OLLAMA_MODEL_STRONG } else { $env:OLLAMA_MODEL }
$env:OLLAMA_EMBEDDING_MODEL = if ($env:OLLAMA_EMBEDDING_MODEL) { $env:OLLAMA_EMBEDDING_MODEL } else { 'bge-m3' }

if (-not $SkipPreparation -and $env:V2_OLLAMA_SKIP_PREPARE -ne '1') {
    & (Join-Path $PSScriptRoot 'Prepare-OllamaV2.ps1')
    if ($LASTEXITCODE -ne 0) {
        throw 'Ollama V2 preparation failed.'
    }
}

# These settings make normal /chat requests V2-first for this process only.
# V2 still passes all semantic, grounding and outcome gates before delivery.
$env:DIALOGUE_V2_ROUTING_ENABLED = 'true'
$env:DIALOGUE_V2_LIVE_DELIVERY_ENABLED = 'true'
$env:DIALOGUE_V2_PUBLIC_PRIMARY_ENABLED = 'true'
$env:DIALOGUE_V2_INTERNAL_CANARY_ENABLED = 'false'
$env:DIALOGUE_V2_INTERNAL_CANARY_PERCENT = '0'
$env:DIALOGUE_V2_LOCAL_PREVIEW_ENABLED = 'false'
$env:DIALOGUE_V2_QA_CONTROLS_ENABLED = 'false'
$env:DIALOGUE_V2_FORCE_LEGACY = 'false'
$env:COMMERCE_EXTERNAL_EXECUTION_ENABLED = 'false'

Write-Host "V2-first widget: http://127.0.0.1:$Port/widget-demo"
Write-Host 'Rollback: stop this process and restart with DIALOGUE_V2_FORCE_LEGACY=true.'
& $PythonBin -m uvicorn app.main:app --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
