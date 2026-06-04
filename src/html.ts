export function generateExamHTML(gen: any, questions: any[], includeAnswer: boolean = false, includeSolution: boolean = false): string {
  const qList = questions.map((q, idx) => {
    const num = idx + 1;
    const tags = q.tags ? (Array.isArray(q.tags) ? q.tags : JSON.parse(q.tags)) : [];
    const images = q.images ? (Array.isArray(q.images) ? q.images : JSON.parse(q.images)) : [];
    const options = q.options ? (Array.isArray(q.options) ? q.options : JSON.parse(q.options)) : null;
    
    let optionsHtml = "";
    if (options && options.length > 0) {
      optionsHtml = `<div class="options">${options.map((o: any) => `<p><b>${o.label}.</b> ${o.text}</p>`).join("")}</div>`;
    }

    let imagesHtml = "";
    if (images.length > 0) {
      imagesHtml = `<div class="images">${images.map((url: string) => `<img src="${url}" alt="图" />`).join("")}</div>`;
    }

    return `
<div class="question" data-score="${q.score || 0}" data-type="${q.q_type || ''}">
  <div class="q-meta">
    <span class="badge">${q.q_type || ''}</span>
    <span class="badge">分值:${q.score || 0}分</span>
    <span class="badge">难度:${"⭐".repeat(q.difficulty || 1)}</span>
    ${tags.map((t: string) => `<span class="tag">${t}</span>`).join("")}
  </div>
  <div class="q-body">
    <span class="q-num">${num}.</span>
    <div class="q-content">${escapeHtml(q.content || "")}</div>
    ${optionsHtml}
    ${imagesHtml}
  </div>
</div>`;
  }).join("");

  const answerSheet = includeAnswer ? `
<div class="answer-sheet">
  <h2>参考答案</h2>
  ${questions.map((q, idx) => `<p><b>${idx + 1}.</b> ${escapeHtml(q.answer || "略")}</p>`).join("")}
</div>` : "";

  const solutionSheet = includeSolution ? `
<div class="solution-sheet">
  <h2>解析</h2>
  ${questions.map((q, idx) => `<div class="solution-item"><b>${idx + 1}.</b> ${escapeHtml(q.solution || "略")}</div>`).join("")}
</div>` : "";

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${escapeHtml(gen.name || "试卷")}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
<style>
@page { size: A4; margin: 2cm; }
body { font-family: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif; font-size: 12pt; line-height: 1.8; color: #1e293b; max-width: 210mm; margin: 0 auto; padding: 20px; }
.header { text-align: center; border-bottom: 2px solid #1e3a5f; padding-bottom: 1em; margin-bottom: 1.5em; }
.header h1 { margin: 0; font-size: 1.6em; color: #1e3a5f; }
.header p { margin: 0.3em 0; color: #64748b; font-size: 10pt; }
.question { margin-bottom: 1.8em; page-break-inside: avoid; }
.q-meta { font-size: 9pt; color: #64748b; margin-bottom: 0.3em; }
.badge { display: inline-block; padding: 1px 6px; border-radius: 4px; background: #f1f5f9; margin-right: 4px; }
.tag { display: inline-block; padding: 1px 6px; border-radius: 4px; background: #dbeafe; color: #1e40af; margin-right: 4px; font-size: 9pt; }
.q-num { font-weight: bold; color: #1e3a5f; float: left; width: 2em; font-size: 1.1em; }
.q-body { margin-left: 2.2em; }
.q-content { white-space: pre-wrap; }
.options { margin: 0.5em 0 0.5em 1em; }
.options p { margin: 0.2em 0; }
.images { margin: 0.5em 0; }
.images img { max-width: 100%; height: auto; display: block; margin: 0.3em 0; }
.answer-sheet, .solution-sheet { page-break-before: always; margin-top: 2em; }
.solution-item { margin-bottom: 1em; }
@media print {
  body { padding: 0; }
  .no-print { display: none; }
}
</style>
</head>
<body>
<div class="header">
  <h1>${escapeHtml(gen.name || "组卷练习")}</h1>
  <p>满分：${gen.total_score || 0}分 | 共 ${questions.length} 题 | 建议用时：120分钟</p>
</div>
${qList}
${answerSheet}
${solutionSheet}
<div class="no-print" style="margin-top:2em;text-align:center;color:#999;font-size:10pt;">
  由 ExamBank 生成 · 打印请使用 A4 纸张
</div>
<script>
document.querySelectorAll('.q-content').forEach(function(el) {
  var text = el.innerText;
  if (text.indexOf('\\\\') !== -1 || text.indexOf('_') !== -1 || text.indexOf('^') !== -1) {
    try {
      katex.render(text, el, {throwOnError: false, displayMode: true});
    } catch(e) {}
  }
});
</script>
</body>
</html>`;
}

function escapeHtml(text: string): string {
  if (!text) return "";
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
