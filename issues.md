# GitHub Issues 清单 — mytiku 功能开发

> 基于 plan.md v3 整理，共 16 个功能需求 issue（含 3 个 Epic）
> 创建方式：复制以下内容到 GitHub Issues，或使用 `gh issue create` 批量提交

---

## 阶段总览

| 阶段 | 优先级 | Issue 范围 | 目标 |
|------|--------|-----------|------|
| **Phase 0** | P0 | #16 | 数据抽取验证 — 建立基线，确认当前质量 |
| **Phase 1** | P0 | #1 ~ #3 | 数据质量优化 — 基于验证结果针对性改进 |
| **Phase 2** | P1 | #4 ~ #7 | API 增强 — 搜索、推荐、详情、补全 |
| **Phase 3** | P1-P2 | #8 ~ #10 | 提取优化 — 子题、答案分离、OCR |
| **Phase 4** | P2 | #11 ~ #12 | 前端界面 — 管理后台、学生练习 |
| **Epic** | P0-P1 | #13 ~ #15 | 验收标准 — 数据质量、API、性能 |

**开发顺序建议**：#16 → #1 → #2 → #3 → #4 → #7 → #6 → #5 → #8 → #9 → #10 → #11 → #12

---

## Phase 0: 数据抽取验证（P0 — 最优先入口）

### Issue #16: [Validation] 数据抽取验证 — 数学/物理各 100 套试卷
**标签**: `validation`, `data-quality`, `high-priority`, `phase-0`  
**优先级**: P0 — **所有阶段的入口任务**

#### 描述
在全面进入数据质量优化和 API 开发之前，先对现有 2,942 份试卷进行**抽样验证**，确认当前抽取流程的基线质量。

从数学、物理两科各抽取 **100 套**（共 200 套）作为验证集，运行完整的数据抽取流程（提取 → 标签 → 元数据 → 图片 → 答案/解析匹配），输出验证报告，为后续 Phase 1 数据质量优化提供依据和改进方向。

#### 验收标准

**验证集构建**
- [ ] 数学 100 套、物理 100 套验证集选定
- [ ] 覆盖不同年份（2020–2024）、地区（北京各区）、考试类型（期中/期末/一模/二模/中考模拟）
- [ ] 分层抽样策略：按年份×地区×考试类型分层，每层至少 5 套

**抽取质量指标**
- [ ] 题目提取成功率 ≥ 95%（提取到有效内容的题目 / 总题目数）
- [ ] 无标签题目比例 < 5%
- [ ] 每道题平均标签数 ≥ 4 个（跨 5 个维度）
- [ ] 图片提取成功率 ≥ 90%
- [ ] 试卷元数据（district/school/round）准确率 ≥ 80%
- [ ] 答案/解析匹配覆盖率 ≥ 90%（基于成对提取 + 区域提取）

**验证报告**
- [ ] 生成 Markdown 验证报告，包含统计数据、问题样本、改进建议
- [ ] 人工抽检 20 套（10%），核对提取结果与原始 PDF/Word
- [ ] 报告提交到仓库 `docs/validation-report-YYYYMMDD.md`

#### 技术要点

**验证集选择策略**
```python
# 分层抽样逻辑
strata = {
    "year": [2020, 2021, 2022, 2023, 2024],
    "region": ["北京"],
    "district": ["海淀", "西城", "东城", "朝阳", "丰台", "其他"],
    "exam_type": ["期中", "期末", "一模", "二模", "中考模拟"],
    "subject": ["math", "physics"]
}
# 每科 100 套，确保各分层有代表性
```

**验证流程**
```
1. 选定验证集（200 套）
2. 运行 extract_all.py → 提取题目/图片/元数据
3. 运行 tag_quality.py（Issue #1）→ 统计标签覆盖率
4. 人工抽检 20 套 → 核对提取准确性
5. 汇总数据 → 生成验证报告
```

