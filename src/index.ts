import { Env, buildQuerySQL, simplifyQuestion, getQuestionDetail, getTagTree, saveGenerated, getGenerated, fullTextSearch, getSimilarQuestions, suggestTags, listPapers, getQuestionById } from "./db";
import { generateExamHTML } from "./html";

// ===== 安全中间件 =====
async function securityCheck(req: Request, env: Env): Promise<Response | null> {
  if (req.method === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization"
      }
    });
  }

  const auth = req.headers.get("Authorization");
  if (!auth || auth !== `Bearer ${env.API_KEY}`) {
    return jsonResponse({ error: "Unauthorized", code: 401 }, 401);
  }

  if (env.RATE_LIMIT_ENABLED === "true") {
    const limitResp = await rateLimit(req, env);
    if (limitResp) return limitResp;
  }

  return null;
}

async function rateLimit(req: Request, env: Env): Promise<Response | null> {
  const ip = req.headers.get("CF-Connecting-IP") || "unknown";
  const window = Math.floor(Date.now() / 600000); // 10分钟窗口
  const key = `rl:${ip}:${window}`;
  const current = await env.KV.get(key);
  const count = current ? parseInt(current) : 0;
  if (count > 100) {
    return jsonResponse({ error: "Too many requests", retry_after: 600 }, 429);
  }
  await env.KV.put(key, String(count + 1), { expirationTtl: 600 });
  return null;
}

function jsonResponse(data: any, status: number = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*"
    }
  });
}

// ===== 端点实现 =====

// 1. POST /api/query — 题目筛选
async function handleQuery(req: Request, env: Env): Promise<Response> {
  const body = await req.json();
  const { sql, params } = buildQuerySQL(body);
  const result = await env.DB.prepare(sql).bind(...params).all();
  const simplified = (result.results || []).map(simplifyQuestion);
  return jsonResponse({ count: simplified.length, questions: simplified });
}

// 2. POST /api/generate — 组卷生成
async function handleGenerate(req: Request, env: Env): Promise<Response> {
  const body = await req.json() as any;
  const { subject, name, filters, template_id } = body;
  if (!subject || !name) return jsonResponse({ error: "Missing subject or name" }, 400);

  let rules: any[] = [];
  if (template_id) {
    const tpl = await env.DB.prepare("SELECT rules FROM templates WHERE template_id = ?").bind(template_id).first();
    if (!tpl) return jsonResponse({ error: "Template not found" }, 404);
    rules = JSON.parse(tpl.rules as string || "[]");
  } else if (filters && Array.isArray(filters)) {
    rules = filters;
  } else {
    return jsonResponse({ error: "Missing filters or template_id" }, 400);
  }

  const selectedIds: string[] = [];
  const selectedDetails: any[] = [];
  const paperCount = new Map<string, number>();

  for (const rule of rules) {
    const { sql, params } = buildQuerySQL({
      subject,
      q_type: rule.q_type,
      position: rule.position,
      difficulty: rule.difficulty,
      score: rule.score,
      tags: rule.tags,
      exclude_tags: rule.exclude_tags,
      region: rule.region,
      exam_type: rule.exam_type,
      year: rule.year,
      // 多维度标签支持（v3 新增）
      knowledge_tags: rule.knowledge_tags,
      ability_tags: rule.ability_tags,
      feature_tags: rule.feature_tags,
      method_tags: rule.method_tags,
      position_tag: rule.position_tag,
      limit: rule.count || 5
    });
    const result = await env.DB.prepare(sql).bind(...params).all();
    const rows = result.results || [];

    for (const row of rows as any[]) {
      if (selectedIds.includes(row.question_id)) continue;
      const pid = row.paper_id as string;
      if ((paperCount.get(pid) || 0) >= (rule.max_per_paper || 2)) continue;

      selectedIds.push(row.question_id);
      paperCount.set(pid, (paperCount.get(pid) || 0) + 1);
      selectedDetails.push(simplifyQuestion(row));
      if (selectedDetails.length >= (rule.total_count || rule.count || 5)) break;
    }
  }

  const genId = crypto.randomUUID();
  const totalScore = selectedDetails.reduce((s, q) => s + (q.score || 0), 0);
  await saveGenerated(env.DB, genId, template_id || null, name, subject, totalScore, selectedIds);

  return jsonResponse({
    gen_id: genId,
    name,
    subject,
    total_score: totalScore,
    question_count: selectedIds.length,
    questions: selectedDetails
  });
}

