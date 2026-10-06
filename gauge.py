"""Independent supersampled Pillow renderer: outer 5h / inner weekly."""
from __future__ import annotations

from pathlib import Path
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from usage import UsageSnapshot, utc_now


def _color_for_pct(pct: float | None, cfg: dict) -> tuple:
    if pct is None:
        key = "unknown"
    elif pct <= cfg["thresholds"]["warn"]:
        key = "ok"
    elif pct <= cfg["thresholds"]["crit"]:
        key = "warn"
    else:
        key = "crit"
    return tuple(cfg["colors"][key]) + (255,)


def render_double_ring(outer_pct: float | None, inner_pct: float | None,
                       cfg: dict, center_text: str | None = None) -> Image.Image:
    size = cfg["icon_size"]
    big = size * 4
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((0, 0, big - 1, big - 1), fill=(28, 28, 30, 240))
    for fraction, width_fraction, pct in ((0.035, 0.125, outer_pct), (0.205, 0.115, inner_pct)):
        inset = round(big * fraction)
        box = (inset, inset, big - inset - 1, big - inset - 1)
        width = round(big * width_fraction)
        color = _color_for_pct(pct, cfg)
        draw.arc(box, 0, 360, fill=tuple(cfg["colors"]["track"]) + (255,), width=width)
        if pct is None:
            for start in range(-90, 270, 45):
                draw.arc(box, start, start + 25, fill=color, width=width)
        elif pct > 0:
            draw.arc(box, -90, -90 + 360 * min(1, pct), fill=color, width=width)
    if center_text is not None:
        font_size = round(big * 0.24)
        font = None
        for name in ("arialbd.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf"):
            try:
                font = ImageFont.truetype(name, font_size)
                break
            except OSError:
                continue
        if font is None:
            font = ImageFont.load_default()
        elif draw.textbbox((0, 0), center_text, font=font)[2] > big * .34:
            # Fit three-digit percentages inside the inner ring as well.
            while font_size > 8:
                font_size -= 1
                font = ImageFont.truetype(name, font_size)
                if draw.textbbox((0, 0), center_text, font=font)[2] <= big * .34:
                    break
        left, top, right, bottom = draw.textbbox((0, 0), center_text, font=font)
        draw.text(((big - right + left) / 2 - left, (big - bottom + top) / 2 - top),
                  center_text, fill=_color_for_pct(outer_pct, cfg), font=font)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def make_icon(snap: UsageSnapshot, cfg: dict, now: datetime | None = None) -> Image.Image:
    now = now or utc_now()
    outer = snap.session.current_pct(now) if snap.session else None
    inner = snap.weekly.current_pct(now) if snap.weekly else None
    text = None
    if cfg["show_center_text"]:
        text = "?" if outer is None else str(round(outer * 100))
    return render_double_ring(outer, inner, cfg, text)


if __name__ == "__main__":
    import config
    cfg = config.load_config()
    for name, outer, inner in (("30", .30, .45), ("70", .70, .55),
                               ("95", .95, .80), ("unknown", None, None)):
        text = "?" if outer is None else str(round(outer * 100))
        destination = Path(__file__).resolve().with_name(f"sample_{name}.png")
        render_double_ring(outer, inner, cfg, text).save(destination)
        print("wrote", destination.name)
