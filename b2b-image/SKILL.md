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

## 示例

```bash
python scripts/generate_from_plan.py \
  --plan "E:\AI Product\木质训练平衡板_营销方案_无品牌.md" \
  --image "C:\path\front.png" \
  --image "C:\path\back.png" \
  --image "C:\path\detail.png" \
  --product-name "木质训练平衡板"
```
