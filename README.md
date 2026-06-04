# ExamBank (mytiku)

基于 **Cloudflare 免费层** 的中考数学 / 物理题库系统。

- **零服务器成本**：利用 Cloudflare Workers + D1 + R2 + KV 免费额度运行
- **本地工业化提取**：Python 多进程从 PDF / Word 试卷中提取题目、图片、元数据
- **智能标签**：正则规则自动识别知识点（圆、二次函数、浮力、欧姆定律等）
- **组卷与预览**：按标签 / 难度 / 题位筛选题目，生成可打印的 A4 HTML 试卷

---

## 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                        本地环境 (macOS/Linux/Windows)        │
│  ┌──────────────┐   ┌──────────────┐   ┌────────────────┐  │
│  │  PDF/Word    │   │  extract_all │   │  upload_to_cf  │  │
│  │  原始试卷     │ → │  提取+压缩   │ → │  D1 SQL + R2   │  │
│  │  (raw_papers)│   │  (images/sql)│   │  批量上传       │  │
│  └──────────────┘   └──────────────┘   └────────────────┘  │
│         ↑                                    │              │
│         └──────────── exam_cli.py ────────────┘              │
│              (查询 / 组卷 / 预览 / 配置)                      │
└─────────────────────────────────────────────────────────────┘
                              │ HTTPS + Bearer Token
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Cloudflare 边缘网络                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │   Workers   │  │     D1      │  │     R2      │         │
│  │  (API 网关) │←→│  (SQLite)   │  │  (对象存储)  │         │
│  │  src/index  │  │  题目/试卷  │  │  题目图片   │         │
│  └─────────────┘  └─────────────┘  └─────────────┘         │
│         ↑                                              │
│  ┌─────────────┐                                       │
│  │     KV      │  —— 限流计数 / 统计缓存 / 配置缓存       │
│  └─────────────┘                                       │
└─────────────────────────────────────────────────────────────┘
```

---

## 文件结构

```
mytiku/
├── extract/                    # 本地 Python 提取与上传工具
│   ├── extract_all.py          # PDF/Word 工业化提取（多进程 / 断点续传 / 压缩）
│   ├── upload_to_cf.py         # 批量上传 SQL 到 D1，图片到 R2
│   ├── exam_cli.py             # 本地 CLI 客户端（查询 / 组卷 / 预览）
│   └── tag_rules.py            # 自动标签正则规则
├── src/                        # Cloudflare Workers TypeScript 源码
│   ├── index.ts                # 主入口：路由 + 安全层 + API 端点
│   ├── db.ts                   # D1 查询辅助函数（参数化 SQL）
│   └── html.ts                 # A4 试卷 HTML 生成器（含 KaTeX 公式）
├── schema.sql                  # D1 数据库建表语句
├── seed_tags.sql               # 标签体系初始化数据
├── wrangler.toml               # Workers / D1 / R2 / KV 绑定配置
├── package.json                # Node.js 依赖（wrangler / TypeScript）
├── tsconfig.json               # TypeScript 编译配置
├── plan.md                     # 项目执行计划
├── 北京9上数学试卷/            # 原始试卷示例（数学）
├── 北京9上物理/                # 原始试卷示例（物理）
├── B京市数学七八九/            # 更多原始试卷
├── B京物理八九/                # 更多原始试卷
└── B京化学七八九/              # 原始试卷示例（化学，预留）
```

---

## 前置要求

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | 3.9+ | 本地提取脚本 |
| Node.js | 18+ | wrangler CLI 与 Workers 部署 |
| wrangler | 3.60+ | Cloudflare 资源管理与部署 |
| PyMuPDF | latest | PDF 文本与图片提取 (`fitz`) |
| python-docx | latest | Word 文档提取 |
| Pillow | latest | 图片压缩与格式转换 |
| requests | latest | CLI HTTP 请求 |
| boto3 | latest | R2 S3 兼容上传（可选，可用 wrangler 替代） |

安装命令：

```bash
# Node.js 依赖
npm install

