#!/usr/bin/env python3
"""Portable wrapper for the cnki-review-fixed-local skill."""

import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[4]


def ensure_project_python() -> None:
    """Relaunch with the project virtualenv when available."""

    if os.environ.get("CNKI_WRAPPER_REEXEC") == "1":
        return

    candidates = [
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python",
    ]
    current = Path(sys.executable).resolve()
    for candidate in candidates:
        if not candidate.exists():
            continue
        target = candidate.resolve()
        if target == current:
            return
        env = os.environ.copy()
        env["CNKI_WRAPPER_REEXEC"] = "1"
        result = subprocess.run([str(target), __file__, *sys.argv[1:]], cwd=str(ROOT), env=env)
        raise SystemExit(result.returncode)


def _extract_cdp_url(argv: list[str]) -> str:
    default = "http://127.0.0.1:9222"
    for index, token in enumerate(argv):
        if token == "--cdp-url" and index + 1 < len(argv):
            return argv[index + 1]
        if token.startswith("--cdp-url="):
            return token.split("=", 1)[1]
    return default


def _port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


def _find_chrome_exe() -> Path | None:
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ]
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(Path(local_appdata) / "Google" / "Chrome" / "Application" / "chrome.exe")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def ensure_cdp_ready() -> None:
    """Auto-start the project Chrome debug session when local CDP is absent."""

    cdp_url = _extract_cdp_url(sys.argv[1:])
    parsed = urlparse(cdp_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 9222
    if host not in {"127.0.0.1", "localhost"}:
        return
    if _port_is_open(host, port):
        return

    chrome_exe = _find_chrome_exe()
    if chrome_exe is None:
        raise SystemExit("未找到 Chrome，无法自动拉起 CDP 浏览器。")

    profile_dir = ROOT / ".chrome-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = [
        str(chrome_exe),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "https://www.cnki.net/",
    ]
    creationflags = 0
    for flag_name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
        creationflags |= int(getattr(subprocess, flag_name, 0))
    subprocess.Popen(args, cwd=str(ROOT), creationflags=creationflags)

    deadline = time.time() + 20
    while time.time() < deadline:
        if _port_is_open(host, port):
            return
        time.sleep(1)

    raise SystemExit(f"CDP 端口 {host}:{port} 启动失败，请检查 Chrome 是否成功拉起。")


ensure_project_python()
ensure_cdp_ready()
sys.path.append(str(ROOT / "cnki-codex-skills" / "_shared" / "cnki"))

from skill_wrapper import run_skill  # type: ignore  # noqa: E402

run_skill("review-fixed")
