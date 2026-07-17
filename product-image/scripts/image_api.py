#!/usr/bin/env python3
"""Image generation client for the Agnes Image 2.1 Flash API.

Both text-to-image and image-to-image (图生图) use the SAME endpoint:

    POST https://apihub.agnes-ai.com/v1/images/generations

Differences per the official Agnes docs:
  * Text-to-image: body needs `model`, `prompt`, `size`.
  * Image-to-image: same endpoint, but input images go into
    `extra_body.image` (array of public URLs or Data URIs), and the
    output format goes into `extra_body.response_format`.
  * `response_format` MUST live inside `extra_body` (never at top level).
  * Do NOT pass `tags: ["img2img"]` for image-to-image.

Reference: https://wiki.agnes-ai.com  (Agnes Image 2.1 Flash)
"""
import argparse
import base64
import json
import mimetypes
import os
import pathlib
import subprocess
import sys
import time
from urllib import error, parse, request

from PIL import Image


DEFAULT_BASE_URL = "https://apihub.agnes-ai.com/v1"
DEFAULT_MODEL = "agnes-image-2.1-flash"
DEFAULT_API_KEY = os.environ.get("OPENAI_COMPAT_IMAGE_API_KEY", os.environ.get("OPENAI_API_KEY", ""))


def env_value(*names):
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def guess_mime(path):
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def file_to_data_uri(path):
    """Read a local image file and return a `data:...;base64,...` URI."""
    path = pathlib.Path(path)
    data = path.read_bytes()
    mime = guess_mime(path)
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def resolve_endpoint(base_url):
    # Agnes uses ONE endpoint for both text-to-image and image-to-image.
    base_url = base_url.rstrip("/")
    return f"{base_url}/images/generations"


def build_payload(args):
    payload = {
        "model": args.model,
        "prompt": args.prompt,
        "size": args.size,
    }
    # Agnes keeps images + response_format inside `extra_body`.
    extra_body = {"response_format": args.response_format}
    if args.image:
        extra_body["image"] = [file_to_data_uri(img) for img in args.image]
    payload["extra_body"] = extra_body
    return payload


def post_json(url, api_key, payload, timeout):
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Request failed for {url}: {exc}") from exc


def extension_from_mime(mime):
    if not mime:
        return ".png"
    if "jpeg" in mime or "jpg" in mime:
        return ".jpg"
    if "webp" in mime:
        return ".webp"
    return ".png"


def save_b64_image(value, out_dir, prefix, index):
    if "," in value and value.lstrip().startswith("data:"):
        header, value = value.split(",", 1)
        mime = header.split(";", 1)[0].replace("data:", "")
    else:
        mime = "image/png"
    data = base64.b64decode(value)
    path = out_dir / f"{prefix}-{index}{extension_from_mime(mime)}"
    path.write_bytes(data)
    return path


def save_url_image(url, out_dir, prefix, index, timeout):
    parsed = parse.urlparse(url)
    suffix = pathlib.Path(parsed.path).suffix
    if suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        suffix = ".png"
    path = out_dir / f"{prefix}-{index}{suffix}"
    try:
        with request.urlopen(url, timeout=timeout) as resp:
            path.write_bytes(resp.read())
    except error.URLError as exc:
        raise RuntimeError(f"Could not download returned image URL {url}: {exc}") from exc
    return path