**关键检查项**
| 检查项 | 检查方式 | 通过标准 |
|--------|---------|---------|
| 题号连续性 | 脚本检查 | 无跳号/重号 |
| 内容完整性 | 人工抽检 | 题目内容无截断 |
| 图片对应 | 人工抽检 | 图片与题目正确关联 |
| 标签合理性 | 人工抽检 | 标签与内容匹配 |
| 元数据准确 | 人工抽检 | 年份/地区/学校正确 |
| 答案匹配 | 脚本+人工 | 题号一一对应 |

#### 关联 Issue
- #1 标签质量检查脚本（验证阶段需要先用起来）
- #13 [Epic] 数据质量验收（验证报告是 Epic 的输入）

#### 产出物
- `docs/validation-report-YYYYMMDD.md` — 验证报告
- `docs/validation-samples/` — 问题样本截图/文件
- `extract/validation_set.json` — 验证集清单（200 套文件路径）

---

## Phase 1: 数据质量（P0 — 基于 Phase 0 验证结果优化）

### Issue #1: [Data Quality] 标签质量检查脚本 `extract/tag_quality.py`
**标签**: `enhancement`, `data-quality`, `high-priority`  
**优先级**: P0

#### 描述
开发标签质量检查脚本，用于监控和评估多维度标签体系的覆盖率和准确性。

#### 验收标准
- [ ] 统计无标签题目比例（目标 < 5%）
- [ ] 统计各维度标签覆盖率（Knowledge / Ability / Feature / Method / Position）
- [ ] 输出需要人工复核的题目列表（CSV 格式）
- [ ] 支持按学科（math/physics）分别统计
- [ ] 生成可视化报告（HTML 或终端输出）

#### 技术要点
- 读取本地 SQLite 数据库 `questions` 表
- 检查 `knowledge_tags`, `ability_tags`, `feature_tags`, `method_tags`, `position_tag` 字段
- 空值/空列表视为无标签
- 输出格式：题目ID + 内容摘要 + 缺失维度

#### 关联文件
- `extract/local_db.py` — 数据库查询接口
- `schema.sql` — 表结构参考

---

### Issue #2: [Data Quality] 批量标签修正工具 `extract/tag_fixer.py`
**标签**: `enhancement`, `data-quality`, `high-priority`  
**优先级**: P0

#### 描述
开发批量标签修正工具，支持按内容模式批量修正题目标签，减少人工干预成本。

#### 验收标准
- [ ] 支持正则匹配题目内容 + 标签增删操作
- [ ] 支持从本地 SQLite 直接执行 UPDATE
- [ ] 提供预览模式（dry-run），先展示变更再执行
- [ ] 支持规则文件导入（YAML/JSON 格式）
- [ ] 修正操作记录日志，支持回滚

#### 技术要点
- 规则格式示例：
  ```yaml
  rules:
    - pattern: "如图.*圆.*切线"
      add_tags:
        knowledge_tags: ["圆的切线"]
        method_tags: ["数形结合"]
      remove_tags: []
  ```
- 支持多条件组合匹配
- 事务安全：单条规则失败不影响其他规则

#### 关联文件
- `extract/tag_rules.py` — 现有标签规则参考
- `extract/local_db.py` — 数据库更新接口

---

### Issue #3: [Data Quality] 试卷元数据校验与标准化
**标签**: `enhancement`, `data-quality`, `high-priority`  
**优先级**: P0

#### 描述
建立试卷元数据（district/school/round）的校验和标准化机制，提高元数据准确率。

#### 验收标准
- [ ] 检查 district/school/round 提取准确率（目标 ≥ 80%）
- [ ] 建立学校别名映射表（如 "四中" → "北京四中"）
- [ ] 建立区名标准化映射（如 "海淀" → "海淀区"）
- [ ] 建立考试类型标准化映射（如 "期中考试" → "期中"）
- [ ] 提供元数据纠错界面或脚本

