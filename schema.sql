-- 试卷表
CREATE TABLE IF NOT EXISTS papers (
    paper_id TEXT PRIMARY KEY,
    title TEXT,
    subject TEXT CHECK(subject IN ('math','physics')),
    region TEXT,
    district TEXT,        -- 区名，如"海淀"
    school TEXT,          -- 学校名，如"北京四中"
    exam_type TEXT,
    round TEXT,           -- 考试轮次：一模/二模/三模/真题/期中/期末/月考
    year INTEGER,
    total_score INTEGER DEFAULT 100,
    question_count INTEGER,
    metadata TEXT
);

-- 题目表（核心）
CREATE TABLE IF NOT EXISTS questions (
    question_id TEXT PRIMARY KEY,
    paper_id TEXT REFERENCES papers(paper_id),
    parent_question_id TEXT REFERENCES questions(question_id),  -- 子题关联父题
    question_number TEXT NOT NULL,
    q_type TEXT CHECK(q_type IN ('choice','fill','calculation','proof','experiment','reading','comprehensive')),
    position TEXT CHECK(position IN ('basic','medium','comprehensive','advanced')),
    score INTEGER DEFAULT 0,
    difficulty INTEGER CHECK(difficulty BETWEEN 1 AND 5),
    content TEXT,
    options TEXT,
    answer TEXT,
    solution TEXT,
    tags TEXT,                    -- 合并后的所有标签 JSON 数组
    images TEXT,
    data_table TEXT,
    estimated_time INTEGER,
    -- 多维度标签（v2 新增）
    knowledge_tags TEXT,        -- 知识点标签 JSON: ["圆","几何"]
    ability_tags TEXT,          -- 能力维度标签 JSON: ["逻辑推理","空间想象"]
    feature_tags TEXT,            -- 题型特征标签 JSON: ["含图表","多步骤"]
    method_tags TEXT,           -- 解题方法标签 JSON: ["数形结合"]
    position_tag TEXT,          -- 考试定位标签: "压轴题"
    fts_content TEXT GENERATED ALWAYS AS (
        COALESCE(content, '') || ' ' || COALESCE(answer, '')
    ) STORED
);

-- FTS5 全文搜索（D1 原生支持）
CREATE VIRTUAL TABLE IF NOT EXISTS question_search USING fts5(
    fts_content,
    content='questions',
    content_rowid='rowid'
);

-- 标签体系
CREATE TABLE IF NOT EXISTS tags (
    tag_id TEXT PRIMARY KEY,
    tag_name TEXT NOT NULL,
    subject TEXT,
    tag_level INTEGER CHECK(tag_level BETWEEN 1 AND 3),
    parent_id TEXT,
    tag_path TEXT
);

CREATE TABLE IF NOT EXISTS question_tags (
    question_id TEXT,
    tag_id TEXT,
    PRIMARY KEY (question_id, tag_id)
);

-- 组卷模板
CREATE TABLE IF NOT EXISTS templates (
    template_id TEXT PRIMARY KEY,
    name TEXT,
    subject TEXT,
    target_score INTEGER DEFAULT 100,
    rules TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 生成记录
CREATE TABLE IF NOT EXISTS generated (
    gen_id TEXT PRIMARY KEY,
    template_id TEXT,
    name TEXT,
    subject TEXT,
    total_score INTEGER,
    question_ids TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