def convert_to_jpg(path, quality=92):
    target = path.with_suffix(".jpg")
    ps = f"""
Add-Type -AssemblyName System.Drawing
$src = [System.Drawing.Image]::FromFile('{str(path).replace("'", "''")}')
try {{
  $bmp = New-Object System.Drawing.Bitmap($src.Width, $src.Height)
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.Clear([System.Drawing.Color]::White)
  $g.DrawImage($src, 0, 0, $src.Width, $src.Height)
  $g.Dispose()
  $codec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object {{ $_.MimeType -eq 'image/jpeg' }}
  $params = New-Object System.Drawing.Imaging.EncoderParameters(1)
  $params.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter([System.Drawing.Imaging.Encoder]::Quality, [long]{quality})
  $bmp.Save('{str(target).replace("'", "''")}', $codec, $params)
  $bmp.Dispose()
}} finally {{
  $src.Dispose()
}}
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        check=True,
        capture_output=True,
        text=True,
    )
    if target != path:
        path.unlink(missing_ok=True)
    return target


def normalize_output(path, output_format, jpeg_quality):
    if output_format == "jpg":
        return convert_to_jpg(path, jpeg_quality)
    return path


def resize_image(path, width, height, quality=95):
    """Resize an on-disk image to (width, height) in place, overwriting it.

    Used for platform-required pixel dimensions (e.g. 1254x1254) that the
    Agnes API cannot emit natively — we downscale from a higher native
    resolution (e.g. 2048x2048) so detail is preserved.
    """
    img = Image.open(path).convert("RGB")
    img = img.resize((width, height), Image.LANCZOS)
    img.save(str(path), "JPEG", quality=quality)
    return path


def extract_and_save_images(response, out_dir, prefix, timeout, output_format, jpeg_quality, resize=None):
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    data = response.get("data")
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected response: {json.dumps(response, ensure_ascii=False)[:1000]}")
    for idx, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            continue
        if item.get("b64_json"):
            path = save_b64_image(item["b64_json"], out_dir, prefix, idx)
            path = normalize_output(path, output_format, jpeg_quality)
        elif item.get("url"):
            path = save_url_image(item["url"], out_dir, prefix, idx, timeout)
            path = normalize_output(path, output_format, jpeg_quality)
        elif item.get("image_base64"):
            path = save_b64_image(item["image_base64"], out_dir, prefix, idx)
            path = normalize_output(path, output_format, jpeg_quality)
        else:
            continue
        if resize:
            rw, rh = resize
            path = resize_image(path, rw, rh)
        saved.append(path)
    return saved


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate or edit images with the Agnes Image 2.1 Flash API."
    )
    parser.add_argument("mode", choices=["generate", "edit"])
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--image", action="append", default=[], help="Input image (Data URI or path). Repeat for many.")
    parser.add_argument("--model", default=env_value("OPENAI_COMPAT_IMAGE_MODEL") or DEFAULT_MODEL)
    parser.add_argument("--base-url", default=env_value("OPENAI_COMPAT_IMAGE_BASE_URL") or DEFAULT_BASE_URL)
    parser.add_argument("--api-key", default=DEFAULT_API_KEY)
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument("--resize", default=None, help="Resize output to WxH after generation, e.g. 1254x1254. API cannot emit arbitrary sizes natively, so we downscale from a higher native resolution.")
    parser.add_argument("--response-format", default="b64_json", choices=["b64_json", "url"])
    parser.add_argument("--out-dir", default="outputs")
    parser.add_argument("--filename-prefix", default=f"image-{int(time.time())}")
    parser.add_argument("--output-format", default="jpg", choices=["jpg", "original"])
    parser.add_argument("--jpeg-quality", type=int, default=92)
    parser.add_argument("--timeout", type=int, default=180)
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.api_key:
        print(
            "Missing API key. Set OPENAI_COMPAT_IMAGE_API_KEY or OPENAI_API_KEY.",
            file=sys.stderr,
        )
        return 2
    # Image-to-image needs at least one input image.
    if args.mode == "edit" and not args.image:
        print("edit mode requires at least one --image.", file=sys.stderr)
        return 2
    for image in args.image:
        # Accept Data URIs directly; otherwise it must be a local file.
        if image.startswith("data:") or image.startswith("http"):
            continue
        if not pathlib.Path(image).is_file():
            print(f"Input image not found: {image}", file=sys.stderr)
            return 2

    url = resolve_endpoint(args.base_url)
    payload = build_payload(args)
    response = post_json(url, args.api_key, payload, args.timeout)
    out_dir = pathlib.Path(args.out_dir)

    resize = None
    if args.resize:
        try:
            rw, rh = (int(x) for x in args.resize.lower().split("x"))
            resize = (rw, rh)
        except ValueError:
            print(f"Invalid --resize value: {args.resize} (expected WxH, e.g. 1254x1254)", file=sys.stderr)
            return 2

    saved = extract_and_save_images(
        response,
        out_dir,
        args.filename_prefix,
        args.timeout,
        args.output_format,
        args.jpeg_quality,
        resize=resize,
    )

    result = {
        "mode": args.mode,
        "model": args.model,
        "base_url": args.base_url,
        "size": args.size,
        "resized_to": args.resize,
        "saved": [str(path.resolve()) for path in saved],
        "created": response.get("created"),
        "count": len(saved),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