#### 技术要点
- 映射表存储：JSON 文件 或 SQLite 配置表
- 学校别名来源：知名学校列表 + 用户反馈
- 区名标准化：基于北京市行政区划
- 支持模糊匹配（如 "北京四中" / "北京市第四中学" / "四中"）

#### 关联文件
- `extract/extract_all.py` — 元数据提取逻辑
- `schema.sql` — papers 表结构

---

## Phase 2: API 增强（P1 — 中优先级）

### Issue #4: [API] 全文搜索端点 `POST /api/search`
**标签**: `enhancement`, `api`, `medium-priority`  
**优先级**: P1

#### 描述
基于 D1 FTS5 全文搜索，支持关键词 + 标签联合筛选的题目搜索端点。

#### 验收标准
- [ ] 支持关键词全文搜索（题目内容、答案、解析）
- [ ] 支持关键词 + 5 维度标签联合筛选
- [ ] 支持分页（limit/offset）
- [ ] 支持排序（相关度 / 难度 / 年份）
- [ ] 响应包含高亮片段（highlight snippets）
- [ ] 单次查询响应 < 500ms（D1 冷启动除外）

#### 技术要点
- D1 SQLite 需启用 FTS5 扩展
- 可能需要创建虚拟表：`CREATE VIRTUAL TABLE questions_fts USING fts5(...)`
- 索引字段：content, answer, explanation
- 与现有 `/api/query` 端点保持参数兼容

#### API 设计草案
```json
POST /api/search
{
  "q": "二次函数 最值",
  "subject": "math",
  "knowledge_tags": ["二次函数"],
  "limit": 20,
  "offset": 0,
  "sort": "relevance"
}
```

#### 关联文件
- `src/index.ts` — API 路由
- `src/db.ts` — 数据库查询
- `schema.sql` — 需添加 FTS5 虚拟表

---

### Issue #5: [API] 相似题目推荐 `GET /api/similar/:question_id`
**标签**: `enhancement`, `api`, `medium-priority`  
**优先级**: P1

#### 描述
基于标签重叠度计算题目相似度，返回 Top 5 相似题目推荐。

#### 验收标准
- [ ] 基于 5 维度标签重叠度计算相似度分数
- [ ] 返回 Top 5 相似题目（含相似度分数）
- [ ] 支持按学科过滤
- [ ] 排除自身题目
- [ ] 响应 < 500ms

#### 技术要点
- 相似度算法：Jaccard 系数或加权余弦相似度
- 权重设计：knowledge_tags (0.4) > ability_tags (0.2) > method_tags (0.2) > feature_tags (0.1) > position_tag (0.1)
- 可扩展：未来支持向量相似度（embedding）
- 缓存：相似度结果可缓存于 KV，TTL 86400

#### API 设计草案
```json
GET /api/similar/123?subject=math&limit=5
{
  "question_id": 123,
  "similar": [
    {"question_id": 456, "score": 0.85, "title": "..."},
    ...
  ]
}
```

#### 关联文件
- `src/index.ts` — API 路由
- `src/db.ts` — 数据库查询

---

### Issue #6: [API] 试卷详情端点 `GET /api/paper/:paper_id`
**标签**: `enhancement`, `api`, `medium-priority`  
**优先级**: P1

#### 描述
返回试卷完整元数据 + 题目列表，支持按题号排序。

#### 验收标准
- [ ] 返回试卷元数据（年份、地区、区、学校、考试类型、轮次）
- [ ] 返回题目列表（按题号排序）
- [ ] 每题包含完整标签信息
- [ ] 支持分页（题目较多时）
- [ ] 响应 < 500ms

#### API 设计草案
```json
GET /api/paper/42
{
  "paper": {
    "id": 42,
    "year": 2023,
    "region": "北京",
    "district": "海淀区",
    "school": "北京四中",
    "exam_type": "期中",
    "round": null,
    "subject": "math",
    "question_count": 28
  },
  "questions": [
    {"id": 101, "number": 1, "content": "...", "tags": {...}},
    ...
  ]
}
```

