#!/usr/bin/env python3
"""Clone and mutate one same-category itemSchema without network access."""

from __future__ import annotations

import copy
from decimal import Decimal, InvalidOperation
import hashlib
import itertools
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


PROTECTED_SIMPLE_FIELDS = {
    "catId", "productId", "sku", "sampleSku", "saleProp", "icbuCatProp",
    "scImages", "detailImage", "imageVideo", "detailVideo", "ladderPrice",
    "ladderPeriod", "inventory", "superText",
}

DEFAULT_STOCK_TARGET = 99999
VALUE_TAGS = {"value", "values", "complex-value", "complex-values"}


def apply_default_inventory(product: dict) -> None:
    """Default omitted SKU inventory without overwriting explicit values."""
    defaults = product.setdefault("sku_defaults", {})
    if defaults.get("stock_target") is None:
        defaults["stock_target"] = DEFAULT_STOCK_TARGET
    for sku in product.get("skus") or []:
        if sku.get("stock_target") is None:
            sku["stock_target"] = DEFAULT_STOCK_TARGET


def top_field(root: ET.Element, field_id: str) -> ET.Element:
    field = next((node for node in root.findall("field") if node.get("id") == field_id), None)
    if field is None:
        raise ValueError(f"Source Render has no top-level field {field_id!r}.")
    return field


def data_node(field: ET.Element, tag: str) -> ET.Element:
    node = field.find(tag)
    if node is None:
        node = ET.Element(tag)
        insert_at = next((i for i, child in enumerate(field) if child.tag in {"fields", "rules", "options"}), len(field))
        field.insert(insert_at, node)
    return node


def replace_value(field: ET.Element, value: object) -> None:
    for node in list(field.findall("value")):
        field.remove(node)
    element = ET.Element("value")
    element.text = str(value)
    insert_at = next((i for i, child in enumerate(field) if child.tag in {"fields", "rules", "options"}), len(field))
    field.insert(insert_at, element)


def clear_values(field: ET.Element) -> int:
    removed = 0
    for value in list(field.findall("value")):
        field.remove(value)
        removed += 1
    return removed


def clear_complex_rows(field: ET.Element) -> int:
    removed = 0
    for row in list(field.findall("complex-values")):
        field.remove(row)
        removed += 1
    return removed


def clear_data_nodes(field: ET.Element) -> int:
    removed = 0
    for child in list(field):
        if child.tag in VALUE_TAGS:
            field.remove(child)
            removed += 1
    return removed


def replace_data_nodes(target: ET.Element, source: ET.Element) -> None:
    clear_data_nodes(target)
    insert_at = 0
    for child in source:
        if child.tag in VALUE_TAGS:
            target.insert(insert_at, copy.deepcopy(child))
            insert_at += 1


def rebase_on_current_schema(source_root: ET.Element, schema_xml: str) -> tuple[ET.Element, dict]:
    """Keep current definitions while carrying source business values forward."""
    current_root = ET.fromstring(schema_xml)
    if current_root.tag != "itemSchema":
        raise ValueError("Current Schema root is not itemSchema.")
    source_fields = {field.get("id"): field for field in source_root.findall("field")}
    current_fields = {field.get("id"): field for field in current_root.findall("field")}
    copied = []
    for field_id, target in current_fields.items():
        source = source_fields.get(field_id)
        if source is not None:
            replace_data_nodes(target, source)
            copied.append(field_id)

    removed_nested: dict[str, list[str]] = {}
    normalized_counts: dict[str, dict[str, int]] = {}
    for top_id in ("icbuCatProp", "saleProp", "sku", "sampleSku", "logisticsSku"):
        container = current_fields.get(top_id)
        if container is None:
            continue
        specs = {field.get("id"): field for field in container.findall("./fields/field")}
        removed = []
        for block in container.findall("complex-value") + container.findall("complex-values"):
            for field in list(block.findall("field")):
                field_id = field.get("id")
                spec = specs.get(field_id)
                if spec is None:
                    block.remove(field)
                    if field_id:
                        removed.append(field_id)
                    continue
                values = field.find("values")
                max_rule = next((
                    rule for rule in spec.findall("./rules/rule")
                    if rule.get("name") == "maxValueRule"
                ), None)
                if values is not None and max_rule is not None:
                    match = re.search(r"(\d+)", str(max_rule.get("value") or ""))
                    maximum = int(match.group(1)) if match else 0
                    rows = values.findall("value")
                    if maximum and len(rows) > maximum:
                        for value in rows[maximum:]:
                            values.remove(value)
                        normalized_counts[field_id or ""] = {
                            "before": len(rows),
                            "after": len(values.findall("value")),
                        }
        if removed:
            removed_nested[top_id] = sorted(set(removed))
    return current_root, {
        "schema_rebased": True,
        "copied_top_fields": copied,
        "removed_undefined_nested_fields": removed_nested,
        "normalized_schema_value_counts": normalized_counts,
        "source_only_top_fields": sorted(set(source_fields) - set(current_fields)),
    }


