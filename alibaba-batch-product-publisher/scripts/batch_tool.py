#!/usr/bin/env python3
"""Guarded batch prepare, submit, verify, and inventory synchronization."""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import urllib.error
import xml.etree.ElementTree as ET

from api_client import AlibabaClient, load_environment, response_payload
from clone_engine import build, extract_render_xml, top_field
from photobank import local_identity, upload_one


SCRIPT_VERSION = "1.5.1"
COMPARE_FIELDS = {
    "catId", "icbuCatProp", "saleProp", "sku", "productTitle", "scImages",
    "pkgMeasure", "pkgWeight", "ladderPeriod", "priceUnit", "ladderPrice",
    "saleType", "scPrice", "minOrderQuantity", "detailImage",
    "customMoreProperty", "companyDesc", "companyImage", "companyFaqDesc",
}


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def client_for(args: argparse.Namespace) -> AlibabaClient:
    return AlibabaClient(load_environment(args.mcp_config))


def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)


def product_rows(response: dict) -> list[dict]:
    found: dict[str, dict] = {}
    for item in objects(response_payload(response)):
        title = item.get("subject") or item.get("product_name") or item.get("title")
        product_id = item.get("id") or item.get("productId") or item.get("product_id")
        if title and product_id:
            found[str(product_id)] = {"product_id": str(product_id), "title": str(title), "status": item.get("status")}
    return list(found.values())


def exact_title_query(client: AlibabaClient, title: str, evidence_dir: Path) -> list[dict]:
    rows = full_product_catalog(client, evidence_dir)
    return [
        row for row in rows
        if row["title"].strip().casefold() == title.strip().casefold()
    ]


def full_product_catalog(client: AlibabaClient, evidence_dir: Path) -> list[dict]:
    found: dict[str, dict] = {}
    for page in range(1, 101):
        response = None
        for attempt in range(1, 4):
            response = client.request("alibaba.icbu.product.list", {
                "current_page": page,
                "page_size": 30,
                "language": "ENGLISH",
            })
            write_json(evidence_dir / f"duplicate-page-{page}-attempt-{attempt}.json", response)
            if "error_response" not in response:
                break
        if response is None or "error_response" in response:
            raise RuntimeError(f"Duplicate query failed on page {page} after three read-only attempts.")
        payload = response_payload(response)
        if payload.get("biz_success") is False:
            raise RuntimeError(f"Duplicate query failed on page {page}.")
        rows = product_rows(response)
        for row in rows:
            found[row["product_id"]] = row
        if len(rows) < 30:
            return list(found.values())
    raise RuntimeError("Duplicate query exceeded 100 pages; refusing an incomplete check.")


def catalog_title_matches(catalog: list[dict], title: str) -> list[dict]:
    folded = title.strip().casefold()
    return [row for row in catalog if row["title"].strip().casefold() == folded]


def validate_manifest(manifest: dict) -> None:
    batch_id = str(manifest.get("batch_id") or "").strip()
    products = manifest.get("products")
    if not batch_id or not isinstance(products, list) or not products:
        raise SystemExit("Manifest requires batch_id and a non-empty products list.")
    keys = [str(item.get("item_key") or "") for item in products]
    if any(not key for key in keys) or len(keys) != len(set(keys)):
        raise SystemExit("Every item_key must be non-empty and unique.")
    titles = [str(item.get("title") or "").strip().casefold() for item in products]
    if any(not title for title in titles) or len(titles) != len(set(titles)):
        raise SystemExit("Every title must be non-empty and unique within the batch.")


def image_entries(manifest: dict):
    for product in manifest["products"]:
        for image in product.get("main_images") or []:
            yield product, "main_image", image
        for gallery in product.get("detail_galleries") or []:
            for image in gallery.get("images") or []:
                yield product, "detail_image", image


def scan_local_images(manifest: dict, manifest_path: Path) -> dict[str, dict]:
    identities: dict[str, dict] = {}
    for product, role, image in image_entries(manifest):
        local = image.get("local_path")
        if not local:
            continue
        path = Path(str(local))
        if not path.is_absolute():
            path = manifest_path.parent / path
        identity = local_identity(path)
        identity.setdefault("uses", []).append({"item_key": product["item_key"], "role": role})
        existing = identities.get(identity["sha256"])
        if existing:
            existing["uses"].extend(identity["uses"])
            existing.setdefault("local_paths", []).append(identity["local_path"])
        else:
            identity["local_paths"] = [identity["local_path"]]
            identities[identity["sha256"]] = identity
    return identities


def image_map_path(batch_dir: Path) -> Path:
    return batch_dir / "image-map.json"


def load_image_map(batch_dir: Path) -> dict:
    path = image_map_path(batch_dir)
    return read_json(path) if path.is_file() else {"images": {}}