// 3. GET /api/analytics — 统计分析
async function handleAnalytics(req: Request, env: Env): Promise<Response> {
  const url = new URL(req.url);
  const subject = url.searchParams.get("subject");
  const type = url.searchParams.get("type");
  const region = url.searchParams.get("region");
  const year = url.searchParams.get("year");

  if (!subject || !type) return jsonResponse({ error: "Missing subject or type" }, 400);

  const cacheKey = `analytics:${type}:${subject}:${region || 'all'}:${year || 'all'}`;
  const cached = await env.KV.get(cacheKey);
  if (cached) return jsonResponse(JSON.parse(cached));

  let result: any;
  if (type === "heatmap") {
    const data = await env.DB.prepare(`
      SELECT t.tag_name, COUNT(*) as count
      FROM question_tags qt
      JOIN tags t ON qt.tag_id = t.tag_id
      JOIN questions q ON qt.question_id = q.question_id
      JOIN papers p ON q.paper_id = p.paper_id
      WHERE t.subject = ? AND t.tag_level >= 3
      ${region ? "AND p.region = ?" : ""}
      ${year ? "AND p.year = ?" : ""}
      GROUP BY t.tag_id
      ORDER BY count DESC
      LIMIT 20
    `).bind(subject, ...(region ? [region] : []), ...(year ? [parseInt(year)] : [])).all();
    result = { type: "heatmap", subject, tags: data.results };
  } else if (type === "trend") {
    const data = await env.DB.prepare(`
      SELECT p.year, p.exam_type, t.tag_name, COUNT(*) as count
      FROM question_tags qt
      JOIN tags t ON qt.tag_id = t.tag_id
      JOIN questions q ON qt.question_id = q.question_id
      JOIN papers p ON q.paper_id = p.paper_id
      WHERE t.subject = ? AND t.tag_level >= 3
      GROUP BY p.year, p.exam_type, t.tag_id
      ORDER BY p.year DESC, count DESC
    `).bind(subject).all();
    result = { type: "trend", subject, data: data.results };
  } else if (type === "position_dist") {
    const data = await env.DB.prepare(`
      SELECT position, difficulty, COUNT(*) as count, AVG(score) as avg_score
      FROM questions q
      JOIN papers p ON q.paper_id = p.paper_id
      WHERE p.subject = ?
      GROUP BY position, difficulty
      ORDER BY position, difficulty
    `).bind(subject).all();
    result = { type: "position_dist", subject, distribution: data.results };
  } else if (type === "tag_tree") {
    result = { type: "tag_tree", subject, tags: await getTagTree(env.DB, subject) };
  } else if (type === "tag_dimension") {
    const dimension = url.searchParams.get("dimension") || "knowledge";
    const dimColumn = {
      "knowledge": "knowledge_tags",
      "ability": "ability_tags",
      "feature": "feature_tags",
      "method": "method_tags",
      "position": "position_tag",
    }[dimension] || "knowledge_tags";
    
    // 解析 JSON 数组字段并统计
    const data = await env.DB.prepare(`
      SELECT ${dimColumn} as dim_value, COUNT(*) as count
      FROM questions q
      JOIN papers p ON q.paper_id = p.paper_id
      WHERE p.subject = ? AND q.${dimColumn} IS NOT NULL AND q.${dimColumn} != '[]'
      GROUP BY ${dimColumn}
      ORDER BY count DESC
      LIMIT 30
    `).bind(subject).all();
    
    // 展开 JSON 数组统计
    const tagCounter = new Map<string, number>();
    for (const row of (data.results || []) as any[]) {
      const val = row.dim_value;
      if (!val) continue;
      try {
        const tags = dimension === "position" ? [val] : JSON.parse(val);
        if (Array.isArray(tags)) {
          for (const t of tags) {
            if (t && t !== "未分类") {
              tagCounter.set(t, (tagCounter.get(t) || 0) + row.count);
            }
          }
        }
      } catch {
        if (val && val !== "未分类") {
          tagCounter.set(val, (tagCounter.get(val) || 0) + row.count);
        }
      }
    }
    
    const sorted = Array.from(tagCounter.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 20)
      .map(([tag_name, count]) => ({ tag_name, count }));
    
    result = { type: "tag_dimension", subject, dimension, tags: sorted };
  } else {
    return jsonResponse({ error: "Unknown analytics type" }, 400);
  }

  const text = JSON.stringify(result);
  await env.KV.put(cacheKey, text, { expirationTtl: 86400 });
  return jsonResponse(result);
}

// 4. GET /api/html/:gen_id — 试卷预览
async function handleHTML(req: Request, env: Env): Promise<Response> {
  const url = new URL(req.url);
  const genId = url.pathname.split("/").pop() || "";
  if (!genId) return jsonResponse({ error: "Missing gen_id" }, 400);

  const gen = await getGenerated(env.DB, genId);
  if (!gen) return jsonResponse({ error: "Generated paper not found" }, 404);

  const qIds = gen.question_ids || [];
  if (qIds.length === 0) return jsonResponse({ error: "No questions" }, 404);

  const placeholders = qIds.map(() => "?").join(",");
  const questions = await env.DB.prepare(
    `SELECT * FROM questions WHERE question_id IN (${placeholders})`
  ).bind(...qIds).all();

  const qMap = new Map((questions.results || []).map((q: any) => [q.question_id, q]));
  const ordered = qIds.map((id: string) => qMap.get(id)).filter(Boolean);

  const includeAnswer = url.searchParams.get("answer") === "1";
  const includeSolution = url.searchParams.get("solution") === "1";
  const html = generateExamHTML(gen, ordered, includeAnswer, includeSolution);

  return new Response(html, {
    status: 200,
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Access-Control-Allow-Origin": "*"
    }
  });
}

