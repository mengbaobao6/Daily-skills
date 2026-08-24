---
name: ps-batch-image-actions
description: "Local batch image processing for product photos and ecommerce assets. Use when Codex needs to run or adapt a Python workflow that covers common Photoshop batch actions: resize, white background flattening, logo placement in a top-left safe area, image or text watermarking, brightness/color tuning, format and quality conversion, and consistent export naming for whole folders."
---

# PS Batch Image Actions

## Product-plan project handoff

When the logo task follows `product-plan` or targets a product project under `E:\AI Product\[产品名称]\`:

- Use `E:\AI Product\[产品名称]\营销图\` as the input folder and `E:\AI Product\[产品名称]\+logo图\` as the output folder. If the product project is unambiguous, do not ask the user to repeat these paths.
- Process only image files in `营销图`; do not recurse into the product root or treat reference images as generated marketing images.
- Preserve every no-logo source image, its dimensions, and its format unless the user explicitly requests conversion or resizing.
- Keep the matching source filename (`Image_01`, `Image_02`, and so on) by setting `export.keep_original_name: true`; the separate `+logo图` folder identifies the logo version. Never overwrite files already present there without explicit permission.
- Keep the top-left 18%×18% safe-zone check enabled. If an image fails, report it for regeneration or repair instead of forcing the logo over content.

## Safe-zone check limits (2026-08 case note)

- The safe-zone check only inspects the **top-left region's edge density** (edge_mean / edge_fraction). It does **NOT** verify whether the image actually depicts the correct product.
- When `logo.position` is anything other than `top-left`, the safe-zone check is **skipped entirely** — before using top-right/bottom corners, confirm visually that the corner is clean.
- Product-shape consistency (e.g. a thin towel rendered as a thick mat) must be verified against the reference images manually or via AI review after generation; the logo script cannot detect it.

## Default logo asset

- The skill bundles a default logo at `assets/logo1-90x130.png` (90×130 px transparent PNG). It matches the shared asset `E:\AI Product\00_公共素材\logo1-90x130.png` (same MD5). Use it when the user says "用默认logo" or no logo file is provided.
- For product-plan projects, preferred position is `top-left` with `margin_top: 30` / `margin_left: 40` (matches the 150×180 px logo reserve area in generated images).

## Quick Start

Use `scripts/batch_image_ps_actions.py` for deterministic folder processing. Prefer a JSON config when the user has repeatable export rules; use command-line flags for quick one-off batches.

```powershell
python scripts/batch_image_ps_actions.py --input "D:\input-images" --output "D:\output-images" --config references/sample_config.json
```

If no config is supplied, pass the main options directly:

```powershell
python scripts/batch_image_ps_actions.py --input "D:\input-images" --output "D:\output-images" --width 1200 --height 1200 --resize-mode fit --background white --format jpg --quality 92 --prefix product
```

## Workflow

1. Confirm the input folder, output folder, and whether subfolders should be included.
2. Ask for optional assets only when needed: logo image path, watermark image path, or watermark text.
3. Choose resize behavior:
   - `fit`: preserve the whole image and pad the canvas.
   - `fill`: crop to fill the exact output size.
   - `stretch`: force exact dimensions.
   - `none`: keep source size unless other steps change it.
4. Use `background: "white"` for ecommerce white-background export. This flattens transparency onto white and also uses white as padding canvas.
5. Configure logo placement with `logo.enabled`, `logo.path`, `logo.position`, `logo.margin`, `logo.max_width_ratio`, and `logo.opacity`. The default position is top-left with a safe margin.
6. Configure watermarks using either `watermark.text` or `watermark.image_path`. Use `tile: true` for repeated watermark coverage.
7. Apply color adjustments with `brightness`, `contrast`, `saturation`, `sharpness`, and `autocontrast`.
8. Export with the requested `format`, `quality`, and naming pattern.

## Script Notes

- Supported input extensions: `.jpg`, `.jpeg`, `.png`, `.webp`, `.bmp`, `.tif`, `.tiff`.
- JPEG output is automatically converted to RGB and flattened.
- Existing output files are not overwritten unless `overwrite` is true.
- Naming supports `prefix`, `start_index`, `padding`, and `keep_original_name`.
- The script prints a summary and per-file success/failure lines for easy troubleshooting.

## Config Reference

Use `references/sample_config.json` as the editable template. Keep paths absolute on Windows when possible, especially for logo and watermark assets.
