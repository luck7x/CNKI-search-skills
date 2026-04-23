param(
    [string]$PythonCommand = "py -3"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvDir = Join-Path $ProjectRoot ".venv"
$RequirementsFile = Join-Path $ProjectRoot "requirements.txt"

if (-not (Test-Path $RequirementsFile)) {
    throw "未找到依赖文件: $RequirementsFile"
}

if (-not (Test-Path $VenvDir)) {
    Write-Host "创建虚拟环境: $VenvDir"
    & powershell -NoProfile -Command "$PythonCommand -m venv `"$VenvDir`""
}

$PythonExe = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    throw "虚拟环境 Python 不存在: $PythonExe"
}

Write-Host "升级 pip"
& $PythonExe -m pip install --upgrade pip

Write-Host "安装项目依赖"
& $PythonExe -m pip install -r $RequirementsFile

Write-Host "验证 Playwright 导入"
& $PythonExe -c "import playwright; print('playwright ok')"

Write-Host ""
Write-Host "完成。后续命令入口:"
Write-Host "  .\scripts\start-chrome-debug.ps1"
Write-Host "  .\scripts\cnki.ps1 search --query `"人工智能`""
