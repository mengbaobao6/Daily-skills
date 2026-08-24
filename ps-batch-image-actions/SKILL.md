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
- Keep the top-left 140×170 px safe-zone check enabled. If an image fails the check, report which image failed and skip it, then continue with the next image. Do not require repair or regeneration, and do not force the logo over content.
- Use the bundled default logo `assets/logo1-90x130.png` by default. Only ask for another logo path when the user explicitly requests a different logo.
- Default export is JPG at quality 80; keep this unless the user explicitly asks for a different quality or format.

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
2. Use bundled `assets/logo1-90x130.png` by default for logo placement. Ask for a different logo only when the user explicitly requests one.
3. Choose resize behavior:
   - `fit`: preserve the whole image and pad the canvas.
   - `fill`: crop to fill the exact output size.
   - `stretch`: force exact dimensions.
   - `none`: keep source size unless other steps change it.
4. Use `background: "white"` for ecommerce white-background export. This flattens transparency onto white and also uses white as padding canvas.
5. Configure logo placement with `logo.enabled`, `logo.path`, `logo.position`, `logo.margin`, `logo.max_width_ratio`, and `logo.opacity`. The default logo is bundled `assets/logo1-90x130.png`; the default position is `top-left` with `margin: 10`, placing it 10 pixels from the upper and left edges.
6. Keep `logo.safe_zone_check: true` for top-left logos. The script checks the upper-left 140×170 px exclusion zone and blocks that image when edge density indicates likely text, product, icon, or another important detail. A blocked image is reported by filename and skipped; processing continues with the next image. Do not require repair or regeneration.
7. Use `--skip-logo-safe-check` only when the user explicitly accepts the overlap risk. Do not use it merely to make a batch complete.
8. Configure watermarks using either `watermark.text` or `watermark.image_path`. Use `tile: true` for repeated watermark coverage.
9. Apply color adjustments with `brightness`, `contrast`, `saturation`, `sharpness`, and `autocontrast`.
10. Export with the requested `format`, `quality`, and naming pattern. Default export is JPG at quality 80.

## Script Notes

- Supported input extensions: `.jpg`, `.jpeg`, `.png`, `.webp`, `.bmp`, `.tif`, `.tiff`.
- JPEG output is automatically converted to RGB and flattened.
- Existing output files are not overwritten unless `overwrite` is true.
- Naming supports `prefix`, `start_index`, `padding`, and `keep_original_name`.
- The script prints a summary and per-file success/failure lines for easy troubleshooting.

## Config Reference

Use `references/sample_config.json` as the editable template. Keep paths absolute on Windows when possible, especially for logo and watermark assets.
