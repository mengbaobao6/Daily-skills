---
name: b2b-image
description: 批量生成跨境电商 B2B 产品图片。Use when the user asks to read a product marketing plan/营销方案 Markdown file, extract AI image English Prompts, combine product reference images, generate images concurrently, save JPG outputs to E:\AI Product, and rename files in order by product name.
---

# B2B Image

用这个技能自动完成整套 B2B 产品图生成流程：

1. 读取营销方案 Markdown 文件。
2. 提取每个 `### Image N` 区块里的 `English Prompt`。
3. 判断是否有产品参考图：有参考图时走图生图，没有参考图时走文生图。
4. 并发调用 `openai-compatible-image-api` 图片 API 技能。
5. 默认在 `E:\AI Product` 下创建一个产品名文件夹。
6. 把 JPG 图片保存到该文件夹，并按产品名顺序重命名为 `<产品名>-1.jpg`、`<产品名>-2.jpg`。

## 依赖

需要已经安装：

- `openai-compatible-image-api`
- API key 环境变量：`OVO_API_KEY`、`OPENAI_COMPAT_IMAGE_API_KEY` 或 `OPENAI_API_KEY`
- Python 命令：`python`

默认图片 API 参数：

```text
Base URL: https://api.0vo.dev/v1
Model: gpt-image-2
Output format: JPG
```

## 默认命令

```bash
python scripts/generate_from_plan.py --plan "E:\AI Product\产品_营销方案_无品牌.md" --image image1.png --image image2.png --product-name "产品名"
```

默认参数：

```text
--out-dir "E:\AI Product"
--workers 6
--size 1024x1024
--quality high
--model gpt-image-2
```

## 使用规则

- 产品参考图不是必填项；传入一个或多个 `--image` 时走图生图，不传 `--image` 时直接走文生图。
- 用户给出产品名时，用 `--product-name` 作为输出文件名前缀。
- 用户未给产品名时，从方案文件名推断，去掉 `_营销方案_无品牌` 等后缀。
- `--out-dir` 是批次根目录；脚本会在根目录下创建 `<产品名>` 子文件夹。
- 如果 `<产品名>` 文件夹已存在，自动创建 `<产品名>01`、`<产品名>02` 这样的新文件夹，避免覆盖旧图。
- 保持方案里的英文 Prompt 原意，不擅自改文案。
- 默认并发生成，6 张图时用 6 个 worker。
- 默认保存 JPG，不保存 PNG。
- 某张图失败时，保留已成功文件。脚本会返回 `status: partial_failed`、`failed_indices` 和 `failures`；必须在前端/最终回复中提示失败的是哪几张。
- 只有全部图片都失败时，脚本才返回失败退出码；部分失败时退出码为 0，便于前端继续展示成功图片。

## 方案格式硬性要求（2026-08 案例固化）

生图脚本按以下格式从方案 Markdown 提取 Prompt，**不满足则解析不到或解析错误**：

- 每个图片区块标题必须是三级标题：`### Image 1`、`### Image 2` …（不是 `####`，也不是 `##`）。
- 每个区块内，英文 Prompt 行必须是 `**English Prompt:**` 加粗标记、独占一行，后面紧跟 Prompt 文本。
- Prompt 结束后另起一行 `**对应中文：**`（或下一 `### Image N`），避免中文被并入英文 Prompt。
- 完整解析正则：`###\s*Image\s*(\d+).*?\*\*English Prompt:\*\*\s*(.*?)(?=\n\s*\*\*|(?:\n\s*---)|(?:\n\s*###\s*Image\s*\d+)|\Z)`（忽略大小写、跨行）。

## 生成后验收（每次生成后必做）

- 对照传入的参考图逐张核验 4 项：①产品形态一致（不是别的产品）；②颜色/材质/结构一致；③左上角安全区干净（可调 ps-batch-image-actions 安全区检查）；④文案位置与拼写。
- 任一张不达标 → 单张重做，不整批重跑；把结果写回 `产品状态.md`。
- 注意：生图 API 返回的实际尺寸可能不是请求的 1024×1024（实测为 1254×1254），属正常；安全区尺寸按实际像素比例换算。

## 已知坑位（避免重复踩）

- **强文字约束会压低参考图权重**：在 Prompt 里叠加过多"禁止/限定文字"约束时，AI 可能偏离参考图把产品画成其他形态（薄铺巾→厚瑜伽垫）。产品一致性要求必须写在最前面，文字约束要短、放在产品一致性之后。
- **copywriting 与左上角留空冲突**：`empty clean area at top left corner` 与 `all in English including copywriting` 并存时，AI 会把文字放左上角。要用强约束：`NO text in the upper half, top-left 220x220px completely empty, copywriting ONLY in the lower third`。

## 示例

```bash
python scripts/generate_from_plan.py \
  --plan "E:\AI Product\木质训练平衡板_营销方案_无品牌.md" \
  --image "C:\path\front.png" \
  --image "C:\path\back.png" \
  --image "C:\path\detail.png" \
  --product-name "木质训练平衡板"
```
