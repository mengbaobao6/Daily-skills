#!/usr/bin/env python3
import argparse
import base64
import json
import mimetypes
import os
import pathlib
import subprocess
import sys
import time
import uuid
from urllib import error, parse, request


DEFAULT_BASE_URL = "https://api.0vo.dev/v1"
DEFAULT_MODEL = "gpt-image-2"


def env_value(*names):
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def guess_mime(path):
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def add_field(parts, boundary, name, value):
    if value is None:
        return
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
    parts.append(str(value).encode("utf-8"))
    parts.append(b"\r\n")


def add_file(parts, boundary, name, path):
    path = pathlib.Path(path)
    data = path.read_bytes()
    filename = path.name
    mime = guess_mime(path)
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
    )
    parts.append(f"Content-Type: {mime}\r\n\r\n".encode())
    parts.append(data)
    parts.append(b"\r\n")


def multipart_body(fields, files):
    boundary = f"----codex-image-api-{uuid.uuid4().hex}"
    parts = []
    for name, value in fields:
        add_field(parts, boundary, name, value)
    for name, path in files:
        add_file(parts, boundary, name, path)
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), boundary


def post_multipart(url, api_key, fields, files, timeout):
    body, boundary = multipart_body(fields, files)
    req = request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Accept", "application/json")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8")
            return json.loads(payload)
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


def extract_and_save_images(response, out_dir, prefix, timeout, output_format, jpeg_quality):
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
            saved.append(normalize_output(path, output_format, jpeg_quality))
        elif item.get("url"):
            path = save_url_image(item["url"], out_dir, prefix, idx, timeout)
            saved.append(normalize_output(path, output_format, jpeg_quality))
        elif item.get("image_base64"):
            path = save_b64_image(item["image_base64"], out_dir, prefix, idx)
            saved.append(normalize_output(path, output_format, jpeg_quality))
    return saved


def build_fields(args):
    fields = [
        ("model", args.model),
        ("prompt", args.prompt),
        ("n", args.count),
        ("response_format", args.response_format),
    ]
    optional = [
        ("size", args.size),
        ("quality", args.quality),
        ("background", args.background),
        ("user", args.user),
    ]
    fields.extend((k, v) for k, v in optional if v)
    return fields


def resolve_endpoint(base_url, mode):
    base_url = base_url.rstrip("/")
    path = "/images/generations" if mode == "generate" else "/images/edits"
    return f"{base_url}{path}"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate or edit images with an OpenAI-compatible Images API."
    )
    parser.add_argument("mode", choices=["generate", "edit"])
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--image", action="append", default=[], help="Input image. Repeat for many.")
    parser.add_argument("--mask", help="PNG mask for edit mode.")
    parser.add_argument("--model", default=env_value("OPENAI_COMPAT_IMAGE_MODEL") or DEFAULT_MODEL)
    parser.add_argument("--base-url", default=env_value("OPENAI_COMPAT_IMAGE_BASE_URL") or DEFAULT_BASE_URL)
    parser.add_argument("--api-key", default=env_value("OVO_API_KEY", "OPENAI_COMPAT_IMAGE_API_KEY", "OPENAI_API_KEY"))
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument("--quality")
    parser.add_argument("--background")
    parser.add_argument("--response-format", default="b64_json", choices=["b64_json", "url"])
    parser.add_argument("--user")
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
            "Missing API key. Set OVO_API_KEY, OPENAI_COMPAT_IMAGE_API_KEY, or OPENAI_API_KEY.",
            file=sys.stderr,
        )
        return 2
    if args.mode == "generate" and args.image:
        print("generate mode does not accept --image; use edit mode.", file=sys.stderr)
        return 2
    if args.mode == "edit" and not args.image:
        print("edit mode requires at least one --image.", file=sys.stderr)
        return 2
    for image in args.image:
        if not pathlib.Path(image).is_file():
            print(f"Input image not found: {image}", file=sys.stderr)
            return 2
    if args.mask and not pathlib.Path(args.mask).is_file():
        print(f"Mask not found: {args.mask}", file=sys.stderr)
        return 2

    fields = build_fields(args)
    files = []
    if args.mode == "edit":
        files.extend(("image", image) for image in args.image)
        if args.mask:
            files.append(("mask", args.mask))

    url = resolve_endpoint(args.base_url, args.mode)
    response = post_multipart(url, args.api_key, fields, files, args.timeout)
    out_dir = pathlib.Path(args.out_dir)
    saved = extract_and_save_images(
        response,
        out_dir,
        args.filename_prefix,
        args.timeout,
        args.output_format,
        args.jpeg_quality,
    )

    result = {
        "mode": args.mode,
        "model": args.model,
        "base_url": args.base_url,
        "saved": [str(path.resolve()) for path in saved],
        "created": response.get("created"),
        "count": len(saved),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