def resolve_manifest_images(manifest: dict, manifest_path: Path, batch_dir: Path) -> tuple[dict, dict]:
    resolved = json.loads(json.dumps(manifest))
    planned = scan_local_images(resolved, manifest_path)
    mapping = load_image_map(batch_dir)
    unresolved = []
    for product, role, image in image_entries(resolved):
        local = image.get("local_path")
        if not local:
            continue
        path = Path(str(local))
        if not path.is_absolute():
            path = manifest_path.parent / path
        identity = local_identity(path)
        uploaded = mapping.get("images", {}).get(identity["sha256"]) or {}
        if uploaded.get("success") and uploaded.get("file_id") and uploaded.get("url"):
            image["file_id"] = uploaded["file_id"]
            image["url"] = uploaded["url"]
            image["local_sha256"] = identity["sha256"]
        else:
            unresolved.append({
                "item_key": product["item_key"],
                "role": role,
                "local_path": identity["local_path"],
                "sha256": identity["sha256"],
            })
    plan = {
        "batch_id": manifest["batch_id"],
        "unique_local_images": len(planned),
        "images": list(planned.values()),
        "unresolved": unresolved,
        "upload_required": bool(unresolved),
    }
    write_json(batch_dir / "image-upload-plan.json", plan)
    return resolved, plan


def upload_images(args: argparse.Namespace) -> None:
    manifest = read_json(args.manifest)
    validate_manifest(manifest)
    batch_id = str(manifest["batch_id"])
    if args.expected_batch_id != batch_id:
        raise SystemExit("Batch ID guard does not match the image-upload manifest.")
    batch_dir = args.work_dir / batch_id
    planned = scan_local_images(manifest, args.manifest)
    mapping = load_image_map(batch_dir)
    mapping.setdefault("batch_id", batch_id)
    mapping.setdefault("images", {})
    client = client_for(args)
    for sha256, identity in planned.items():
        prior = mapping["images"].get(sha256)
        if prior:
            if prior.get("success") and prior.get("file_id") and prior.get("url"):
                continue
            raise SystemExit(f"Image {sha256} already has an attempted or incomplete upload; no automatic retry.")
        attempt = {
            **identity,
            "api": "alibaba.icbu.photobank.upload",
            "attempted_at_epoch_ms": int(time.time() * 1000),
            "automatic_retry_allowed": False,
            "outcome": "attempt_started",
        }
        mapping["images"][sha256] = attempt
        write_json(image_map_path(batch_dir), mapping)
        try:
            result = upload_one(client, Path(identity["local_path"]))
            attempt.update(result)
            attempt["outcome"] = "uploaded" if result["success"] else "api_rejected"
        except Exception as exc:
            attempt.update({"outcome": "ambiguous_exception", "exception_type": type(exc).__name__, "success": False})
            write_json(image_map_path(batch_dir), mapping)
            raise SystemExit(f"Ambiguous image upload for {identity['local_path']}; do not retry automatically.") from exc
        write_json(image_map_path(batch_dir), mapping)
        if not attempt["success"]:
            raise SystemExit(f"Photobank rejected {identity['local_path']}; later images were not uploaded.")
    print(json.dumps({
        "batch_id": batch_id,
        "unique_images": len(planned),
        "resolved_images": sum(
            bool(item.get("success") and item.get("file_id") and item.get("url"))
            for item in mapping["images"].values()
        ),
        "image_map": str(image_map_path(batch_dir)),
    }, ensure_ascii=False, indent=2))


def fetch_render(client: AlibabaClient, product: dict) -> dict:
    return client.request("alibaba.icbu.product.schema.render", {
        "param_product_top_publish_request": {
            "product_id": str(product["source_product_id"]),
            "cat_id": int(product["category_id"]),
            "language": "en_US",
        }
    })


def resolve_live_category(client: AlibabaClient, product: dict, item_dir: Path) -> None:
    if product.get("category_id"):
        return
    response = client.request("alibaba.icbu.product.get", {
        "product_id": str(product["source_product_id"]),
        "language": "ENGLISH",
    })
    write_json(item_dir / "source-product-get.json", response)
    source = response_payload(response).get("product") or {}
    category_id = source.get("category_id") or source.get("categoryId")
    if not category_id:
        raise SystemExit(f"{product['item_key']}: cannot resolve source category_id.")
    product["category_id"] = int(category_id)


def runtime_manifest(args: argparse.Namespace) -> dict:
    original = read_json(args.manifest)
    batch_dir = args.work_dir / str(original["batch_id"])
    resolved = batch_dir / "resolved-manifest.json"
    return read_json(resolved) if resolved.is_file() else original


