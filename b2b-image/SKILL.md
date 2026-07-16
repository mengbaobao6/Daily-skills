---
name: b2b-image
description: 批量生成跨境电商 B2B 产品图片。Use when the user asks to read a product marketing plan/营销方案 Markdown file, extract AI image English Prompts, combine product reference images, generate images concurrently, save JPG outputs to E:\AI Product, and rename files in order by product name.
---

# B2B Image

## Image generation retry rule

- Submit every image once initially. If that submission fails for any reason, resubmit the same image task up to 3 times (`--max-retries 3`), for at most 4 attempts total.
- Retry only the failed image task. Do not regenerate images that have already succeeded.
- Treat non-zero API exit codes, invalid JSON, missing `saved` results, and missing output files as failed attempts.
- After all 3 retries fail, stop retrying that image and notify the frontend through the final JSON: set `status` to `partial_failed` or `failed`, include its index in `failed_indices`, and include the final error in `failures`. Also surface the result message in the final response.
- Keep successful images even when another image exhausts all retries.

## Product consistency rule

- 默认启用 `--consistency strict`。脚本会从营销方案表格中读取 `产品名称`、`产品结构`、`规格参数`、`材质`，并把这些内容作为同一批 6 张图的产品身份锁注入到每个 `English Prompt` 前面。
- 脚本还会把 Image 1 的 `English Prompt` 作为主产品身份指纹注入到所有图片里。编写方案时，Image 1 必须完整描述产品组件、颜色、材质、尺寸和包装，因为后续图片会以它作为统一产品外观基准。
- 有参考图时，参考图优先级最高。每张图都必须把参考图作为严格产品身份来源，保持相同组件数量、软木颜色、橙色脚趾阻力带、网袋布袋、圆角软木边缘、凹槽结构和组装绑带形态。
- 允许变化的是构图、镜头角度、背景、模特、尺寸标注、图标和 B2B 文案；不允许把产品改成其他健身器材、增减配件、改变关键颜色、改变材料质感或发明品牌 LOGO。
- 如果方案中的单张图 Prompt 与产品结构或参考图冲突，优先遵守参考图和产品结构。必要时先修改方案 Prompt，再生图。
- 只有用户明确要求关闭一致性增强时，才使用 `--consistency off`。

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
--consistency strict
```

## 使用规则

- 产品参考图不是必填项；传入一个或多个 `--image` 时走图生图，不传 `--image` 时直接走文生图。
- 用户给出产品名时，用 `--product-name` 作为输出文件名前缀。
- 用户未给产品名时，从方案文件名推断，去掉 `_营销方案_无品牌` 等后缀。
- `--out-dir` 是批次根目录；脚本会在根目录下创建 `<产品名>` 子文件夹。
- 如果 `<产品名>` 文件夹已存在，自动创建 `<产品名>01`、`<产品名>02` 这样的新文件夹，避免覆盖旧图。
- 保持方案里的英文 Prompt 原意，不擅自改文案。
- 默认增强产品一致性，不改变单张图的营销意图，只补充统一产品身份约束。
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
