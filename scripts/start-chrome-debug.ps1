param(
    [int]$Port = 9222,
    [string]$Url = "https://www.cnki.net/",
    [string]$ProfileDir = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $ProfileDir) {
    $ProfileDir = Join-Path $ProjectRoot ".chrome-profile"
}

$ChromeCandidates = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
)

$ChromeExe = $ChromeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $ChromeExe) {
    throw "未找到 Chrome，可手动修改脚本中的路径。"
}

New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null

$Arguments = @(
    "--remote-debugging-port=$Port"
    "--user-data-dir=$ProfileDir"
    "--no-first-run"
    "--no-default-browser-check"
    $Url
)

Start-Process -FilePath $ChromeExe -ArgumentList $Arguments | Out-Null

Write-Host "Chrome 已启动"
Write-Host "  远程调试端口: $Port"
Write-Host "  本地隔离配置: $ProfileDir"
Write-Host "  首次请在这个窗口里登录知网和机构账号"