def prepare(args: argparse.Namespace) -> None:
    original_manifest = read_json(args.manifest)
    validate_manifest(original_manifest)
    batch_dir = args.work_dir / str(original_manifest["batch_id"])
    batch_dir.mkdir(parents=True, exist_ok=True)
    manifest, image_plan = resolve_manifest_images(original_manifest, args.manifest, batch_dir)
    client = None if args.offline_render_dir else client_for(args)
    catalog = full_product_catalog(client, batch_dir / "duplicate-catalog") if client else []
    reports = []
    for product in manifest["products"]:
        item_dir = batch_dir / str(product["item_key"])
        item_dir.mkdir(parents=True, exist_ok=True)
        if args.offline_render_dir:
            candidate_json = args.offline_render_dir / f"{product['source_product_id']}.json"
            candidate_xml = args.offline_render_dir / f"{product['source_product_id']}.xml"
            source_path = candidate_json if candidate_json.is_file() else candidate_xml
            if not source_path.is_file():
                raise SystemExit(f"Missing offline Render for {product['source_product_id']}.")
            render_path = item_dir / ("source-render-response.json" if source_path.suffix.lower() == ".json" else "source-render.xml")
            source_xml = extract_render_xml(source_path)
            render_path.write_text(source_path.read_text(encoding="utf-8-sig"), encoding="utf-8")
            if not product.get("category_id"):
                product["category_id"] = int(top_field(ET.fromstring(source_xml), "catId").findtext("value"))
        else:
            resolve_live_category(client, product, item_dir)
            render_path = item_dir / "source-render-response.json"
            response = fetch_render(client, product)
            write_json(render_path, response)
            source_xml = extract_render_xml(render_path)
        xml, report = build(source_xml, product)
        xml_path = item_dir / "product.xml"
        # Write the exact UTF-8 bytes used by clone_engine for xml_sha256.
        # Path.write_text can translate LF to CRLF on Windows and invalidate
        # the preflight hash even though the parsed XML is unchanged.
        xml_path.write_bytes(xml.encode("utf-8"))
        report["xml_file"] = str(xml_path)
        report["source_render_file"] = str(render_path)
        if client:
            matches = catalog_title_matches(catalog, str(product["title"]))
            report["duplicate_check"] = {"performed": True, "exact_matches": matches}
            if matches:
                report["errors"].append("Exact-title product already exists.")
        else:
            report["duplicate_check"] = {"performed": False, "exact_matches": []}
        if image_plan["upload_required"]:
            report["errors"].append("Local images remain unresolved; run upload-images, then prepare again.")
        report["ready_for_submission"] = not report["errors"] and report["duplicate_check"]["performed"]
        write_json(item_dir / "preflight.json", report)
        write_json(item_dir / "diff-summary.json", {
            "item_key": report["item_key"],
            "source_product_id": report["source_product_id"],
            "changed_top_level_fields": report["changed_top_level_fields"],
            "xml_sha256": report["xml_sha256"],
        })
        reports.append(report)
    write_json(batch_dir / "resolved-manifest.json", manifest)
    batch_report = {
        "tool_version": SCRIPT_VERSION,
        "batch_id": manifest["batch_id"],
        "manifest": str(args.manifest),
        "offline": bool(args.offline_render_dir),
        "submission_performed": False,
        "image_upload_plan": str(batch_dir / "image-upload-plan.json"),
        "unresolved_local_image_count": len(image_plan["unresolved"]),
        "ready_count": sum(bool(item["ready_for_submission"]) for item in reports),
        "blocked_count": sum(not item["ready_for_submission"] for item in reports),
        "products": reports,
    }
    write_json(batch_dir / "batch-report.json", batch_report)
    print(json.dumps(batch_report, ensure_ascii=False, indent=2))
    if batch_report["blocked_count"]:
        raise SystemExit(2)


def load_preflights(manifest: dict, batch_dir: Path) -> list[tuple[dict, dict, Path]]:
    result = []
    for product in manifest["products"]:
        item_dir = batch_dir / str(product["item_key"])
        report = read_json(item_dir / "preflight.json")
        xml_path = item_dir / "product.xml"
        actual_hash = hashlib.sha256(xml_path.read_bytes()).hexdigest()
        if actual_hash != report.get("xml_sha256"):
            raise SystemExit(f"{product['item_key']}: XML hash differs from preflight.")
        if report.get("errors") or not report.get("ready_for_submission"):
            raise SystemExit(f"{product['item_key']}: preflight is not ready.")
        result.append((product, report, item_dir))
    return result


def response_product_id(response: dict) -> str | None:
    payload = response_payload(response)
    value = payload.get("productId") or payload.get("product_id")
    return str(value) if value else None


