#!/usr/bin/env python3
"""Generate browser assets whose decoded pixels match their PNG sources."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "themes" / "mutating" / "static"

LOSSLESS_ASSETS = {
    "pillar-verifiability-v1.png": "pillar-verifiability-v1-lossless.webp",
    "pillar-interpretability-v1.png": "pillar-interpretability-v1-lossless.webp",
    "pillar-testing-speed-rotor-v2.png": (
        "pillar-testing-speed-rotor-v2-lossless.webp"
    ),
    "pillar-testing-speed-rotor-v4.png": (
        "pillar-testing-speed-rotor-v4-lossless.webp"
    ),
    "thesis-resistance-field-pink.png": (
        "thesis-resistance-field-pink-v1-lossless.webp"
    ),
}

LOSSLESS_AVIF_ASSETS = {
    "muta-v2-transparent-v10.webp": "muta-v2-transparent-v10-lossless.avif",
    "muta-v2-transparent-2x-v10.webp": (
        "muta-v2-transparent-2x-v10-lossless.avif"
    ),
}


def generate_pixel_exact_webp(source_name: str, output_name: str) -> None:
    source_path = STATIC / source_name
    output_path = STATIC / output_name
    with Image.open(source_path) as source:
        expected = source.convert("RGBA")
        expected.save(
            output_path,
            "WEBP",
            lossless=True,
            method=6,
            exact=True,
        )
    with Image.open(output_path) as generated:
        difference = ImageChops.difference(expected, generated.convert("RGBA"))
        if difference.getbbox() is not None:
            output_path.unlink(missing_ok=True)
            raise RuntimeError(f"Decoded pixels changed for {source_name}")


def generate_alpha_exact_eye_mask() -> None:
    source_path = STATIC / "muta-eye-surface.webp"
    output_path = STATIC / "muta-eye-surface-alpha-v1.webp"
    with Image.open(source_path) as source:
        expected_alpha = source.convert("RGBA").getchannel("A")
        mask = Image.new("RGBA", source.size, (0, 0, 0, 0))
        mask.putalpha(expected_alpha)
        mask.save(
            output_path,
            "WEBP",
            lossless=True,
            method=6,
            exact=True,
        )
    with Image.open(output_path) as generated:
        if ImageChops.difference(
            expected_alpha,
            generated.convert("RGBA").getchannel("A"),
        ).getbbox() is not None:
            output_path.unlink(missing_ok=True)
            raise RuntimeError("The generated eye mask changed an alpha pixel")


def generate_pixel_exact_avif(source_name: str, output_name: str) -> None:
    source_path = STATIC / source_name
    output_path = STATIC / output_name
    with Image.open(source_path) as source:
        expected = source.convert("RGBA")
        expected.save(
            output_path,
            "AVIF",
            quality=100,
            speed=0,
        )
    with Image.open(output_path) as generated:
        difference = ImageChops.difference(expected, generated.convert("RGBA"))
        if difference.getbbox() is not None:
            output_path.unlink(missing_ok=True)
            raise RuntimeError(f"Decoded pixels changed for {source_name}")


def main() -> None:
    for source_name, output_name in LOSSLESS_ASSETS.items():
        generate_pixel_exact_webp(source_name, output_name)
    for source_name, output_name in LOSSLESS_AVIF_ASSETS.items():
        generate_pixel_exact_avif(source_name, output_name)
    generate_alpha_exact_eye_mask()


if __name__ == "__main__":
    main()
