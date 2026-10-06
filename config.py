"""Partial config is deep merged, validated, and accompanied by UI warnings."""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).resolve().with_name("config.json")
DEFAULTS: dict[str, Any] = {
    "source": "local_logs",
    "codex_home": None,
    "sessions_dir": None,
    "poll_interval_sec": 30,
    "manual_min_interval_sec": 5,
    "stale_after_sec": 300,
    "max_files": 100,
    "max_tail_bytes": 4194304,
    "thresholds": {"warn": 0.60, "crit": 0.85},
    "colors": {"ok": [60, 200, 90], "warn": [235, 185, 20],
               "crit": [225, 55, 55], "track": [70, 70, 70], "unknown": [130, 130, 130]},
    "show_center_text": True,
    "icon_size": 64,
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg = copy.deepcopy(DEFAULTS)
    errors = []
    try:
        user = json.loads((path or CONFIG_PATH).read_text(encoding="utf-8-sig"))
        if not isinstance(user, dict):
            raise ValueError("config root must be object")
        cfg = _deep_merge(cfg, user)
    except FileNotFoundError:
        errors.append("設定なし・既定値")
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        errors.append(f"設定読込失敗 ({type(exc).__name__})")

    for key, floor, ceiling in (
        ("poll_interval_sec", 10, 86400), ("manual_min_interval_sec", 5, 86400),
        ("stale_after_sec", 60, 604800), ("max_files", 1, 10000),
        ("max_tail_bytes", 1024, 67108864), ("icon_size", 32, 256),
    ):
        value = cfg.get(key)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or value < floor or value > ceiling or not math.isfinite(value)):
            errors.append(f"設定 {key} 無効・既定値")
            cfg[key] = DEFAULTS[key]
        else:
            cfg[key] = int(value)

    thresholds = cfg.get("thresholds")
    if (not isinstance(thresholds, dict)
            or any(isinstance(thresholds.get(k), bool)
                   or not isinstance(thresholds.get(k), (int, float)) for k in ("warn", "crit"))
            or not 0 <= thresholds["warn"] < thresholds["crit"] <= 1):
        errors.append("設定 thresholds 無効・既定値")
        cfg["thresholds"] = copy.deepcopy(DEFAULTS["thresholds"])
    colors = cfg.get("colors")
    if not isinstance(colors, dict):
        colors = {}
    for key, fallback in DEFAULTS["colors"].items():
        rgb = colors.get(key)
        if (not isinstance(rgb, list) or len(rgb) != 3
                or any(type(x) is not int or not 0 <= x <= 255 for x in rgb)):
            errors.append(f"設定 colors.{key} 無効・既定値")
            colors[key] = fallback.copy()
    cfg["colors"] = colors
    for key in ("codex_home", "sessions_dir"):
        if cfg.get(key) is not None and (not isinstance(cfg[key], str) or not cfg[key].strip()):
            errors.append(f"設定 {key} 無効・既定値")
            cfg[key] = None
    if type(cfg.get("show_center_text")) is not bool:
        errors.append("設定 show_center_text 無効・既定値")
        cfg["show_center_text"] = True
    if cfg.get("source") != "local_logs":
        errors.append("設定 source 非対応・local_logsを使用")
        cfg["source"] = "local_logs"
    cfg["_warnings"] = errors
    return cfg


def codex_home(cfg: dict) -> Path:
    value = cfg.get("codex_home") or os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
    return Path(os.path.expandvars(value)).expanduser().resolve()


def sessions_dir(cfg: dict) -> Path:
    value = cfg.get("sessions_dir")
    return Path(os.path.expandvars(value)).expanduser().resolve() if value else codex_home(cfg) / "sessions"
