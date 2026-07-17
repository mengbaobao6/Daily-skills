#!/usr/bin/env python3
import argparse
import concurrent.futures
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile


DEFAULT_OUT_DIR = r"E:\AI Product"
DEFAULT_IMAGE_API_SCRIPT = (
    r"C:\Users\UserComputer\.workbuddy\skills\product-image\scripts\image_api.py"
)


def read_text(path):
    data = pathlib.Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def infer_product_name(plan_path):
    stem = pathlib.Path(plan_path).stem
    for suffix in ("_营销方案_无品牌", "-营销方案-无品牌", "营销方案_无品牌", "_无品牌"):
        stem = stem.replace(suffix, "")
    return stem.strip(" _-") or "product-image"


def sanitize_filename(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "product-image"


def create_batch_dir(root_dir, product_name):
    root = pathlib.Path(root_dir)
    root.mkdir(parents=True, exist_ok=True)
    for number in range(0, 1000):
        suffix = "" if number == 0 else f"{number:02d}"
        batch_dir = root / f"{product_name}{suffix}"
        try:
            batch_dir.mkdir()
            return batch_dir
        except FileExistsError:
            continue
    raise RuntimeError(f"Could not create a unique output folder under {root}")


def clean_prompt(prompt):
    prompt = prompt.strip()
    prompt = prompt.replace("黑纾ㄧ爞 sandpaper", "black sandpaper")
    prompt = prompt.replace("black纾ㄧ爞 sandpaper", "black sandpaper")
    prompt = re.sub(r"\s+", " ", prompt)
    return prompt


def extract_prompts(markdown):
    pattern = re.compile(
        r"###\s*Image\s*(\d+).*?\*\*English Prompt:\*\*\s*(.*?)(?=\n\s*\*\*|(?:\n\s*---)|(?:\n\s*###\s*Image\s*\d+)|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    prompts = []
    for match in pattern.finditer(markdown):
        index = int(match.group(1))
        prompt = clean_prompt(match.group(2))
        if prompt:
            prompts.append((index, prompt))
    prompts.sort(key=lambda item: item[0])
    return prompts


def run_one(args, index, prompt, temp_dir):
    mode = "edit" if args.image else "generate"
    prefix = f"tmp-product-plan-{index}"
    cmd = [
        sys.executable,
        args.image_api_script,
        mode,
        "--prompt",
        prompt,
        "--model",
        args.model,
        "--size",
        args.size,
        "--quality",
        args.quality,
        "--count",
        "1",
        "--response-format",
        "b64_json",
        "--output-format",
        "jpg",
        "--out-dir",
        str(temp_dir),
        "--filename-prefix",
        prefix,
        "--timeout",
        str(args.timeout),
    ]
    if args.resize:
        cmd.extend(["--resize", args.resize])
    if mode == "edit":
        for image in args.image:
            cmd.extend(["--image", image])

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Image {index} failed with exit {proc.returncode}:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Image {index} returned non-JSON output:\n{proc.stdout}") from exc

    saved = result.get("saved") or []
    if not saved:
        raise RuntimeError(f"Image {index} did not report any saved file.")
    source = pathlib.Path(saved[0])
    if not source.is_file():
        raise RuntimeError(f"Image {index} output file does not exist: {source}")
    return index, source


def compact_error(error):
    text = str(error).strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else text


def build_result(plan, product_name, root_dir, batch_dir, mode, outputs, failures):
    failed_indices = [item["index"] for item in sorted(failures, key=lambda item: item["index"])]
    if failures and outputs:
        status = "partial_failed"
        message = f"Generated {len(outputs)} image(s); failed image index: {', '.join(map(str, failed_indices))}."
    elif failures:
        status = "failed"
        message = f"All image requests failed. Failed image indices: {', '.join(map(str, failed_indices))}."
    else:
        status = "success"
        message = f"Generated {len(outputs)} image(s) successfully."
    return {
        "status": status,
        "message": message,
        "plan": str(plan.resolve()),
        "product_name": product_name,
        "root_dir": str(root_dir.resolve()),
        "out_dir": str(batch_dir.resolve()),
        "mode": mode,
        "count": len(outputs),
        "outputs": outputs,
        "failed_indices": failed_indices,
        "failures": sorted(failures, key=lambda item: item["index"]),
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate numbered JPG product images from a marketing plan, with optional reference images."
    )
    parser.add_argument("--plan", required=True, help="Marketing plan Markdown file.")
    parser.add_argument("--image", action="append", default=[], help="Optional reference product image. Repeat for many.")
    parser.add_argument("--product-name", help="Output filename prefix.")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--model", default="agnes-image-2.1-flash")
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument("--resize", default=None, help="Resize output to WxH after generation (e.g. 1254x1254). When set and --size is default, generation uses 2048x2048 first for a quality downscale.")
    parser.add_argument("--quality", default="high")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--image-api-script", default=DEFAULT_IMAGE_API_SCRIPT)
    parser.add_argument("--keep-temp", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    plan = pathlib.Path(args.plan)
    if not plan.is_file():
        print(f"Plan file not found: {plan}", file=sys.stderr)
        return 2
    for image in args.image:
        if not pathlib.Path(image).is_file():
            print(f"Reference image not found: {image}", file=sys.stderr)
            return 2
    if not pathlib.Path(args.image_api_script).is_file():
        print(f"Image API script not found: {args.image_api_script}", file=sys.stderr)
        return 2

    # When resizing to a target (e.g. 1254x1254) and size is left at default,
    # generate at 2048x2048 first so the downscale keeps more detail.
    if args.resize and args.size == "1024x1024":
        args.size = "2048x2048"

    prompts = extract_prompts(read_text(plan))
    if not prompts:
        print("No Image N English Prompt sections found in the plan.", file=sys.stderr)
        return 2

    product_name = sanitize_filename(args.product_name or infer_product_name(plan))
    root_dir = pathlib.Path(args.out_dir)
    out_dir = create_batch_dir(root_dir, product_name)
    temp_dir = pathlib.Path(tempfile.mkdtemp(prefix="product-plan-image-batch-"))

    successes = []
    failures = []
    try:
        max_workers = max(1, min(args.workers, len(prompts)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(run_one, args, index, prompt, temp_dir): index
                for index, prompt in prompts
            }
            for future in concurrent.futures.as_completed(future_map):
                index = future_map[future]
                try:
                    successes.append(future.result())
                except Exception as exc:
                    failures.append({"index": index, "error": compact_error(exc)})

        outputs = []
        for index, source in sorted(successes, key=lambda item: item[0]):
            target = out_dir / f"{product_name}-{index}.jpg"
            shutil.move(str(source), str(target))
            outputs.append({"index": index, "path": str(target.resolve()), "bytes": target.stat().st_size})

        mode = "edit" if args.image else "generate"
        result = build_result(plan, product_name, root_dir, out_dir, mode, outputs, failures)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if failures:
            print(result["message"], file=sys.stderr)
        return 1 if failures and not outputs else 0
    finally:
        if not args.keep_temp:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
