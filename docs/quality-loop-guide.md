# 1→10→50→200 渐进式质量循环操作指南

## 目标

通过**单张卷子人机协作循环**，逐步建立可信任的数据质量基线，避免一次性全量跑 200/2000 套导致无法定位问题。

## 阶段闸门

| 阶段 | 样本量 | 平均分 | 通过率 | 新增要求 |
|------|--------|--------|--------|---------|
| Gate 0 | 1 张 | ≥85 或提升 ≥10 分 | 1/1 | 人机一致率 ≥90%，至少 1 条有效规则 |
| Gate 1 | 10 张 | ≥85 | ≥80% (8/10) | Top 3 问题类型都有对应规则 |
| Gate 2 | 50 张 | ≥85 | ≥80% (40/50) | 问题类型分布稳定，无大量新类型 |
| Gate 3 | 200 张 | ≥85 | ≥85% (170/200) | 规则库冻结，不再新增高频规则 |

## 操作步骤

### Gate 0：单张卷子循环

1. 准备 1 张真实试卷（建议选问题较多的，如扫描版/表格版/子题多的卷子）。
2. 提取并启动复核服务器：
   ```bash
   python extract/single_paper_loop.py extract \
     --input ./raw_papers/2023海淀一模数学.pdf \
     --output ./loop/single/001/
   ```
3. 浏览器访问 `http://127.0.0.1:8765`，逐题复核：
   - 题号是否正确
   - content 是否完整、是否含噪声
   - options/answer/solution 是否正确
   - images 是否归属正确
   - metadata 是否正确
   - tags 是否合理
   - 对噪声题点击"删除本题"
4. 点击"保存复核结果"，关闭服务器。
5. 计算人机 diff：
   ```bash
   python extract/single_paper_loop.py diff --output ./loop/single/001/
   ```
6. 生成修复规则候选：
   ```bash
   python extract/single_paper_loop.py rules --output ./loop/single/001/
   ```
7. 人工检查 `./loop/single/001/fix_rules/` 中的规则，合并到代码：
   - `tag_fix_rules.json` → `extract/tag_rules.py`
   - `metadata_alias_candidates.json` → `extract/metadata_mappings.py`
   - `noise_patterns.json` → `extract/extract_all.py` 的 `NOISE_TEXT_PATTERNS`
8. 重跑验证：
   ```bash
   python extract/single_paper_loop.py verify \
     --input ./raw_papers/2023海淀一模数学.pdf \
     --output ./loop/single/001/
   ```
9. 若 Gate 0 通过（分数 ≥85 或提升 ≥10 分），该卷自动保存到 `gt/`，成为后续迭代的 Ground Truth。

### Gate 1：10 张卷子

1. 从 200 套中分层抽取 10 张（数学/物理各 5 张，覆盖不同年份/区/考试类型）。
2. 对每张卷子执行 Gate 0 流程。
3. 汇总 10 张的评分：
   ```bash
   python extract/quality_loop.py audit \
     --db ./loop/gate1/local.sqlite \
     --output ./loop/gate1/report/ \
     --gt-dir ./gt/
   ```
4. 检查是否满足：
   - 平均分 ≥85
   - 通过率 ≥80%
   - Top 3 问题类型都有对应规则
5. 不满足则回到单张循环，补充规则；满足则进入 Gate 2。

### Gate 2：50 张卷子

1. 用已通过 Gate 1 的规则，批量处理 50 张卷子。
2. 运行 audit：
   ```bash
   python extract/quality_loop.py audit \
     --db ./loop/gate2/local.sqlite \
     --output ./loop/gate2/report/ \
     --gt-dir ./gt/
   ```
3. 检查：
   - 平均分 ≥85
   - 通过率 ≥80%
   - 问题类型分布与 Gate 1 相比无显著变化（新类型占比 <10%）
4. 若出现新的高频问题类型，抽取 5–10 张典型卷子回到 Gate 0 处理。

### Gate 3：200 张卷子

1. 用 Gate 2 冻结的规则库，全量处理 200 张。
2. 运行 audit：
   ```bash
   python extract/quality_loop.py audit \
     --db ./loop/gate3/local.sqlite \
     --output ./loop/gate3/report/ \
     --gt-dir ./gt/
   ```
3. 检查：
   - 平均分 ≥85
   - 通过率 ≥85%
4. 若未达标，分析未通过卷子的共性问题，决定是否回到 Gate 1/Gate 2 补充规则。

## 关键原则

1. **任何规则调整必须先经过 Gate 0 单张验证**。
2. **只有验证通过的规则才能进入下一阶段**。
3. **每张通过 Gate 0 的卷子都保存为 GT**。
4. **不要把未达标的规则直接跑全量**。

## 常用命令速查

```bash
# 单张循环 - 提取 + 启动复核服务器
python extract/single_paper_loop.py extract --input xxx.pdf --output ./loop/001/

# 单张循环 - 计算 diff
python extract/single_paper_loop.py diff --output ./loop/001/

# 单张循环 - 生成规则
python extract/single_paper_loop.py rules --output ./loop/001/

# 单张循环 - 重跑验证
python extract/single_paper_loop.py verify --input xxx.pdf --output ./loop/001/

# 批量 audit
python extract/quality_loop.py audit --db local.sqlite --output ./report/ --gt-dir ./gt/

# 与 GT benchmark
python extract/quality_loop.py benchmark --ground-truth ./gt/ --db local.sqlite --output ./benchmark/
```
