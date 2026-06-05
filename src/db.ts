export interface Env {
  DB: D1Database;
  BUCKET: R2Bucket;
  KV: KVNamespace;
  API_KEY: string;
  RATE_LIMIT_ENABLED?: string;
  ASSETS?: Fetcher;
}

// 构建动态查询 SQL
export function buildQuerySQL(filters: any): { sql: string; params: any[] } {
  let sql = `SELECT q.question_id, q.question_number, q.paper_id, q.q_type, q.position, q.score, q.difficulty, q.content, q.tags, q.images, p.region, p.exam_type, p.year, p.subject
             FROM questions q
             JOIN papers p ON q.paper_id = p.paper_id
             WHERE 1=1`;
  const params: any[] = [];

  if (filters.subject) {
    sql += " AND p.subject = ?";
    params.push(filters.subject);
  }
  if (filters.q_type?.length) {
    sql += ` AND q.q_type IN (${filters.q_type.map(() => "?").join(",")})`;
    params.push(...filters.q_type);
  }
  if (filters.position?.length) {
    sql += ` AND q.position IN (${filters.position.map(() => "?").join(",")})`;
    params.push(...filters.position);
  }
  if (filters.difficulty?.length === 2) {
    sql += " AND q.difficulty BETWEEN ? AND ?";
    params.push(filters.difficulty[0], filters.difficulty[1]);
  }
  if (filters.score?.length === 2) {
    sql += " AND q.score BETWEEN ? AND ?";
    params.push(filters.score[0], filters.score[1]);
  }
  if (filters.region?.length) {
    sql += ` AND p.region IN (${filters.region.map(() => "?").join(",")})`;
    params.push(...filters.region);
  }
  if (filters.exam_type?.length) {
    sql += ` AND p.exam_type IN (${filters.exam_type.map(() => "?").join(",")})`;
    params.push(...filters.exam_type);
  }
  if (filters.year?.length === 2) {
    sql += " AND p.year BETWEEN ? AND ?";
    params.push(filters.year[0], filters.year[1]);
  }
  // 多维度标签筛选（v2 新增）
  if (filters.knowledge_tags?.length) {
    for (const tag of filters.knowledge_tags) {
      sql += ` AND q.knowledge_tags LIKE ?`;
      params.push(`%"${tag}"%`);
    }
  }
  if (filters.ability_tags?.length) {
    for (const tag of filters.ability_tags) {
      sql += ` AND q.ability_tags LIKE ?`;
      params.push(`%"${tag}"%`);
    }
  }
  if (filters.feature_tags?.length) {
    for (const tag of filters.feature_tags) {
      sql += ` AND q.feature_tags LIKE ?`;
      params.push(`%"${tag}"%`);
    }
  }
  if (filters.method_tags?.length) {
    for (const tag of filters.method_tags) {
      sql += ` AND q.method_tags LIKE ?`;
      params.push(`%"${tag}"%`);
    }
  }
  if (filters.position_tag?.length) {
    sql += ` AND q.position_tag IN (${filters.position_tag.map(() => "?").join(",")})`;
    params.push(...filters.position_tag);
  }

  // 标签筛选：JSON 数组模糊匹配（SQLite json_each 或 LIKE）
  if (filters.tags?.length) {
    for (const tag of filters.tags) {
      sql += ` AND q.tags LIKE ?`;
      params.push(`%"${tag}"%`);
    }
  }
  if (filters.exclude_tags?.length) {
    for (const tag of filters.exclude_tags) {
      sql += ` AND (q.tags IS NULL OR q.tags NOT LIKE ?)`;
      params.push(`%"${tag}"%`);
    }
  }

  // 去重：同一试卷最多取 N 题
  // 注：SQLite 3.25+ 支持 window function，D1 支持情况需测试；如不支持则应用层去重
  sql += " ORDER BY RANDOM()";
  const limit = Math.min(filters.limit || 20, 50);
  sql += " LIMIT ?";
  params.push(limit);
  if (filters.offset) {
    sql += " OFFSET ?";
    params.push(filters.offset);
  }

  return { sql, params };
}

// 简化返回字段，减少 token/体积（保留 options 供前端选择题渲染）
export function simplifyQuestion(row: any): any {
  return {
    question_id: row.question_id,
    question_number: row.question_number,
    q_type: row.q_type,
    position: row.position,
    score: row.score,
    difficulty: row.difficulty,
    content_preview: row.content ? String(row.content).substring(0, 150) + "..." : "",
    tags: row.tags ? JSON.parse(row.tags) : [],
    // 多维度标签（v2）
    knowledge_tags: row.knowledge_tags ? JSON.parse(row.knowledge_tags) : [],
    ability_tags: row.ability_tags ? JSON.parse(row.ability_tags) : [],
    feature_tags: row.feature_tags ? JSON.parse(row.feature_tags) : [],
    method_tags: row.method_tags ? JSON.parse(row.method_tags) : [],
    position_tag: row.position_tag || "",
    images_count: row.images ? JSON.parse(row.images).length : 0,
    options: row.options ? JSON.parse(row.options) : null,
    answer: row.answer || null,
    region: row.region,
    exam_type: row.exam_type,
    year: row.year,
    subject: row.subject
  };
}

