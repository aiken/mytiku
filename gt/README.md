# Ground Truth（人工校对基准）标注指南

## 目的

Ground Truth 是数据质量循环的“锚点”。所有自动提取规则、正则调整、模型优化都必须以提升 GT 指标为准，而不是凭感觉调代码。

## 目录结构

```
gt/
├── schema.json              # GT JSON Schema
├── README.md                # 本文件
├── math/
│   ├── paper_001/
│   │   ├── meta.json        # 试卷元数据
│   │   ├── questions.json   # 题目列表
│   │   └── images/          # 题目图片（可选）
│   └── ...
└── physics/
    └── ...
```

## 标注步骤

1. **选卷**：从原始试卷中按年份/区/考试类型分层抽取，优先选包含复杂版式的卷子（表格、多图、子题、扫描版）。
2. **创建目录**：`gt/<subject>/paper_<NNN>/`
3. **填写 meta.json**：试卷元数据必须与原始卷面一致。
4. **填写 questions.json**：逐题人工校对。
5. **校验**：运行 `python extract/gt_manager.py validate gt/`。

## 题目内容标注规范

- `content` 只保留题干和选项文本，**不包含**答案、解析、页眉页脚、考试说明。
- 选择题的 `options` 必须包含 A/B/C/D 四个选项文本。
- 子题填写 `parent_question_number`，题号格式为 `17(1)`、`17(2)`。
- 图片只在真正属于该题时放入 `images`，不明确的图片单独备注。
- `knowledge_tags` 必须来自 `seed_tags.sql` 中已注册的知识点标签。

## 质量控制

- 每套 GT 必须由至少 2 人校对：1 人初标，1 人复核。
- 标注人需在 `meta.json` 中记录 `annotator` 和 `annotated_at`。
- 发现卷面本身模糊/破损/无法辨认时，在 `notes` 中说明，该卷不计入指标。

## 使用方式

```bash
# 校验所有 GT
python extract/gt_manager.py validate gt/

# 查看 GT 统计
python extract/gt_manager.py stats gt/

# 与提取结果对比
python extract/quality_loop.py benchmark --ground-truth gt/ --db ./extract_output/local.sqlite
```