# Python 依赖
pip install PyMuPDF python-docx Pillow requests boto3
```

---

## 部署步骤

### 1. 安装依赖

```bash
# 克隆项目后进入目录
cd mytiku
npm install
pip install PyMuPDF python-docx Pillow requests boto3
```

### 2. 创建 Cloudflare 资源

登录 Cloudflare 并创建以下资源：

```bash
# 登录 Cloudflare（浏览器授权）
npx wrangler login

# 创建 D1 数据库
npx wrangler d1 create exam-bank-db
# 记录返回的 database_id

# 创建 R2 存储桶
npx wrangler r2 bucket create exam-bank-assets

# 创建 KV 命名空间
npx wrangler kv:namespace create KV
# 记录返回的 namespace id
```

### 3. 配置 wrangler.toml

将上一步获取的 `database_id` 和 `kv_namespace id` 填入 `wrangler.toml`：

```toml
name = "exam-bank"
main = "src/index.ts"
compatibility_date = "2026-06-04"

[[d1_databases]]
binding = "DB"
database_name = "exam-bank-db"
database_id = "<你的 D1 database_id>"

[[r2_buckets]]
binding = "BUCKET"
bucket_name = "exam-bank-assets"

[[kv_namespaces]]
binding = "KV"
id = "<你的 KV namespace id>"
```

同时设置 API Key（用于请求鉴权）：

```bash
# 设置 Workers Secret
npx wrangler secret put API_KEY
# 输入你的密钥，例如：sk-exambank-2024
```

可选：开启 KV 限流

```bash
npx wrangler secret put RATE_LIMIT_ENABLED
# 输入 true 开启，空或 false 关闭
```

### 4. 初始化数据库

```bash
# 执行建表 SQL
npx wrangler d1 execute exam-bank-db --file schema.sql

# 初始化标签体系
npx wrangler d1 execute exam-bank-db --file seed_tags.sql
```

### 5. 部署 Workers

```bash
npm run deploy
# 或
npx wrangler deploy
```

部署成功后，记录 Workers 域名，例如：
`https://exam-bank.your-subdomain.workers.dev`

### 6. 本地提取与上传

```bash
# 1) 从原始试卷提取题目与图片
python extract/extract_all.py \
  --input ./北京9上数学试卷/ \
  --output ./extract_output/ \
  --max-workers 4

# 2) 上传 SQL 到 D1，图片到 R2
python extract/upload_to_cf.py \
  --sql-dir ./extract_output/batch_sql/ \
  --images-dir ./extract_output/images/ \
  --init-tags \
  --seed-sql seed_tags.sql \
  --use-wrangler-r2
```

### 7. CLI 配置

```bash
python extract/exam_cli.py config
```

按提示输入：
- **API 基础地址**：`https://exam-bank.your-subdomain.workers.dev`
- **API Key**：第 3 步设置的密钥

配置保存在 `~/.exam-bank/config.json`，权限 `600`。

---

## 使用指南

### 提取试卷

```bash
python extract/extract_all.py \
  --input ./raw_papers/ \
  --output ./extract_output/ \
  --batch-size 5000 \
  --max-workers 4 \
  --compress-quality 75 \
  --max-image-width 1200
```

参数说明：
- `--input`：原始试卷目录（支持嵌套子目录，自动识别 PDF / Word）
- `--output`：输出目录，生成 `batch_sql/` 和 `images/`
- `--batch-size`：每批 SQL 最大 INSERT 条数（默认 5000）
- `--max-workers`：多进程并发数（默认 4）
- `--compress-quality`：JPEG 压缩质量（默认 75）
- `--max-image-width`：图片最大宽度（默认 1200px）

支持**断点续传**：中断后重新执行会自动跳过已处理文件。

### 上传到 Cloudflare