// 5. POST /api/search — 全文搜索
async function handleSearch(req: Request, env: Env): Promise<Response> {
  const body = await req.json() as any;
  const { query, subject, q_type, difficulty, limit = 20 } = body;

  if (!query || typeof query !== "string" || query.trim().length === 0) {
    return jsonResponse({ error: "Missing or empty query" }, 400);
  }

  const results = await fullTextSearch(env.DB, query, {
    subject,
    q_type,
    difficulty,
    limit: Math.min(limit, 50),
  });

  return jsonResponse({
    query: query.trim(),
    count: results.length,
    questions: results.map(simplifyQuestion),
  });
}

// 6. GET /api/similar/:question_id — 相似题目推荐 (Issue #5)
async function handleSimilar(req: Request, env: Env): Promise<Response> {
  const url = new URL(req.url);
  const questionId = url.pathname.split("/").pop() || "";
  if (!questionId) return jsonResponse({ error: "Missing question_id" }, 400);

  const limit = parseInt(url.searchParams.get("limit") || "5");
  const subject = url.searchParams.get("subject") || undefined;
  const results = await getSimilarQuestions(env.DB, questionId, Math.min(limit, 20), subject);

  return jsonResponse({
    question_id: questionId,
    count: results.length,
    similar: results.map((q: any) => ({
      question_id: q.question_id,
      similarity_score: q.similarity_score,
      content_preview: q.content ? String(q.content).substring(0, 150) + "..." : "",
      q_type: q.q_type,
      position: q.position,
      score: q.score,
      difficulty: q.difficulty,
      knowledge_tags: q.knowledge_tags ? JSON.parse(q.knowledge_tags) : [],
      ability_tags: q.ability_tags ? JSON.parse(q.ability_tags) : [],
      feature_tags: q.feature_tags ? JSON.parse(q.feature_tags) : [],
      method_tags: q.method_tags ? JSON.parse(q.method_tags) : [],
      position_tag: q.position_tag || "",
      region: q.region,
      exam_type: q.exam_type,
      year: q.year,
      subject: q.subject,
    })),
  });
}

// 7. GET /api/paper/:paper_id — 试卷详情 (Issue #6)
async function handlePaper(req: Request, env: Env): Promise<Response> {
  const url = new URL(req.url);
  const paperId = url.pathname.split("/").pop() || "";
  if (!paperId) return jsonResponse({ error: "Missing paper_id" }, 400);

  const paper = await env.DB.prepare("SELECT * FROM papers WHERE paper_id = ?").bind(paperId).first();
  if (!paper) return jsonResponse({ error: "Paper not found" }, 404);

  // 分页参数
  const page = parseInt(url.searchParams.get("page") || "1");
  const pageSize = parseInt(url.searchParams.get("page_size") || "50");
  const safePageSize = Math.min(Math.max(pageSize, 1), 100);
  const offset = (Math.max(page, 1) - 1) * safePageSize;

  // 先统计总题数
  const countResult = await env.DB.prepare(
    "SELECT COUNT(*) as total FROM questions WHERE paper_id = ?"
  ).bind(paperId).first();
  const totalQuestions = (countResult?.total as number) || 0;

  // 分页查询题目
  const questions = await env.DB.prepare(
    `SELECT question_id, question_number, q_type, position, score, difficulty, content, tags, images, knowledge_tags, ability_tags, feature_tags, method_tags, position_tag
     FROM questions WHERE paper_id = ? ORDER BY CAST(question_number AS INTEGER) LIMIT ? OFFSET ?`
  ).bind(paperId, safePageSize, offset).all();

  const questionList = (questions.results || []).map((q: any) => ({
    ...q,
    tags: q.tags ? JSON.parse(q.tags) : [],
    images: q.images ? JSON.parse(q.images) : [],
    knowledge_tags: q.knowledge_tags ? JSON.parse(q.knowledge_tags) : [],
    ability_tags: q.ability_tags ? JSON.parse(q.ability_tags) : [],
    feature_tags: q.feature_tags ? JSON.parse(q.feature_tags) : [],
    method_tags: q.method_tags ? JSON.parse(q.method_tags) : [],
  }));

  return jsonResponse({
    paper,
    question_count: totalQuestions,
    page,
    page_size: safePageSize,
    total_pages: Math.ceil(totalQuestions / safePageSize),
    questions: questionList,
  });
}

