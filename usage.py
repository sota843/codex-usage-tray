"""Read only rate-limit events. No HTTP, token refresh, or conversation logging."""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import appdata
import config


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SchemaError(ValueError):
    """Messages contain only fixed descriptions, never source field values."""


def parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise SchemaError("イベント時刻の型が不正")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            raise ValueError("timezone required")
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise SchemaError("イベント時刻が不正・timezone必須") from None


def finite_number(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


@dataclass(frozen=True)
class Gauge:
    used_percent: float
    reset: datetime | None

    @property
    def pct(self) -> float:
        return max(0.0, min(1.0, self.used_percent / 100.0))

    def current_pct(self, now: datetime) -> float | None:
        # Unknown reset is not proof that this value is still current.
        return self.pct if self.reset is not None and now < self.reset else None


@dataclass(frozen=True)
class UsageSnapshot:
    session: Gauge | None
    weekly: Gauge | None
    observed_at: datetime | None
    fetched_at: datetime
    error: str | None = None
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.error is None and self.observed_at is not None


def parse_event(record: Any, now: datetime) -> UsageSnapshot | None:
    if not isinstance(record, dict) or not isinstance(record.get("type"), str):
        raise SchemaError("JSONLの外側schemaが不正")
    if record["type"] != "event_msg":
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
        raise SchemaError("イベントschemaが不正")
    if payload["type"] != "token_count":
        return None
    limits = payload.get("rate_limits")
    if limits is None:
        return None  # Token usage can be emitted independently of quota updates.
    if not isinstance(limits, dict):
        raise SchemaError("rate_limitsの型が不正")
    if limits.get("limit_id") not in (None, "codex"):
        return None  # Other metered model buckets must not replace the Codex bucket.
    stamp = parse_datetime(record.get("timestamp"))
    if stamp > now + timedelta(minutes=5):
        raise SchemaError("イベント時刻が未来・時計を確認")
    windows: dict[int, Gauge] = {}
    issues = []
    for key in ("primary", "secondary"):
        window = limits.get(key)
        if window is None:
            continue
        if not isinstance(window, dict):
            raise SchemaError("枠schemaが不正")
        duration = window.get("window_minutes")
        if type(duration) is not int or duration not in (300, 10080):
            issues.append("枠長不明・非対応")
            continue
        value = window.get("used_percent")
        if value is None:
            issues.append("使用率不明")
            continue
        if not finite_number(value):
            raise SchemaError("使用率の型・数値が不正")
        if value < 0 or value > 100:
            issues.append("使用率範囲外・描画を0–100に制限")
        reset_value = window.get("resets_at")
        reset = None
        if reset_value is None:
            issues.append("リセット時刻不明")
        else:
            if type(reset_value) is not int:
                raise SchemaError("リセット時刻はUnix秒の整数が必要")
            try:
                reset = datetime.fromtimestamp(reset_value, timezone.utc)
            except (ValueError, OverflowError, OSError):
                raise SchemaError("リセット時刻が範囲外") from None
        if duration in windows:
            raise SchemaError("同じ枠長が重複")
        windows[duration] = Gauge(float(value), reset)
    if not windows:
        raise SchemaError("対応する使用率の枠なし")
    if len(windows) == 1:
        issues.append("片方の枠が不明")
    return UsageSnapshot(windows.get(300), windows.get(10080), stamp, now,
                         warnings=tuple(dict.fromkeys(issues)))


class LocalLogReader:
    """Bounded tail scans; cache contains only gauges and fixed issue strings."""
    def __init__(self) -> None:
        self._cache: dict[Path, tuple[tuple, UsageSnapshot | None, tuple[str, ...]]] = {}

    @staticmethod
    def _scan(path: Path, cfg: dict, now: datetime) -> tuple[UsageSnapshot | None, tuple[str, ...]]:
        issues = []
        best = None
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - cfg["max_tail_bytes"])
            if start:
                handle.seek(start - 1)
                boundary = handle.read(1) == b"\n"
            else:
                boundary = True
            handle.seek(start)
            data = handle.read(cfg["max_tail_bytes"])
        lines = data.splitlines(keepends=True)
        if not boundary and lines:
            lines.pop(0)
        if lines and not lines[-1].endswith(b"\n"):
            # Never parse a half-written event. A subsequent scan retries it.
            lines.pop()
            issues.append("書込途中の末尾行・次回再試行")
        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                candidate = parse_event(record, now)
                if candidate and (best is None or candidate.observed_at >= best.observed_at):
                    best = candidate
            except (ValueError, UnicodeError, RecursionError) as exc:
                issues.append(str(exc) if isinstance(exc, SchemaError) else "JSONL破損")
        if start and best is None:
            issues.append("末尾読取範囲に使用量なし")
        return best, tuple(dict.fromkeys(issues))

    def fetch(self, cfg: dict, now: datetime | None = None) -> UsageSnapshot:
        now = now or utc_now()
        root = config.sessions_dir(cfg)
        issues: list[str] = []
        files = []
        try:
            if not root.is_dir():
                raise FileNotFoundError()
            def walk_error(exc: OSError) -> None:
                issues.append(f"ログ列挙失敗 ({type(exc).__name__})")
            for folder, _, names in os.walk(root, onerror=walk_error, followlinks=False):
                for name in names:
                    if name.startswith("rollout-") and name.endswith(".jsonl"):
                        path = Path(folder) / name
                        try:
                            stat = path.stat()
                            files.append((stat.st_mtime_ns, path, stat.st_size, stat.st_ino))
                        except OSError as exc:
                            issues.append(f"ログ属性読込失敗 ({type(exc).__name__})")
            files.sort(key=lambda row: (row[0], str(row[1])), reverse=True)
            chosen = files[:cfg["max_files"]]
            if len(files) > len(chosen):
                issues.append("ファイル数上限あり")
            active = {row[1] for row in chosen}
            self._cache = {p: v for p, v in self._cache.items() if p in active}
            best = None
            for modified, path, size, inode in chosen:
                signature = (modified, size, inode, cfg["max_tail_bytes"])
                cached = self._cache.get(path)
                try:
                    if cached is not None and cached[0] == signature:
                        candidate, file_issues = cached[1:]
                    else:
                        candidate, file_issues = self._scan(path, cfg, now)
                        self._cache[path] = (signature, candidate, file_issues)
                    issues.extend(file_issues)
                    if candidate and (best is None or candidate.observed_at > best.observed_at):
                        best = candidate
                except (OSError, ValueError) as exc:
                    issues.append(f"ログ読込失敗 ({type(exc).__name__})")
            if best is None:
                reason = "ログファイルなし" if not files else "使用量イベントなし"
                return UsageSnapshot(None, None, None, now,
                                     error=" / ".join([reason, *dict.fromkeys(issues)]))
            # Fetching an unchanged file must not refresh its observation timestamp.
            return replace(best, fetched_at=now,
                           warnings=tuple(dict.fromkeys((*best.warnings, *issues))))
        except OSError as exc:
            return UsageSnapshot(None, None, None, now,
                                 error=f"ログ取得失敗 ({type(exc).__name__})")


