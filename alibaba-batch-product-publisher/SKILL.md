---
name: alibaba-batch-product-publisher
description: "Agent-agnostic Alibaba.com ICBU batch publisher. Use when any shell-capable agent must validate and publish final, user-approved product data from the bundled Excel template: clone a same-leaf-category source product, upload all images in each row's folder, rebuild current Schema XML, call schema.add exactly once, and write 已上传 plus the new product ID back to the workbook. This skill is only the publishing stage; it does not create marketing strategies, infer final prices/SKUs, or generate product images."
---

# Alibaba Batch Product Publisher

Run the bundled deterministic Python programs. Do not reimplement API signing, XML
generation, image upload, duplicate detection, state recovery, or Excel write-back in the
agent's own reasoning. This keeps behavior consistent across Codex, Claude, Gemini, local
models, and other agents that can use a shell and local files.

## Hard boundary: planning is not publishing

This skill begins only after the publishing package is final.

```text
Marketing-plan skill creates a draft
→ user corrects and confirms prices, SKUs, attributes, titles, and other facts
→ image-generation step creates final assets
→ confirmed Excel + image folders + plan files become the handoff package
→ this skill validates and publishes that package
```

Never use this skill to create a marketing plan or generate images. Never allow inferred
draft prices, SKUs, or product facts to flow directly into publishing. If the user has not
confirmed the contents or the image folders are incomplete, stop before any upload.

## Runtime setup

Read `references/portable-runtime.md` when setting up a new machine or agent. The only
required runtime is Python 3.10+ plus `requirements.txt`. Credentials may come from process
environment variables or any supported JSON config; no specific agent installation is
required. Run `scripts/doctor.py` before the first job on a machine.

Never expose AppSecret, access tokens, or raw credential files in prompts, logs, Excel,
artifacts, or terminal summaries.

## Input contract

Read `references/excel-input-contract.md`. Use `assets/阿里国际站批量发品模板.xlsx`
as the canonical handoff workbook. Copy it to the user's chosen location; never edit the
bundled template in place.

Each data row is an independent publishing job and must contain a source product ID, final
title, final image-folder path, final specification data, and final SKU plan. A missing SKU
stock target defaults to `99999`; explicit values, including `0`, always win.

The source product supplies category-compatible structure only. User-approved values in
the handoff package control changed fields. This skill does not improve or reinterpret the
marketing strategy.

## Standard execution

1. Run the doctor. If the read-only check fails, do not upload images or submit products.
2. Run read-only preparation first. Report invalid rows without blocking valid rows.
3. Present the row summary and obtain explicit approval for platform writes.
4. Run guarded preparation with image upload. Image upload is a separately recorded write
   and never triggers product creation by itself.
5. Run submission. Each eligible row receives exactly one `schema.add` attempt. After a
   clear acceptance, immediately read real SKU inventory, write the required delta once,
   and read it back. XML `skuStock` is only a request and never proof of actual inventory.
6. Write `已上传` only when real SKU inventory equals every requested target. If real SKU
   mapping is temporarily unavailable, write `已上传（库存待同步）`, preserve the queue, and
   run `reconcile-inventory` later. This does not wait for platform review.
7. Run `watch` only when the user explicitly requests later approval and full-field
   verification.

```bash
# Safe machine/authentication check (product.list is read-only)
python scripts/doctor.py --config /path/to/config.json

# Read-only preparation
python scripts/excel_workflow.py prepare --input products.xlsx --work-dir artifacts --config /path/to/config.json

# Confirmed image upload and complete preflight
python scripts/excel_workflow.py prepare --input products.xlsx --work-dir artifacts --config /path/to/config.json --upload-images --confirm

# Exactly-once product creation
python scripts/excel_workflow.py submit --input products.xlsx --work-dir artifacts --config /path/to/config.json --confirm

# Resume only products whose add succeeded but real inventory could not yet be mapped
python scripts/excel_workflow.py reconcile-inventory --input products.xlsx --work-dir artifacts --config /path/to/config.json --confirm

# Combined confirmed upload + submit, then exit after 已上传
python scripts/excel_workflow.py run --input products.xlsx --work-dir artifacts --config /path/to/config.json --confirm

# Optional later verification only
python scripts/excel_workflow.py watch --input products.xlsx --work-dir artifacts --config /path/to/config.json --max-wait-seconds 900
```

Resume with the same workbook and work directory. `workflow-state.json` is the source of
truth for recovery; a row with a recorded add attempt must never be submitted again.

## Speed model

When the confirmed package is already complete, target roughly ten ordinary products in
twenty minutes. Speed comes from deterministic execution, caches, deduplication, and safe
concurrency—not from reducing validation:

- read-only calls: at most 8 concurrently;
- image uploads: at most 5 concurrently;
- local XML builds: at most 8 concurrently;
- `schema.add`: at most 2 concurrently;
- same source structure and identical image bytes are cached and reused;
- Excel state is checkpointed in groups instead of saved after every row;
- review waiting is outside the default publishing run.

## Non-negotiable safety

- Keep every row in the source product's same leaf category.
- Fetch the source once and fetch the current clean category Schema with `cat_id`.
- Rebase source values onto the current Schema; never submit Render XML as-is.
- Scan the complete product catalog for exact duplicate titles and reuse the snapshot for
  up to 15 minutes.
- Treat the image path as one folder. Upload all supported files directly inside it in
  natural filename order; do not recurse. The first image is the main image.
- Hash each image once. Reuse only complete SHA-256-to-file-ID/CDN evidence.
- Clear product IDs, SKU IDs, outer supply IDs, product video IDs, obsolete fields, and
  create-only service bindings before add.
- Preserve every source company-introduction image exactly once and in source order. Block
  submission if the source/output URL lists or counts differ; never submit a shortened
  Company overview gallery.
- Verify the prepared XML hash immediately before submission.
- Record the add attempt atomically before the call. Never retry timeout, HTTP error,
  `SYS_ERROR`, or any ambiguous write response automatically.
- Open the write circuit breaker on ambiguity and resolve by read-only exact-title lookup.
- Record the real inventory-update attempt before writing. Never repeat an uncertain
  inventory delta. An embedded `99999` in XML does not satisfy inventory verification.
- Do not bypass `excel_workflow.py` with a hand-built recovery XML for production adds.
- Treat `已上传` as interface acceptance plus verified requested inventory, not platform
  approval. Do not write `发布成功`
  until an explicitly requested later verification confirms approval, display state,
  requested fields, real SKU IDs, and inventory.

## Agent execution contract

Read `references/agent-task-contract.md` before an agent connects planning and publishing
in one conversation. The two stages must remain separate even if one agent can invoke both
skills.

Read `references/validation-rules.md` when a row is blocked and
`references/api-methods.md` before changing request names or envelopes.