// 获取单题详情（完整字段，含答案/解析/选项）
export async function getQuestionById(db: D1Database, question_id: string): Promise<any | null> {
  const row = await db.prepare(
    `SELECT q.*, p.region, p.exam_type, p.year, p.subject, p.title as paper_title, p.district, p.school, p.round
     FROM questions q JOIN papers p ON q.paper_id = p.paper_id
     WHERE q.question_id = ?`
  ).bind(question_id).first();
  if (!row) return null;
  return {
    ...row,
    tags: row.tags ? JSON.parse(String(row.tags)) : [],
    images: row.images ? JSON.parse(String(row.images)) : [],
    options: row.options ? JSON.parse(String(row.options)) : null,
    data_table: row.data_table ? JSON.parse(String(row.data_table)) : null,
    knowledge_tags: row.knowledge_tags ? JSON.parse(String(row.knowledge_tags)) : [],
    ability_tags: row.ability_tags ? JSON.parse(String(row.ability_tags)) : [],
    feature_tags: row.feature_tags ? JSON.parse(String(row.feature_tags)) : [],
    method_tags: row.method_tags ? JSON.parse(String(row.method_tags)) : [],
  };
}

// 获取单题详情
export async function getQuestionDetail(db: D1Database, question_id: string): Promise<any | null> {
  const row = await db.prepare(
    `SELECT q.*, p.region, p.exam_type, p.year, p.subject, p.title
     FROM questions q JOIN papers p ON q.paper_id = p.paper_id
     WHERE q.question_id = ?`
  ).bind(question_id).first();
  if (!row) return null;
  return {
    ...row,
    tags: row.tags ? JSON.parse(String(row.tags)) : [],
    images: row.images ? JSON.parse(String(row.images)) : [],
    options: row.options ? JSON.parse(String(row.options)) : null,
    data_table: row.data_table ? JSON.parse(String(row.data_table)) : null
  };
}

// 获取试卷及题目列表
export async function getPaperWithQuestions(db: D1Database, paper_id: string): Promise<any> {
  const paper = await db.prepare("SELECT * FROM papers WHERE paper_id = ?").bind(paper_id).first();
  if (!paper) return null;
  const questions = await db.prepare(
    `SELECT question_id, question_number, q_type, position, score, difficulty, content, tags, images
     FROM questions WHERE paper_id = ? ORDER BY CAST(question_number AS INTEGER)`
  ).bind(paper_id).all();
  return { paper, questions: questions.results };
}

// 分页列表试卷（支持学科/年份/地区/考试类型筛选）
export async function listPapers(
  db: D1Database,
  filters: {
    subject?: string;
    year?: number;
    region?: string;
    exam_type?: string;
    district?: string;
    school?: string;
    round?: string;
    limit?: number;
    offset?: number;
  }
): Promise<{ papers: any[]; total: number }> {
  let whereSql = "WHERE 1=1";
  const params: any[] = [];

  if (filters.subject) {
    whereSql += " AND subject = ?";
    params.push(filters.subject);
  }
  if (filters.year) {
    whereSql += " AND year = ?";
    params.push(filters.year);
  }
  if (filters.region) {
    whereSql += " AND region = ?";
    params.push(filters.region);
  }
  if (filters.exam_type) {
    whereSql += " AND exam_type = ?";
    params.push(filters.exam_type);
  }
  if (filters.district) {
    whereSql += " AND district = ?";
    params.push(filters.district);
  }
  if (filters.school) {
    whereSql += " AND school = ?";
    params.push(filters.school);
  }
  if (filters.round) {
    whereSql += " AND round = ?";
    params.push(filters.round);
  }

  // 统计总数
  const countResult = await db.prepare(`SELECT COUNT(*) as total FROM papers ${whereSql}`).bind(...params).first();
  const total = (countResult?.total as number) || 0;

  // 分页查询
  const limit = Math.min(Math.max(filters.limit || 20, 1), 100);
  const offset = Math.max(filters.offset || 0, 0);
  const papers = await db.prepare(
    `SELECT * FROM papers ${whereSql} ORDER BY year DESC, title LIMIT ? OFFSET ?`
  ).bind(...params, limit, offset).all();

  return { papers: papers.results || [], total };
}

