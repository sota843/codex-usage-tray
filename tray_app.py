"""Windows 11 pystray entry point, one worker and a reset-aware display timer."""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from dataclasses import replace

import appdata
import autostart
import config
import usage


def tooltip_limit(text: str) -> str:
    """Windows NOTIFYICONDATA.szTip has 128 UTF-16 units including terminator."""
    return text.encode("utf-16-le")[:254].decode("utf-16-le", errors="ignore")


class RefreshGate:
    def __init__(self, minimum: float) -> None:
        self.minimum = max(5.0, minimum)
        self.last_started = float("-inf")
        self.last_requested = float("-inf")
        self._lock = threading.Lock()

    def request(self, now: float) -> float:
        with self._lock:
            remaining = max(self.last_started, self.last_requested) + self.minimum - now
            if remaining > 0:
                return remaining
            self.last_requested = now
            return 0

    def begin(self, now: float) -> float:
        with self._lock:
            remaining = self.last_started + self.minimum - now
            if remaining > 0:
                return remaining
            self.last_started = now
            return 0


class TrayApp:
    def __init__(self) -> None:
        import pystray
        import gauge
        self.pystray = pystray
        self.gauge = gauge
        self.cfg = config.load_config()
        self.monitor = usage.UsageMonitor(self.cfg)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._gate = RefreshGate(self.cfg["manual_min_interval_sec"])
        self._notice = self.monitor.storage_error
        self._last_image_key = None
        self._last_title = None
        self._worker_thread = None
        self.icon = pystray.Icon("codex_usage", gauge.make_icon(self.monitor.current, self.cfg),
                                 "Codex Usage: 読込中", self._menu())

    def _menu(self):
        return self.pystray.Menu(
            self.pystray.MenuItem("今すぐ更新", self._refresh),
            self.pystray.MenuItem("Windows起動時に自動起動", self._autostart,
                                  checked=lambda _: autostart.is_enabled()),
            self.pystray.MenuItem("ログを開く", self._open_log),
            self.pystray.Menu.SEPARATOR,
            self.pystray.MenuItem("終了", self._quit),
        )

    def _log(self, message: str) -> None:
        failure = appdata.log(message)
        if failure:
            self._notice = failure

    def _notify(self, message: str) -> None:
        try:
            self.icon.notify(message, "Codex Usage Tray")
        except Exception as exc:
            self._log(f"通知失敗 ({type(exc).__name__})")
            self._notice = message

    def _refresh(self, _icon=None, _item=None) -> None:
        remaining = self._gate.request(time.monotonic())
        if remaining > 0:
            self._notify(f"{int(remaining) + 1}秒後に更新できます")
        else:
            self._wake.set()

    def _autostart(self, _icon=None, _item=None) -> None:
        try:
            enabled = autostart.toggle()
            self._log(f"自動起動: {'ON' if enabled else 'OFF'}")
            self.icon.update_menu()
        except Exception as exc:
            reason = f"自動起動設定失敗 ({type(exc).__name__})"
            self._log(reason)
            self._notify(reason)

    def _open_log(self, _icon=None, _item=None) -> None:
        try:
            os.startfile(str(appdata.LOG_PATH))
        except OSError as exc:
            reason = f"ログを開けません ({type(exc).__name__})"
            self._log(reason)
            self._notify(reason)

    def _quit(self, _icon=None, _item=None) -> None:
        self._stop.set()
        self._wake.set()
        self.icon.stop()

    def _paint(self) -> None:
        snap = self.monitor.current
        now = usage.utc_now()
        outer = snap.session.current_pct(now) if snap.session else None
        inner = snap.weekly.current_pct(now) if snap.weekly else None
        image_key = (outer, inner)
        if image_key != self._last_image_key:
            self.icon.icon = self.gauge.make_icon(snap, self.cfg, now)
            self._last_image_key = image_key
        title = usage.tooltip_text(snap, now, self.cfg["stale_after_sec"])
        notices = [self.monitor.storage_error, self._notice, *self.cfg.get("_warnings", [])]
        notices = list(dict.fromkeys(n for n in notices if n))
        if notices:
            # Surface storage/config failures before the optional source warnings.
            lines = title.splitlines()
            summary = notices[0] + (f" ほか{len(notices) - 1}件" if len(notices) > 1 else "")
            title = "\n".join([lines[0], "注意: " + summary, *lines[1:]])
        title = tooltip_limit(title)
        if title != self._last_title:
            self.icon.title = title
            self._last_title = title

    def _worker(self) -> None:
        interval = max(10, self.cfg["poll_interval_sec"])
        next_poll = 0.0
        self._log(f"起動: source=local_logs, interval={interval}s, network=unused")
        for warning in self.cfg.get("_warnings", []):
            self._log(warning)
        if self._notice:
            self._log(self._notice)
        while not self._stop.is_set():
            try:
                current_time = time.monotonic()
                if self._wake.is_set() or current_time >= next_poll:
                    wait = self._gate.begin(current_time)
                    if wait <= 0:
                        self._wake.clear()
                        snapshot = self.monitor.refresh()
                        self._log(usage.tooltip_text(snapshot,
                                  stale_after_sec=self.cfg["stale_after_sec"]).replace("\n", " | "))
                        next_poll = time.monotonic() + interval
                self._paint()  # Re-evaluate reset expiry even between polls.
            except Exception as exc:
                reason = f"更新処理失敗 ({type(exc).__name__})"
                self._wake.clear()
                self.monitor.current = replace(self.monitor.current, error=reason)
                self._log(reason)
                try:
                    self._paint()
                except Exception:
                    self._notify(reason)
                next_poll = time.monotonic() + interval
            self._stop.wait(1)
        self._log("ポーリング終了")

    def _setup(self, icon) -> None:
        icon.visible = True
        self._worker_thread = threading.Thread(target=self._worker, name="codex-usage-poll", daemon=True)
        self._worker_thread.start()
        self._notify("起動しました。タスクトレイの隠れたアイコンも確認してください。")

    def run(self) -> None:
        try:
            self.icon.run(setup=self._setup)
        finally:
            self._stop.set()
            if self._worker_thread:
                self._worker_thread.join(timeout=2)


def _instance_mutex():
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateMutexW(None, False, "Local\\CodexUsageTray")
    if not handle:
        raise OSError("instance mutex unavailable")
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle(handle)
        return kernel, None
    return kernel, handle


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    if sys.platform != "win32":
        appdata.log("Windows 11専用です。この環境ではトレイを起動できません。")
        return 1
    kernel, handle = None, None
    try:
        kernel, handle = _instance_mutex()
        if handle is None:
            appdata.log("既に起動しています")
            return 0
        TrayApp().run()
        return 0
    except Exception as exc:
        reason = f"起動失敗 ({type(exc).__name__})。依存パッケージとtray.logを確認してください。"
        appdata.log(reason)
        ctypes.windll.user32.MessageBoxW(None, reason, "Codex Usage Tray", 0x10)
        return 1
    finally:
        if kernel and handle:
            kernel.CloseHandle(handle)


if __name__ == "__main__":
    raise SystemExit(main())