#### 关联文件
- `src/index.ts` — API 路由
- `src/db.ts` — 数据库查询
- `schema.sql` — papers/questions 表结构

---

### Issue #7: [API] 标签自动补全 `GET /api/tags/suggest`
**标签**: `enhancement`, `api`, `medium-priority`  
**优先级**: P1

#### 描述
输入前缀返回匹配标签，支持按学科过滤，用于前端搜索框的自动补全。

#### 验收标准
- [ ] 输入前缀返回匹配标签列表（如 `q=圆` → `["圆的切线", "圆的性质", "圆与多边形"]`）
- [ ] 支持按学科过滤（math/physics）
- [ ] 支持按维度过滤（knowledge/ability/feature/method/position）
- [ ] 返回标签使用频率（可选）
- [ ] 响应 < 200ms

#### 技术要点
- 数据来源：`seed_tags.sql` 或数据库中已有标签的 DISTINCT 值
- 缓存：标签列表可缓存于 KV，TTL 86400
- 支持拼音首字母匹配（未来扩展）

#### API 设计草案
```json
GET /api/tags/suggest?q=圆&subject=math&dimension=knowledge&limit=10
{
  "query": "圆",
  "suggestions": [
    {"tag": "圆的切线", "count": 156, "dimension": "knowledge"},
    {"tag": "圆的性质", "count": 89, "dimension": "knowledge"},
    {"tag": "圆与多边形", "count": 34, "dimension": "knowledge"}
  ]
}
```

#### 关联文件
- `src/index.ts` — API 路由
- `seed_tags.sql` — 标签种子数据

---

## Phase 3: 提取优化（中优先级）

### Issue #8: [Extraction] 子题拆分增强
**标签**: `enhancement`, `extraction`, `medium-priority`  
**优先级**: P1

#### 描述
增强题目提取逻辑，支持 (1)(2)(3) 子题的独立提取，子题继承父题标签并支持独立标签。

#### 验收标准
- [ ] 识别并拆分 (1)(2)(3) / ①②③ / (i)(ii)(iii) 等子题格式
- [ ] 子题独立存储，保留父题关联（parent_question_id）
- [ ] 子题继承父题标签（可覆盖）
- [ ] 子题支持独立答案和解析
- [ ] 组卷时支持选择子题或整题

#### 技术要点
- 子题识别模式：
  - `(1)`, `(2)`, `(3)` — 中文括号数字
  - `①`, `②`, `③` — 中文圆圈数字
  - `(i)`, `(ii)`, `(iii)` — 罗马数字
  - `第一步`, `第二步` — 中文步骤
- 数据库变更：questions 表添加 `parent_question_id` 字段
- 子题内容提取：基于正则或 NLP 分段

#### 关联文件
- `extract/extract_all.py` — 提取主流程
- `schema.sql` — 需添加 parent_question_id 字段
- `extract/local_db.py` — 子题存储逻辑

---

### Issue #9: [Extraction] 答案/解析分离（从试卷末尾参考答案区域提取）
**标签**: `enhancement`, `extraction`, `medium-priority`  
**优先级**: P1

#### 描述
从试卷末尾的"参考答案"区域提取答案和解析，与题目按题号匹配关联，作为成对提取的补充方案。

#### 验收标准
- [ ] 识别试卷末尾的"参考答案" / "解析" / "解答" 区域
- [ ] 按题号匹配答案/解析到对应题目
- [ ] 支持多种答案格式（纯答案 / 详细解析 / 步骤分解）
- [ ] 与现有成对提取方案互补（优先成对提取， fallback 到区域提取）
- [ ] 覆盖率目标：≥ 90% 题目有答案和解析

#### 技术要点
- 区域识别：基于标题关键词（"参考答案", "答案与解析", "解答"）
- 题号匹配：正则提取答案前的题号标记
- 内容分段：按题号分割答案区域
- 与 `extract_all.py` 的 `generate_sql_inserts()` 集成

