# Row validation rules

Block the affected Excel row, not the entire workbook, when any condition fails:

- Source product ID, category ID, title, image path, specification input, or SKU plan is missing.
- A workbook path is unresolved, the workbook row changed after preflight, or the row key is ambiguous.
- Source Render is unsuccessful or its `catId` differs.
- Current `schema.get(cat_id=...)` is unsuccessful, or the generated XML was not rebased
  onto that current Schema.
- A populated source-only top-level or nested field survives rebasing, or a multi-value
  field exceeds the current Schema's `maxValueRule`.
- The final company galleries differ from the source in group count/order, gallery IDs or
  names, nested image/text values, image URLs, or create-only service bindings retain values.
- Title is empty, over the current Render byte limit, or already exists exactly.
- Main images are outside 1–6 or lack a nonzero Photobank file ID and Alibaba CDN URL.
- A local image is missing, empty, unsupported, changed since mapping, or has no successful upload evidence.
- A requested attribute or sale-property field is absent from the source definitions.
- An option ID is absent from definitions unless a supported custom negative ID includes `input_value`.
- Custom values reuse a negative ID within one property.
- SKU rows do not cover every sale-property dimension exactly once.
- SKU combinations or populated outer IDs are duplicated.
- Price, quantity, MOQ, lead time, package measurement, weight, or stock target is negative or structurally invalid.
- `stock_policy=fixed` lacks a non-negative integer `stock_target` or `warehouse_code` on
  any SKU, or its generated `skuStock` count differs from the selected technical mode.
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

Default to `stock_policy=unlimited`: remove any legacy numeric stock values, leave every
`skuStock` empty, and skip all inventory API calls. Fixed inventory is an advanced explicit
opt-in only. For `stock_policy=fixed`, read real inventory after add, calculate one delta,
record it atomically, update once, and read back. If SKU mapping is temporarily unavailable,
write `已上传（库存待同步）` and use `reconcile-inventory` later. Never repeat an ambiguous
or unverified delta.
