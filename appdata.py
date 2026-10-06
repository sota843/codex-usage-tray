"""Local, atomic state and rotating logs. Never pass raw session/auth content here."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

APP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CodexUsageTray"
LOG_PATH = APP_DIR / "tray.log"
STATE_PATH = APP_DIR / "state.json"
_lock = threading.RLock()


def log(message: str) -> str | None:
    """Return a safe failure reason if file logging fails (also report to stderr)."""
    line = f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] {message}"
    if sys.stdout is not None:
        try:
            print(line, flush=True)
        except (OSError, UnicodeError):
            pass
    try:
        with _lock:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            if LOG_PATH.exists() and LOG_PATH.stat().st_size >= 512 * 1024:
                os.replace(LOG_PATH, LOG_PATH.with_suffix(".log.1"))
            with LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except OSError as exc:
        reason = f"ログ保存失敗 ({type(exc).__name__})"
        if sys.stderr is not None:
            try:
                print(reason, file=sys.stderr)
            except (OSError, UnicodeError):
                pass
        return reason
    return None


def load_state() -> dict[str, Any]:
    with _lock:
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("state root must be object")
            return data
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
            reason = f"状態読込失敗 ({type(exc).__name__})"
            log(reason)
            return {"storage_error": reason}


def save_state(state: dict[str, Any]) -> None:
    """Raises on failure so the tray can surface storage failures in its UI."""
    with _lock:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                             dir=STATE_PATH.parent, delete=False) as handle:
                name = handle.name
                json.dump(state, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, STATE_PATH)
        finally:
            if name and Path(name).exists():
                Path(name).unlink()