#### 关联文件
- `extract/extract_all.py` — 提取主流程
- `extract/local_db.py` — 数据库存储

---

### Issue #10: [Extraction] 扫描版 PDF OCR 支持
**标签**: `enhancement`, `extraction`, `medium-priority`, `ocr`  
**优先级**: P2

#### 描述
支持纯图片 PDF（扫描版试卷）的文本识别，扩展提取能力到扫描版试卷。

#### 验收标准
- [ ] 识别扫描版 PDF（纯图片，无文本层）
- [ ] 集成 Tesseract OCR 或云端 OCR API
- [ ] 中文识别准确率 ≥ 85%
- [ ] 数学公式识别（可选，可标记为图片保留）
- [ ] 与现有提取流程集成（自动检测 PDF 类型）

#### 技术要点
- PDF 类型检测：检查是否包含文本层
- OCR 引擎选择：
  - 本地：Tesseract + 中文训练数据
  - 云端：百度 OCR / 腾讯 OCR / Azure Computer Vision
- 图片预处理：去噪、二值化、倾斜校正
- 性能优化：多进程并行 OCR

#### 关联文件
- `extract/extract_all.py` — 提取主流程
- 新增：`extract/ocr_engine.py`

---

## Phase 4: 前端界面（低优先级）

### Issue #11: [Frontend] Web 管理后台
**标签**: `enhancement`, `frontend`, `low-priority`  
**优先级**: P2

#### 描述
开发 Web 管理后台，提供题目浏览、标签编辑、组卷配置、试卷预览与打印功能。

#### 验收标准
- [ ] 题目浏览与搜索（支持 5 维度标签筛选）
- [ ] 标签编辑界面（单题标签修改 + 批量修改）
- [ ] 组卷可视化配置（拖拽/表单配置 filters）
- [ ] 试卷预览（A4 排版，支持 KaTeX 公式渲染）
- [ ] 试卷打印（调用浏览器打印或生成 PDF）
- [ ] 响应式设计（桌面 + 平板）

#### 技术要点
- 前端框架：React / Vue / 纯 HTML+JS
- UI 组件库：Ant Design / Element Plus / Tailwind
- 公式渲染：KaTeX（与后端 HTML 生成一致）
- 图片展示：R2 对象存储 URL
- 状态管理：URL 参数同步筛选条件

#### 页面结构草案
- `/admin` — 管理后台首页
- `/admin/questions` — 题目列表
- `/admin/questions/:id` — 题目详情/编辑
- `/admin/papers` — 试卷列表
- `/admin/papers/:id` — 试卷详情
- `/admin/generate` — 组卷配置
- `/admin/preview/:gen_id` — 试卷预览

#### 关联文件
- `src/html.ts` — 试卷 HTML 生成逻辑参考
- `src/index.ts` — API 端点

---

### Issue #12: [Frontend] 学生练习界面
**标签**: `enhancement`, `frontend`, `low-priority`  
**优先级**: P2

#### 描述
开发学生练习界面，支持按知识点/能力维度选题练习、答题记录、错题本和学习进度统计。

#### 验收标准
- [ ] 按知识点/能力维度选题练习
- [ ] 答题界面（题目展示 + 答案输入/选择）
- [ ] 答题记录（历史答题记录存储于 localStorage 或 D1）
- [ ] 错题本（自动收录错误题目，支持复习）
- [ ] 学习进度统计（各维度正确率、练习时长）
- [ ] 响应式设计（桌面 + 平板 + 手机）

#### 技术要点
- 用户身份：匿名（localStorage）或 简单账号（D1 存储）
- 答题记录结构：question_id + user_answer + is_correct + timestamp
- 错题本逻辑：is_correct = false 的题目自动收录
- 进度统计：按维度聚合正确率
- 练习模式：顺序练习 / 随机练习 / 专项突破

