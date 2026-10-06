"""Startup shortcut through Windows PowerShell COM, with no extra dependency."""
from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHORTCUT_NAME = "CodexUsageTray.lnk"


def shortcut_path() -> Path:
    if sys.platform != "win32":
        raise OSError("Windows 11専用です")
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup" / SHORTCUT_NAME


def is_enabled() -> bool:
    return shortcut_path().is_file()


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def enable() -> None:
    path = shortcut_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        raise FileNotFoundError("pythonw.exeが見つかりません")
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        "$shell = New-Object -ComObject WScript.Shell",
        f"$shortcut = $shell.CreateShortcut({_ps_quote(str(path))})",
        f"$shortcut.TargetPath = {_ps_quote(str(pythonw))}",
        f"$shortcut.Arguments = {_ps_quote(chr(34) + str(HERE / 'tray_app.py') + chr(34))}",
        f"$shortcut.WorkingDirectory = {_ps_quote(str(HERE))}",
        "$shortcut.WindowStyle = 7",
        "$shortcut.Save()",
    ])
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                   check=True, capture_output=True, timeout=15,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def disable() -> None:
    shortcut_path().unlink(missing_ok=True)


def toggle() -> bool:
    disable() if is_enabled() else enable()
    return is_enabled()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", nargs="?", default="status", choices=["status", "enable", "disable"])
    action = parser.parse_args().action
    try:
        if action == "enable":
            enable()
        elif action == "disable":
            disable()
        print("autostart:", is_enabled(), "->", shortcut_path())
    except (OSError, KeyError, subprocess.SubprocessError) as exc:
        print("自動起動設定失敗:", type(exc).__name__)
        raise SystemExit(1)
