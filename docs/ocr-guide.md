# OCR 引擎安装与使用指南

## 概述

`extract/ocr_engine.py` 提供扫描版 PDF 的 OCR 识别能力，支持以下引擎：

| 引擎 | 类型 | 优点 | 缺点 |
|------|------|------|------|
| Tesseract | 本地 | 免费、离线、隐私安全 | 需要安装、中文准确率一般 |
| 百度 OCR | 云端 | 中文准确率高、API 稳定 | 需要网络、有调用费用 |
| Mock | 测试 | 无需安装 | 无实际识别能力 |

## 安装 Tesseract（推荐）

### macOS

```bash
brew install tesseract tesseract-lang
```

### Ubuntu/Debian

```bash
sudo apt update
sudo apt install tesseract-ocr tesseract-ocr-chi-sim
```

### Windows

1. 下载安装包：https://github.com/UB-Mannheim/tesseract/wiki
2. 安装时勾选中文语言包
3. 将安装目录添加到 PATH

## 验证安装

```bash
tesseract --version
```

## 测试 OCR

```bash
# 检测 PDF 类型
python extract/ocr_engine.py --detect-only ./test.pdf

# 使用 Tesseract 识别
python extract/ocr_engine.py ./test.pdf --engine tesseract

# 使用百度 OCR（需配置 API Key）
python extract/ocr_engine.py ./test.pdf --engine baidu --api-key YOUR_KEY --secret-key YOUR_SECRET
```

## 批量测试

```bash
# 扫描目录中的所有 PDF，检测扫描版比例
python extract/test_ocr.py --scan-dir ./北京数学八上

# 运行基准测试
python extract/test_ocr.py --benchmark ./sample_pdfs
```

## 与提取流程集成

`extract_all.py` 已自动集成 OCR：

1. 处理 PDF 时自动检测类型（文本层 vs 扫描版）
2. 扫描版 PDF 自动调用 OCR 引擎提取文本
3. 扫描版 PDF 的页面也会渲染为图片并关联到题目
4. OCR 失败时自动回退到标准 PDF 提取流程

## 准确率评估

当前项目中的扫描版 PDF 比例约为 **14%**（以数学试卷为例）。

Tesseract 中文识别准确率：
- 印刷体中文：~85-90%
- 手写体：~60-70%
- 数学公式：建议保留为图片

## 配置

在 `extract_all.py` 中，OCR 引擎类型默认为 `"auto"`：

- 优先尝试 Tesseract
- 未安装则使用 Mock 引擎（记录警告日志）

可通过环境变量或修改代码切换引擎：

```python
# 强制使用 Tesseract
ocr_text = extract_text_from_pdf(pdf_path, engine_type="tesseract")

# 使用百度 OCR
ocr_text = extract_text_from_pdf(pdf_path, engine_type="baidu")
```

## 故障排除

| 问题 | 解决方案 |
|------|---------|
| Tesseract 未找到 | 检查 PATH 或指定路径：`TesseractEngine(tesseract_cmd="/usr/local/bin/tesseract")` |
| 中文识别乱码 | 确认安装了 `tesseract-ocr-chi-sim` 语言包 |
| OCR 结果为空 | 检查 PDF 是否为纯图片，尝试提高 DPI |
| 识别速度慢 | 降低渲染 DPI（默认 300，可改为 150） |

---

*关联 Issue: #10 [Extraction] 扫描版 PDF OCR 支持*
