#!/usr/bin/env python3
"""Offline regression checks for the clone engine."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from clone_engine import build, company_image_urls, extract_render_xml, normalize_company_images, top_field
from batch_tool import product_rows, scan_local_images, selected_comparison
from intake_tool import compile_manifest


SOURCE_XML: str = ""


def sample_product() -> dict:
    return {
        "item_key": "TEST-001",
        "source_product_id": "1601877747613",
        "category_id": 201273078,
        "title": "Offline Test Product Unique Title",
        "inventory_mode": "embedded",
        "main_images": [
            {"file_id": "123456", "url": "https://sc04.alicdn.com/kf/Htest1.jpg"},
            {"file_id": "123457", "url": "https://sc04.alicdn.com/kf/Htest2.jpg"},
        ],
        "detail_galleries": [
            {"gallery": "-2", "display_name": "product", "images": [
                {"url": "https://sc04.alicdn.com/kf/Htest1.jpg"},
                {"url": "https://sc04.alicdn.com/kf/Htest2.jpg"},
            ]}
        ],
        "super_text": "<div><img src=\"https://sc04.alicdn.com/kf/Htest1.jpg\"></div>",
        "category_attributes": [
            {"field_id": "p-211046063", "type": "multiCheck", "value_id": "104451652", "input_value": "PP"}
        ],
        "sale_properties": [
            {"field_id": "p-200001168", "name": "Color", "values": [
                {"value_id": "-1", "input_value": "Natural"}
            ]},
        ],
        "skus": [
            {
                "sku_outer_id": "TEST-NATURAL-STD",
                "price": "10.50",
                "stock_target": 50,
                "warehouse_code": "CN_LOCAL_01",
                "props": [
                    {"field_id": "p-200001168", "prop_id": "200001168", "value_id": "-1", "value_name": "Natural"},
                ],
            }
        ],
        "price_tiers": [{"quantity": 10, "price": "10.50"}, {"quantity": 100, "price": "9.50"}],
        "moq": 10,
        "lead_times": [{"quantity": 10, "day": 15}],
        "package": {"length": 40, "width": 15, "height": 15, "weight": 1.5},
        "simple_fields": {"priceUnit": "4", "saleType": "normal", "scPrice": "1"},
    }


class CloneEngineTests(unittest.TestCase):
    def test_company_gallery_preserves_every_source_image_in_order(self):
        source_root = ET.fromstring(SOURCE_XML)
        source_urls = company_image_urls(source_root)
        xml, report = build(SOURCE_XML, sample_product())
        output_urls = company_image_urls(ET.fromstring(xml))
        self.assertTrue(report["ready_for_submission"], report["errors"])
        self.assertGreater(len(source_urls), 0)
        self.assertEqual(output_urls, source_urls)
        self.assertEqual(report["source_company_image_count"], len(source_urls))
        self.assertTrue(report["company_images_exact_match"])

    def test_company_gallery_loss_blocks_submission(self):
        def lossy_normalizer(root: ET.Element) -> int:
            count = normalize_company_images(root)
            rows = root.findall(
                "./field[@id='companyImage']/complex-values/field[@id='images']/complex-values"
            )
            if rows:
                parent = root.find("./field[@id='companyImage']/complex-values/field[@id='images']")
                parent.remove(rows[-1])
            return count - 1

        with patch("clone_engine.normalize_company_images", side_effect=lossy_normalizer):
            _, report = build(SOURCE_XML, sample_product())
        self.assertFalse(report["ready_for_submission"])
        self.assertFalse(report["company_images_exact_match"])
        self.assertTrue(any("Company gallery changed" in error for error in report["errors"]))

    def test_full_clone_is_ready_and_clears_identity(self):
        xml, report = build(SOURCE_XML, sample_product())
        self.assertTrue(report["ready_for_submission"], report["errors"])
        self.assertEqual(report["sku_count"], 1)
        self.assertEqual(report["inventory_targets"][0]["target"], 50)
        self.assertEqual(report["inventory_mode"], "embedded")
        self.assertEqual(report["embedded_inventory_count"], 1)
        root = ET.fromstring(xml)
        self.assertEqual(top_field(root, "productTitle").findtext("value"), "Offline Test Product Unique Title")
        self.assertIsNone(root.find(".//field[@id='skuId']/value"))
        self.assertIsNone(top_field(root, "imageVideo").find("value"))
        self.assertIsNone(top_field(root, "detailVideo").find("value"))
        stock = root.find(".//field[@id='skuStock']/values/value")
        self.assertIsNotNone(stock)
        self.assertEqual(stock.text, "50")
        self.assertEqual(stock.get("warehouseCode"), "CN_LOCAL_01")
        self.assertEqual(stock.get("srcValue"), "50")
        sku_row = top_field(root, "sku").find("complex-values")
        row_field_ids = [field.get("id") for field in sku_row.findall("field")]
        self.assertIn("outerSupplyId", row_field_ids)
        self.assertEqual(
            sku_row.find("field[@id='skuStock']").get("name"),
            "Inventory",
        )

    def test_current_schema_rebase_drops_source_only_fields(self):
        source_root = ET.fromstring(SOURCE_XML)
        legacy = ET.SubElement(source_root, "field", {"id": "legacyOnly", "type": "input"})
        ET.SubElement(legacy, "value").text = "legacy"
        cat_values = top_field(source_root, "icbuCatProp").find("complex-value")
        extra = ET.SubElement(cat_values, "field", {"id": "p-legacy", "type": "input"})
        ET.SubElement(extra, "value").text = "legacy"
        xml, report = build(
            ET.tostring(source_root, encoding="unicode"),
            sample_product(),
            current_schema_xml=SOURCE_XML,
        )
        self.assertTrue(report["ready_for_submission"], report["errors"])
        compatibility = report["schema_compatibility"]
        self.assertTrue(compatibility["schema_rebased"])
        self.assertIn("legacyOnly", compatibility["source_only_top_fields"])
        self.assertIn("p-legacy", compatibility["removed_undefined_nested_fields"]["icbuCatProp"])
        self.assertIsNone(ET.fromstring(xml).find("./field[@id='legacyOnly']"))

    def test_deferred_inventory_does_not_enter_publish_xml(self):
        product = sample_product()
        product["inventory_mode"] = "deferred"
        xml, report = build(SOURCE_XML, product)
        self.assertTrue(report["ready_for_submission"], report["errors"])
        self.assertEqual(report["embedded_inventory_count"], 0)
        root = ET.fromstring(xml)
        self.assertIsNone(root.find(".//field[@id='skuStock']/values/value"))

    def test_embedded_inventory_requires_warehouse(self):
        product = sample_product()
        product["skus"][0]["warehouse_code"] = ""
        _, report = build(SOURCE_XML, product)
        self.assertFalse(report["ready_for_submission"])
        self.assertTrue(any("warehouse_code is required" in error for error in report["errors"]))

    def test_embedded_inventory_rejects_negative_target(self):
        product = sample_product()
        product["skus"][0]["stock_target"] = -1
        _, report = build(SOURCE_XML, product)
        self.assertFalse(report["ready_for_submission"])
        self.assertTrue(any("stock_target must be a non-negative integer" in error for error in report["errors"]))

    def test_protected_simple_field_blocks(self):
        product = sample_product()
        product["simple_fields"]["catId"] = "999"
        _, report = build(SOURCE_XML, product)
        self.assertFalse(report["ready_for_submission"])
        self.assertTrue(any("protected field catId" in error for error in report["errors"]))

    def test_duplicate_sku_combination_blocks(self):
        product = sample_product()
        second = json.loads(json.dumps(product["skus"][0]))
        second["sku_outer_id"] = "TEST-SECOND"
        product["skus"].append(second)
        _, report = build(SOURCE_XML, product)
        self.assertTrue(any("Duplicate SKU property combination" in error for error in report["errors"]))

    def test_local_images_are_deduplicated_by_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "same.jpg"
            image.write_bytes(b"offline-image-bytes")
            manifest_path = root / "batch.json"
            manifest = {
                "batch_id": "image-test",
                "products": [{
                    "item_key": "P1",
                    "main_images": [{"local_path": "same.jpg"}],
                    "detail_galleries": [{"images": [{"local_path": "same.jpg"}]}],
                }],
            }
            identities = scan_local_images(manifest, manifest_path)
            self.assertEqual(len(identities), 1)
            only = next(iter(identities.values()))
            self.assertEqual(len(only["uses"]), 2)
            self.assertEqual(only["size"], len(b"offline-image-bytes"))

    def test_product_list_prefers_usable_numeric_id(self):
        response = {
            "alibaba_icbu_product_list_response": {
                "products": {
                    "alibaba_product_brief_response": [{
                        "id": 1601885016838,
                        "product_id": "encrypted-id",
                        "subject": "Exact title",
                        "status": "approved",
                    }]
                }
            }
        }
        self.assertEqual(product_rows(response), [{
            "product_id": "1601885016838",
            "title": "Exact title",
            "status": "approved",
        }])

    def test_explicit_create_only_field_omission(self):
        product = sample_product()
        product["omit_top_level_fields"] = ["designAndSampleService"]
        xml, report = build(SOURCE_XML, product)
        self.assertTrue(report["ready_for_submission"], report["errors"])
        self.assertEqual(report["omitted_top_level_fields"], ["designAndSampleService"])
        root = ET.fromstring(xml)
        self.assertIsNone(root.find("./field[@id='designAndSampleService']"))

    def test_human_readable_variants_resolve_schema_options(self):
        product = sample_product()
        product.pop("category_attributes")
        product.pop("sale_properties")
        product.pop("skus")
        product["category_attribute_overrides"] = {"Material": "PP"}
        product["variant_axes"] = [{"name": "Color", "values": ["Gray", "Red"]}]
        product["sku_defaults"] = {"stock_target": 99999, "warehouse_code": "CN_LOCAL_01"}
        xml, report = build(SOURCE_XML, product)
        self.assertTrue(report["ready_for_submission"], report["errors"])
        self.assertEqual(report["sku_count"], 2)
        root = ET.fromstring(xml)
        values = root.findall("./field[@id='saleProp']/complex-value/field[@id='p-200001168']/values/value")
        self.assertEqual([value.text for value in values], ["5412614", "3331260"])
        self.assertEqual(len(root.findall("./field[@id='sku']/complex-values")), 2)

    def test_missing_inventory_defaults_to_99999(self):
        product = sample_product()
        product["skus"][0].pop("stock_target")
        xml, report = build(SOURCE_XML, product)
        self.assertTrue(report["ready_for_submission"], report["errors"])
        self.assertEqual(report["inventory_targets"][0]["target"], 99999)
        stock = ET.fromstring(xml).find(
            "./field[@id='sku']/complex-values/field[@id='skuStock']/values/value"
        )
        self.assertEqual(stock.text, "99999")
        self.assertEqual(stock.get("srcValue"), "99999")

    def test_explicit_zero_inventory_is_preserved(self):
        product = sample_product()
        product["skus"][0]["stock_target"] = 0
        xml, report = build(SOURCE_XML, product)
        self.assertTrue(report["ready_for_submission"], report["errors"])
        self.assertEqual(report["inventory_targets"][0]["target"], 0)
        stock = ET.fromstring(xml).find(
            "./field[@id='sku']/complex-values/field[@id='skuStock']/values/value"
        )
        self.assertEqual(stock.text, "0")

    def test_markdown_intake_compiles_without_category_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "batch.md"
            path.write_text(
                "batch_id: md-test\n"
                "source_product_id: 1601877747613\n"
                "images_dir: images\n"
                "shared_images: 4.jpg;5.jpg\n"
                "variants: Gray;Red\n"
                "price_tiers: 10=12.00;100=11.50\n"
                "moq: 10\nlead_time_days: 15\nstock: 99999\n"
                "pkg_length: 80\npkg_width: 31\npkg_height: 11\npkg_weight: 4\n\n"
                "| item_key | title | hero_image |\n"
                "|---|---|---|\n"
                "| P-A | Unique title A | 1.jpg |\n",
                encoding="utf-8",
            )
            manifest = compile_manifest(path)
            self.assertEqual(manifest["batch_id"], "md-test")
            self.assertNotIn("category_id", manifest["products"][0])
            self.assertEqual(len(manifest["products"][0]["main_images"]), 3)
            self.assertEqual(manifest["products"][0]["variant_axes"][0]["values"], ["Gray", "Red"])

    def test_csv_intake_uses_row_batch_id(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "batch.csv"
            path.write_text(
                "batch_id,source_product_id,images_dir,shared_images,variants,price_tiers,"
                "moq,lead_time_days,stock,pkg_length,pkg_width,pkg_height,pkg_weight,item_key,title,hero_image\n"
                "csv-test,1601877747613,images,4.jpg;5.jpg,Gray;Red,10=12.00,"
                "10,15,99999,80,31,11,4,P-A,Unique title A,1.jpg\n",
                encoding="utf-8",
            )
            manifest = compile_manifest(path)
            self.assertEqual(manifest["batch_id"], "csv-test")
            self.assertEqual(manifest["products"][0]["source_product_id"], "1601877747613")

    def test_semantic_comparison_ignores_platform_order_and_decimal_format(self):
        xml, report = build(SOURCE_XML, sample_product())
        self.assertTrue(report["ready_for_submission"], report["errors"])
        root = ET.fromstring(xml)
        replace = root.find("./field[@id='minOrderQuantity']/value")
        replace.text = "10.0"
        tiers = root.find("./field[@id='ladderPrice']/complex-value")
        tiers[:] = list(reversed(list(tiers)))
        sale_values = root.find("./field[@id='saleProp']/complex-value/field/values")
        sale_values[:] = list(reversed(list(sale_values)))
        normalized = ET.tostring(root, encoding="unicode")
        self.assertEqual(selected_comparison(xml), selected_comparison(normalized))

    def test_semantic_comparison_accepts_platform_image_and_custom_id_normalization(self):
        xml, report = build(SOURCE_XML, sample_product())
        self.assertTrue(report["ready_for_submission"], report["errors"])
        root = ET.fromstring(xml)
        image = root.find("./field[@id='scImages']/complex-value/field/value")
        token = "H0123456789abcdef0123456789abcdef"
        image.text = f"https://sc04.alicdn.com/kf/{token}/folder/{token}.jpg"
        normalized_root = ET.fromstring(ET.tostring(root, encoding="unicode"))
        normalized_image = normalized_root.find("./field[@id='scImages']/complex-value/field/value")
        normalized_image.text = f"//sc04.alicdn.com/kf/{token}.jpg_350x350.jpg"
        normalized_image.set("fileId", "0")
        brand = root.find("./field[@id='icbuCatProp']/complex-value/field[@id='p-2']/value")
        normalized_brand = normalized_root.find("./field[@id='icbuCatProp']/complex-value/field[@id='p-2']/value")
        if brand is not None and normalized_brand is not None:
            brand.text = "-1100"
            normalized_brand.text = "-3"
        self.assertEqual(
            selected_comparison(ET.tostring(root, encoding="unicode")),
            selected_comparison(ET.tostring(normalized_root, encoding="unicode")),
        )

    def test_optional_inherited_category_attribute_can_be_omitted_by_name(self):
        product = sample_product()
        product["omit_category_attributes"] = ["Model Number"]
        xml, report = build(SOURCE_XML, product)
        self.assertTrue(report["ready_for_submission"], report["errors"])
        self.assertIn("p-3", report["omitted_category_attributes"])
        root = ET.fromstring(xml)
        self.assertIsNone(root.find("./field[@id='icbuCatProp']/complex-value/field[@id='p-3']"))

    def test_custom_properties_replace_general_specs(self):
        product = sample_product()
        product["custom_properties"] = {
            "Height": "10 / 15 / 20 cm",
            "Product Size": "78 x 29 x 10/15/20 cm",
        }
        xml, report = build(SOURCE_XML, product)
        self.assertTrue(report["ready_for_submission"], report["errors"])
        self.assertEqual(report["custom_property_count"], 2)
        root = ET.fromstring(xml)
        block = root.find("./field[@id='customMoreProperty']/complex-value")
        pairs = {
            item.findtext("complex-value/field[@id='propName']/value"):
            item.findtext("complex-value/field[@id='valueName']/value")
            for item in block.findall("field")
        }
        self.assertEqual(pairs["Height"], "10 / 15 / 20 cm")
        self.assertEqual(pairs["Product Size"], "78 x 29 x 10/15/20 cm")

    def test_custom_property_value_over_70_characters_is_blocked(self):
        product = sample_product()
        product["custom_properties"] = {"Color Options": "x" * 71}
        _, report = build(SOURCE_XML, product)
        self.assertFalse(report["ready_for_submission"])
        self.assertTrue(any("exceeds 70 characters" in error for error in report["errors"]))


def main() -> None:
    global SOURCE_XML
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-render", required=True, type=Path)
    args = parser.parse_args()
    SOURCE_XML = extract_render_xml(args.source_render)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CloneEngineTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