// 获取标签树
export async function getTagTree(db: D1Database, subject: string): Promise<any[]> {
  const rows = await db.prepare(
    "SELECT tag_id, tag_name, tag_level, parent_id, tag_path FROM tags WHERE subject = ? ORDER BY tag_path"
  ).bind(subject).all();
  return rows.results as any[];
}

// 保存生成记录
export async function saveGenerated(db: D1Database, gen_id: string, template_id: string | null, name: string, subject: string, total_score: number, question_ids: string[]): Promise<void> {
  await db.prepare(
    `INSERT INTO generated (gen_id, template_id, name, subject, total_score, question_ids) VALUES (?, ?, ?, ?, ?, ?)`
  ).bind(gen_id, template_id, name, subject, total_score, JSON.stringify(question_ids)).run();
}

// 获取生成记录
export async function getGenerated(db: D1Database, gen_id: string): Promise<any | null> {
  const row = await db.prepare("SELECT * FROM generated WHERE gen_id = ?").bind(gen_id).first();
  if (!row) return null;
  return { ...row, question_ids: row.question_ids ? JSON.parse(String(row.question_ids)) : [] };
}

// 全文搜索（基于内容 LIKE 匹配 + 标签筛选）
export async function fullTextSearch(db: D1Database, query: string, filters: any): Promise<any[]> {
  const keywords = query.trim().split(/\s+/).filter(k => k.length >= 2);
  if (keywords.length === 0) return [];

  let sql = `SELECT q.question_id, q.question_number, q.paper_id, q.q_type, q.position, q.score, q.difficulty, q.content, q.tags, q.images, q.knowledge_tags, q.ability_tags, q.feature_tags, q.method_tags, q.position_tag, p.region, p.exam_type, p.year, p.subject, p.title as paper_title
             FROM questions q
             JOIN papers p ON q.paper_id = p.paper_id
             WHERE 1=1`;
  const params: any[] = [];

  // 关键词匹配（内容或标签）
  for (const kw of keywords) {
    sql += ` AND (q.content LIKE ? OR q.knowledge_tags LIKE ? OR q.ability_tags LIKE ? OR q.feature_tags LIKE ?)`;
    const pattern = `%${kw}%`;
    params.push(pattern, pattern, pattern, pattern);
  }

  // 附加筛选
  if (filters.subject) {
    sql += " AND p.subject = ?";
    params.push(filters.subject);
  }
  if (filters.q_type?.length) {
    sql += ` AND q.q_type IN (${filters.q_type.map(() => "?").join(",")})`;
    params.push(...filters.q_type);
  }
  if (filters.difficulty?.length === 2) {
    sql += " AND q.difficulty BETWEEN ? AND ?";
    params.push(filters.difficulty[0], filters.difficulty[1]);
  }

  sql += " ORDER BY q.difficulty DESC, RANDOM()";
  const limit = Math.min(filters.limit || 20, 50);
  sql += " LIMIT ?";
  params.push(limit);

  const result = await db.prepare(sql).bind(...params).all();
  return result.results as any[];
}

// 相似题目推荐（基于 5 维度标签加权相似度）
// Issue #5: 权重设计 knowledge(0.4) > ability(0.2) > method(0.2) > feature(0.1) > position(0.1)
const SIMILARITY_WEIGHTS = {
  knowledge: 0.4,
  ability: 0.2,
  method: 0.2,
  feature: 0.1,
  position: 0.1,
};

function jaccardSimilarity(setA: string[], setB: string[]): number {
  if (setA.length === 0 && setB.length === 0) return 0;
  const intersection = setA.filter(x => setB.includes(x));
  const union = new Set([...setA, ...setB]);
  return union.size === 0 ? 0 : intersection.length / union.size;
}

function computeWeightedSimilarity(
  source: Record<string, any>,
  target: Record<string, any>
): number {
  const sKnowledge = source.knowledge_tags ? JSON.parse(String(source.knowledge_tags)) : [];
  const sAbility = source.ability_tags ? JSON.parse(String(source.ability_tags)) : [];
  const sMethod = source.method_tags ? JSON.parse(String(source.method_tags)) : [];
  const sFeature = source.feature_tags ? JSON.parse(String(source.feature_tags)) : [];
  const sPosition = source.position_tag ? [String(source.position_tag)] : [];

  const tKnowledge = target.knowledge_tags ? JSON.parse(String(target.knowledge_tags)) : [];
  const tAbility = target.ability_tags ? JSON.parse(String(target.ability_tags)) : [];
  const tMethod = target.method_tags ? JSON.parse(String(target.method_tags)) : [];
  const tFeature = target.feature_tags ? JSON.parse(String(target.feature_tags)) : [];
  const tPosition = target.position_tag ? [String(target.position_tag)] : [];

  const score =
    SIMILARITY_WEIGHTS.knowledge * jaccardSimilarity(sKnowledge, tKnowledge) +
    SIMILARITY_WEIGHTS.ability * jaccardSimilarity(sAbility, tAbility) +
    SIMILARITY_WEIGHTS.method * jaccardSimilarity(sMethod, tMethod) +
    SIMILARITY_WEIGHTS.feature * jaccardSimilarity(sFeature, tFeature) +
    SIMILARITY_WEIGHTS.position * jaccardSimilarity(sPosition, tPosition);

  return Math.round(score * 100) / 100; // 保留两位小数
}

