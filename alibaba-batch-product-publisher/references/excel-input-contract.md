# Excel input contract

Use `.xlsx` or `.xlsm`. Preserve the original workbook, formulas, formatting, and
unrelated sheets. For `.xlsm`, load and save with VBA preservation enabled.

## Required columns

Column names may be Chinese or the listed English alias:

| Meaning | Preferred column | Accepted alias |
|---|---|---|
| Source product | 源商品ID | source_product_id |
| New title | 新产品标题 | title |
| Product images | 新产品图片路径 | image_paths |
| Specifications | 新产品规格参数 | specifications |
| SKU plan file | SKU方案文件路径 | sku_plan_path |

One row represents one new product. Blank rows are ignored.

`新产品图片路径` must contain one folder path. Relative paths resolve against the
workbook directory. Upload every supported image directly inside that folder, sorted
naturally by filename (`1.jpg`, `2.jpg`, ..., `10.jpg`). Do not recurse into
subfolders such as `带Logo` unless that subfolder itself is the path in the Excel cell.
The first image is the main image; the remaining images follow the source product's
image/detail structure unless the SKU plan explicitly assigns image roles.

Supported extensions are `.jpg`, `.jpeg`, `.png`, and `.webp`, case-insensitively.
Ignore non-image files. Block the row if the folder does not exist or contains no
supported images.

`新产品规格参数` may contain compact `名称=值` pairs separated by newlines or
semicolons, or a path to a UTF-8 `.json`, `.md`, or `.txt` plan. Resolve human-readable
names against the source's current Render definitions. Never guess an ambiguous field.

`SKU方案文件路径` must point to a UTF-8 JSON, Markdown, TXT, CSV, or XLSX file that
defines sale-property axes, combinations, outer IDs, prices, tier prices, MOQ, lead
times, package measurements, inventory targets, and warehouse codes as applicable.
The agent must parse it into the source Schema and report missing required values.
If an SKU does not specify an inventory target, assign `stock_target=99999`.
Do not replace an explicitly supplied value, including `0`. Warehouse-code requirements
still follow the selected inventory mode and the source-compatible Schema.

For deterministic automated publishing, prefer JSON. It may directly provide:
`variant_axes` or `sale_properties`, `skus` or `sku_defaults`, `price_tiers`, `moq`,
`lead_times`, `package`, `inventory_mode`, `custom_properties`, and optional omission
lists. Markdown/TXT supports `key: value` lines or a fenced JSON object. CSV/XLSX uses
the first data row as the plan.

## Optional input columns

- `任务ID`: stable user-provided identifier; otherwise use `sheet!row`.
- `库存模式`: `embedded` or `deferred`; default to the plan or source-compatible mode.
- `允许相似商品`: explicit yes/no decision. Exact title duplicates remain blocked.
- `备注`: user instructions for that row.

## Managed output columns

Create these columns at the end of the header row if absent:

- `发布状态`
- `新商品ID`
- `验证时间`
- `验证摘要`

Allowed states are `待处理`, `预检失败`, `图片已上传`, `预检完成`, `已上传`, `提交结果不明确`,
`待审核`, `验证失败`, `待人工确认`, `发布失败`, and `发布成功`.

Write `已上传` immediately after `schema.add` clearly succeeds and returns a new product
ID. This is the default terminal state and means only that the platform accepted the
upload; it does not mean the product is approved or displayed. Do not poll review or
inventory automatically.

Write `发布成功` only when the created product is approved and displayed, the value-level
comparison passes, real SKU IDs exist, and inventory read-back equals every requested
target during an explicitly requested verification run. Store detailed reports outside
the workbook and put only a concise summary in it.
