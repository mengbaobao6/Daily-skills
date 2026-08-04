#!/usr/bin/env python3
"""Compile a simple UTF-8 Markdown table or CSV into a batch manifest."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re


def split_list(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[;,，；]", value or "") if item.strip()]


def parse_pairs(value: str, value_key: str) -> list[dict]:
    rows = []
    for item in split_list(value):
        left, right = (part.strip() for part in item.split("=", 1))
        rows.append({"quantity": int(left), value_key: right})
    return rows


def parse_markdown(path: Path) -> tuple[dict[str, str], list[dict[str, str]]]:
    text = path.read_text(encoding="utf-8-sig")
    defaults: dict[str, str] = {}
    lines = text.splitlines()
    table_start = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("|") and "item_key" in stripped.casefold() and "title" in stripped.casefold():
            table_start = index
            break
        if ":" in stripped and not stripped.startswith(("#", "|")):
            key, value = stripped.split(":", 1)
            defaults[key.strip()] = value.strip()
    if table_start is None or table_start + 2 >= len(lines):
        raise ValueError("Markdown must contain a table with item_key and title columns.")
    headers = [cell.strip() for cell in lines[table_start].strip().strip("|").split("|")]
    rows = []
    for line in lines[table_start + 2:]:
        if not line.strip().startswith("|"):
            if rows:
                break
            continue
        values = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(values) == len(headers):
            rows.append(dict(zip(headers, values)))
    return defaults, rows


def parse_csv_input(path: Path) -> tuple[dict[str, str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {}, list(csv.DictReader(handle))


def row_value(row: dict[str, str], defaults: dict[str, str], key: str, fallback: str = "") -> str:
    return str(row.get(key) or defaults.get(key) or fallback).strip()


def compile_manifest(input_path: Path) -> dict:
    defaults, rows = (
        parse_markdown(input_path)
        if input_path.suffix.lower() in {".md", ".markdown"}
        else parse_csv_input(input_path)
    )
    batch_id = defaults.get("batch_id") or (rows[0].get("batch_id") if rows else "") or input_path.stem
    products = []
    for row in rows:
        item_key = row_value(row, defaults, "item_key")
        source_id = row_value(row, defaults, "source_product_id")
        title = row_value(row, defaults, "title")
        image_dir = Path(row_value(row, defaults, "images_dir", "."))
        if not image_dir.is_absolute():
            image_dir = input_path.parent / image_dir
        hero = row_value(row, defaults, "hero_image")
        shared = split_list(row_value(row, defaults, "shared_images"))
        image_names = [hero, *shared]
        main_images = [{"local_path": str(image_dir / name)} for name in image_names if name]
        variants = split_list(row_value(row, defaults, "variants"))
        axis = row_value(row, defaults, "variant_axis", "Color")
        price_tiers = parse_pairs(row_value(row, defaults, "price_tiers"), "price")
        moq = int(row_value(row, defaults, "moq", "100"))
        lead_days = int(row_value(row, defaults, "lead_time_days", "15"))
        stock_policy = row_value(row, defaults, "stock_policy", "unlimited")
        sku_defaults = {}
        if stock_policy.casefold() in {"fixed", "limited", "固定库存"}:
            stock_value = row_value(row, defaults, "stock")
            warehouse = row_value(row, defaults, "warehouse_code")
            if not stock_value or not warehouse:
                raise ValueError(f"{item_key}: fixed stock requires stock and warehouse_code")
            sku_defaults = {"stock_target": int(stock_value), "warehouse_code": warehouse}
        attributes = {}
        for pair in split_list(row_value(row, defaults, "attribute_overrides")):
            key, value = (part.strip() for part in pair.split("=", 1))
            attributes[key] = [part.strip() for part in value.split("+")] if "+" in value else value
        custom_properties = {}
        for pair in split_list(row_value(row, defaults, "custom_properties")):
            key, value = (part.strip() for part in pair.split("=", 1))
            custom_properties[key] = value
        product = {
            "item_key": item_key,
            "source_product_id": source_id,
            "title": title,
            "stock_policy": stock_policy,
            "inventory_mode": row_value(row, defaults, "inventory_mode", "deferred"),
            "main_images": main_images,
            "detail_galleries": [{
                "gallery": row_value(row, defaults, "detail_gallery", "200"),
                "display_name": "Scene image",
                "images": [{"local_path": image["local_path"]} for image in main_images],
            }],
            "category_attribute_overrides": attributes,
            "custom_properties": custom_properties,
            "omit_category_attributes": split_list(
                row_value(row, defaults, "omit_category_attributes")
            ),
            "variant_axes": [{"name": axis, "values": variants}],
            "sku_defaults": sku_defaults,
            "price_tiers": price_tiers,
            "moq": moq,
            "lead_times": [{"quantity": moq, "day": lead_days}],
            "package": {
                "length": float(row_value(row, defaults, "pkg_length")),
                "width": float(row_value(row, defaults, "pkg_width")),
                "height": float(row_value(row, defaults, "pkg_height")),
                "weight": float(row_value(row, defaults, "pkg_weight")),
            },
        }
        omit = split_list(row_value(row, defaults, "omit_top_level_fields"))
        if omit:
            product["omit_top_level_fields"] = omit
        products.append(product)
    if not products:
        raise ValueError("Input contains no products.")
    return {"batch_id": batch_id, "products": products}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    manifest = compile_manifest(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "batch_id": manifest["batch_id"],
        "product_count": len(manifest["products"]),
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