def submit_draft(args: argparse.Namespace) -> None:
    manifest = runtime_manifest(args)
    validate_manifest(manifest)
    batch_id = str(manifest["batch_id"])
    if args.expected_batch_id != batch_id:
        raise SystemExit("Batch ID guard does not match the draft manifest.")
    batch_dir = args.work_dir / batch_id
    items = load_preflights(manifest, batch_dir)
    state_path = batch_dir / "draft-submission-state.json"
    state = read_json(state_path) if state_path.is_file() else {"batch_id": batch_id, "items": {}}
    client = client_for(args)
    catalog = full_product_catalog(client, batch_dir / "draft-submit-duplicate-catalog")
    for product, report, item_dir in items:
        key = str(product["item_key"])
        if key in state["items"]:
            raise SystemExit(f"{key}: a draft-add attempt is already recorded; automatic retry is forbidden.")
        matches = catalog_title_matches(catalog, str(product["title"]))
        if matches:
            raise SystemExit(f"{key}: exact-title product exists; draft submission blocked.")
        xml = (item_dir / "product.xml").read_text(encoding="utf-8")
        attempt = {
            "item_key": key,
            "api": "alibaba.icbu.product.schema.add.draft",
            "category_id": product["category_id"],
            "title": product["title"],
            "xml_sha256": report["xml_sha256"],
            "inventory_mode": str(product.get("inventory_mode") or "deferred"),
            "attempted_at_epoch_ms": int(time.time() * 1000),
            "automatic_retry_allowed": False,
            "outcome": "attempt_started",
        }
        state["items"][key] = attempt
        write_json(state_path, state)
        write_json(item_dir / "draft-add-attempt.json", attempt)
        try:
            response = client.request("alibaba.icbu.product.schema.add.draft", {
                "param_product_top_publish_request": {
                    "cat_id": int(product["category_id"]),
                    "language": "en_US",
                    "xml": xml,
                }
            })
            write_json(item_dir / "draft-add-response.json", response)
            payload = response_payload(response)
            product_id = response_product_id(response)
            attempt.update({
                "outcome": "accepted" if payload.get("biz_success") and product_id else "api_rejected",
                "product_id": product_id,
                "msg_code": payload.get("msg_code"),
                "request_id": payload.get("request_id"),
                "trace_id": payload.get("trace_id") or payload.get("_trace_id_"),
            })
        except Exception as exc:
            attempt.update({"outcome": "ambiguous_exception", "exception_type": type(exc).__name__})
            write_json(item_dir / "draft-add-exception.json", attempt)
            write_json(state_path, state)
            raise SystemExit(f"{key}: ambiguous draft-add outcome; do not retry.") from exc
        write_json(item_dir / "draft-add-attempt.json", attempt)
        write_json(state_path, state)
        if attempt["outcome"] != "accepted":
            raise SystemExit(f"{key}: API rejected the draft add; later items were not submitted.")
    print(json.dumps(state, ensure_ascii=False, indent=2))


def verify_draft(args: argparse.Namespace) -> None:
    manifest = runtime_manifest(args)
    validate_manifest(manifest)
    batch_dir = args.work_dir / str(manifest["batch_id"])
    state = read_json(batch_dir / "draft-submission-state.json")
    client = client_for(args)
    results = []
    for product in manifest["products"]:
        key = str(product["item_key"])
        product_id = (state.get("items", {}).get(key) or {}).get("product_id")
        if not product_id:
            results.append({"item_key": key, "verified": False, "reason": "No accepted draft product ID."})
            continue
        item_dir = batch_dir / key
        response = client.request("alibaba.icbu.product.schema.render.draft", {
            "param_product_top_publish_request": {
                "product_id": product_id,
                "cat_id": int(product["category_id"]),
                "language": "en_US",
            }
        })
        write_json(item_dir / "draft-render.json", response)
        payload = response_payload(response)
        expected_xml = (item_dir / "product.xml").read_text(encoding="utf-8")
        render_ok = bool(payload.get("biz_success") and payload.get("data"))
        checks = {
            "render_success": render_ok,
            "changed_fields_match": render_ok
            and selected_comparison(expected_xml) == selected_comparison(str(payload["data"])),
        }
        result = {"item_key": key, "product_id": product_id, "checks": checks, "verified": all(checks.values())}
        write_json(item_dir / "draft-verification-report.json", result)
        results.append(result)
    report = {
        "batch_id": manifest["batch_id"],
        "products": results,
        "fully_verified": all(item["verified"] for item in results),
    }
    write_json(batch_dir / "draft-verification-summary.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["fully_verified"]:
        raise SystemExit(3)


def submit(args: argparse.Namespace) -> None:
    manifest = runtime_manifest(args)
    validate_manifest(manifest)
    batch_id = str(manifest["batch_id"])
    if args.expected_batch_id != batch_id:
        raise SystemExit("Batch ID guard does not match the product manifest.")
    batch_dir = args.work_dir / batch_id
    items = load_preflights(manifest, batch_dir)
    state_path = batch_dir / "submission-state.json"
    state = read_json(state_path) if state_path.is_file() else {"batch_id": batch_id, "items": {}}
    client = client_for(args)
    catalog = full_product_catalog(client, batch_dir / "submit-duplicate-catalog")
    for product, report, item_dir in items:
        key = str(product["item_key"])
        if key in state["items"]:
            raise SystemExit(f"{key}: an add attempt is already recorded; automatic retry is forbidden.")
        matches = catalog_title_matches(catalog, str(product["title"]))
        if matches:
            raise SystemExit(f"{key}: exact-title product exists; submission blocked.")
        xml_path = item_dir / "product.xml"
        xml = xml_path.read_text(encoding="utf-8")
        attempt = {
            "item_key": key,
            "api": "alibaba.icbu.product.schema.add",
            "category_id": product["category_id"],
            "title": product["title"],
            "xml_sha256": report["xml_sha256"],
            "inventory_mode": str(product.get("inventory_mode") or "deferred"),
            "embedded_inventory_targets": report.get("inventory_targets")
            if str(product.get("inventory_mode") or "deferred") == "embedded"
            else [],
            "attempted_at_epoch_ms": int(time.time() * 1000),
            "automatic_retry_allowed": False,
            "outcome": "attempt_started",
        }
        state["items"][key] = attempt
        write_json(state_path, state)
        write_json(item_dir / "add-attempt.json", attempt)
        try:
            response = client.request("alibaba.icbu.product.schema.add", {
                "param_product_top_publish_request": {
                    "cat_id": int(product["category_id"]),
                    "language": "en_US",
                    "xml": xml,
                }
            })
            write_json(item_dir / "add-response.json", response)
            payload = response_payload(response)
            product_id = response_product_id(response)
            attempt.update({
                "outcome": "accepted" if payload.get("biz_success") and product_id else "api_rejected",
                "product_id": product_id,
                "msg_code": payload.get("msg_code"),
                "request_id": payload.get("request_id"),
                "trace_id": payload.get("trace_id") or payload.get("_trace_id_"),
            })
        except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError, Exception) as exc:
            attempt.update({"outcome": "ambiguous_exception", "exception_type": type(exc).__name__})
            write_json(item_dir / "add-exception.json", attempt)
            write_json(state_path, state)
            raise SystemExit(f"{key}: ambiguous add outcome; do not retry. Run read-only duplicate checks.") from exc
        write_json(item_dir / "add-attempt.json", attempt)
        write_json(state_path, state)
        if attempt["outcome"] != "accepted":
            raise SystemExit(f"{key}: API rejected the add; later items were not submitted.")
    print(json.dumps(state, ensure_ascii=False, indent=2))


