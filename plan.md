# ExamBank (mytiku) 执行计划 — 修订版 v2

## 项目概述
基于 Cloudflare 免费层的中考数学/物理题库系统。项目名称：`mytiku`。

**本次修订目标**：
1. **本地数据存放到 SQLite** — 提取后先写入本地 SQLite 数据库，支持查询/验证/清洗/增量更新，再导出 SQL 上传 D1
2. **多维度标签体系** — 每道题从 5 个维度打标签（知识点/能力/特征/方法/定位），方便后续智能组卷

---

## 当前状态（截至 2026-06-04）

### 已完成 ✅
- [x] 13 个代码/配置文件全部生成
- [x] 提取脚本测试通过（28 题/卷，0 异常）
- [x] Python 依赖全部安装（PyMuPDF/Pillow/python-docx/requests/boto3）
- [x] 试卷统计：2,942 份原卷版，预计 82,000 题

### 待完成 ⏳（本次修订）
- [ ] **Step 1**: 更新 plan.md（当前）
- [ ] **Step 2**: 完成 `extract_all.py` 主流程改造 — 接入 SQLite
- [ ] **Step 3**: 更新 `schema.sql` — 支持多维度标签字段
- [ ] **Step 4**: 更新 `seed_tags.sql` — 扩展标签体系数据
- [ ] **Step 5**: 更新 `src/db.ts` — 查询接口支持多维度标签筛选
- [ ] **Step 6**: 测试验证 — 提取 → SQLite → 查询标签分布 → 导出 SQL
- [ ] **Step 7**: 更新项目报告 HTML — 反映新架构

---

## 详细步骤

### Step 2: extract_all.py 主流程改造
**目标**: 将数据流从 "直接生成 SQL 文件" 改为 "先写 SQLite，再导出 SQL"

**当前数据流**:
```
PDF/Word → extract_all.py → questions_part_001.sql → upload_to_cf.py → D1
                              questions_part_002.sql
                              images/
```

**新数据流**:
```
PDF/Word → extract_all.py → local.sqlite → 验证/清洗/查询 → 导出 SQL → D1
                              images/
                              ↑
                              本地查询：SELECT * FROM questions WHERE tags LIKE '%圆%'
```

**修改点**:
1. 导入 `local_db.LocalDB`
2. 主流程初始化 `LocalDB(out_dir / "local.sqlite")`
3. 每处理完一个文件，立即 `db.insert_paper()` + `db.insert_question()` + `db.commit()`
4. 每 100 文件保存 checkpoint（不变）
5. 最终 `db.export_to_sql()` 生成 questions_part_NNN.sql
6. `db.get_stats()` 输出标签分布统计

---

### Step 3: schema.sql 更新
**目标**: D1 表结构支持多维度标签字段

**新增字段**:
```sql
-- questions 表新增
knowledge_tags TEXT,    -- JSON 数组: ["圆", "几何"]
ability_tags TEXT,      -- JSON 数组: ["逻辑推理", "空间想象"]
feature_tags TEXT,      -- JSON 数组: ["含图表", "多步骤"]
method_tags TEXT,       -- JSON 数组: ["数形结合"]
position_tag TEXT,      -- 单值: "压轴题"
```

**原因**: Workers 端也需要按维度筛选题目，如 "给我 5 道考查逻辑推理的圆的综合题"

---

### Step 4: seed_tags.sql 更新
**目标**: 标签体系表 tags 包含所有多维度标签

**新增标签**:
- 能力维度: 计算能力、逻辑推理、空间想象、阅读理解、实验设计、数据分析、建模能力、归纳抽象、分类讨论
- 题型特征: 含图表、多步骤、实际情境、最值问题、存在性、证明题、开放性、单选题、填空题、解答题、综合题、新定义题、材料阅读题、操作题
- 解题方法: 数形结合、分类讨论、构造法、反证法、换元法、配方法、待定系数法、整体代入、方程思想、函数思想、转化化归、特殊值法、排除法、归纳法、对称法、面积法
- 考试定位: 基础题、中档题、综合题、压轴题、创新题