```bash
# 方式一：使用 wrangler 上传 R2（推荐，无需 boto3）
python extract/upload_to_cf.py \
  --sql-dir ./extract_output/batch_sql/ \
  --images-dir ./extract_output/images/ \
  --use-wrangler-r2

# 方式二：使用 boto3 上传 R2（需 S3 兼容密钥）
python extract/upload_to_cf.py \
  --sql-dir ./extract_output/batch_sql/ \
  --images-dir ./extract_output/images/ \
  --r2-endpoint https://<account_id>.r2.cloudflarestorage.com \
  --r2-access-key <access_key> \
  --r2-secret-key <secret_key>
```

支持**断点续传**：`upload_checkpoint.json` 记录已上传文件。

### CLI 查询题目

```bash
# 按通用标签查询
python extract/exam_cli.py query \
  --subject math \
  --tags 圆 \
  --limit 5

# 按多维度标签查询（知识点 + 能力 + 定位）
python extract/exam_cli.py query \
  --subject math \
  --knowledge-tags 圆 几何 \
  --ability-tags 逻辑推理 \
  --feature-tags 含图表 \
  --position-tag 综合题 \
  --limit 5

# 按解题方法查询
python extract/exam_cli.py query \
  --subject math \
  --method-tags 数形结合 分类讨论 \
  --limit 5
```

### CLI 组卷

```bash
python extract/exam_cli.py generate \
  --subject math \
  --name "圆专项练习" \
  --tags 圆 \
  --count 5
```

返回 `gen_id`，可用于预览或打印。

### CLI 预览试卷

```bash
python extract/exam_cli.py preview <gen_id>
```

自动打开浏览器，访问 `/api/html/<gen_id>` 查看 A4 排版试卷。

附加参数（直接在浏览器地址栏修改）：
- `?answer=1` — 显示参考答案
- `?solution=1` — 显示解析

---

## API 端点文档

所有端点均需请求头：`Authorization: Bearer <API_KEY>`

### 1. POST `/api/query` — 题目筛选

**请求体**：

```json
{
  "subject": "math",
  "tags": ["圆", "几何"],
  "difficulty": [2, 4],
  "position": ["medium", "advanced"],
  "q_type": ["choice", "calculation"],
  "region": ["北京"],
  "exam_type": ["期中", "期末"],
  "year": [2022, 2024],
  "exclude_tags": ["新定义"],
  "limit": 20,
  "offset": 0
}
```

**响应**（含多维度标签）**：

```json
{
  "count": 5,
  "questions": [
    {
      "question_id": "q_math_2022-2023_北京_期中_a1b2c3d4_3",
      "question_number": 3,
      "q_type": "choice",
      "position": "medium",
      "score": 3,
      "difficulty": 2,
      "content_preview": "如图，AB 是 ⊙O 的直径...",
      "tags": ["圆", "几何", "逻辑推理", "含图表", "数形结合", "中档题"],
      "knowledge_tags": ["圆", "几何"],
      "ability_tags": ["逻辑推理", "空间想象"],
      "feature_tags": ["含图表"],
      "method_tags": ["数形结合"],
      "position_tag": "中档题",
      "images_count": 1,
      "region": "北京",
      "exam_type": "期中",
      "year": 2022,
      "subject": "math"
    }
  ]
}
```

**多维度标签筛选示例**：

```json
{
  "subject": "math",
  "knowledge_tags": ["圆", "二次函数"],
  "ability_tags": ["逻辑推理"],
  "feature_tags": ["含图表", "多步骤"],
  "method_tags": ["数形结合"],
  "position_tag": ["综合题", "压轴题"],
  "difficulty": [3, 5],
  "limit": 10
}
```

### 2. POST `/api/generate` — 组卷生成

**请求体（按规则组卷）**：

```json
{
  "subject": "math",
  "name": "圆专项练习",
  "filters": [
    {
      "q_type": "choice",
      "tags": ["圆"],
      "count": 5,
      "max_per_paper": 2
    },
    {
      "q_type": "calculation",
      "tags": ["圆"],
      "difficulty": [3, 4],
      "count": 3
    }
  ]
}
```

