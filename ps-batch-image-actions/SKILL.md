---
name: ps-batch-image-actions
description: "Local batch image processing for product photos and ecommerce assets. Use when Codex needs to run or adapt a Python workflow that covers common Photoshop batch actions: resize, white background flattening, logo placement in a top-left safe area, image or text watermarking, brightness/color tuning, format and quality conversion, and consistent export naming for whole folders."
---

# PS Batch Image Actions

## Quick Start

Use `scripts/batch_image_ps_actions.py` for deterministic folder processing. Prefer a JSON config when the user has repeatable export rules; use command-line flags for quick one-off batches.

```powershell
python scripts/batch_image_ps_actions.py --input "D:\input-images" --output "D:\output-images" --config references/sample_config.json
```

If no config is supplied, pass the main options directly:

```powershell
python scripts/batch_image_ps_actions.py --input "D:\input-images" --output "D:\output-images" --width 1200 --height 1200 --resize-mode fit --background white --format jpg --quality 92 --prefix product
```

Enable the bundled default logo with `--logo` and no path:

```powershell
python scripts/batch_image_ps_actions.py --input "D:\input-images" --output "D:\output-images" --logo
```

## Workflow

1. Confirm the input folder, output folder, and whether subfolders should be included.
2. Use bundled `assets/logo1-90x130.png` when logo placement is requested without another logo path. Ask for optional assets only when a different logo, watermark image, or watermark text is needed.
3. Choose resize behavior:
   - `fit`: preserve the whole image and pad the canvas.
   - `fill`: crop to fill the exact output size.
   - `stretch`: force exact dimensions.
   - `none`: keep source size unless other steps change it.
4. Use `background: "white"` for ecommerce white-background export. This flattens transparency onto white and also uses white as padding canvas.
5. Configure logo placement with `logo.enabled`, `logo.path`, `logo.position`, `logo.margin`, `logo.max_width_ratio`, and `logo.opacity`. The default logo is bundled `assets/logo1-90x130.png`; the default position is `top-left` with `margin: 10`, placing it 10 pixels from the upper and left edges.
6. Before placing a top-left logo, keep `logo.safe_zone_check: true`. The script checks the upper-left 18%×18% exclusion zone and blocks that image when edge density indicates likely text, product, icon, or another important detail. A blocked image must be repaired or regenerated before logo placement.
7. Use `--skip-logo-safe-check` only when the user explicitly accepts the overlap risk. Do not use it merely to make a batch complete.
8. Configure watermarks using either `watermark.text` or `watermark.image_path`. Use `tile: true` for repeated watermark coverage.
9. Apply color adjustments with `brightness`, `contrast`, `saturation`, `sharpness`, and `autocontrast`.
10. Export with the requested `format`, `quality`, and naming pattern.

## Script Notes

- Supported input extensions: `.jpg`, `.jpeg`, `.png`, `.webp`, `.bmp`, `.tif`, `.tiff`.
- JPEG output is automatically converted to RGB and flattened.
- Existing output files are not overwritten unless `overwrite` is true.
- Logo-safe preflight failures are reported per file and do not stop unrelated files in the same folder.
- Naming supports `prefix`, `start_index`, `padding`, and `keep_original_name`.
- The script prints a summary and per-file success/failure lines for easy troubleshooting.

## Config Reference

Use `references/sample_config.json` as the editable template. Keep paths absolute on Windows when possible, especially for logo and watermark assets.
