---
name: openai-compatible-image-api
description: Generate images and edit images through a third-party OpenAI-compatible Images API. Use when the user asks for text-to-image, image-to-image, reference-image generation, multi-image editing, product infographic image generation, or using the api.0vo.dev gpt-image-2 image API instead of Codex built-in image tools.
---

# OpenAI-Compatible Image API

Use this skill to call a third-party OpenAI-compatible Images API at `https://api.0vo.dev/v1` with model `gpt-image-2`.

The bundled script supports:
- Text-to-image via `/v1/images/generations`
- Image-to-image / edits via `/v1/images/edits`
- Multiple input images by repeating `--image`
- Optional mask image for edit requests
- URL or base64 JSON responses
- Saving returned images into the workspace or a user-selected output directory as JPG by default

## Required Environment

Require an API key in one of these environment variables:

```text
OVO_API_KEY
OPENAI_COMPAT_IMAGE_API_KEY
OPENAI_API_KEY
```

Use `OVO_API_KEY` by preference. Never ask the user to paste the key in chat. Ask them to set it locally and confirm.

Optional overrides:

```text
OPENAI_COMPAT_IMAGE_BASE_URL=https://api.0vo.dev/v1
OPENAI_COMPAT_IMAGE_MODEL=gpt-image-2
```

## Workflow

1. Determine the mode.
   - No input image: use `generate`.
   - One or more input images: use `edit`.
   - A mask implies `edit`.
2. Preserve user intent. Do not add unrelated subjects, brands, text, or style constraints.
3. For ecommerce/product image requests, keep all requested English text exact and concise.
4. Run `scripts/image_api.py` from this skill folder.
5. Save final images into the active workspace unless the user explicitly requests another path.
6. Report the saved paths and the mode/model used.

## Commands

Text-to-image:

```bash
python scripts/image_api.py generate --prompt "..." --out-dir outputs
```

Image-to-image with one source:

```bash
python scripts/image_api.py edit --prompt "..." --image input.png --out-dir outputs
```

Image-to-image with many sources:

```bash
python scripts/image_api.py edit --prompt "..." --image front.png --image side.png --image detail.png --out-dir outputs
```

Masked edit:

```bash
python scripts/image_api.py edit --prompt "..." --image input.png --mask mask.png --out-dir outputs
```

Useful options:

```text
--model gpt-image-2
--base-url https://api.0vo.dev/v1
--size 1024x1024
--quality high
--count 1
--response-format b64_json
--output-format jpg
--filename-prefix product-image
```

## API Notes

- `generate` posts multipart form data to `/v1/images/generations`.
- `edit` posts multipart form data to `/v1/images/edits`.
- The script sends repeated `image` fields for multiple input images.
- Use `response_format=b64_json` by default so results can be saved deterministically.
- If the provider returns URLs only, the script downloads them and saves local copies.
- The script saves final files as JPG by default. Use `--output-format original` only when the user explicitly needs PNG/WebP.
- Network access is required. If the sandbox blocks the request, rerun with escalation.

## Safety

Before calling the API, understand that prompts, input images, and masks are sent to the configured third-party endpoint. Do not use private, regulated, or sensitive images unless the user clearly intends to send them to this service.

Do not hide persistence. Always save generated results only to the requested output directory or the workspace `outputs/` directory, and report those paths.
