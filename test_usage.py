"""Synthetic JSONL/state fixtures only; no Codex session, credential or HTTP access."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import appdata
import config
import usage

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def window(percent=30, minutes=300, reset=None):
    return {"used_percent": percent, "window_minutes": minutes,
            "resets_at": int((reset or (NOW + timedelta(hours=2))).timestamp())}


def event(primary=None, secondary=None, stamp=NOW, **limits):
    return {"timestamp": stamp.isoformat(), "type": "event_msg", "payload": {
        "type": "token_count", "info": None,
        "rate_limits": {"primary": primary, "secondary": secondary, **limits}}}


def normal(stamp=NOW):
    return event(window(30), window(70, 10080, NOW + timedelta(days=3)), stamp)


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()
        self.cfg = copy.deepcopy(config.DEFAULTS)
        self.cfg["sessions_dir"] = str(self.sessions)
        self.cfg["codex_home"] = str(self.root)
        self.stack.enter_context(patch("appdata.STATE_PATH", self.root / "state.json"))
        self.stack.enter_context(patch("appdata.LOG_PATH", self.root / "tray.log"))
        self.logs = self.stack.enter_context(patch("appdata.log"))
        self.socket = self.stack.enter_context(patch("socket.socket", side_effect=AssertionError("no network")))

    def write(self, records, name="rollout-test.jsonl"):
        path = self.sessions / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        return path

    def fetch(self):
        return usage.fetch_snapshot(self.cfg, now=NOW)

    def test_normal_windows_and_unix_seconds(self):
        self.write([normal()], "2026/10/05/rollout-test.jsonl")
        snap = self.fetch()
        self.assertTrue(snap.ok, snap.error)
        self.assertEqual(snap.session.used_percent, 30)
        self.assertEqual(snap.weekly.used_percent, 70)
        self.assertEqual(snap.session.reset, NOW + timedelta(hours=2))
        self.assertEqual(snap.weekly.reset, NOW + timedelta(days=3))
        self.assertEqual(snap.session.reset.tzinfo, timezone.utc)
        self.socket.assert_not_called()

    def test_window_duration_not_position(self):
        snap = usage.parse_event(event(window(70, 10080), window(30, 300)), NOW)
        self.assertEqual(snap.session.used_percent, 30)
        self.assertEqual(snap.weekly.used_percent, 70)

    def test_percent_boundaries_and_clamped_geometry(self):
        for percent, expected in [(0, 0), (100, 1), (125, 1), (-3, 0)]:
            with self.subTest(percent=percent):
                snap = usage.parse_event(event(window(percent), window(0, 10080)), NOW)
                self.assertEqual(snap.session.used_percent, percent)
                self.assertEqual(snap.session.current_pct(NOW), expected)
                self.assertIn(f"{percent}%", usage.tooltip_text(snap, NOW))

    def test_null_and_missing_percent(self):
        for value in [None, "missing"]:
            w = window(None)
            if value == "missing":
                w.pop("used_percent")
            snap = usage.parse_event(event(w, window(42, 10080)), NOW)
            self.assertIsNone(snap.session)
            self.assertEqual(snap.weekly.used_percent, 42)

    def test_single_window(self):
        self.write([event(None, window(42, 10080))])
        snap = self.fetch()
        self.assertTrue(snap.ok)
        self.assertIsNone(snap.session)
        self.assertIn("5h: 不明", usage.tooltip_text(snap, NOW))

    def test_null_quota_event_does_not_erase_known_reading(self):
        record = normal(NOW + timedelta(seconds=1))
        record["payload"]["rate_limits"] = None
        self.write([normal(), record])
        self.assertEqual(self.fetch().observed_at, NOW)

    def test_other_bucket_is_ignored(self):
        self.write([normal(), event(window(99), None, NOW + timedelta(seconds=1), limit_id="other")])
        self.assertEqual(self.fetch().session.used_percent, 30)

    def test_codex_bucket_is_supported(self):
        self.write([event(window(42), None, limit_id="codex")])
        self.assertEqual(self.fetch().session.used_percent, 42)

    def test_missing_duration_never_guesses_five_hours(self):
        for minutes in [None, 60, 10081, True, "300"]:
            with self.subTest(minutes=minutes):
                snap = usage.parse_event(event(window(30, minutes), window(42, 10080)), NOW)
                self.assertIsNone(snap.session)
                self.assertEqual(snap.weekly.used_percent, 42)

    def test_no_supported_duration_is_failure(self):
        self.write([event(window(30, 60))])
        snap = self.fetch()
        self.assertFalse(snap.ok)
        self.assertIn("対応する使用率の枠なし", snap.error)

    def test_expired_reset_is_unknown_not_zero(self):
        self.write([event(window(70, reset=NOW), window(25, 10080))])
        snap = self.fetch()
        self.assertIsNone(snap.session.current_pct(NOW))
        self.assertEqual(snap.session.used_percent, 70)
        self.assertEqual(snap.weekly.current_pct(NOW), .25)
        self.assertIn("5h: 不明", usage.tooltip_text(snap, NOW))
        self.assertIn("経過", usage.tooltip_text(snap, NOW))

    def test_unknown_reset_is_not_current_usage(self):
        for missing in [False, True]:
            w = window(30)
            w["resets_at"] = None
            if missing:
                w.pop("resets_at")
            snap = usage.parse_event(event(w, window(42, 10080)), NOW)
            self.assertIsNone(snap.session.current_pct(NOW))
            self.assertIn("リセット時刻不明", snap.warnings)

    def test_timestamp_offset_conversion(self):
        record = normal()
        record["timestamp"] = "2026-10-05T21:00:00+09:00"
        snap = usage.parse_event(record, NOW)
        self.assertEqual(snap.observed_at, NOW)
        self.assertEqual(snap.session.reset.astimezone(timezone(timedelta(hours=9))).hour, 23)

    def test_timestamp_requires_timezone(self):
        record = normal()
        record["timestamp"] = "2026-10-05T12:00:00"
        self.write([record])
        self.assertIn("timezone", self.fetch().error)

    def test_future_event_rejected(self):
        self.write([normal(NOW + timedelta(hours=1))])
        self.assertIn("時計を確認", self.fetch().error)

    def test_latest_event_selected_by_timestamp(self):
        self.write([normal(), event(window(42), None, NOW - timedelta(minutes=1))])
        self.write([event(window(80), None, NOW - timedelta(hours=1))], "rollout-new-name.jsonl")
        self.assertEqual(self.fetch().session.used_percent, 30)

    def test_new_partial_snapshot_does_not_mix_old_weekly_value(self):
        self.write([normal(), event(window(40), None, NOW + timedelta(seconds=1))])
        snap = self.fetch()
        self.assertEqual(snap.session.used_percent, 40)
        self.assertIsNone(snap.weekly)

    def test_corrupt_json_reports_failure(self):
        (self.sessions / "rollout-test.jsonl").write_text('{invalid}\n', encoding="utf-8")
        snap = self.fetch()
        self.assertFalse(snap.ok)
        self.assertIn("JSONL破損", snap.error)

    def test_corrupt_line_does_not_hide_valid_event(self):
        path = self.write([normal()])
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{invalid}\n')
        snap = self.fetch()
        self.assertTrue(snap.ok)
        self.assertIn("JSONL破損", snap.warnings)
        self.assertIn("JSONL破損", usage.tooltip_text(snap, NOW))

    def test_unexpected_schemas(self):
        for record in [[], {}, {"type": "event_msg", "payload": []},
                       {"type": "event_msg", "payload": {"type": "token_count", "rate_limits": []}}]:
            with self.subTest(record=record):
                self.write([record])
                self.assertFalse(self.fetch().ok)

    def test_invalid_numeric_values_and_reset_types(self):
        for value in [True, "30", float("nan"), float("inf"), [], 10**400]:
            with self.subTest(value=value):
                self.write([event(window(value))])
                self.assertIn("使用率", self.fetch().error)
        for value in [True, "123", 1.5, 10**30]:
            w = window()
            w["resets_at"] = value
            self.write([event(w)])
            self.assertIn("リセット", self.fetch().error)

    def test_duplicate_duration_is_not_silently_overwritten(self):
        self.write([event(window(30), window(90))])
        self.assertIn("重複", self.fetch().error)

    def test_missing_directory_and_empty_directory(self):
        self.assertIn("ログファイルなし", self.fetch().error)
        self.sessions.rmdir()
        self.assertIn("FileNotFoundError", self.fetch().error)

    def test_permission_error_is_visible(self):
        self.write([normal()])
        with patch.object(usage.LocalLogReader, "_scan", side_effect=PermissionError):
            self.assertIn("PermissionError", self.fetch().error)

    def test_file_disappears_while_reading(self):
        self.write([normal()])
        with patch.object(usage.LocalLogReader, "_scan", side_effect=FileNotFoundError):
            self.assertIn("FileNotFoundError", self.fetch().error)

    def test_partial_last_line_is_retried_after_completion(self):
        path = self.write([normal()])
        updated = event(window(45), None, NOW + timedelta(seconds=1))
        encoded = json.dumps(updated)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(encoded[:40])
        reader = usage.LocalLogReader()
        first = reader.fetch(self.cfg, NOW)
        self.assertEqual(first.session.used_percent, 30)
        self.assertIn("書込途中", " ".join(first.warnings))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(encoded[40:] + "\n")
        self.assertEqual(reader.fetch(self.cfg, NOW).session.used_percent, 45)

    def test_cached_read_keeps_original_observation_and_expires(self):
        self.write([normal()])
        reader = usage.LocalLogReader()
        with patch.object(reader, "_scan", wraps=reader._scan) as scan:
            first = reader.fetch(self.cfg, NOW)
            later = reader.fetch(self.cfg, NOW + timedelta(hours=3))
        self.assertEqual(scan.call_count, 1)
        self.assertEqual(later.observed_at, first.observed_at)
        self.assertIsNone(later.session.current_pct(later.fetched_at))
        self.assertIn("180分前", usage.tooltip_text(later, later.fetched_at))

    def test_tail_read_handles_large_unrelated_record(self):
        self.cfg["max_tail_bytes"] = 1024
        path = self.write([{"type": "response_item", "payload": "x" * 2048}, normal()])
        self.assertGreater(path.stat().st_size, 1024)
        self.assertEqual(self.fetch().session.used_percent, 30)

    def test_failure_retains_snapshot_and_persists_across_restart(self):
        path = self.write([normal()])
        monitor = usage.UsageMonitor(self.cfg)
        good = monitor.refresh(NOW)
        path.unlink()
        failed = monitor.refresh(NOW + timedelta(minutes=1))
        self.assertEqual(failed.session, good.session)
        self.assertEqual(failed.observed_at, NOW)
        self.assertIn("取得失敗 / 1分前", usage.tooltip_text(failed, failed.fetched_at))
        restored = usage.UsageMonitor(self.cfg)
        self.assertEqual(restored.last_good.session, good.session)
        failed_again = restored.refresh(NOW + timedelta(hours=3))
        self.assertIsNone(failed_again.session.current_pct(failed_again.fetched_at))

    def test_state_write_failure_keeps_value_in_memory_and_surfaces_error(self):
        self.write([normal()])
        monitor = usage.UsageMonitor(self.cfg)
        with patch("appdata.save_state", side_effect=PermissionError):
            monitor.refresh(NOW)
        self.assertEqual(monitor.last_good.session.used_percent, 30)
        self.assertIn("PermissionError", monitor.storage_error)
        self.logs.assert_called()

    def test_state_only_contains_quota_and_storage_scope(self):
        self.write([normal()])
        monitor = usage.UsageMonitor(self.cfg)
        monitor.refresh(NOW)
        data = json.loads(appdata.STATE_PATH.read_text())
        self.assertEqual(set(data), {"version", "scope", "last_snapshot"})
        self.assertEqual(set(data["last_snapshot"]), {"session", "weekly", "observed_at", "fetched_at"})

    def test_corrupt_state_does_not_crash(self):
        appdata.STATE_PATH.write_text("{invalid}")
        monitor = usage.UsageMonitor(self.cfg)
        self.assertIsNone(monitor.last_good)
        self.assertIn("状態読込失敗", monitor.storage_error)

    def test_invalid_saved_snapshot_is_reported(self):
        appdata.save_state({"scope": str(self.sessions.resolve()), "last_snapshot": {
            "session": {"used_percent": "invalid"}, "observed_at": NOW.isoformat()}})
        monitor = usage.UsageMonitor(self.cfg)
        self.assertIsNone(monitor.last_good)
        self.assertIn("破損", monitor.storage_error)

    def test_other_session_directory_does_not_restore_state(self):
        self.write([normal()])
        usage.UsageMonitor(self.cfg).refresh(NOW)
        self.cfg["sessions_dir"] = str(self.root / "different")
        self.assertIsNone(usage.UsageMonitor(self.cfg).last_good)

    def test_diagnostics_only_include_known_keys_and_types(self):
        (self.root / "auth.json").write_text(json.dumps({"tokens": {"access_token": None},
                                                       "unrecognized": "private content"}))
        result = usage.safe_auth_schema(self.root)
        self.assertEqual(result["schema"]["tokens"], {"access_token": "NoneType"})
        self.assertNotIn("private content", json.dumps(result))
        self.assertNotIn("unrecognized", json.dumps(result))

    def test_diagnostics_missing_corrupt_and_unexpected_auth(self):
        self.assertIn("ファイルなし", usage.safe_auth_schema(self.root)["status"])
        path = self.root / "auth.json"
        path.write_text("{invalid}")
        self.assertIn("読込失敗", usage.safe_auth_schema(self.root)["status"])
        path.write_text("[]")
        self.assertIn("schema不正", usage.safe_auth_schema(self.root)["status"])

    def test_errors_do_not_echo_session_content(self):
        record = normal()
        record["timestamp"] = "private content"
        self.write([record])
        self.assertNotIn("private content", self.fetch().error)


class ConfigTests(unittest.TestCase):
    def test_partial_deep_merge_and_config_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps({"colors": {"ok": [1, 2, 3]}}))
            cfg = config.load_config(path)
            self.assertEqual(cfg["colors"]["ok"], [1, 2, 3])
            self.assertEqual(cfg["colors"]["warn"], config.DEFAULTS["colors"]["warn"])
            path.write_text("{invalid}")
            self.assertTrue(config.load_config(path)["_warnings"])

    def test_invalid_options_use_safe_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            for value in [None, [], "invalid", True]:
                path.write_text(json.dumps({"poll_interval_sec": value, "thresholds": value,
                                           "colors": value, "sessions_dir": value}))
                cfg = config.load_config(path)
                self.assertEqual(cfg["poll_interval_sec"], 30)
                self.assertEqual(cfg["thresholds"], config.DEFAULTS["thresholds"])

    def test_polling_floors_are_enforced(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text('{"poll_interval_sec": 1, "manual_min_interval_sec": 0}')
            cfg = config.load_config(path)
            self.assertGreaterEqual(cfg["poll_interval_sec"], 10)
            self.assertGreaterEqual(cfg["manual_min_interval_sec"], 5)

    def test_codex_home_environment_is_respected(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict("os.environ", {"CODEX_HOME": folder}):
            self.assertEqual(config.codex_home(config.DEFAULTS), Path(folder).resolve())


if __name__ == "__main__":
    unittest.main()
