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
      "stock_policy": "unlimited",
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
- Omit stock settings or use `stock_policy=unlimited` for the default behavior. The builder
  removes legacy numeric targets, leaves `skuStock` empty, and never calls inventory APIs.
- Use `stock_policy=fixed` only for an explicitly requested advanced case. Every SKU must
  then contain a non-negative `stock_target` and `warehouse_code`; the post-add workflow
  reads, updates once, and verifies actual inventory.
- `simple_fields` may only modify existing top-level scalar fields. Protected identity and structured fields are rejected.
- When the source uses legacy `productDescType=2` and detail images change, provide a complete
  `super_text` replacement so source-product images do not survive in the HTML.
- Omit a section only when it should be inherited unchanged from the source. Provide an empty list only when the current Schema explicitly permits clearing it.
- For offline preparation, save each raw source Render response as `<source_product_id>.json` and use `--offline-render-dir`.
