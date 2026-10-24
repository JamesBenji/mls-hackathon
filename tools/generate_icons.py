"""Generate Medicall launcher icons. Requires Pillow (pip install Pillow)."""

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]


def make_icon(size: int) -> Image.Image:
    scale = 4
    canvas = Image.new("RGB", (size * scale, size * scale), "#F5F8F6")
    draw = ImageDraw.Draw(canvas)
    unit = size * scale / 100

    def box(values):
        return tuple(round(value * unit) for value in values)

    draw.rounded_rectangle(box((7, 7, 93, 93)), radius=22 * unit, fill="#16766D")
    draw.rounded_rectangle(box((42, 25, 58, 75)), radius=4 * unit, fill="white")
    draw.rounded_rectangle(box((25, 42, 75, 58)), radius=4 * unit, fill="white")
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def save_png(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    make_icon(size).save(path)


def generate(patient: Path, dashboard: Path) -> None:
    branding = ROOT / "branding"
    save_png(branding / "medicall.png", 1024)

    densities = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
    for density, size in densities.items():
        save_png(patient / f"android/app/src/main/res/mipmap-{density}/ic_launcher.png", size)

    catalog = patient / "ios/Runner/Assets.xcassets/AppIcon.appiconset"
    entries = json.loads((catalog / "Contents.json").read_text())["images"]
    for entry in entries:
        filename = entry.get("filename")
        if filename:
            points = float(entry["size"].split("x")[0])
            multiplier = float(entry["scale"].removesuffix("x"))
            save_png(catalog / filename, round(points * multiplier))

    for app in (patient, dashboard):
        if (app / "web").exists():
            save_png(app / "web/favicon.png", 32)
            for size in (192, 512):
                save_png(app / f"web/icons/Icon-{size}.png", size)
                save_png(app / f"web/icons/Icon-maskable-{size}.png", size)

    resource_dir = dashboard / "windows/runner/resources"
    if resource_dir.exists():
        make_icon(256).save(
            resource_dir / "app_icon.ico",
            sizes=[(size, size) for size in (16, 24, 32, 48, 64, 128, 256)],
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard", type=Path, default=ROOT / "dashboard")
    args = parser.parse_args()
    generate(ROOT / "patient_app", args.dashboard)
