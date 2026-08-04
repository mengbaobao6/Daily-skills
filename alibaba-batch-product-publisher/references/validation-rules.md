# Row validation rules

Block the affected Excel row, not the entire workbook, when any condition fails:

- Source product ID, category ID, title, image path, specification input, or SKU plan is missing.
- A workbook path is unresolved, the workbook row changed after preflight, or the row key is ambiguous.
- Source Render is unsuccessful or its `catId` differs.
- Current `schema.get(cat_id=...)` is unsuccessful, or the generated XML was not rebased
  onto that current Schema.
- A populated source-only top-level or nested field survives rebasing, or a multi-value
  field exceeds the current Schema's `maxValueRule`.
- The final company-image URL list differs from the source in count, membership, or order,
  or create-only service bindings retain source values.
- Title is empty, over the current Render byte limit, or already exists exactly.
- Main images are outside 1–6 or lack a nonzero Photobank file ID and Alibaba CDN URL.
- A local image is missing, empty, unsupported, changed since mapping, or has no successful upload evidence.
- A requested attribute or sale-property field is absent from the source definitions.
- An option ID is absent from definitions unless a supported custom negative ID includes `input_value`.
- Custom values reuse a negative ID within one property.
- SKU rows do not cover every sale-property dimension exactly once.
- SKU combinations or populated outer IDs are duplicated.
- Price, quantity, MOQ, lead time, package measurement, weight, or stock target is negative or structurally invalid.
- `inventory_mode=embedded` lacks a non-negative integer `stock_target` or `warehouse_code`
  on any SKU, or the generated `skuStock` row count differs from the SKU count.
- Tier quantities are not strictly increasing.
- A protected field is supplied through `simple_fields`.
- Persisted SKU IDs or product-bound video values remain.
- Rebuilt SKU rows omit fields present in the cloned source row template, including
  `outerSupplyId`, or retain persisted `skuId`/`outerSupplyId` values.
- The generated XML hash differs from the preflight hash.
- The row state already records an add attempt for that sheet and row.
- An image hash already records an attempted but incomplete upload.

Protected fields include `catId`, product identifiers, `sku`, `sampleSku`, `saleProp`,
`icbuCatProp`, `scImages`, `detailImage`, video fields, tier prices, lead times, and inventory.

Successful API submission is not final verification. Final verification requires:

- `status=approved`
- `display=Y`
- exact category and title
- product image URLs in requested order
- requested attribute values
- sale-property values and exact SKU combinations
- tier quantities and prices
- MOQ and lead-time values
- real SKU IDs
- inventory read-back equal to every requested target

For `inventory_mode=embedded`, submit the requested stock inside the `schema.add` XML but
do not trust it as actual inventory. Immediately read real inventory, calculate one delta,
atomically record it, update once, and read back. If SKU mapping is temporarily unavailable,
write `已上传（库存待同步）` and use `reconcile-inventory` later. Never repeat an ambiguous
or unverified inventory delta.