#### 页面结构草案
- `/practice` — 练习首页（选择维度）
- `/practice/:mode` — 练习界面
- `/practice/wrong` — 错题本
- `/practice/stats` — 学习统计

#### 关联文件
- `src/index.ts` — API 端点
- `src/html.ts` — 题目 HTML 渲染参考

---

## 验收标准 Epic Issues

### Issue #13: [Epic] 数据质量验收
**标签**: `epic`, `data-quality`, `acceptance-criteria`  
**优先级**: P0

#### 验收标准
- [ ] 提取 100 份测试文件，无标签题目 < 5%
- [ ] 每道题平均标签数 ≥ 4 个（跨维度）
- [ ] 试卷元数据（district/school/round）准确率 ≥ 80%
- [ ] 图片提取成功率 ≥ 90%

#### 前置依赖
- #16 [Validation] 数据抽取验证 — 数学/物理各 100 套试卷（**Phase 0 入口**，提供基线数据）

#### 关联 Issue
- #1 标签质量检查脚本
- #2 批量标签修正工具
- #3 试卷元数据校验

#### 说明
本 Epic 的验收标准基于 Phase 0 验证报告的数据。先完成 #16 验证，再根据验证结果决定 Phase 1 的优化重点。

---

### Issue #14: [Epic] API 功能验收
**标签**: `epic`, `api`, `acceptance-criteria`  
**优先级**: P1

#### 验收标准
- [ ] `/api/query` 支持所有 5 个维度标签筛选
- [ ] `/api/generate` 支持按模板 + 按规则两种组卷方式
- [ ] `/api/analytics` 支持 heatmap/trend/position_dist/tag_tree/tag_dimension
- [ ] `/api/html/:gen_id` 生成完整 A4 试卷，图片正常显示

#### 关联 Issue
- #4 全文搜索端点
- #5 相似题目推荐
- #6 试卷详情端点
- #7 标签自动补全

---

### Issue #15: [Epic] 性能验收
**标签**: `epic`, `performance`, `acceptance-criteria`  
**优先级**: P1

#### 验收标准
- [ ] 单次查询响应 < 500ms（D1 冷启动除外）
- [ ] 组卷生成 < 2s（50 题以内）
- [ ] 统计分析结果缓存于 KV，TTL 86400

#### 关联 Issue
- #4 全文搜索端点（性能要求）
- #5 相似题目推荐（缓存设计）
- #14 API 功能验收

---

## 优先级总览

| 优先级 | Issue | 标题 | 阶段 | 说明 |
|--------|-------|------|------|------|
| P0 | #16 | 数据抽取验证 — 数学/物理各 100 套 | **Phase 0** | **入口任务，最先执行** |
| P0 | #1 | 标签质量检查脚本 | Phase 1 | 验证阶段即需使用 |
| P0 | #2 | 批量标签修正工具 | Phase 1 | 基于验证结果优化 |
| P0 | #3 | 试卷元数据校验 | Phase 1 | 基于验证结果优化 |
| P0 | #13 | [Epic] 数据质量验收 | Epic | 依赖 #16 验证报告 |
| P1 | #4 | 全文搜索端点 | Phase 2 | |
| P1 | #5 | 相似题目推荐 | Phase 2 | |
| P1 | #6 | 试卷详情端点 | Phase 2 | |
| P1 | #7 | 标签自动补全 | Phase 2 | |
| P1 | #8 | 子题拆分增强 | Phase 3 | |
| P1 | #9 | 答案/解析分离 | Phase 3 | |
| P1 | #14 | [Epic] API 功能验收 | Epic | |
| P2 | #10 | 扫描版 PDF OCR | Phase 3 | |
| P2 | #11 | Web 管理后台 | Phase 4 | |
| P2 | #12 | 学生练习界面 | Phase 4 | |
| P2 | #15 | [Epic] 性能验收 | Epic | |

---

## 批量创建脚本