def comparable(node: ET.Element):
    attrs = tuple(sorted((key, value) for key, value in node.attrib.items() if key not in {"name", "displayName"}))
    children = [
        comparable(child)
        for child in node
        if child.tag not in {"fields", "rules", "options", "label-group"}
    ]
    if node.get("id") == "skuId":
        children = []
    return node.tag, attrs, (node.text or "").strip(), tuple(children)


def normalized_number(value: object) -> str:
    text = str(value or "").strip()
    try:
        number = Decimal(text)
    except InvalidOperation:
        return text
    return format(number.normalize(), "f")


def value_signature(value: ET.Element) -> tuple:
    attrs = tuple(sorted(
        (key, item) for key, item in value.attrib.items()
        if key not in {"displayName", "valid"}
    ))
    return (str(value.text or "").strip(), attrs)


def attribute_value_signature(value: ET.Element) -> tuple:
    text = str(value.text or "").strip()
    input_value = str(value.get("inputValue") or "").strip()
    if text.startswith("-"):
        return ("custom", input_value.casefold())
    return (text, input_value.casefold())


def image_identity(value: object) -> str:
    text = str(value or "")
    match = re.search(r"(H[0-9A-Fa-f]{32})", text)
    return match.group(1).casefold() if match else text.strip().casefold()


def structured_field_signature(field: ET.Element):
    field_id = str(field.get("id") or "")
    if field_id in {"icbuCatProp", "saleProp"}:
        block = field.find("complex-value")
        return tuple(sorted(
            (
                str(child.get("id") or ""),
                tuple(sorted(attribute_value_signature(value) for value in child.findall("./value") + child.findall("./values/value"))),
            )
            for child in (block.findall("field") if block is not None else [])
        ))
    if field_id == "sku":
        rows = []
        for row in field.findall("complex-values"):
            outer = row.findtext("field[@id='skuOuterId']/value") or ""
            price = normalized_number(row.findtext("field[@id='price']/value"))
            props = tuple(sorted(value_signature(value) for value in row.findall("field[@id='props']/values/value")))
            rows.append((outer, price, props))
        return tuple(sorted(rows))
    if field_id in {"ladderPrice", "ladderPeriod"}:
        second = "price" if field_id == "ladderPrice" else "day"
        rows = []
        block = field.find("complex-value")
        for tier in (block.findall("field") if block is not None else []):
            content = tier.find("complex-value")
            if content is not None:
                rows.append((
                    normalized_number(content.findtext("field[@id='quantity']/value")),
                    normalized_number(content.findtext(f"field[@id='{second}']/value")),
                ))
        return tuple(sorted(rows, key=lambda row: Decimal(row[0] or "0")))
    if field_id == "pkgMeasure":
        block = field.find("complex-value")
        return tuple(sorted(
            (str(child.get("id") or ""), normalized_number(child.findtext("value")))
            for child in (block.findall("field") if block is not None else [])
        ))
    if field_id == "scImages":
        block = field.find("complex-value")
        return tuple(sorted(
            (
                str(child.get("id") or ""),
                image_identity(child.findtext("value")),
            )
            for child in (block.findall("field") if block is not None else [])
        ))
    if field_id in {"detailImage", "companyImage"}:
        galleries = []
        for row in field.findall("complex-values"):
            gallery = str(row.findtext("field[@id='gallery']/value") or "")
            images = tuple(
                image_identity(value.text)
                for value in row.findall("field[@id='images']/complex-values/field[@id='imageURL']/value")
            )
            galleries.append((gallery, images))
        return tuple(sorted(galleries))
    if field_id == "customMoreProperty":
        block = field.find("complex-value")
        rows = []
        for item in (block.findall("field") if block is not None else []):
            content = item.find("complex-value")
            if content is not None:
                rows.append((
                    str(content.findtext("field[@id='propName']/value") or "").strip(),
                    str(content.findtext("field[@id='valueName']/value") or "").strip(),
                ))
        return tuple(sorted(rows))
    if field_id in {"pkgWeight", "minOrderQuantity"}:
        return normalized_number(field.findtext("value"))
    return comparable(field)


