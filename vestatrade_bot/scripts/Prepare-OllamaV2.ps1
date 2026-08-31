<#
Prepare the pinned local Ollama models and the one existing passport index.
No secrets are read or printed. This is the Windows counterpart of
prepare_ollama_v2.sh.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $ProjectDir

$PythonBin = if ($env:PYTHON_BIN) {
    $env:PYTHON_BIN
} else {
    Join-Path $ProjectDir '.venv\Scripts\python.exe'
}
$ChatModel = if ($env:OLLAMA_MODEL) { $env:OLLAMA_MODEL } else { 'qwen3-vl:8b-instruct' }
$EmbeddingModel = if ($env:OLLAMA_EMBEDDING_MODEL) { $env:OLLAMA_EMBEDDING_MODEL } else { 'bge-m3' }
$OllamaUrl = if ($env:OLLAMA_BASE_URL) { $env:OLLAMA_BASE_URL } else { 'http://127.0.0.1:11434' }

if (-not (Test-Path $PythonBin)) {
    throw 'Python environment is missing. Create .venv and install requirements.txt first.'
}
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    throw 'Ollama CLI is not installed or is not on PATH. Install Ollama, start it, then run this script again.'
}

foreach ($Model in @($ChatModel, $EmbeddingModel)) {
    & ollama show $Model *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Downloading required Ollama model: $Model"
        & ollama pull $Model
        if ($LASTEXITCODE -ne 0) {
            throw "Could not download Ollama model: $Model"
        }
    } else {
        Write-Host "Ollama model is ready: $Model"
    }
}

$env:LLM_PROVIDER = 'ollama'
$env:OLLAMA_BASE_URL = $OllamaUrl
$env:OLLAMA_MODEL = $ChatModel
$env:OLLAMA_MODEL_STRONG = if ($env:OLLAMA_MODEL_STRONG) { $env:OLLAMA_MODEL_STRONG } else { $ChatModel }
$env:OLLAMA_EMBEDDING_MODEL = $EmbeddingModel

& $PythonBin 'scripts/check_llm.py'
if ($LASTEXITCODE -ne 0) {
    throw "Ollama health check failed. Check that Ollama is running at $OllamaUrl."
}
& $PythonBin 'scripts/prepare_passport_index.py'
if ($LASTEXITCODE -ne 0) {
    throw 'Passport index preparation failed.'
}

Write-Host 'Ollama V2 preparation completed. Start the widget with: .\scripts\Start-V2Ollama.ps1'