def fetch_snapshot(cfg: dict, reader: LocalLogReader | None = None,
                   now: datetime | None = None) -> UsageSnapshot:
    return (reader or LocalLogReader()).fetch(cfg, now)


def snapshot_to_dict(snap: UsageSnapshot) -> dict:
    def pack(g: Gauge | None) -> dict | None:
        return None if g is None else {"used_percent": g.used_percent,
                                     "resets_at": int(g.reset.timestamp()) if g.reset else None}
    return {"session": pack(snap.session), "weekly": pack(snap.weekly),
            "observed_at": snap.observed_at.isoformat() if snap.observed_at else None,
            "fetched_at": snap.fetched_at.isoformat()}


def snapshot_from_dict(data: Any) -> UsageSnapshot | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise SchemaError("保存された使用量schemaが不正")
    def unpack(value: Any) -> Gauge | None:
        if value is None:
            return None
        if not isinstance(value, dict) or not finite_number(value.get("used_percent")):
            raise SchemaError("保存された使用率が不正")
        reset_value = value.get("resets_at")
        if reset_value is not None and type(reset_value) is not int:
            raise SchemaError("保存されたリセットが不正")
        return Gauge(float(value["used_percent"]),
                     datetime.fromtimestamp(reset_value, timezone.utc) if reset_value is not None else None)
    snap = UsageSnapshot(unpack(data.get("session")), unpack(data.get("weekly")),
                         parse_datetime(data.get("observed_at")), parse_datetime(data.get("fetched_at")))
    if snap.session is None and snap.weekly is None:
        raise SchemaError("保存された枠なし")
    return snap


