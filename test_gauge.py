"""Rendering boundaries, stale display and UI refresh throttling."""
import copy
import unittest
from datetime import timedelta
from unittest.mock import patch

import config
import gauge
import usage
from test_usage import NOW, normal
from tray_app import RefreshGate, tooltip_limit


class GaugeAndUITests(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(config.DEFAULTS)

    def test_thresholds_are_inclusive(self):
        for pct, key in [(0, "ok"), (.60, "ok"), (.60001, "warn"),
                         (.85, "warn"), (.85001, "crit"), (1, "crit"), (None, "unknown")]:
            self.assertEqual(gauge._color_for_pct(pct, self.cfg)[:3], tuple(self.cfg["colors"][key]))

    def test_icon_is_rgba_and_requested_size(self):
        image = gauge.render_double_ring(.3, .7, self.cfg, "30")
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.size, (64, 64))
        self.assertEqual(image.getpixel((0, 0))[3], 0)

    def test_display_expires_without_another_fetch(self):
        snap = usage.parse_event(normal(), NOW)
        with patch("gauge.render_double_ring") as render:
            gauge.make_icon(snap, self.cfg, NOW + timedelta(hours=3))
        self.assertEqual(render.call_args.args[0], None)
        self.assertEqual(render.call_args.args[1], .7)
        self.assertEqual(render.call_args.args[3], "?")

    def test_failure_preserves_unexpired_rings(self):
        snap = usage.replace(usage.parse_event(normal(), NOW), error="読込失敗")
        with patch("gauge.render_double_ring") as render:
            gauge.make_icon(snap, self.cfg, NOW)
        self.assertEqual(render.call_args.args[:2], (.3, .7))

    def test_unknown_and_zero_are_different_images(self):
        zero = gauge.render_double_ring(0, 0, self.cfg, "0")
        unknown = gauge.render_double_ring(None, None, self.cfg, "?")
        self.assertNotEqual(zero.tobytes(), unknown.tobytes())

    def test_manual_updates_are_throttled_even_after_failure(self):
        gate = RefreshGate(5)
        self.assertEqual(gate.begin(100), 0)
        self.assertEqual(gate.request(101), 4)
        self.assertEqual(gate.request(105), 0)
        self.assertGreater(gate.request(105), 0)
        self.assertEqual(gate.begin(105), 0)
        self.assertGreater(gate.begin(106), 0)

    def test_tooltip_limits_utf16_units(self):
        text = "日本語" * 60 + "\U0001f600" * 100
        short = tooltip_limit(text)
        self.assertLessEqual(len(short.encode("utf-16-le")), 254)
        self.assertEqual(short, text[:len(short)])
        self.assertLessEqual(len(tooltip_limit("\U0001f600" * 100).encode("utf-16-le")), 254)


if __name__ == "__main__":
    unittest.main()