export async function getSimilarQuestions(db: D1Database, question_id: string, limit: number = 5, subjectFilter?: string): Promise<any[]> {
  // 获取源题信息
  const source = await db.prepare(
    `SELECT q.*, p.subject FROM questions q JOIN papers p ON q.paper_id = p.paper_id WHERE q.question_id = ?`
  ).bind(question_id).first();
  if (!source) return [];

  const subject = subjectFilter || (source.subject as string);
  const paperId = source.paper_id as string;
  const qType = source.q_type as string;

  // 查询同学科、同题型、不同试卷的候选题目（扩大候选池到 limit*3）
  const candidateLimit = Math.min(limit * 3, 100);
  const result = await db.prepare(
    `SELECT q.question_id, q.question_number, q.paper_id, q.q_type, q.position, q.score, q.difficulty, q.content, q.tags, q.images, q.knowledge_tags, q.ability_tags, q.feature_tags, q.method_tags, q.position_tag, p.region, p.exam_type, p.year, p.subject, p.district, p.school
     FROM questions q
     JOIN papers p ON q.paper_id = p.paper_id
     WHERE q.question_id != ? AND p.subject = ? AND q.q_type = ? AND q.paper_id != ?
     ORDER BY RANDOM()
     LIMIT ?`
  ).bind(question_id, subject, qType, paperId, candidateLimit).all();

  const candidates = result.results || [];

  // 计算加权相似度并排序
  const scored = candidates.map((row: any) => {
    const score = computeWeightedSimilarity(source, row);
    return { ...row, similarity_score: score };
  });

  scored.sort((a: any, b: any) => b.similarity_score - a.similarity_score);

  return scored.slice(0, limit);
}

// 标签自动补全（Issue #7）
// 返回标签使用频率，支持 KV 缓存
export async function suggestTags(
  db: D1Database,
  kv: KVNamespace,
  subject: string,
  query: string,
  dimension: string = "knowledge",
  limit: number = 10
): Promise<any[]> {
  const cacheKey = `tags:suggest:${subject}:${dimension}:${query}`;
  const cached = await kv.get(cacheKey);
  if (cached) {
    const parsed = JSON.parse(cached);
    return parsed.slice(0, limit);
  }

  const dimColumn = {
    "knowledge": "knowledge_tags",
    "ability": "ability_tags",
    "feature": "feature_tags",
    "method": "method_tags",
    "position": "position_tag",
  }[dimension] || "knowledge_tags";

  // 从 questions 表中提取标签并匹配前缀
  const pattern = `%${query}%`;
  const sql = `
    SELECT ${dimColumn} as tag_value
    FROM questions q
    JOIN papers p ON q.paper_id = p.paper_id
    WHERE p.subject = ? AND q.${dimColumn} LIKE ? AND q.${dimColumn} IS NOT NULL AND q.${dimColumn} != '[]'
    LIMIT 200
  `;

  const result = await db.prepare(sql).bind(subject, pattern).all();

  // 提取标签并统计频率
  const tagCounter = new Map<string, number>();
  for (const row of (result.results || []) as any[]) {
    const val = row.tag_value;
    if (!val) continue;
    try {
      const tags = dimension === "position" ? [val] : JSON.parse(val);
      if (Array.isArray(tags)) {
        for (const t of tags) {
          if (t && t.toLowerCase().includes(query.toLowerCase()) && t !== "未分类") {
            tagCounter.set(t, (tagCounter.get(t) || 0) + 1);
          }
        }
      }
    } catch {
      if (val && val.toLowerCase().includes(query.toLowerCase()) && val !== "未分类") {
        tagCounter.set(val, (tagCounter.get(val) || 0) + 1);
      }
    }
  }

  // 按频率排序
  const sorted = Array.from(tagCounter.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, Math.min(limit, 20))
    .map(([tag_name, count]) => ({ tag_name, count, dimension }));

  // 缓存结果
  await kv.put(cacheKey, JSON.stringify(sorted), { expirationTtl: 86400 });

  return sorted;
}
