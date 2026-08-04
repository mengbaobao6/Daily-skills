# Batch input contract

Non-technical users may fill `simple-intake-template.md` instead. The compiler
supports Markdown tables and CSV, permits a missing category ID, accepts
category overrides by field name, and builds SKU combinations from
human-readable variant names.

Save UTF-8 JSON with one batch and one or more products:

```json
{
  "batch_id": "fitness-20260723-a",
  "products": [
    {
      "item_key": "P001",
      "source_product_id": "1601686906915",
      "category_id": 202111401,
      "title": "New unique English title",
      "inventory_mode": "embedded",
      "main_images": [
        {"local_path": "images/main-01.jpg"}
      ],
      "detail_galleries": [
        {
          "gallery": "-2",
          "display_name": "product",
          "images": [{"local_path": "images/detail-01.jpg"}]
        }
      ],
      "category_attributes": [
        {"field_id": "p-191284014", "type": "singleCheck", "value_id": "-2", "input_value": "Cork"}
      ],
      "sale_properties": [
        {
          "field_id": "p-200001168",
          "name": "Color",
          "values": [{"value_id": "-1", "input_value": "Natural", "image_url": "https://sc04.alicdn.com/kf/example.jpg"}]
        }
      ],
      "skus": [
        {
          "sku_outer_id": "P001-NATURAL",
          "price": "10.50",
          "stock_target": 999,
          "warehouse_code": "CN_LOCAL_01",
          "props": [
            {"field_id": "p-200001168", "prop_id": "200001168", "value_id": "-1", "value_name": "Natural"}
          ]
        }
      ],
      "price_tiers": [
        {"quantity": 10, "price": "10.50"},
        {"quantity": 100, "price": "9.50"}
      ],
      "moq": 10,
      "lead_times": [{"quantity": 10, "day": 15}],
      "package": {"length": 40, "width": 15, "height": 15, "weight": 1.5},
      "simple_fields": {
        "priceUnit": "4",
        "saleType": "normal",
        "scPrice": "1"
      },
      "super_text": "<div>Complete replacement legacy detail HTML when productDescType is 2</div>",
      "duplicate_decision": {
        "allow_similar_listing": false,
        "reason": ""
      }
    }
  ]
}
```

## Rules

- `batch_id` and every `item_key` must be unique and stable.
- `category_id` must equal the source Render `catId`.
- Use unique English titles. Exact-title matches cannot be overridden.
- Product images may provide `local_path`, or an already verified Photobank `file_id + url`.
- Resolve relative local paths against the manifest directory.
- The first `prepare` hashes local files and blocks on unresolved images. After
  `upload-images`, the next `prepare` injects the returned `file_id + url`.
- Reuse an image mapping only when the current local SHA-256 exactly matches the saved mapping.
- `category_attributes` and `sale_properties` IDs must exist in the source Render definitions.
- Every SKU must contain exactly one value for each sale-property dimension.
- Multiple SKUs require unique, non-empty `sku_outer_id`.
- `stock_target` is an inventory target, not proof of inventory in the add XML.
- Set `inventory_mode` to `embedded` to include initial inventory in the product-create call.
  Every SKU must then contain a non-negative integer
  `stock_target` and a non-empty `warehouse_code`; `prepare` writes both into `skuStock`.
- Omit `inventory_mode` or set it to `deferred` to keep `skuStock` empty and use the
  post-publish inventory workflow.
- `simple_fields` may only modify existing top-level scalar fields. Protected identity and structured fields are rejected.
- When the source uses legacy `productDescType=2` and detail images change, provide a complete
  `super_text` replacement so source-product images do not survive in the HTML.
- Omit a section only when it should be inherited unchanged from the source. Provide an empty list only when the current Schema explicitly permits clearing it.
- For offline preparation, save each raw source Render response as `<source_product_id>.json` and use `--offline-render-dir`.
