param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Command,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$CliPath = Join-Path $ProjectRoot "cnki-codex-skills\_shared\cnki\cli.py"

if (-not (Test-Path $CliPath)) {
    throw "未找到 CLI: $CliPath"
}

$PythonExe = if (Test-Path $VenvPython) { $VenvPython } else { "python" }

& $PythonExe $CliPath $Command @Arguments