def company_image_urls(root: ET.Element) -> list[str]:
    """Return unique company-gallery URLs in source order."""
    field = top_field(root, "companyImage")
    urls: list[str] = []
    for value in field.findall(
        "./complex-values/field[@id='images']/complex-values/field[@id='imageURL']/value"
    ):
        url = str(value.text or "").strip()
        if url and url not in urls:
            urls.append(url)
    return urls


def normalize_company_images(root: ET.Element) -> int:
    """Convert persisted per-gallery rows to the add-compatible single gallery form."""
    field = top_field(root, "companyImage")
    urls = company_image_urls(root)
    if not urls:
        return 0
    clear_complex_rows(field)
    row = ET.Element("complex-values")
    images = ET.SubElement(row, "field", {"id": "images", "type": "multiComplex"})
    for url in urls:
        image_row = ET.SubElement(images, "complex-values")
        image_field = ET.SubElement(image_row, "field", {"id": "imageURL", "type": "input"})
        replace_value(image_field, url)
    gallery = ET.SubElement(row, "field", {"id": "gallery", "type": "singleCheck"})
    gallery_value = ET.Element("value", {"displayName": "Company overview"})
    gallery_value.text = "400"
    gallery.append(gallery_value)
    insert_at = next((i for i, child in enumerate(field) if child.tag == "fields"), len(field))
    field.insert(insert_at, row)
    return len(urls)


def definition(field: ET.Element, field_id: str) -> ET.Element | None:
    return next((node for node in field.findall("./fields/field") if node.get("id") == field_id), None)


def validate_option(definition_node: ET.Element, value_id: str, input_value: object | None) -> bool:
    if value_id.startswith("-"):
        return bool(str(input_value or "").strip())
    options = {str(option.get("value")) for option in definition_node.findall("./options/option")}
    return not options or value_id in options


def find_definition(container: ET.Element, key: str) -> ET.Element | None:
    folded = key.strip().casefold()
    return next((
        node for node in container.findall("./fields/field")
        if str(node.get("id") or "").casefold() == folded
        or str(node.get("name") or "").casefold() == folded
    ), None)


def resolve_named_value(spec: ET.Element, label: str, custom_id: int) -> tuple[str, str]:
    folded = label.strip().casefold()
    option = next((
        item for item in spec.findall("./options/option")
        if str(item.get("value") or "").casefold() == folded
        or str(item.get("displayName") or "").strip().casefold() == folded
    ), None)
    if option is not None:
        return str(option.get("value")), str(option.get("displayName") or label)
    return str(custom_id), label.strip()


def apply_attribute_overrides(root: ET.Element, overrides: dict, errors: list[str]) -> None:
    container = top_field(root, "icbuCatProp")
    block = data_node(container, "complex-value")
    for index, (key, raw_value) in enumerate(overrides.items(), start=1):
        spec = find_definition(container, str(key))
        if spec is None:
            errors.append(f"Unknown category attribute override: {key}")
            continue
        field_id = str(spec.get("id"))
        node = next((item for item in block.findall("field") if item.get("id") == field_id), None)
        if node is None:
            node = ET.SubElement(block, "field", {
                "id": field_id,
                "name": str(spec.get("name") or field_id),
                "type": str(spec.get("type") or "singleCheck"),
            })
        clear_values(node)
        for values_node in list(node.findall("values")):
            node.remove(values_node)
        labels = raw_value if isinstance(raw_value, list) else [raw_value]
        parent = ET.SubElement(node, "values") if node.get("type") == "multiCheck" else node
        for offset, label in enumerate(labels):
            value_id, display = resolve_named_value(spec, str(label), -1000 - index * 100 - offset)
            parent.append(make_value(value_id, display))