**请求体（按模板组卷）**：

```json
{
  "subject": "math",
  "name": "期中模拟卷",
  "template_id": "tpl-xxx"
}
```

**响应**：

```json
{
  "gen_id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "圆专项练习",
  "subject": "math",
  "total_score": 24,
  "question_count": 8,
  "questions": [...]
}
```

### 3. GET `/api/analytics` — 统计分析

**参数**：
- `subject`（必填）：`math` 或 `physics`
- `type`（必填）：`heatmap` | `trend` | `position_dist` | `tag_tree` | `tag_dimension`
- `dimension`（`type=tag_dimension` 时必填）：`knowledge` | `ability` | `feature` | `method` | `position`
- `region`（可选）：地区筛选
- `year`（可选）：年份筛选

**示例**：

```bash
# 知识点热度图
curl "https://exam-bank.your-subdomain.workers.dev/api/analytics?subject=math&type=heatmap" \
  -H "Authorization: Bearer <API_KEY>"

# 各维度标签分布统计
curl "https://exam-bank.your-subdomain.workers.dev/api/analytics?subject=math&type=tag_dimension&dimension=knowledge" \
  -H "Authorization: Bearer <API_KEY>"
```

**响应**：

```json
{
  "type": "heatmap",
  "subject": "math",
  "tags": [
    {"tag_name": "圆", "count": 142},
    {"tag_name": "二次函数", "count": 98}
  ]
}
```

结果缓存于 KV，TTL 86400 秒。

### 4. GET `/api/html/:gen_id` — 试卷预览

**参数**：
- `answer=1` — 附加参考答案
- `solution=1` — 附加解析

**示例**：

```bash
curl "https://exam-bank.your-subdomain.workers.dev/api/html/<gen_id>?answer=1" \
  -H "Authorization: Bearer <API_KEY>"
```

返回完整 HTML 页面，支持浏览器直接打开与 A4 打印。

### 5. GET `/api/tags?subject=math` — 标签树

**响应**：

```json
{
  "tags": [
    {"tag_id": "math", "tag_name": "数学", "tag_level": 1, "parent_id": null, "tag_path": "math"},
    {"tag_id": "math_geometry", "tag_name": "几何", "tag_level": 2, "parent_id": "math", "tag_path": "math/geometry"},
    {"tag_id": "math_circle", "tag_name": "圆", "tag_level": 3, "parent_id": "math_geometry", "tag_path": "math/geometry/circle"}
  ]
}
```

### 6. POST `/api/template` — 创建组卷模板

**请求体**：

```json
{
  "name": "北京中考标准卷",
  "subject": "math",
  "rules": [
    {"q_type": "choice", "count": 8, "score": [2, 3]},
    {"q_type": "fill", "count": 8, "score": [2, 3]},
    {"q_type": "calculation", "count": 4, "score": [5, 7]}
  ]
}
```

**响应**：

```json
{
  "template_id": "tpl-550e8400-e29b-41d4-a716-446655440000",
  "status": "created"
}
```

---

## 安全说明

### API Key 鉴权

所有端点（含 `OPTIONS` 预检外）强制校验请求头：

```
Authorization: Bearer <API_KEY>
```

未提供或密钥不匹配返回 `401 Unauthorized`。

### CORS

 Workers 默认开启跨域，允许：
- `Access-Control-Allow-Origin: *`
- 方法：`GET, POST, OPTIONS`
- 头部：`Content-Type, Authorization`

### 限流

开启 `RATE_LIMIT_ENABLED=true` 后，基于 KV 实现 IP 级限流：
- 时间窗口：10 分钟
- 单 IP 上限：100 请求 / 窗口
- 超限返回 `429 Too Many Requests`，`Retry-After: 600`

### SQL 注入防护

- **所有 D1 查询使用参数化 SQL**（`?` 占位符 + `.bind(...)`）
- 本地提取脚本的 `sanitize_sql()` 仅用于生成静态 INSERT 文件，运行时无拼接
- 禁止在 Workers 中拼接用户输入到 SQL 字符串

