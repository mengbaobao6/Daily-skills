#!/usr/bin/env python3
"""
Batch product image processor covering common Photoshop batch actions.

Requires Pillow:
    python -m pip install pillow
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps


SUPPORTED_INPUTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
FORMAT_EXTENSIONS = {"jpg": ".jpg", "jpeg": ".jpg", "png": ".png", "webp": ".webp", "tiff": ".tif"}
SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOGO_PATH = str(SKILL_ROOT / "assets" / "logo1-90x130.png")


@dataclass
class ResizeConfig:
    width: int | None = None
    height: int | None = None
    mode: str = "none"


@dataclass
class LogoConfig:
    enabled: bool = False
    path: str = DEFAULT_LOGO_PATH
    position: str = "top-left"
    margin: int = 10
    max_width_ratio: float = 0.16
    opacity: float = 0.92


@dataclass
class WatermarkConfig:
    enabled: bool = False
    text: str = ""
    image_path: str = ""
    position: str = "center"
    tile: bool = False
    opacity: float = 0.18
    font_size: int = 64
    color: str = "#808080"
    margin: int = 80


@dataclass
class AdjustConfig:
    brightness: float = 1.0
    contrast: float = 1.0
    saturation: float = 1.0
    sharpness: float = 1.0
    autocontrast: bool = False


@dataclass
class ExportConfig:
    format: str = "jpg"
    quality: int = 92
    prefix: str = "image"
    start_index: int = 1
    padding: int = 3
    keep_original_name: bool = False


@dataclass
class BatchConfig:
    recursive: bool = False
    overwrite: bool = False
    background: str = "white"
    resize: ResizeConfig = field(default_factory=ResizeConfig)
    logo: LogoConfig = field(default_factory=LogoConfig)
    watermark: WatermarkConfig = field(default_factory=WatermarkConfig)
    adjust: AdjustConfig = field(default_factory=AdjustConfig)
    export: ExportConfig = field(default_factory=ExportConfig)


def deep_update(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_update(base[key], value)
        else:
            base[key] = value
    return base


def dataclass_to_dict(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return {key: dataclass_to_dict(getattr(obj, key)) for key in obj.__dataclass_fields__}
    return obj


def load_config(args: argparse.Namespace) -> BatchConfig:
    data = dataclass_to_dict(BatchConfig())
    if args.config:
        config_path = Path(args.config)
        with config_path.open("r", encoding="utf-8") as handle:
            deep_update(data, json.load(handle))

    cli_overrides: dict[str, Any] = {}
    if args.recursive:
        cli_overrides["recursive"] = True
    if args.overwrite:
        cli_overrides["overwrite"] = True
    if args.background:
        cli_overrides["background"] = args.background
    if args.width or args.height or args.resize_mode:
        cli_overrides["resize"] = {}
        if args.width:
            cli_overrides["resize"]["width"] = args.width
        if args.height:
            cli_overrides["resize"]["height"] = args.height
        if args.resize_mode:
            cli_overrides["resize"]["mode"] = args.resize_mode
    if args.logo:
        cli_overrides["logo"] = {"enabled": True, "path": args.logo}
    if args.watermark_text:
        cli_overrides["watermark"] = {"enabled": True, "text": args.watermark_text}
    if args.fmt:
        cli_overrides.setdefault("export", {})["format"] = args.fmt
    if args.quality:
        cli_overrides.setdefault("export", {})["quality"] = args.quality
    if args.prefix:
        cli_overrides.setdefault("export", {})["prefix"] = args.prefix

    deep_update(data, cli_overrides)
    return BatchConfig(
        recursive=bool(data["recursive"]),
        overwrite=bool(data["overwrite"]),
        background=str(data["background"]),
        resize=ResizeConfig(**data["resize"]),
        logo=LogoConfig(**data["logo"]),
        watermark=WatermarkConfig(**data["watermark"]),
        adjust=AdjustConfig(**data["adjust"]),
        export=ExportConfig(**data["export"]),
    )


def parse_color(value: str) -> tuple[int, int, int, int]:
    value = value.strip()
    if value.lower() == "white":
        return (255, 255, 255, 255)
    if value.lower() == "black":
        return (0, 0, 0, 255)
    if value.startswith("#"):
        value = value[1:]
        if len(value) == 6:
            return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4)) + (255,)
    raise ValueError(f"Unsupported color value: {value}")


def list_images(input_dir: Path, recursive: bool) -> list[Path]:
    pattern = "**/*" if recursive else "*"
    return sorted(path for path in input_dir.glob(pattern) if path.suffix.lower() in SUPPORTED_INPUTS and path.is_file())


def flatten_to_background(image: Image.Image, color: str) -> Image.Image:
    bg = Image.new("RGBA", image.size, parse_color(color))
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    bg.alpha_composite(image)
    return bg


def resize_image(image: Image.Image, config: ResizeConfig, background: str) -> Image.Image:
    mode = config.mode.lower()
    width, height = config.width, config.height
    if mode == "none" or not width or not height:
        return image
    if mode == "stretch":
        return image.resize((width, height), Image.Resampling.LANCZOS)
    if mode == "fill":
        return ImageOps.fit(image, (width, height), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    if mode == "fit":
        fitted = ImageOps.contain(image, (width, height), method=Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (width, height), parse_color(background))
        offset = ((width - fitted.width) // 2, (height - fitted.height) // 2)
        canvas.alpha_composite(fitted.convert("RGBA"), offset)
        return canvas
    raise ValueError(f"Unsupported resize mode: {config.mode}")


def scale_overlay(overlay: Image.Image, max_width: int) -> Image.Image:
    if overlay.width <= max_width:
        return overlay
    ratio = max_width / overlay.width
    new_size = (max(1, int(overlay.width * ratio)), max(1, int(overlay.height * ratio)))
    return overlay.resize(new_size, Image.Resampling.LANCZOS)


def set_opacity(image: Image.Image, opacity: float) -> Image.Image:
    opacity = max(0.0, min(1.0, opacity))
    image = image.convert("RGBA")
    alpha = image.getchannel("A").point(lambda p: int(p * opacity))
    image.putalpha(alpha)
    return image


def resolve_position(base_size: tuple[int, int], overlay_size: tuple[int, int], position: str, margin: int) -> tuple[int, int]:
    bw, bh = base_size
    ow, oh = overlay_size
    pos = position.lower()
    x_map = {
        "left": margin,
        "center": (bw - ow) // 2,
        "right": bw - ow - margin,
    }
    y_map = {
        "top": margin,
        "center": (bh - oh) // 2,
        "bottom": bh - oh - margin,
    }
    if "-" in pos:
        vertical, horizontal = pos.split("-", 1)
        x = x_map.get(horizontal, margin)
        y = y_map.get(vertical, margin)
    else:
        x = x_map.get(pos, (bw - ow) // 2)
        y = y_map.get(pos, (bh - oh) // 2)
    return (max(0, x), max(0, y))


def apply_logo(image: Image.Image, config: LogoConfig) -> Image.Image:
    if not config.enabled or not config.path:
        return image
    logo_path = Path(config.path)
    if not logo_path.is_absolute() and not logo_path.exists():
        logo_path = SKILL_ROOT / logo_path
    if not logo_path.exists():
        raise FileNotFoundError(f"Logo not found: {logo_path}")
    logo = Image.open(logo_path).convert("RGBA")
    logo = scale_overlay(logo, max(1, int(image.width * config.max_width_ratio)))
    logo = set_opacity(logo, config.opacity)
    out = image.convert("RGBA")
    out.alpha_composite(logo, resolve_position(out.size, logo.size, config.position, config.margin))
    return out


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in ("arial.ttf", "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/msyh.ttc"):
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def text_overlay(text: str, font_size: int, color: str, opacity: float) -> Image.Image:
    font = load_font(font_size)
    scratch = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    draw = ImageDraw.Draw(scratch)
    bbox = draw.textbbox((0, 0), text, font=font)
    overlay = Image.new("RGBA", (bbox[2] - bbox[0] + 24, bbox[3] - bbox[1] + 24), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    fill = parse_color(color)
    fill = (fill[0], fill[1], fill[2], int(255 * max(0.0, min(1.0, opacity))))
    draw.text((12 - bbox[0], 12 - bbox[1]), text, font=font, fill=fill)
    return overlay


def apply_watermark(image: Image.Image, config: WatermarkConfig) -> Image.Image:
    if not config.enabled:
        return image
    if config.image_path:
        overlay = Image.open(config.image_path).convert("RGBA")
        overlay = scale_overlay(overlay, max(1, int(image.width * 0.35)))
        overlay = set_opacity(overlay, config.opacity)
    elif config.text:
        overlay = text_overlay(config.text, config.font_size, config.color, config.opacity)
    else:
        return image

    out = image.convert("RGBA")
    if config.tile:
        step_x = overlay.width + config.margin
        step_y = overlay.height + config.margin
        for y in range(config.margin // 2, out.height, step_y):
            for x in range(config.margin // 2, out.width, step_x):
                out.alpha_composite(overlay, (x, y))
    else:
        out.alpha_composite(overlay, resolve_position(out.size, overlay.size, config.position, config.margin))
    return out


def adjust_image(image: Image.Image, config: AdjustConfig) -> Image.Image:
    out = image.convert("RGBA")
    if config.autocontrast:
        rgb = ImageOps.autocontrast(out.convert("RGB"))
        out = Image.merge("RGBA", (*rgb.split(), out.getchannel("A")))
    for enhancer, factor in (
        (ImageEnhance.Brightness, config.brightness),
        (ImageEnhance.Contrast, config.contrast),
        (ImageEnhance.Color, config.saturation),
        (ImageEnhance.Sharpness, config.sharpness),
    ):
        if not math.isclose(float(factor), 1.0):
            out = enhancer(out).enhance(float(factor))
    return out


def output_name(source: Path, index: int, config: ExportConfig) -> str:
    fmt = config.format.lower()
    ext = FORMAT_EXTENSIONS.get(fmt, f".{fmt}")
    if config.keep_original_name:
        return f"{source.stem}{ext}"
    number = str(config.start_index + index).zfill(config.padding)
    return f"{config.prefix}_{number}{ext}"


def unique_output_path(output_dir: Path, filename: str, overwrite: bool) -> Path:
    path = output_dir / filename
    if overwrite or not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    counter = 2
    while True:
        candidate = output_dir / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def save_image(image: Image.Image, path: Path, config: ExportConfig, background: str) -> None:
    fmt = config.format.lower()
    save_kwargs: dict[str, Any] = {}
    if fmt in {"jpg", "jpeg", "webp"}:
        save_kwargs["quality"] = int(config.quality)
        save_kwargs["optimize"] = True
    if fmt in {"jpg", "jpeg"}:
        image = flatten_to_background(image, background).convert("RGB")
        fmt = "JPEG"
    elif fmt == "png":
        fmt = "PNG"
    elif fmt == "webp":
        fmt = "WEBP"
    else:
        fmt = fmt.upper()
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, fmt, **save_kwargs)


def process_one(source: Path, destination: Path, config: BatchConfig) -> None:
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original).convert("RGBA")
    image = flatten_to_background(image, config.background)
    image = resize_image(image, config.resize, config.background)
    image = adjust_image(image, config.adjust)
    image = apply_logo(image, config.logo)
    image = apply_watermark(image, config.watermark)
    save_image(image, destination, config.export, config.background)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Batch process product images with Photoshop-like actions.")
    parser.add_argument("--input", required=True, help="Input image folder.")
    parser.add_argument("--output", required=True, help="Output folder.")
    parser.add_argument("--config", help="Optional JSON config path.")
    parser.add_argument("--recursive", action="store_true", help="Include subfolders.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing output files.")
    parser.add_argument("--width", type=int, help="Output width.")
    parser.add_argument("--height", type=int, help="Output height.")
    parser.add_argument("--resize-mode", choices=["none", "fit", "fill", "stretch"], help="Resize mode.")
    parser.add_argument("--background", help="Canvas/background color, e.g. white or #ffffff.")
    parser.add_argument(
        "--logo",
        nargs="?",
        const=DEFAULT_LOGO_PATH,
        help="Enable a logo. With no path, use bundled assets/logo1-90x130.png in the top-left corner with a 10px margin.",
    )
    parser.add_argument("--watermark-text", help="Watermark text.")
    parser.add_argument("--format", dest="fmt", choices=["jpg", "jpeg", "png", "webp", "tiff"], help="Export format.")
    parser.add_argument("--quality", type=int, help="Export quality for jpg/webp.")
    parser.add_argument("--prefix", help="Output filename prefix.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    if not input_dir.exists() or not input_dir.is_dir():
        print(f"Input folder not found: {input_dir}", file=sys.stderr)
        return 2

    config = load_config(args)
    images = list_images(input_dir, config.recursive)
    if not images:
        print(f"No supported images found in: {input_dir}")
        return 0

    success = 0
    failures: list[tuple[Path, str]] = []
    for index, source in enumerate(images):
        try:
            if config.recursive:
                target_dir = output_dir / source.parent.relative_to(input_dir)
            else:
                target_dir = output_dir
            destination = unique_output_path(target_dir, output_name(source, index, config.export), config.overwrite)
            process_one(source, destination, config)
            success += 1
            print(f"[OK] {source.name} -> {destination}")
        except Exception as exc:
            failures.append((source, str(exc)))
            print(f"[FAIL] {source}: {exc}", file=sys.stderr)

    print(f"Done. Success: {success}, Failed: {len(failures)}, Output: {output_dir}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