def omit_category_attributes(root: ET.Element, keys: list, errors: list[str]) -> list[str]:
    container = top_field(root, "icbuCatProp")
    block = data_node(container, "complex-value")
    omitted = []
    for key in keys:
        spec = find_definition(container, str(key))
        if spec is None:
            errors.append(f"Unknown category attribute to omit: {key}")
            continue
        field_id = str(spec.get("id"))
        node = next((item for item in block.findall("field") if item.get("id") == field_id), None)
        if node is not None:
            block.remove(node)
        omitted.append(field_id)
    return omitted


def materialize_variant_axes(root: ET.Element, product: dict, errors: list[str]) -> None:
    axes = product.get("variant_axes")
    if not axes:
        return
    container = top_field(root, "saleProp")
    resolved_axes = []
    for axis_index, axis in enumerate(axes, start=1):
        spec = find_definition(container, str(axis.get("field_id") or axis.get("name") or ""))
        if spec is None:
            errors.append(f"Unknown sale-property axis: {axis.get('name') or axis.get('field_id')}")
            continue
        values = []
        for value_index, label in enumerate(axis.get("values") or [], start=1):
            value_id, display = resolve_named_value(spec, str(label), -axis_index * 1000 - value_index)
            values.append({"value_id": value_id, "input_value": display})
        if not values:
            errors.append(f"Sale-property axis {spec.get('name') or spec.get('id')} has no values.")
        resolved_axes.append({
            "field_id": str(spec.get("id")),
            "prop_id": str(spec.get("id")).removeprefix("p-"),
            "name": str(spec.get("name") or spec.get("id")),
            "values": values,
        })
    if errors:
        return
    product["sale_properties"] = [
        {"field_id": axis["field_id"], "name": axis["name"], "values": axis["values"]}
        for axis in resolved_axes
    ]
    defaults = product.get("sku_defaults") or {}
    skus = []
    for combination in itertools.product(*(axis["values"] for axis in resolved_axes)):
        labels = [str(value["input_value"]) for value in combination]
        suffix = "-".join(
            re.sub(r"[^A-Za-z0-9]+", "-", label).strip("-").upper()[:18]
            for label in labels
        )
        props = []
        for axis, value in zip(resolved_axes, combination):
            props.append({
                "field_id": axis["field_id"],
                "prop_id": axis["prop_id"],
                "value_id": value["value_id"],
                "value_name": value["input_value"],
            })
        skus.append({
            "sku_outer_id": f"{product.get('item_key')}-{suffix}"[:64],
            "price": defaults.get("price"),
            "stock_target": defaults.get("stock_target"),
            "warehouse_code": defaults.get("warehouse_code"),
            "props": props,
        })
    product["skus"] = skus


def make_value(value_id: object, input_value: object | None = None, **attrs: object) -> ET.Element:
    value = ET.Element("value")
    value.text = str(value_id)
    if input_value is not None:
        value.set("inputValue", str(input_value))
    for key, item in attrs.items():
        if item is not None:
            value.set(key, str(item))
    return value


