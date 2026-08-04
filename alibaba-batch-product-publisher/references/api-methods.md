# API methods

Use the authorized ICBU sync gateway methods:

| Purpose | Method | Mutation |
|---|---|---|
| Source product | `alibaba.icbu.product.get` | No |
| Source/current Render | `alibaba.icbu.product.schema.render` | No |
| Current clean category Schema | `alibaba.icbu.product.schema.get` | No |
| Duplicate list | `alibaba.icbu.product.list` | No |
| Create new product | `alibaba.icbu.product.schema.add` | Yes |
| Read SKU inventory | `alibaba.icbu.product.sku.inventory.get` | No |
| Adjust inventory delta | `alibaba.icbu.product.inventory.update` | Yes |
| Photobank list | `alibaba.icbu.photobank.list` | No |
| Photobank upload | `alibaba.icbu.photobank.upload` | Yes |

Call the sync gateway with form encoding and HMAC-SHA256. Load credentials from the
existing local MCP configuration or explicit environment variables. Never serialize secrets
into row artifacts or the workbook.

`schema.get` must use this envelope. The parameter is `cat_id`; `category_id` can return
`biz_success=true` with an unrelated default category Schema and must never be used:

```json
{
  "param_product_top_publish_request": {
    "cat_id": 201623401,
    "language": "en_US"
  }
}
```

`schema.add` request envelope:

```json
{
  "param_product_top_publish_request": {
    "cat_id": 202111401,
    "language": "en_US",
    "xml": "<itemSchema>...</itemSchema>"
  }
}
```

When the current Render defines `sku/fields/skuStock` with `warehouseCode` and `srcValue`,
a row may use `inventory_mode=embedded` and include initial stock inside each new SKU row:

```xml
<field id="skuStock" type="multiInput">
  <values>
    <value warehouseCode="CN_LOCAL_01" srcValue="99999">99999</value>
  </values>
</field>
```

Read it back after creation; do not automatically call `inventory.update` if the platform filters it.

Inventory update accepts a relative `plus` or `sub` amount. Always read current inventory,
calculate `target-current`, write once, and read back. Never repeat a delta because of delayed
propagation.

Photobank upload uses multipart form data with the signed public parameters and an
`image_bytes` file part. Record the local absolute path, byte size, SHA-256, returned file ID,
CDN URL, request ID, and raw response. Do not retry an ambiguous upload automatically.
