---
name: product-image
description: 批量生成产品图片。基于营销方案 Markdown 文件，提取每个 ### Image N 区块里的 English Prompt，结合产品参考图生成图片。生图模型默认使用 agnes-image-2.1-flash，API Endpoint 为 https://apihub.agnes-ai.com/v1。
---

# Product Image

基于营销方案自动批量生成产品图片：

1. 读取营销方案 Markdown 文件。
2. 提取每个 `### Image N` 区块里的 `English Prompt`。
3. 有产品参考图时走图生图（edit），没有时走文生图（generate）。
4. 并发调用 `product-image/scripts/image_api.py` 图片 API。
5. 输出 JPG 到 `E:\AI Product/<产品名>-N.jpg`，自动避免覆盖。

## 依赖

- Python 3.x
- API key：通过 `OVO_API_KEY`、`OPENAI_COMPAT_IMAGE_API_KEY` 或 `OPENAI_API_KEY` 环境变量传入
- Base URL: `https://apihub.agnes-ai.com/v1`
- 默认模型: `agnes-image-2.1-flash`

## 默认命令

```bash
python scripts/generate_from_plan.py --plan "E:\AI Product\产品_营销方案_无品牌.md" --image image1.png --product-name "产品名"
```

## 使用规则

- 产品参考图可选：传入 `--image` 走图生图，不传走文生图
- `--product-name` 为输出文件前缀，省略时从方案文件名推断
- 输出目录为 `E:\AI Product`，自动创建编号子文件夹避免覆盖
- 并发生成，默认 6 workers
- 图片格式 JPG，JPEG 质量 92
- 失败时保留已成功图片，脚本返回 `partial_failed` 状态