def set_attributes(root: ET.Element, items: list[dict], errors: list[str]) -> None:
    field = top_field(root, "icbuCatProp")
    block = data_node(field, "complex-value")
    block.clear()
    for item in items:
        field_id = str(item.get("field_id") or "")
        spec = definition(field, field_id)
        if spec is None:
            errors.append(f"Unknown category attribute field: {field_id}")
            continue
        node = ET.SubElement(block, "field", {
            "id": field_id,
            "name": str(item.get("name") or spec.get("name") or field_id),
            "type": str(item.get("type") or spec.get("type") or "singleCheck"),
        })
        entries = item.get("values") or [item]
        parent = ET.SubElement(node, "values") if len(entries) > 1 or node.get("type") == "multiCheck" else node
        seen: set[str] = set()
        for entry in entries:
            value_id = str(entry.get("value_id") or "")
            if value_id in seen:
                errors.append(f"Duplicate value {value_id} in category attribute {field_id}.")
            seen.add(value_id)
            if not validate_option(spec, value_id, entry.get("input_value")):
                errors.append(f"Invalid option {value_id} for category attribute {field_id}.")
            parent.append(make_value(value_id, entry.get("input_value")))


def set_sale_properties(root: ET.Element, items: list[dict], errors: list[str]) -> None:
    field = top_field(root, "saleProp")
    block = data_node(field, "complex-value")
    block.clear()
    for item in items:
        field_id = str(item.get("field_id") or "")
        spec = definition(field, field_id)
        if spec is None:
            errors.append(f"Unknown sale-property field: {field_id}")
            continue
        node = ET.SubElement(block, "field", {
            "id": field_id,
            "name": str(item.get("name") or spec.get("name") or field_id),
            "type": "multiCheck",
        })
        values = ET.SubElement(node, "values")
        seen: set[str] = set()
        for entry in item.get("values") or []:
            value_id = str(entry.get("value_id") or "")
            if value_id in seen:
                errors.append(f"Duplicate value {value_id} in sale property {field_id}.")
            seen.add(value_id)
            if not validate_option(spec, value_id, entry.get("input_value")):
                errors.append(f"Invalid option {value_id} for sale property {field_id}.")
            values.append(make_value(value_id, entry.get("input_value"), img=entry.get("image_url")))


def non_negative_integer(value: object, label: str, errors: list[str]) -> bool:
    try:
        number = Decimal(str(value))
        if number < 0 or number != number.to_integral_value():
            raise ValueError
    except (InvalidOperation, ValueError):
        errors.append(f"{label} must be a non-negative integer.")
        return False
    return True


def set_skus(
    root: ET.Element,
    items: list[dict],
    sale_properties: list[dict],
    inventory_mode: str,
    errors: list[str],
) -> None:
    field = top_field(root, "sku")
    source_rows = list(field.findall("complex-values"))
    row_template = copy.deepcopy(source_rows[0]) if source_rows else ET.Element("complex-values")
    for row in source_rows:
        field.remove(row)

    def template_field(row: ET.Element, field_id: str, field_type: str) -> ET.Element:
        node = next((child for child in row.findall("field") if child.get("id") == field_id), None)
        if node is not None:
            return node
        spec = definition(field, field_id)
        attrs = {"id": field_id, "type": field_type}
        if spec is not None and spec.get("name") is not None:
            attrs["name"] = str(spec.get("name"))
        return ET.SubElement(row, "field", attrs)

    dimensions = {str(item["field_id"]) for item in sale_properties}
    combinations: set[tuple[tuple[str, str], ...]] = set()
    outer_ids: list[str] = []
    for item in items:
        row = copy.deepcopy(row_template)
        price = template_field(row, "price", "input")
        clear_values(price)
        if item.get("price") is not None:
            replace_value(price, item["price"])
        stock = template_field(row, "skuStock", "multiInput")
        clear_values(stock)
        for values_node in list(stock.findall("values")):
            stock.remove(values_node)
        stock_target = item.get("stock_target")
        warehouse_code = str(item.get("warehouse_code") or "").strip()
        stock_valid = stock_target is not None and non_negative_integer(
            stock_target, f"SKU {item.get('sku_outer_id') or '<missing>'} stock_target", errors
        )
        if stock_target is not None and not warehouse_code:
            errors.append(f"SKU {item.get('sku_outer_id') or '<missing>'} warehouse_code is required.")
        if inventory_mode == "embedded":
            if stock_target is None:
                errors.append(
                    f"SKU {item.get('sku_outer_id') or '<missing>'} requires stock_target in embedded inventory mode."
                )
            elif warehouse_code and stock_valid:
                stock_values = ET.SubElement(stock, "values")
                stock_values.append(make_value(
                    stock_target,
                    warehouseCode=warehouse_code,
                    srcValue=stock_target,
                ))
        outer = template_field(row, "skuOuterId", "input")
        clear_values(outer)
        outer_id = str(item.get("sku_outer_id") or "")
        outer_ids.append(outer_id)
        if outer_id:
            replace_value(outer, outer_id)
        sku_id = template_field(row, "skuId", "input")
        clear_values(sku_id)
        outer_supply = template_field(row, "outerSupplyId", "input")
        clear_values(outer_supply)
        props = template_field(row, "props", "multiInput")
        clear_values(props)
        for values_node in list(props.findall("values")):
            props.remove(values_node)
        values = ET.SubElement(props, "values")
        pairs: list[tuple[str, str]] = []
        for prop in item.get("props") or []:
            field_id, value_id = str(prop.get("field_id") or ""), str(prop.get("value_id") or "")
            pairs.append((field_id, value_id))
            values.append(make_value(
                f"{prop.get('prop_id')}:{value_id}",
                propName=field_id,
                propId=prop.get("prop_id"),
                propValueId=value_id,
                propValueName=prop.get("value_name"),
            ))
        combination = tuple(sorted(pairs))
        if {key for key, _ in pairs} != dimensions or len(pairs) != len(dimensions):
            errors.append("Every SKU must contain exactly one value for every sale-property dimension.")
        if combination in combinations:
            errors.append("Duplicate SKU property combination.")
        combinations.add(combination)
        insert_at = next((i for i, child in enumerate(field) if child.tag == "fields"), len(field))
        field.insert(insert_at, row)
    populated = [value for value in outer_ids if value]
    if len(items) > 1 and len(populated) != len(items):
        errors.append("Every multi-SKU row requires sku_outer_id.")
    if len(populated) != len(set(populated)):
        errors.append("sku_outer_id values must be unique.")


