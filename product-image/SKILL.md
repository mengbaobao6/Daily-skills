---
name: product-image
description: 批量生成产品图片。基于营销方案 Markdown 文件，提取每个 ### Image N 区块里的 English Prompt，结合产品参考图生成图片。生图模型使用 agnes-image-2.1-flash，API Endpoint 为 POST https://apihub.agnes-ai.com/v1/images/generations（文生图与图生图共用同一端点）。
---

# Product Image

基于营销方案自动批量生成产品图片：

1. 读取营销方案 Markdown 文件。
2. 提取每个 `### Image N` 区块里的 `English Prompt`。
3. 有产品参考图时走**图生图**（输入图通过 `extra_body.image` Data URI 数组传入），没有时走**文生图**。
4. 并发调用 `product-image/scripts/image_api.py` 图片 API。
5. 输出 JPG 到 `E:\AI Product/<产品名>-N.jpg`，自动避免覆盖。

## 依赖

- Python 3.x
- API key：内置默认值（`image_api.py` 中），也可通过 `OPENAI_COMPAT_IMAGE_API_KEY` 或 `OPENAI_API_KEY` 环境变量覆盖
- **API Endpoint**: `POST https://apihub.agnes-ai.com/v1/images/generations`
- **模型**: `agnes-image-2.1-flash`
- 文生图与图生图**共用同一端点**

## Agnes API 调用规范（重要）

| 项目 | 说明 |
|------|------|
| 端点 | `POST /v1/images/generations`（文生图 + 图生图统一） |
| 认证 | `Authorization: Bearer <API_KEY>` |
| Content-Type | `application/json` |
| 必填参数 | `model`, `prompt`, `size` |
| 图生图额外参数 | 输入图片放入 `extra_body.image`（Data URI 数组） |
| 输出格式 | 放在 `extra_body.response_format`（`"b64_json"` 或 `"url"`） |
| ⚠️ 禁止 | ❌ 不传 `tags: ["img2img"]`；❌ `response_format` 不放顶层 |

### 请求体结构

```json
{
  "model": "agnes-image-2.1-flash",
  "prompt": "...",
  "size": "1024x1024",
  "extra_body": {
    "image": ["data:image/jpeg;base64,..."],
    "response_format": "b64_json"
  }
}
```

### 响应体结构

```json
{
  "created": 1780000000,
  "data": [
    {"url": "...", "b64_json": null}
  ]
}
```

### 尺寸（size）约束（2026-07-17 实测）

`size` 不是任意值都生效，Agnes `agnes-image-2.1-flash` 实测行为：

| 传入 size | 实际像素 | 结论 |
|-----------|----------|------|
| `1024x1024` | 1024×1024 | ✅ 标准尺寸，始终可用 |
| `1254x1254` / `1280x1280` | 1024×1024 | ❌ 被静默钳制，不会报错但无效 |
| `1024x1536` | 832×1248 | 按比例缩放（长边封顶约 1248） |
| `2048x2048` | 2048×2048 | ✅ 真正生效的更高清正方形 |

> ⚠️ 若平台要求精确像素（如 1254×1254），API 无法直接产出，需生图后由 PIL 重采样到目标尺寸（仅插值放大，不增加细节）。脚本当前把 `--size` 原样透传，无白名单校验。

### 精确像素尺寸（--resize）

`image_api.py` 与 `generate_from_plan.py` 均支持 `--resize WxH`：生图完成后用 PIL LANCZOS 重采样到目标像素并覆盖原文件。

**推荐组合（平台要求正方形 1254×1254 时）：**

```bash
# 直接调用脚本
python scripts/image_api.py edit --prompt "..." --image ref.jpg \
  --size 2048x2048 --resize 1254x1254 --output-format jpg --out-dir <输出目录>

# 走计划文件（--resize 会自动把生成尺寸提升到 2048x2048 再下采样）
python scripts/generate_from_plan.py --plan 产品_营销方案_无品牌.md \
  --image ref.jpg --resize 1254x1254
```

> 先以 2048×2048 生成、再缩到 1254×1254 是**下采样**，比 1024 上采样到 1254 更清晰。若目标尺寸 ≤ 1024（如 1000×1000），直接用 `--size 1024x1024 --resize 1000x1000` 即可。

## 默认命令

```bash
python scripts/generate_from_plan.py --plan "E:\AI Product\产品_营销方案_无品牌.md" --image ref1.jpg --product-name "产品名"
```

## 使用规则

- 产品参考图可选：传入 `--image` 走图生图，不传走文生图
- `--product-name` 为输出文件前缀，省略时从方案文件名推断
- 输出目录为 `E:\AI Product`，自动创建编号子文件夹避免覆盖
- 并发生成，默认 6 workers（可通过 `--workers N` 调整）
- 图片格式 JPG，JPEG 质量 92
- 失败时保留已成功图片，脚本返回 `partial_failed` 状态