def selected_comparison(xml: str) -> dict:
    root = ET.fromstring(xml)
    result = {}
    for field_id in COMPARE_FIELDS:
        field = next((field for field in root.findall("field") if field.get("id") == field_id), None)
        result[field_id] = structured_field_signature(field) if field is not None else None
    return result


def inventory_targets_from_xml(xml: str) -> list[dict]:
    root = ET.fromstring(xml)
    sku = top_field(root, "sku")
    targets = []
    if sku is None:
        return targets
    for row in sku.findall("complex-values"):
        outer_id = str(row.findtext("field[@id='skuOuterId']/value") or "")
        stock = row.find("field[@id='skuStock']")
        if not outer_id or stock is None:
            continue
        for value in stock.findall("values/value"):
            raw_target = value.get("srcValue") or value.text
            warehouse = str(value.get("warehouseCode") or "")
            if raw_target is None or not warehouse:
                continue
            targets.append({
                "sku_outer_id": outer_id,
                "warehouse_code": warehouse,
                "stock_target": int(raw_target),
            })
    return targets


def verify(args: argparse.Namespace) -> None:
    manifest = runtime_manifest(args)
    validate_manifest(manifest)
    batch_dir = args.work_dir / str(manifest["batch_id"])
    state = read_json(batch_dir / "submission-state.json")
    client = client_for(args)
    results = []
    for product in manifest["products"]:
        key = str(product["item_key"])
        product_id = (state.get("items", {}).get(key) or {}).get("product_id")
        if not product_id:
            results.append({"item_key": key, "verified": False, "reason": "No accepted product ID."})
            continue
        item_dir = batch_dir / key
        get_response = client.request("alibaba.icbu.product.get", {"product_id": product_id, "language": "ENGLISH"})
        render_response = client.request("alibaba.icbu.product.schema.render", {
            "param_product_top_publish_request": {
                "product_id": product_id,
                "cat_id": int(product["category_id"]),
                "language": "en_US",
            }
        })
        write_json(item_dir / "verify-get.json", get_response)
        write_json(item_dir / "verify-render.json", render_response)
        get_product = response_payload(get_response).get("product") or {}
        render_payload = response_payload(render_response)
        expected_xml = (item_dir / "product.xml").read_text(encoding="utf-8")
        changed_fields_match = bool(render_payload.get("biz_success") and render_payload.get("data")) and (
            selected_comparison(expected_xml) == selected_comparison(str(render_payload["data"]))
        )
        inventory_mode = str(product.get("inventory_mode") or "deferred")
        embedded_inventory_matches = True
        if inventory_mode == "embedded":
            embedded_inventory_matches = False
            if render_payload.get("biz_success") and render_payload.get("data"):
                sku_map = sku_ids_by_outer_id(str(render_payload["data"]))
                inventory_response = client.request("alibaba.icbu.product.sku.inventory.get", {
                    "language": "en_US", "product_id": product_id,
                })
                write_json(item_dir / "verify-inventory.json", inventory_response)
                rows = inventory_rows(inventory_response)
                xml_targets = inventory_targets_from_xml(expected_xml)
                expected_inventory = {
                    (
                        sku_map.get(str(target["sku_outer_id"])),
                        str(target["warehouse_code"]),
                    ): int(target["stock_target"])
                    for target in xml_targets
                }
                actual_inventory = {
                    (str(row.get("sku_id") or ""), str(row.get("inventory_code") or "")): int(row.get("inventory") or 0)
                    for row in rows
                }
                embedded_inventory_matches = bool(expected_inventory) and all(
                    sku_id and actual_inventory.get((sku_id, warehouse)) == target
                    for (sku_id, warehouse), target in expected_inventory.items()
                )
        checks = {
            "product_exists": bool(get_product),
            "status_approved": str(get_product.get("status") or "").lower() == "approved",
            "display_yes": str(get_product.get("display") or "").upper() == "Y",
            "category_matches": str(get_product.get("category_id")) == str(product["category_id"]),
            "title_matches": get_product.get("subject") == product["title"],
            "render_success": bool(render_payload.get("biz_success")),
            "changed_fields_match": changed_fields_match,
            "embedded_inventory_matches": embedded_inventory_matches,
        }
        result = {"item_key": key, "product_id": product_id, "checks": checks, "verified": all(checks.values())}
        write_json(item_dir / "verification-report.json", result)
        results.append(result)
    report = {"batch_id": manifest["batch_id"], "products": results, "fully_verified": all(item["verified"] for item in results)}
    write_json(batch_dir / "verification-summary.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["fully_verified"]:
        raise SystemExit(3)


def inventory_rows(response: dict) -> list[dict]:
    payload = response_payload(response)
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    rows = result.get("data_list") or []
    if isinstance(rows, dict):
        rows = rows.get("data") or rows.get("inventory") or []
    return [rows] if isinstance(rows, dict) else (rows if isinstance(rows, list) else [])


def sku_ids_by_outer_id(render_xml: str) -> dict[str, str]:
    root = ET.fromstring(render_xml)
    sku = top_field(root, "sku")
    mapping = {}
    for row in sku.findall("complex-values"):
        outer = row.findtext("field[@id='skuOuterId']/value") or ""
        sku_id = row.findtext("field[@id='skuId']/value") or ""
        if outer and sku_id:
            mapping[outer] = sku_id
    return mapping


def plan_inventory(args: argparse.Namespace) -> None:
    manifest = runtime_manifest(args)
    validate_manifest(manifest)
    batch_id = str(manifest["batch_id"])
    batch_dir = args.work_dir / batch_id
    state = read_json(batch_dir / "submission-state.json")
    client = client_for(args)
    report = {"batch_id": batch_id, "api_write_calls": 0, "products": []}
    for product in manifest["products"]:
        key = str(product["item_key"])
        if str(product.get("inventory_mode") or "deferred") == "embedded":
            raise SystemExit(f"{key}: inventory planning requires a separate deferred recovery batch.")
        product_id = (state.get("items", {}).get(key) or {}).get("product_id")
        if not product_id:
            raise SystemExit(f"{key}: no accepted product ID.")
        item_dir = batch_dir / key
        item_dir.mkdir(parents=True, exist_ok=True)
        get_response = client.request("alibaba.icbu.product.get", {
            "product_id": product_id, "language": "ENGLISH",
        })
        write_json(item_dir / "inventory-plan-get.json", get_response)
        current_product = response_payload(get_response).get("product") or {}
        approved = str(current_product.get("status") or "").lower() == "approved"
        display_yes = str(current_product.get("display") or "").upper() == "Y"
        render_response = client.request("alibaba.icbu.product.schema.render", {
            "param_product_top_publish_request": {
                "product_id": product_id,
                "cat_id": int(product["category_id"]),
                "language": "en_US",
            }
        })
        write_json(item_dir / "inventory-plan-render.json", render_response)
        render_payload = response_payload(render_response)
        render_ok = bool(render_payload.get("biz_success") and render_payload.get("data"))
        sku_map = sku_ids_by_outer_id(str(render_payload.get("data") or "")) if render_ok else {}
        inventory_response = client.request("alibaba.icbu.product.sku.inventory.get", {
            "language": "en_US", "product_id": product_id,
        })
        write_json(item_dir / "inventory-plan-current.json", inventory_response)
        rows = inventory_rows(inventory_response)
        planned = []
        for sku in product.get("skus") or []:
            if sku.get("stock_target") is None:
                continue
            outer_id = str(sku.get("sku_outer_id") or "")
            sku_id = sku_map.get(outer_id)
            warehouse = str(sku.get("warehouse_code") or "")
            target = int(sku["stock_target"])
            row = next(
                (
                    row for row in rows
                    if str(row.get("sku_id")) == str(sku_id)
                    and str(row.get("inventory_code")) == warehouse
                ),
                None,
            )
            current = int(row.get("inventory") or 0) if row is not None else None
            ready = bool(approved and display_yes and sku_id and warehouse and target >= 0 and row is not None)
            planned.append({
                "sku_outer_id": outer_id,
                "sku_id": sku_id,
                "warehouse_code": warehouse,
                "current": current,
                "target": target,
                "delta": target - current if current is not None else None,
                "ready": ready,
            })
        product_ready = bool(planned and all(row["ready"] for row in planned))
        report["products"].append({
            "item_key": key,
            "product_id": str(product_id),
            "status_approved": approved,
            "display_yes": display_yes,
            "render_success": render_ok,
            "sku_count": len(planned),
            "ready": product_ready,
            "inventory_updates": planned,
        })
    report["ready"] = bool(report["products"] and all(item["ready"] for item in report["products"]))
    write_json(batch_dir / "inventory-plan.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["ready"]:
        raise SystemExit(2)


def sync_inventory(args: argparse.Namespace) -> None:
    manifest = runtime_manifest(args)
    validate_manifest(manifest)
    batch_id = str(manifest["batch_id"])
    if args.expected_batch_id != batch_id:
        raise SystemExit("Batch ID guard does not match the inventory manifest.")
    batch_dir = args.work_dir / batch_id
    state_path = batch_dir / "submission-state.json"
    state = read_json(state_path)
    client = client_for(args)
    for product in manifest["products"]:
        key = str(product["item_key"])
        if str(product.get("inventory_mode") or "deferred") == "embedded":
            raise SystemExit(
                f"{key}: sync-inventory is blocked for embedded inventory mode; "
                "verify the initial stock read-only and create a separate recovery batch if needed."
            )
        product_id = (state.get("items", {}).get(key) or {}).get("product_id")
        if not product_id:
            raise SystemExit(f"{key}: no accepted product ID.")
        item_dir = batch_dir / key
        get_response = client.request("alibaba.icbu.product.get", {"product_id": product_id, "language": "ENGLISH"})
        current_product = response_payload(get_response).get("product") or {}
        if str(current_product.get("status") or "").lower() != "approved":
            raise SystemExit(f"{key}: inventory blocked until status=approved.")
        render_response = client.request("alibaba.icbu.product.schema.render", {
            "param_product_top_publish_request": {
                "product_id": product_id,
                "cat_id": int(product["category_id"]),
                "language": "en_US",
            }
        })
        render_payload = response_payload(render_response)
        if not render_payload.get("biz_success") or not render_payload.get("data"):
            raise SystemExit(f"{key}: cannot resolve real SKU IDs.")
        sku_map = sku_ids_by_outer_id(str(render_payload["data"]))
        before_response = client.request("alibaba.icbu.product.sku.inventory.get", {
            "language": "en_US", "product_id": product_id,
        })
        rows = inventory_rows(before_response)
        for sku in product.get("skus") or []:
            if sku.get("stock_target") is None:
                continue
            outer_id = str(sku.get("sku_outer_id") or "")
            sku_id = sku_map.get(outer_id)
            warehouse = str(sku.get("warehouse_code") or "")
            target = int(sku["stock_target"])
            if not sku_id or not warehouse or target < 0:
                raise SystemExit(f"{key}/{outer_id}: invalid inventory target or unresolved SKU.")
            attempt_key = f"inventory:{key}:{sku_id}:{warehouse}"
            if attempt_key in state["items"]:
                raise SystemExit(f"{attempt_key}: inventory attempt already recorded; no retry.")
            row = next((row for row in rows if str(row.get("sku_id")) == sku_id and str(row.get("inventory_code")) == warehouse), None)
            if row is None:
                raise SystemExit(f"{key}/{outer_id}: inventory service did not return the SKU/warehouse pair.")
            before = int(row.get("inventory") or 0)
            delta = target - before
            attempt = {
                "api": "alibaba.icbu.product.inventory.update",
                "product_id": product_id,
                "sku_id": sku_id,
                "warehouse_code": warehouse,
                "before": before,
                "target": target,
                "delta": delta,
                "outcome": "attempt_started",
            }
            state["items"][attempt_key] = attempt
            write_json(state_path, state)
            if delta:
                response = client.request("alibaba.icbu.product.inventory.update", {
                    "request_param": {
                        "product_id": int(product_id),
                        "inventory_list": [{
                            "sku_id": int(sku_id),
                            "inventory_code": warehouse,
                            "inventory": abs(delta),
                            "operate": "plus" if delta > 0 else "sub",
                        }],
                    }
                })
                write_json(item_dir / f"inventory-{sku_id}-response.json", response)
            after = None
            for poll in range(5):
                if poll:
                    time.sleep(1)
                latest = client.request("alibaba.icbu.product.sku.inventory.get", {
                    "language": "en_US", "product_id": product_id,
                })
                latest_row = next(
                    (row for row in inventory_rows(latest) if str(row.get("sku_id")) == sku_id and str(row.get("inventory_code")) == warehouse),
                    None,
                )
                after = int(latest_row.get("inventory") or 0) if latest_row else None
                if after == target:
                    break
            attempt.update({"after": after, "outcome": "verified" if after == target else "unverified"})
            write_json(state_path, state)
            if after != target:
                raise SystemExit(f"{key}/{outer_id}: inventory write was not verified; do not repeat the delta.")
    print(json.dumps({"batch_id": batch_id, "inventory_sync": "verified"}, ensure_ascii=False))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    for name in ("prepare", "upload-images", "submit-draft", "verify-draft", "submit", "verify", "plan-inventory", "sync-inventory"):
        command = sub.add_parser(name)
        command.add_argument("--manifest", required=True, type=Path)
        command.add_argument("--work-dir", required=True, type=Path)
        command.add_argument("--config", "--mcp-config", dest="mcp_config", type=Path)
        if name == "prepare":
            command.add_argument("--offline-render-dir", type=Path)
        elif name in {"upload-images", "submit", "submit-draft", "sync-inventory"}:
            command.add_argument("--expected-batch-id", required=True)
    return root


def main() -> None:
    args = parser().parse_args()
    {
        "prepare": prepare,
        "upload-images": upload_images,
        "submit-draft": submit_draft,
        "verify-draft": verify_draft,
        "submit": submit,
        "verify": verify,
        "plan-inventory": plan_inventory,
        "sync-inventory": sync_inventory,
    }[args.command](args)


if __name__ == "__main__":
    main()