def set_main_images(root: ET.Element, images: list[dict]) -> None:
    field = top_field(root, "scImages")
    block = data_node(field, "complex-value")
    block.clear()
    for index, image in enumerate(images):
        node = ET.SubElement(block, "field", {"id": f"scImages_{index}", "type": "input"})
        value = make_value(image["url"], fileFlag="no", fileId=image["file_id"])
        node.append(value)


def set_detail_galleries(root: ET.Element, galleries: list[dict]) -> None:
    field = top_field(root, "detailImage")
    for block in list(field.findall("complex-values")):
        field.remove(block)
    for gallery in galleries:
        block = ET.Element("complex-values")
        images_field = ET.SubElement(block, "field", {"id": "images", "type": "multiComplex"})
        for image in gallery["images"]:
            image_block = ET.SubElement(images_field, "complex-values")
            image_field = ET.SubElement(image_block, "field", {"id": "imageURL", "type": "input"})
            replace_value(image_field, image["url"])
        gallery_field = ET.SubElement(block, "field", {"id": "gallery", "type": "singleCheck"})
        value = make_value(gallery["gallery"])
        if gallery.get("display_name"):
            value.set("displayName", str(gallery["display_name"]))
        gallery_field.append(value)
        insert_at = next((i for i, child in enumerate(field) if child.tag == "fields"), len(field))
        field.insert(insert_at, block)


def set_tiers(root: ET.Element, field_id: str, prefix: str, items: list[dict], second_key: str) -> None:
    field = top_field(root, field_id)
    block = data_node(field, "complex-value")
    block.clear()
    for index, item in enumerate(items):
        tier = ET.SubElement(block, "field", {"id": f"{prefix}_{index}", "type": "complex"})
        content = ET.SubElement(tier, "complex-value")
        quantity = ET.SubElement(content, "field", {"id": "quantity", "type": "input"})
        replace_value(quantity, item["quantity"])
        other = ET.SubElement(content, "field", {"id": second_key, "type": "input"})
        replace_value(other, item[second_key])