---

## 关键约束

| 资源 | 免费层约束 | 项目应对策略 |
|------|-----------|------------|
| **D1 写入** | 每日限额 | 提取时分批生成 SQL，每批 ≤ 5000 条 INSERT；上传时逐文件执行，失败即停 |
| **D1 读取** | 每日限额 | 统计分析结果缓存于 KV（TTL 86400）；查询返回简化字段，减少行读取 |
| **R2 存储** | 10 GB | 图片强制压缩至单张 < 50KB，总存储控制在 < 8GB |
| **Workers CPU** | 单次请求 CPU 时间受限 | 查询 LIMIT 上限 50；复杂统计预计算并缓存；HTML 生成在边缘完成，无后端计算 |
| **Workers 请求** | 每日 10 万次 | 纯 API 服务，无静态资源托管；本地 CLI 直接操作，减少不必要的 Workers 调用 |
| **KV 读写** | 每日限额 | 仅用于限流计数、统计缓存、配置缓存，不存储业务数据 |

---

## 故障排查

### 1. `wrangler d1 execute` 超时或失败

**现象**：上传 SQL 时命令卡住或报错。

**解决**：
- 检查单文件大小，若过大用 `extract_all.py --batch-size 3000` 重新生成
- 确保已执行 `npx wrangler login` 且令牌未过期
- 使用 `--yes` 参数跳过确认交互

### 2. R2 图片上传失败

**现象**：`upload_to_cf.py` 报 `boto3` 或 `wrangler` 错误。

**解决**：
- 若使用 boto3：确认 `--r2-endpoint`、`--r2-access-key`、`--r2-secret-key` 正确
- 若使用 wrangler：添加 `--use-wrangler-r2`，无需 boto3 密钥
- 检查单张图片是否超过 `--max-image-size-kb`（默认 50KB），超大会自动跳过

### 3. Workers 返回 `401 Unauthorized`

**现象**：CLI 或 curl 请求被拒。

**解决**：
- 确认请求头包含 `Authorization: Bearer <API_KEY>`
- 确认 API Key 与 `wrangler secret put API_KEY` 时输入的一致
- 检查 Workers 域名是否正确（有无拼写错误）

### 4. 题目查询结果为空

**现象**：`query` 或 `generate` 返回 0 条。

**解决**：
- 检查标签拼写是否与 `seed_tags.sql` 一致（如 `"圆"` 而非 `"圆的相关知识"`）
- 检查 `subject` 字段：`math` 或 `physics`，区分大小写
- 确认 D1 中已有数据：`npx wrangler d1 execute exam-bank-db --command "SELECT COUNT(*) FROM questions"`

### 5. 图片在试卷预览中不显示

**现象**：HTML 试卷中图片裂图。

**解决**：
- 确认图片已上传至 R2，且 `images` 字段 JSON 中的路径与 R2 `remote_key` 一致
- 检查 R2 存储桶是否绑定到 Workers（`wrangler.toml` 中 `[[r2_buckets]]`）
- 若通过自定义域名访问 R2，确保 Workers 中图片 URL 拼接正确

### 6. 提取时 PyMuPDF / python-docx 报错

**现象**：`extract_all.py` 提示模块未找到或文件打开失败。

**解决**：
- `pip install PyMuPDF python-docx Pillow`
- 某些 `.doc` 格式（旧版 Word）不被 `python-docx` 支持，建议先转换为 `.docx`
- 扫描版 PDF（纯图片）无法提取文本，需 OCR 预处理

### 7. 多进程提取内存占用过高

**现象**：系统卡顿或进程被杀死。

**解决**：
- 降低 `--max-workers`（如改为 2）
- 降低 `--max-image-width`（如改为 800）减少图片处理内存
- 分批处理：将原始试卷分目录存放，逐目录执行提取

---

## License

MIT
