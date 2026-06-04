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

// 简化返回字段，减少 token/体积
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
    region: row.region,
    exam_type: row.exam_type,
    year: row.year,
    subject: row.subject
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
    tags: row.tags ? JSON.parse(row.tags) : [],
    images: row.images ? JSON.parse(row.images) : [],
    options: row.options ? JSON.parse(row.options) : null,
    data_table: row.data_table ? JSON.parse(row.data_table) : null
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
  return { ...row, question_ids: row.question_ids ? JSON.parse(row.question_ids) : [] };
}