def set_custom_properties(root: ET.Element, properties: dict, errors: list[str]) -> None:
    field = top_field(root, "customMoreProperty")
    definitions = field.findall("./fields/field")
    if len(properties) > len(definitions):
        errors.append(
            f"custom_properties has {len(properties)} entries but Schema permits {len(definitions)}."
        )
        return
    for node in list(field.findall("complex-value")):
        field.remove(node)
    if not properties:
        return
    block = ET.Element("complex-value")
    for index, (name, value) in enumerate(properties.items()):
        if not str(name).strip() or not str(value).strip():
            errors.append("custom_properties names and values must be non-empty.")
            continue
        if len(str(value).strip()) > 70:
            errors.append(f"custom_properties value for {name!r} exceeds 70 characters.")
            continue
        item = ET.SubElement(block, "field", {
            "id": f"customMoreProperty_{index}",
            "type": "complex",
        })
        content = ET.SubElement(item, "complex-value")
        name_field = ET.SubElement(content, "field", {"id": "propName", "type": "input"})
        replace_value(name_field, str(name).strip())
        value_field = ET.SubElement(content, "field", {"id": "valueName", "type": "input"})
        replace_value(value_field, str(value).strip())
    insert_at = next((i for i, child in enumerate(field) if child.tag == "fields"), len(field))
    field.insert(insert_at, block)


def positive_decimal(value: object, label: str, errors: list[str], allow_zero: bool = False) -> None:
    try:
        number = Decimal(str(value))
        if number < 0 or (not allow_zero and number == 0):
            raise ValueError
    except (InvalidOperation, ValueError):
        errors.append(f"{label} must be {'non-negative' if allow_zero else 'positive'}.")


