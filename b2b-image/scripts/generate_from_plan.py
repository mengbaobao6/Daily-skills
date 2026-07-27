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

from PIL import Image, ImageFilter, ImageStat


DEFAULT_OUT_DIR = r"E:\AI Product"
DEFAULT_IMAGE_API_SCRIPT = (
    r"C:\Users\UserComputer\.codex\skills\openai-compatible-image-api\scripts\image_api.py"
)
SAFE_ZONE_LAYOUT_BLOCK = (
    "MANDATORY LOGO EXCLUSION ZONE — HIGHEST PRIORITY: "
    "Reserve the upper-left rectangle covering x=0 to 18 percent and y=0 to 18 percent "
    "of the canvas as a completely empty logo-safe exclusion zone. "
    "This rectangle must contain only clean continuous background. "
    "No text, letter, number, icon, product, packaging, model, hand, prop, dimension line, "
    "copywriting decoration, shadow, outline, or important visual detail may enter, overlap, "
    "or touch this rectangle. "
    "All copywriting may be placed freely anywhere outside this exclusion zone according to "
    "the best visual composition. Do not force copywriting into the upper-right corner; only "
    "keep it outside the exclusion zone with clear spacing from the boundary."
)
SAFE_ZONE_FINAL_CHECK = (
    "FINAL LAYOUT CHECK: Keep the entire upper-left 18 percent by 18 percent exclusion zone "
    "completely empty. Keep every part of all copywriting, outlines, shadows, products, and "
    "important details outside this zone."
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


def extract_table_value(markdown, label):
    pattern = re.compile(rf"^\|\s*{re.escape(label)}\s*\|\s*(.*?)\s*\|", re.MULTILINE)
    match = pattern.search(markdown)
    return clean_prompt(match.group(1)) if match else ""


def build_consistency_block(args, markdown, product_name, primary_prompt=""):
    if args.consistency == "off":
        return ""

    product_display_name = extract_table_value(markdown, "产品名称") or product_name
    structure = extract_table_value(markdown, "产品结构")
    specs = extract_table_value(markdown, "规格参数")
    material = extract_table_value(markdown, "材质")
    reference_rule = (
        "Use the supplied reference images as the strict product identity source. "
        if args.image
        else ""
    )

    strict_lines = [
        "Batch product identity lock for all generated images:",
        reference_rule
        + "Generate the exact same product kit in every image and do not redesign the product.",
        f"Product name: {product_display_name}.",
    ]
    if primary_prompt:
        strict_lines.append(
            "Primary product identity fingerprint from Image 1 prompt: "
            f"{clean_prompt(primary_prompt)}."
        )
    if structure:
        strict_lines.append(f"Fixed product structure: {structure}.")
    if specs:
        strict_lines.append(f"Fixed product dimensions and specifications: {specs}.")
    if material:
        strict_lines.append(f"Fixed materials and colors: {material}.")

    strict_lines.extend(
        [
            "Keep the same component count, cork color, orange toe resistance bands, mesh fabric drawstring bag, rounded cork edges, carved board channel, and compact strap assembly whenever the product appears.",
            "If the scene type changes, only change the camera angle, layout, background, model, measurement graphics, or B2B copywriting; never change the physical product design.",
            "Do not add extra accessories, remove required components, change the cork kit into a different fitness product, change the orange bands to another color, or invent a brand logo.",
            "If any prompt detail conflicts with the reference images or fixed product structure, follow the reference images and fixed product structure.",
        ]
    )

    if args.consistency_note:
        strict_lines.append(f"Additional consistency note: {clean_prompt(args.consistency_note)}.")

    return " ".join(line for line in strict_lines if line).strip()


def apply_consistency(prompt, consistency_block):
    if not consistency_block:
        return prompt
    return clean_prompt(f"{consistency_block} Image-specific instruction: {prompt}")


def apply_safe_zone_constraint(prompt, args):
    if args.safe_zone_check == "off":
        return prompt
    return clean_prompt(f"{SAFE_ZONE_LAYOUT_BLOCK} Image instruction: {prompt} {SAFE_ZONE_FINAL_CHECK}")


def inspect_safe_zone(image_path, args):
    with Image.open(image_path) as image:
        image = image.convert("RGB")
        zone_width = max(8, round(image.width * args.safe_zone_width_ratio))
        zone_height = max(8, round(image.height * args.safe_zone_height_ratio))
        zone = image.crop((0, 0, zone_width, zone_height)).convert("L")
        edges = zone.filter(ImageFilter.FIND_EDGES)
        if edges.width > 6 and edges.height > 6:
            edges = edges.crop((3, 3, edges.width - 3, edges.height - 3))
        histogram = edges.histogram()
        total = max(1, sum(histogram))
        edge_mean = sum(value * count for value, count in enumerate(histogram)) / total
        edge_fraction = sum(histogram[args.safe_zone_edge_threshold + 1 :]) / total
        contrast_stddev = ImageStat.Stat(zone).stddev[0]
        passed = (
            edge_mean <= args.safe_zone_max_edge_mean
            and edge_fraction <= args.safe_zone_max_edge_fraction
        )
        return {
            "status": "passed" if passed else "failed",
            "zone_pixels": [zone_width, zone_height],
            "edge_mean": round(edge_mean, 4),
            "edge_fraction": round(edge_fraction, 6),
            "contrast_stddev": round(contrast_stddev, 4),
            "limits": {
                "edge_mean": args.safe_zone_max_edge_mean,
                "edge_fraction": args.safe_zone_max_edge_fraction,
                "edge_threshold": args.safe_zone_edge_threshold,
            },
        }


def build_safe_zone_repair_prompt(original_prompt):
    return clean_prompt(
        "AI REPAIR TASK. The first supplied image is the image to repair. "
        "Keep the product design, product count, colors, materials, dimensions, camera angle, "
        "lighting, background, and all existing marketing content unchanged. "
        "Clear the entire upper-left 18 percent width by 18 percent height. "
        "Remove every letter, number, icon, product part, prop, line, shadow, and important "
        "detail from that rectangle and reconstruct the continuous background naturally. "
        "Relocate any displaced English copywriting to any visually suitable position outside "
        "the exclusion zone. Preserve the exact English wording and spelling. "
        "Do not add a logo and do not redesign the product. "
        f"Original image instruction: {original_prompt} {SAFE_ZONE_FINAL_CHECK}"
    )


def run_one_attempt(args, index, prompt, temp_dir, input_images=None, prefix_suffix=""):
    input_images = list(args.image if input_images is None else input_images)
    mode = "edit" if input_images else "generate"
    prefix = f"tmp-product-plan-{index}{prefix_suffix}"
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
    if mode == "edit":
        for image in input_images:
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
    return source


def run_one(args, index, prompt, temp_dir):
    max_attempts = args.max_retries + 1
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            source = run_one_attempt(args, index, prompt, temp_dir)
            break
        except Exception as exc:
            last_error = exc
            if attempt < max_attempts:
                print(
                    f"Image {index} attempt {attempt} failed; retrying "
                    f"({attempt}/{args.max_retries} retries used): {compact_error(exc)}",
                    file=sys.stderr,
                )
    else:
        raise RuntimeError(
            f"Image {index} failed after {max_attempts} attempts "
            f"(initial attempt plus {args.max_retries} retries): {compact_error(last_error)}"
        )

    safe_zone = {"status": "not_checked"}
    repair_attempts = 0
    if args.safe_zone_check == "strict":
        safe_zone = inspect_safe_zone(source, args)
        while safe_zone["status"] != "passed" and repair_attempts < args.safe_zone_retries:
            repair_attempts += 1
            repair_prompt = build_safe_zone_repair_prompt(prompt)
            repair_inputs = [str(source), *args.image]
            try:
                source = run_one_attempt(
                    args,
                    index,
                    repair_prompt,
                    temp_dir,
                    input_images=repair_inputs,
                    prefix_suffix=f"-safe-repair-{repair_attempts}",
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Image {index} safe-zone AI repair {repair_attempts} failed: "
                    f"{compact_error(exc)}"
                ) from exc
            safe_zone = inspect_safe_zone(source, args)
        if safe_zone["status"] != "passed":
            raise RuntimeError(
                f"Image {index} failed logo safe-zone validation after "
                f"{repair_attempts} AI repair attempt(s): "
                f"edge_mean={safe_zone['edge_mean']}, "
                f"edge_fraction={safe_zone['edge_fraction']}"
            )
    return index, source, attempt, repair_attempts, safe_zone


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
    parser.add_argument("--model", default="gpt-image-2")
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument("--quality", default="high")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Retries for each failed image after its initial attempt (default: 3).",
    )
    parser.add_argument(
        "--consistency",
        choices=("strict", "off"),
        default="strict",
        help="Inject a batch product identity lock into every prompt to improve product consistency (default: strict).",
    )
    parser.add_argument(
        "--consistency-note",
        help="Optional extra English consistency instruction to inject into every image prompt.",
    )
    parser.add_argument(
        "--safe-zone-check",
        choices=("strict", "off"),
        default="strict",
        help="Validate the upper-left logo exclusion zone and AI-repair failures (default: strict).",
    )
    parser.add_argument(
        "--safe-zone-retries",
        type=int,
        default=2,
        help="AI repair attempts after a generated image fails logo safe-zone validation (default: 2).",
    )
    parser.add_argument("--safe-zone-width-ratio", type=float, default=0.18)
    parser.add_argument("--safe-zone-height-ratio", type=float, default=0.18)
    parser.add_argument("--safe-zone-edge-threshold", type=int, default=32)
    parser.add_argument("--safe-zone-max-edge-mean", type=float, default=4.0)
    parser.add_argument("--safe-zone-max-edge-fraction", type=float, default=0.008)
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
    if args.max_retries < 0:
        print("--max-retries must be zero or greater.", file=sys.stderr)
        return 2
    if args.safe_zone_retries < 0:
        print("--safe-zone-retries must be zero or greater.", file=sys.stderr)
        return 2
    if not 0 < args.safe_zone_width_ratio <= 0.5:
        print("--safe-zone-width-ratio must be greater than 0 and at most 0.5.", file=sys.stderr)
        return 2
    if not 0 < args.safe_zone_height_ratio <= 0.5:
        print("--safe-zone-height-ratio must be greater than 0 and at most 0.5.", file=sys.stderr)
        return 2
    if not 0 <= args.safe_zone_edge_threshold <= 255:
        print("--safe-zone-edge-threshold must be between 0 and 255.", file=sys.stderr)
        return 2
    if args.safe_zone_max_edge_mean < 0 or args.safe_zone_max_edge_fraction < 0:
        print("Safe-zone edge limits must be zero or greater.", file=sys.stderr)
        return 2

    markdown = read_text(plan)
    prompts = extract_prompts(markdown)
    if not prompts:
        print("No Image N English Prompt sections found in the plan.", file=sys.stderr)
        return 2

    product_name = sanitize_filename(args.product_name or infer_product_name(plan))
    primary_prompt = prompts[0][1] if prompts else ""
    consistency_block = build_consistency_block(args, markdown, product_name, primary_prompt)
    prompts = [
        (
            index,
            apply_safe_zone_constraint(
                apply_consistency(prompt, consistency_block),
                args,
            ),
        )
        for index, prompt in prompts
    ]
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
        for index, source, attempts, repair_attempts, safe_zone in sorted(
            successes, key=lambda item: item[0]
        ):
            target = out_dir / f"{product_name}-{index}.jpg"
            shutil.move(str(source), str(target))
            outputs.append(
                {
                    "index": index,
                    "path": str(target.resolve()),
                    "bytes": target.stat().st_size,
                    "attempts": attempts,
                    "safe_zone_repair_attempts": repair_attempts,
                    "safe_zone": safe_zone,
                }
            )

        mode = "edit" if args.image else "generate"
        result = build_result(plan, product_name, root_dir, out_dir, mode, outputs, failures)
        result["consistency"] = args.consistency
        result["safe_zone_check"] = args.safe_zone_check
        result["safe_zone_retries"] = args.safe_zone_retries
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if failures:
            print(result["message"], file=sys.stderr)
        return 1 if failures and not outputs else 0
    finally:
        if not args.keep_temp:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