### 使用 GitHub CLI (gh)

```bash
# 确保已安装 gh 并登录
# brew install gh
# gh auth login

# Phase 0: 数据抽取验证（入口）
gh issue create --repo aiken/mytiku --title "[Validation] 数据抽取验证 — 数学/物理各 100 套试卷" --label "validation,data-quality,high-priority,phase-0" --body "见 issues.md Issue #16"

# Phase 1: 数据质量
gh issue create --repo aiken/mytiku --title "[Data Quality] 标签质量检查脚本 extract/tag_quality.py" --label "enhancement,data-quality,high-priority" --body "见 issues.md Issue #1"
gh issue create --repo aiken/mytiku --title "[Data Quality] 批量标签修正工具 extract/tag_fixer.py" --label "enhancement,data-quality,high-priority" --body "见 issues.md Issue #2"
gh issue create --repo aiken/mytiku --title "[Data Quality] 试卷元数据校验与标准化" --label "enhancement,data-quality,high-priority" --body "见 issues.md Issue #3"

# Phase 2: API 增强
gh issue create --repo aiken/mytiku --title "[API] 全文搜索端点 POST /api/search" --label "enhancement,api,medium-priority" --body "见 issues.md Issue #4"
gh issue create --repo aiken/mytiku --title "[API] 相似题目推荐 GET /api/similar/:question_id" --label "enhancement,api,medium-priority" --body "见 issues.md Issue #5"
gh issue create --repo aiken/mytiku --title "[API] 试卷详情端点 GET /api/paper/:paper_id" --label "enhancement,api,medium-priority" --body "见 issues.md Issue #6"
gh issue create --repo aiken/mytiku --title "[API] 标签自动补全 GET /api/tags/suggest" --label "enhancement,api,medium-priority" --body "见 issues.md Issue #7"

# Phase 3: 提取优化
gh issue create --repo aiken/mytiku --title "[Extraction] 子题拆分增强" --label "enhancement,extraction,medium-priority" --body "见 issues.md Issue #8"
gh issue create --repo aiken/mytiku --title "[Extraction] 答案/解析分离（从试卷末尾参考答案区域提取）" --label "enhancement,extraction,medium-priority" --body "见 issues.md Issue #9"
gh issue create --repo aiken/mytiku --title "[Extraction] 扫描版 PDF OCR 支持" --label "enhancement,extraction,medium-priority,ocr" --body "见 issues.md Issue #10"

# Phase 4: 前端界面
gh issue create --repo aiken/mytiku --title "[Frontend] Web 管理后台" --label "enhancement,frontend,low-priority" --body "见 issues.md Issue #11"
gh issue create --repo aiken/mytiku --title "[Frontend] 学生练习界面" --label "enhancement,frontend,low-priority" --body "见 issues.md Issue #12"

# Epic: 验收标准
gh issue create --repo aiken/mytiku --title "[Epic] 数据质量验收" --label "epic,data-quality,acceptance-criteria" --body "见 issues.md Issue #13"
gh issue create --repo aiken/mytiku --title "[Epic] API 功能验收" --label "epic,api,acceptance-criteria" --body "见 issues.md Issue #14"
gh issue create --repo aiken/mytiku --title "[Epic] 性能验收" --label "epic,performance,acceptance-criteria" --body "见 issues.md Issue #15"
```

### 使用 GitHub API (curl)

需要 `GITHUB_TOKEN` 环境变量（Personal Access Token，需 `repo` 权限）：

```bash
export GITHUB_TOKEN="ghp_xxxxxxxxxxxx"

# 创建 issue 示例
curl -X POST \
  -H "Authorization: token $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/repos/aiken/mytiku/issues \
  -d '{
    "title": "[Validation] 数据抽取验证 — 数学/物理各 100 套试卷",
    "labels": ["validation", "data-quality", "high-priority", "phase-0"],
    "body": "见 issues.md Issue #16"
  }'
```