// 8. GET /api/tags/suggest — 标签自动补全 (Issue #7)
async function handleTagSuggest(req: Request, env: Env): Promise<Response> {
  const url = new URL(req.url);
  const subject = url.searchParams.get("subject");
  const query = url.searchParams.get("q");
  const dimension = url.searchParams.get("dimension") || "knowledge";
  const limit = parseInt(url.searchParams.get("limit") || "10");

  if (!subject) return jsonResponse({ error: "Missing subject" }, 400);
  if (!query || query.length < 1) return jsonResponse({ error: "Missing query (min 1 char)" }, 400);

  const suggestions = await suggestTags(env.DB, env.KV, subject, query, dimension, Math.min(limit, 20));

  return jsonResponse({
    subject,
    query,
    dimension,
    count: suggestions.length,
    suggestions,
  });
}

// 9. GET /api/question/:id — 单题完整详情
async function handleQuestion(req: Request, env: Env): Promise<Response> {
  const url = new URL(req.url);
  const questionId = url.pathname.split("/").pop() || "";
  if (!questionId) return jsonResponse({ error: "Missing question_id" }, 400);

  const question = await getQuestionById(env.DB, questionId);
  if (!question) return jsonResponse({ error: "Question not found" }, 404);

  return jsonResponse({ question });
}

// 10. GET /api/papers — 试卷分页列表
async function handlePapersList(req: Request, env: Env): Promise<Response> {
  const url = new URL(req.url);
  const subject = url.searchParams.get("subject") || undefined;
  const year = url.searchParams.get("year") ? parseInt(url.searchParams.get("year")!) : undefined;
  const region = url.searchParams.get("region") || undefined;
  const exam_type = url.searchParams.get("exam_type") || undefined;
  const district = url.searchParams.get("district") || undefined;
  const school = url.searchParams.get("school") || undefined;
  const round = url.searchParams.get("round") || undefined;
  const limit = parseInt(url.searchParams.get("limit") || "20");
  const offset = parseInt(url.searchParams.get("offset") || "0");

  const { papers, total } = await listPapers(env.DB, {
    subject, year, region, exam_type, district, school, round, limit, offset,
  });

  return jsonResponse({
    papers,
    total,
    limit,
    offset,
    has_more: offset + papers.length < total,
  });
}

// ===== 主入口 =====
export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const sec = await securityCheck(req, env);
    if (sec) return sec;

    const url = new URL(req.url);
    const path = url.pathname;

    try {
      if (path === "/api/query" && req.method === "POST") {
        return await handleQuery(req, env);
      }
      if (path === "/api/generate" && req.method === "POST") {
        return await handleGenerate(req, env);
      }
      if (path === "/api/analytics" && req.method === "GET") {
        return await handleAnalytics(req, env);
      }
      if (path.startsWith("/api/html/") && req.method === "GET") {
        return await handleHTML(req, env);
      }
      if (path === "/api/search" && req.method === "POST") {
        return await handleSearch(req, env);
      }
      if (path.startsWith("/api/question/") && req.method === "GET") {
        return await handleQuestion(req, env);
      }
      if (path === "/api/papers" && req.method === "GET") {
        return await handlePapersList(req, env);
      }
      if (path.startsWith("/api/similar/") && req.method === "GET") {
        return await handleSimilar(req, env);
      }
      if (path.startsWith("/api/paper/") && req.method === "GET") {
        return await handlePaper(req, env);
      }
      if (path === "/api/tags/suggest" && req.method === "GET") {
        return await handleTagSuggest(req, env);
      }
      if (path === "/api/tags" && req.method === "GET") {
        const subject = url.searchParams.get("subject");
        if (!subject) return jsonResponse({ error: "Missing subject" }, 400);
        return jsonResponse({ tags: await getTagTree(env.DB, subject) });
      }
      if (path === "/api/template" && req.method === "POST") {
        const body = await req.json() as any;
        const { name, subject, rules } = body;
        if (!name || !subject || !rules) return jsonResponse({ error: "Missing fields" }, 400);
        const tid = crypto.randomUUID();
        await env.DB.prepare(
          `INSERT INTO templates (template_id, name, subject, rules) VALUES (?, ?, ?, ?)`
        ).bind(tid, name, subject, JSON.stringify(rules)).run();
        return jsonResponse({ template_id: tid, status: "created" });
      }

      return jsonResponse({ error: "Not Found", path }, 404);
    } catch (err: any) {
      // 生产环境不返回堆栈信息，避免泄露敏感细节
      console.error("[API Error]", err);
      return jsonResponse({ error: err.message || "Internal Error" }, 500);
    }
  }
};