class UsageMonitor:
    """Shared by CLI/tray; retains readings in memory even if state writes fail."""
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.reader = LocalLogReader()
        self.scope = str(config.sessions_dir(cfg))
        state = appdata.load_state()
        self.storage_error = state.get("storage_error")
        self.last_good = None
        self.current = UsageSnapshot(None, None, None, utc_now(), error="読込中")
        if state.get("scope") == self.scope:
            try:
                self.last_good = snapshot_from_dict(state.get("last_snapshot"))
                if self.last_good:
                    self.current = replace(self.last_good, error="保存値・更新待ち")
            except (ValueError, TypeError, OSError, OverflowError):
                self.storage_error = "保存された使用量が破損"
                appdata.log(self.storage_error)

    def accept(self, snap: UsageSnapshot) -> UsageSnapshot:
        if snap.ok:
            self.last_good = snap
            self.current = snap
        elif self.last_good:
            self.current = replace(self.last_good, error=snap.error, fetched_at=snap.fetched_at)
        else:
            self.current = snap
        try:
            appdata.save_state({"version": 1, "scope": self.scope,
                                "last_snapshot": snapshot_to_dict(self.last_good) if self.last_good else None})
            self.storage_error = None
        except (OSError, ValueError) as exc:
            self.storage_error = f"状態保存失敗 ({type(exc).__name__})"
            appdata.log(self.storage_error)
        return self.current

    def refresh(self, now: datetime | None = None) -> UsageSnapshot:
        return self.accept(self.reader.fetch(self.cfg, now))


def tooltip_text(snap: UsageSnapshot, now: datetime | None = None,
                 stale_after_sec: int = 300) -> str:
    now = now or utc_now()
    lines = ["Codex Usage"]
    for name, gauge in (("5h", snap.session), ("週次", snap.weekly)):
        pct = gauge.current_pct(now) if gauge else None
        value = f"{round(gauge.used_percent, 1):g}%" if pct is not None else "不明"
        reset = gauge.reset.astimezone().strftime("%m/%d %H:%M") if gauge and gauge.reset else "不明"
        if gauge and gauge.reset and now >= gauge.reset:
            reset += " 経過"
        lines.append(f"{name}: {value} / reset {reset}")
    age_sec = max(0, (now - snap.observed_at).total_seconds()) if snap.observed_at else None
    age = f"{int(age_sec // 60)}分前の値" if age_sec is not None else "値なし"
    if snap.error:
        lines.append(f"取得失敗 / {age}")
        lines.append(snap.error)
    elif age_sec is not None:
        lines.append(("古いログ / " if age_sec >= stale_after_sec else "ログ / ") + age)
    if snap.warnings:
        lines.append("注意: " + " / ".join(snap.warnings))
    return "\n".join(lines)


def safe_auth_schema(home: Path) -> dict:
    """Diagnostic only: return whitelisted key names and types; never any values."""
    path = home / "auth.json"
    try:
        with path.open("rb") as handle:
            content = handle.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            return {"status": "サイズ上限超過"}
        data = json.loads(content)
        if not isinstance(data, dict):
            return {"status": "schema不正"}
        allowed = {"auth_mode", "OPENAI_API_KEY", "tokens", "last_refresh"}
        schema = {k: type(data[k]).__name__ for k in sorted(allowed & data.keys())}
        tokens = data.get("tokens")
        if isinstance(tokens, dict):
            schema["tokens"] = {k: type(tokens[k]).__name__ for k in
                                ("id_token", "access_token", "refresh_token", "account_id") if k in tokens}
        return {"status": "存在・内容は非表示", "schema": schema}
    except FileNotFoundError:
        return {"status": "ファイルなし（keyring等の可能性・ログ読取に認証不要）"}
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        return {"status": f"読込失敗 ({type(exc).__name__})"}


def diagnose(cfg: dict) -> None:
    print("source: local_logs / network: unused / auth: read-only diagnostic")
    print("codex_home:", config.codex_home(cfg))
    print("sessions_dir:", config.sessions_dir(cfg))
    print("auth:", json.dumps(safe_auth_schema(config.codex_home(cfg)), ensure_ascii=False))
    print("log:", appdata.LOG_PATH)
    print("state:", appdata.STATE_PATH)
    snap = fetch_snapshot(cfg)
    print(tooltip_text(snap, stale_after_sec=cfg["stale_after_sec"]))
    for warning in cfg.get("_warnings", []):
        print(warning)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["diag"], nargs="?")
    args = parser.parse_args()
    cfg = config.load_config()
    if args.action == "diag":
        diagnose(cfg)
    else:
        monitor = UsageMonitor(cfg)
        snap = monitor.refresh()
        output = tooltip_text(snap, stale_after_sec=cfg["stale_after_sec"])
        print(output)
        appdata.log(output.replace("\n", " | "))
        if monitor.storage_error:
            print(monitor.storage_error)
        raise SystemExit(0 if snap.ok else 1)