---

### Step 5: src/db.ts 更新
**目标**: `buildQuerySQL()` 支持按标签维度筛选

**新增筛选参数**:
```typescript
if (filters.knowledge_tags?.length) {
  sql += ` AND knowledge_tags LIKE ?`;
  params.push(`%"${tag}"%`);
}
if (filters.ability_tags?.length) { ... }
if (filters.feature_tags?.length) { ... }
if (filters.method_tags?.length) { ... }
if (filters.position_tag) { ... }
```

---

### Step 6: 测试验证
**验证清单**:
- [ ] 提取 5 个测试文件 → local.sqlite 生成
- [ ] SQLite 查询: `SELECT knowledge_tags, ability_tags, feature_tags FROM questions LIMIT 5`
- [ ] 标签分布统计: `db.get_stats()` 输出各维度 Top 10
- [ ] 导出 SQL: `db.export_to_sql()` 生成 questions_part_001.sql
- [ ] SQL 文件与 schema 字段匹配
- [ ] 无标签题目数 < 10%

---

## 关键设计决策

### 为什么用 SQLite 而不是直接 SQL 文件？
| 场景 | 直接 SQL 文件 | SQLite |
|------|--------------|--------|
| 检查提取质量 | ❌ cat/grep 痛苦 | ✅ SELECT COUNT(*) |
| 修正错误标签 | ❌ 重新提取 2-3 小时 | ✅ UPDATE 秒级 |
| 增量更新 | ⚠️ 易重复 | ✅ INSERT OR IGNORE |
| 统计标签分布 | ❌ 写 Python 脚本 | ✅ GROUP BY |
| 按标签查题 | ❌ 无法做到 | ✅ SELECT ... WHERE tags LIKE |

### 多维度标签 vs 单维度标签
| 维度 | 用途 | 示例 |
|------|------|------|
| 知识点 | 按章节/概念筛选 | "给我 10 道二次函数题" |
| 能力维度 | 按考查能力筛选 | "给我 5 道考查逻辑推理的题" |
| 题型特征 | 按题目形式筛选 | "给我 3 道含图表的实际情境题" |
| 解题方法 | 按方法训练筛选 | "给我 5 道需要用分类讨论的题" |
| 考试定位 | 按难度/功能筛选 | "给我 2 道压轴难度的综合题" |

**组卷场景**: "生成一份 100 分数学卷，包含：基础题 30 分（计算能力）、中档题 40 分（圆+逻辑推理）、压轴题 30 分（二次函数+数形结合+最值）"

---

## 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `plan.md` | 更新 | 本文件 |
| `extract/local_db.py` | 新增 | SQLite 本地数据库模块 |
| `extract/tag_rules.py` | 重写 | 多维度标签引擎（5 维度 × 80+ 规则） |
| `extract/extract_all.py` | 修改 | 接入 SQLite + 新标签系统 |
| `schema.sql` | 修改 | questions 表新增 5 个标签维度字段 |
| `seed_tags.sql` | 修改 | 扩展标签体系数据 |
| `src/db.ts` | 修改 | 查询接口支持多维度标签筛选 |
| `project-report.html` | 更新 | 反映新架构 |

---

## 验收标准

- [ ] `extract_all.py` 处理 5 个测试文件，生成 local.sqlite
- [ ] SQLite 中每道题有 ≥3 个标签（跨维度）
- [ ] `db.get_stats()` 输出各维度标签分布
- [ ] 导出 SQL 文件与 schema 字段完全匹配
- [ ] `schema.sql` 可在 D1 成功执行
- [ ] `src/db.ts` 的 `buildQuerySQL()` 支持 knowledge_tags/ability_tags 筛选
- [ ] 项目报告 HTML 更新完成