def build(source_xml: str, product: dict, current_schema_xml: str | None = None) -> tuple[str, dict]:
    product = copy.deepcopy(product)
    apply_default_inventory(product)
    source_root = ET.fromstring(source_xml)
    if source_root.tag != "itemSchema":
        raise ValueError("Source Render root is not itemSchema.")
    source_company_urls = company_image_urls(source_root)
    root = source_root
    schema_report = {"schema_rebased": False}
    if current_schema_xml:
        root, schema_report = rebase_on_current_schema(root, current_schema_xml)
    original_root = copy.deepcopy(root)
    errors: list[str] = []
    normalized_company_image_count = normalize_company_images(root)
    normalized_company_urls = company_image_urls(root)
    if normalized_company_urls != source_company_urls:
        missing = [url for url in source_company_urls if url not in normalized_company_urls]
        extra = [url for url in normalized_company_urls if url not in source_company_urls]
        errors.append(
            "Company gallery changed while cloning: "
            f"source={len(source_company_urls)}, output={len(normalized_company_urls)}, "
            f"missing={len(missing)}, extra={len(extra)}."
        )
    if root.find("./field[@id='designAndSampleService']") is not None:
        clear_data_nodes(top_field(root, "designAndSampleService"))
    source_persisted_sku_ids = sum(
        1 for node in root.findall(".//field[@id='skuId']/value") if str(node.text or "").strip()
    )
    omitted_category_attributes = omit_category_attributes(
        root, product.get("omit_category_attributes") or [], errors
    )
    omitted_top_level_fields: list[str] = []
    for field_id in product.get("omit_top_level_fields") or []:
        field_id = str(field_id)
        node = next((field for field in root.findall("field") if field.get("id") == field_id), None)
        if node is None:
            errors.append(f"Cannot omit missing top-level field {field_id}.")
            continue
        root.remove(node)
        omitted_top_level_fields.append(field_id)
    source_category = top_field(root, "catId").findtext("value")
    if str(source_category) != str(product.get("category_id")):
        errors.append(f"Source category {source_category} differs from requested {product.get('category_id')}.")
    title = str(product.get("title") or "").strip()
    if not title:
        errors.append("Title is required.")
    if len(title.encode("utf-8")) > 128:
        errors.append("Title exceeds 128 UTF-8 bytes; confirm the current Schema limit.")
    replace_value(top_field(root, "productTitle"), title)

    main_images = product.get("main_images") or []
    if not 1 <= len(main_images) <= 6:
        errors.append("Main image count must be 1–6.")
    main_images_ready = True
    for image in main_images:
        if not str(image.get("file_id") or "").strip("0") or "alicdn.com/" not in str(image.get("url") or ""):
            errors.append("Every main image requires a nonzero file_id and Alibaba CDN URL.")
            main_images_ready = False
    if main_images and main_images_ready:
        set_main_images(root, main_images)

    galleries = product.get("detail_galleries")
    if galleries is not None:
        if not galleries or any(not gallery.get("images") for gallery in galleries):
            errors.append("detail_galleries must contain at least one non-empty gallery.")
        elif any(
            "alicdn.com/" not in str(image.get("url") or "")
            for gallery in galleries
            for image in gallery.get("images") or []
        ):
            errors.append("Every detail image requires an Alibaba CDN URL.")
        else:
            set_detail_galleries(root, galleries)

    if "category_attributes" in product:
        if not product["category_attributes"]:
            errors.append("category_attributes cannot be empty.")
        else:
            set_attributes(root, product["category_attributes"], errors)
    if product.get("category_attribute_overrides"):
        apply_attribute_overrides(root, product["category_attribute_overrides"], errors)
    materialize_variant_axes(root, product, errors)
    if "sale_properties" in product:
        if not product["sale_properties"]:
            errors.append("sale_properties cannot be empty.")
        else:
            set_sale_properties(root, product["sale_properties"], errors)
    inventory_mode = str(product.get("inventory_mode") or "deferred")
    if inventory_mode not in {"deferred", "embedded"}:
        errors.append("inventory_mode must be either 'deferred' or 'embedded'.")
    if "skus" in product:
        if not product["skus"]:
            errors.append("skus cannot be empty.")
        else:
            set_skus(
                root,
                product["skus"],
                product.get("sale_properties") or [],
                inventory_mode,
                errors,
            )
            # These rows are tied to the source product's SKU IDs and props. Preserve
            # their Schema definitions but never inherit their persisted data.
            clear_complex_rows(top_field(root, "sampleSku"))
            clear_complex_rows(top_field(root, "logisticsSku"))
    embedded_inventory_count = sum(
        1
        for value in root.findall(".//field[@id='skuStock']/values/value")
        if value.get("warehouseCode") and value.get("srcValue") is not None
    )
    if inventory_mode == "embedded" and embedded_inventory_count != len(product.get("skus") or []):
        errors.append("Embedded skuStock row count must equal SKU row count.")

    tiers = product.get("price_tiers") or []
    if not tiers:
        errors.append("price_tiers is required.")
    else:
        quantities = [int(item["quantity"]) for item in tiers]
        if quantities != sorted(set(quantities)):
            errors.append("Price-tier quantities must be strictly increasing.")
        for item in tiers:
            positive_decimal(item["quantity"], "Price-tier quantity", errors)
            positive_decimal(item["price"], "Price-tier price", errors)
        set_tiers(root, "ladderPrice", "ladderPrice", tiers, "price")

    lead_times = product.get("lead_times") or []
    if not lead_times:
        errors.append("lead_times is required.")
    else:
        for item in lead_times:
            positive_decimal(item["quantity"], "Lead-time quantity", errors)
            positive_decimal(item["day"], "Lead-time day", errors)
        set_tiers(root, "ladderPeriod", "ladderPeriod", lead_times, "day")

    positive_decimal(product.get("moq"), "MOQ", errors)
    if product.get("moq") is not None:
        replace_value(top_field(root, "minOrderQuantity"), product["moq"])

    package = product.get("package") or {}
    for key in ("length", "width", "height", "weight"):
        positive_decimal(package.get(key), f"Package {key}", errors)
    if package:
        measure = data_node(top_field(root, "pkgMeasure"), "complex-value")
        for key in ("length", "width", "height"):
            node = next((node for node in measure.findall("field") if node.get("id") == key), None)
            if node is None:
                node = ET.SubElement(measure, "field", {"id": key, "type": "input"})
            replace_value(node, package[key])
        replace_value(top_field(root, "pkgWeight"), package["weight"])

    if "custom_properties" in product:
        set_custom_properties(root, product.get("custom_properties") or {}, errors)

    for field_id, value in (product.get("simple_fields") or {}).items():
        if field_id in PROTECTED_SIMPLE_FIELDS:
            errors.append(f"simple_fields cannot modify protected field {field_id}.")
            continue
        field = top_field(root, field_id)
        if field.get("type") not in {"input", "singleCheck"}:
            errors.append(f"simple_fields only supports scalar fields; {field_id} is {field.get('type')}.")
            continue
        replace_value(field, value)

    desc_type = top_field(root, "productDescType").findtext("value")
    if "super_text" in product:
        replace_value(top_field(root, "superText"), product["super_text"])
    elif galleries is not None and str(desc_type) == "2":
        errors.append("Legacy productDescType=2 requires explicit super_text when detail images change.")

    cleared_sku_ids = 0
    for sku_id in root.findall(".//field[@id='skuId']"):
        cleared_sku_ids += clear_values(sku_id)
    cleared_video_values = clear_values(top_field(root, "imageVideo")) + clear_values(top_field(root, "detailVideo"))
    clear_values(top_field(root, "inventory"))
    for identity_id in ("productId", "id"):
        for identity in root.findall(f".//field[@id='{identity_id}']"):
            clear_values(identity)
    if root.find(".//field[@id='skuId']/value") is not None:
        errors.append("Persisted skuId remains.")

    xml = ET.tostring(root, encoding="unicode")
    sha256 = hashlib.sha256(xml.encode("utf-8")).hexdigest()
    original_fields = {field.get("id"): ET.tostring(field, encoding="unicode") for field in original_root.findall("field")}
    current_fields = {field.get("id"): ET.tostring(field, encoding="unicode") for field in root.findall("field")}
    changed_top_level_fields = sorted(
        field_id for field_id in set(original_fields) | set(current_fields)
        if original_fields.get(field_id) != current_fields.get(field_id)
    )
    report = {
        "item_key": product.get("item_key"),
        "source_product_id": str(product.get("source_product_id")),
        "category_id": product.get("category_id"),
        "title": title,
        "main_image_count": len(main_images),
        "detail_image_count": sum(len(item.get("images") or []) for item in (galleries or [])),
        "category_attribute_count": len(top_field(root, "icbuCatProp").findall("./complex-value/field")),
        "category_attribute_override_count": len(product.get("category_attribute_overrides") or {}),
        "omitted_category_attributes": omitted_category_attributes,
        "sale_property_count": len(product.get("sale_properties") or []),
        "sku_count": len(product.get("skus") or []),
        "price_tier_count": len(tiers),
        "custom_property_count": len(product.get("custom_properties") or {}),
        "moq": product.get("moq"),
        "lead_time_count": len(lead_times),
        "inventory_mode": inventory_mode,
        "embedded_inventory_count": embedded_inventory_count,
        "inventory_targets": [
            {
                "sku_outer_id": sku.get("sku_outer_id"),
                "target": sku.get("stock_target"),
                "warehouse_code": sku.get("warehouse_code"),
            }
            for sku in product.get("skus") or []
            if sku.get("stock_target") is not None
        ],
        "source_persisted_sku_ids": source_persisted_sku_ids,
        "cleared_sku_ids": source_persisted_sku_ids,
        "post_build_sku_id_values_removed": cleared_sku_ids,
        "cleared_video_values": cleared_video_values,
        "omitted_top_level_fields": omitted_top_level_fields,
        "schema_compatibility": schema_report,
        "normalized_company_image_count": normalized_company_image_count,
        "source_company_image_count": len(source_company_urls),
        "company_images_exact_match": normalized_company_urls == source_company_urls,
        "changed_top_level_fields": changed_top_level_fields,
        "xml_sha256": sha256,
        "errors": errors,
        "ready_for_submission": not errors,
    }
    return xml, report


def extract_render_xml(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig")
    if text.lstrip().startswith("<itemSchema"):
        return text
    response = json.loads(text)
    payload = next((value for key, value in response.items() if key.startswith("alibaba_")), {})
    if not payload.get("biz_success") or not payload.get("data"):
        raise ValueError(f"Render response is not successful: {path}")
    return str(payload["data"])
