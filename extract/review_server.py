#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
人工复核 Web 服务器 (Review Server)
====================================

为单张卷子人机协作打分提供本地 Web 界面。
基于 Python 标准库，无需 Flask/Django。

用法:
    python extract/review_server.py \
        --work-dir ./loop/single/paper_001/ \
        --port 8765

然后浏览器打开 http://localhost:8765
"""

import json
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Dict, Any, Optional
from wsgiref.simple_server import make_server, WSGIServer
from wsgiref.util import setup_testing_defaults
from socketserver import ThreadingMixIn


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    """支持多线程并发的 WSGI 服务器"""
    daemon_threads = True


HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<title>试卷人工复核</title>
<style>
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    margin: 0; padding: 20px; background: #f5f5f5; color: #333;
  }
  h1 { margin-top: 0; font-size: 22px; }
  .toolbar {
    position: sticky; top: 0; background: #fff; padding: 12px 16px;
    border-bottom: 1px solid #ddd; margin: -20px -20px 20px -20px;
    display: flex; gap: 12px; align-items: center; flex-wrap: wrap; z-index: 10;
  }
  .toolbar button {
    padding: 8px 16px; border: none; border-radius: 4px; cursor: pointer;
    background: #1976d2; color: #fff; font-size: 14px;
  }
  .toolbar button.secondary { background: #666; }
  .toolbar button.danger { background: #d32f2f; }
  .toolbar .status { margin-left: auto; font-weight: bold; }
  .paper-meta {
    background: #fff; padding: 16px; border-radius: 8px; margin-bottom: 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
  }
  .paper-meta h2 { margin: 0 0 12px 0; font-size: 18px; }
  .meta-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 10px; }
  .meta-grid label { font-size: 13px; color: #666; }
  .meta-grid input {
    width: 100%; padding: 6px; border: 1px solid #ddd; border-radius: 4px;
  }
  .question-card {
    background: #fff; padding: 16px; border-radius: 8px; margin-bottom: 16px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    border-left: 4px solid #4caf50;
  }
  .question-card.error { border-left-color: #f44336; }
  .question-card.warning { border-left-color: #ff9800; }
  .question-header {
    display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;
  }
  .question-number { font-weight: bold; font-size: 16px; }
  .question-actions button {
    padding: 4px 10px; margin-left: 6px; font-size: 12px; cursor: pointer;
    border: 1px solid #ddd; border-radius: 4px; background: #f5f5f5;
  }
  .question-actions button.active { background: #ffebee; border-color: #f44336; color: #d32f2f; }
  .field { margin-bottom: 10px; }
  .field label { display: block; font-size: 12px; color: #666; margin-bottom: 4px; }
  .field textarea, .field input[type="text"] {
    width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-family: inherit;
  }
  .field textarea { min-height: 60px; resize: vertical; }
  .formula-preview { margin-top: 6px; padding: 8px; background: #f9f9f9; border: 1px dashed #ddd; border-radius: 4px; min-height: 24px; display: none; }
  .formula-preview sup { font-size: 0.75em; vertical-align: super; }
  .formula-preview sub { font-size: 0.75em; vertical-align: sub; }
  .math-sqrt { display: inline-block; white-space: nowrap; }
  .sqrt-symbol { font-size: 1.2em; margin-right: 1px; }
  .sqrt-over { border-top: 1px solid #333; padding-top: 1px; }
  .tags-input { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .tags-input input { flex: 1; min-width: 120px; }
  .tag { background: #e3f2fd; padding: 2px 8px; border-radius: 12px; font-size: 12px; }
  .options-list { display: flex; flex-direction: column; gap: 12px; }
  .option-item { display: flex; align-items: flex-start; gap: 10px; padding: 8px; border: 1px solid #e0e0e0; border-radius: 6px; background: #fafafa; }
  .option-label { font-weight: bold; min-width: 24px; text-align: center; padding-top: 6px; }
  .option-body { flex: 1; }
  .options-list input[type="text"] { width: 100%; }
  .nav { display: flex; justify-content: space-between; margin-top: 20px; }
  .nav button { padding: 10px 20px; }
  .score-panel {
    background: #fff; padding: 16px; border-radius: 8px; margin-bottom: 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
  }
  .score-panel h3 { margin: 0 0 10px 0; }
  .score-bar { height: 20px; background: #e0e0e0; border-radius: 10px; overflow: hidden; }
  .score-bar-inner { height: 100%; background: #4caf50; transition: width 0.3s; }
  .human-score { margin-top: 16px; padding-top: 16px; border-top: 1px solid #e0e0e0; }
  .human-score label { display: inline-block; font-size: 14px; font-weight: bold; margin-right: 8px; }
  .human-score .score-number { font-size: 20px; font-weight: bold; color: #1976d2; }
  .data-table { border-collapse: collapse; width: 100%; margin-top: 8px; }
  .data-table td, .data-table th { border: 1px solid #ddd; padding: 8px; text-align: center; }
  .data-table tr:nth-child(even) { background: #f9f9f9; }
  .hidden { display: none; }
</style>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css" crossorigin="anonymous">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js" crossorigin="anonymous"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js" crossorigin="anonymous"></script>
</head>
<body>
  <div class="toolbar">
    <button onclick="saveReview()">保存复核结果</button>
    <button class="secondary" onclick="loadReview()">重新加载</button>
    <button class="danger" onclick="markAllCorrect()">全部标记正确</button>
    <span class="status" id="saveStatus"></span>
  </div>

  <h1>试卷人工复核</h1>

  <div class="score-panel">
    <h3>机器自动评分: <span id="machineScore">--</span> / 100</h3>
    <div class="score-bar"><div class="score-bar-inner" id="machineScoreBar"></div></div>
    <p id="machineIssues" style="color:#666;font-size:13px;"></p>
    <div class="human-score">
      <h3 style="margin:0 0 8px 0;">人工评分: <span class="score-number" id="humanScoreDisplay">--</span> / 100</h3>
      <p id="humanScoreDetail" style="color:#666;font-size:13px;margin:0;">正确题得该题固定分，有误/删除题为 0 分</p>
    </div>
  </div>

  <div class="paper-meta">
    <h2>试卷元数据</h2>
    <div class="meta-grid" id="metaGrid"></div>
  </div>

  <div id="questions"></div>

  <div class="nav">
    <button onclick="prevQuestion()">上一题</button>
    <button onclick="nextQuestion()">下一题</button>
  </div>

<script>
let data = { meta: {}, questions: [], machine_score: {} };
let currentIndex = 0;
let review = { corrections: {}, deleted: [], meta_corrections: {}, human_score: undefined };

async function init() {
  const res = await fetch('/api/data');
  data = await res.json();
  await loadReviewFromServer();
  renderMeta();
  renderScore();
  renderQuestion();
}

async function loadReviewFromServer() {
  try {
    const res = await fetch('/api/review');
    if (res.ok) {
      review = await res.json();
    }
  } catch (e) {
    console.log('no existing review');
  }
}

function renderMeta() {
  const meta = data.meta;
  const fields = ['paper_id','subject','year','region','district','school','exam_type','round','title','source_file','total_score','expected_question_count'];
  const grid = document.getElementById('metaGrid');
  grid.innerHTML = fields.map(f => `
    <div>
      <label>${f}</label>
      <input type="text" id="meta_${f}" value="${meta[f] || ''}" onchange="onMetaChange('${f}', this.value)">
    </div>
  `).join('');
}

function onMetaChange(field, value) {
  review.meta_corrections[field] = value;
}

function calculateHumanScore() {
  let total = 0;
  let earned = 0;
  for (const q of data.questions) {
    const qscore = parseFloat(q.score);
    if (isNaN(qscore) || qscore <= 0) continue;
    total += qscore;
    const isDeleted = review.deleted.includes(q.question_id);
    const hasCorrections = review.corrections[q.question_id] && Object.keys(review.corrections[q.question_id]).length > 0;
    if (!isDeleted && !hasCorrections) {
      earned += qscore;
    }
  }
  const pct = total > 0 ? Math.round(earned / total * 100 * 10) / 10 : 0;
  review.human_score = pct;
  return { pct, earned, total };
}

function renderScore() {
  const score = data.machine_score || {};
  document.getElementById('machineScore').textContent = score.score || '--';
  const pct = score.score ? Math.min(100, Math.max(0, score.score)) : 0;
  document.getElementById('machineScoreBar').style.width = pct + '%';
  document.getElementById('machineIssues').textContent = (score.issues || []).join('； ') || '无明显问题';
  const hs = calculateHumanScore();
  document.getElementById('humanScoreDisplay').textContent = hs.pct;
  document.getElementById('humanScoreDetail').textContent = `正确题得分 ${hs.earned} / ${hs.total} 分，折算 ${hs.pct} / 100`;
}

function renderQuestion() {
  try {
  const q = data.questions[currentIndex];
  if (!q) return;
  // 兼容 options 为 JSON 字符串或数组
  if (typeof q.options === 'string') {
    try { q.options = JSON.parse(q.options); } catch (e) { q.options = null; }
  }
  if (!Array.isArray(q.options)) q.options = [];
  if (typeof q.images === 'string') {
    try { q.images = JSON.parse(q.images); } catch (e) { q.images = []; }
  }
  if (!Array.isArray(q.images)) q.images = [];
  if (typeof q.tags === 'string') {
    try { q.tags = JSON.parse(q.tags); } catch (e) { q.tags = []; }
  }
  if (!Array.isArray(q.tags)) q.tags = [];
  let dataTable = null;
  if (q.data_table) {
    if (typeof q.data_table === 'string') {
      try { dataTable = JSON.parse(q.data_table); } catch (e) { dataTable = null; }
    } else if (Array.isArray(q.data_table)) {
      dataTable = q.data_table;
    }
  }

  const corr = review.corrections[q.question_id] || {};
  const isDeleted = review.deleted.includes(q.question_id);
  const container = document.getElementById('questions');

  // 判断是否为图片选项：选项文本普遍为空且题目有图片
  const hasImageOptions = q.images && q.images.length > 0 &&
    q.options && q.options.length > 0 &&
    q.options.every(opt => {
      const t = (opt.text !== undefined ? opt.text : opt) || '';
      return String(t).trim().length < 2;
    });

  // 区分题干图片与选项图片：图片数多于选项数时，前面多余的图片视为题干图片
  let questionImages = q.images || [];
  let optionImages = q.images || [];
  if (hasImageOptions && q.images.length > q.options.length) {
    const extra = q.images.length - q.options.length;
    questionImages = q.images.slice(0, extra);
    optionImages = q.images.slice(extra);
  }

  // 表格渲染
  const tableHtml = (dataTable && dataTable.length)
    ? '<div class="field"><label>数据表格</label><table class="data-table">' +
        dataTable.map(row => '<tr>' + row.map(cell => `<td>${escapeHtml(cell)}</td>`).join('') + '</tr>').join('') +
      '</table></div>'
    : '';

  // 图片渲染
  const imagesHtml = (questionImages && questionImages.length)
    ? '<div class="field"><label>题目图片</label><div class="image-gallery">' +
        questionImages.map(img => `<img src="/${escapeHtml(img)}" alt="题目图片" style="max-width:100%;max-height:240px;border:1px solid #ddd;margin:4px 0;">`).join('') +
      '</div></div>'
    : '';

  let optionsHtml = '';
  if (q.options && q.options.length) {
    optionsHtml = '<div class="field"><label>选项</label><div class="options-list">' +
      q.options.map((opt, i) => {
        const label = escapeHtml(opt.label || String.fromCharCode(65 + i));
        const textVal = opt.text !== undefined ? opt.text : opt;
        const optFormulaBlock = hasFormula(textVal)
          ? `<div class="formula-preview option-formula" id="option_formula_${q.question_id}_${i}" style="display:block;margin-top:4px;">${escapeHtml(toLatexMath(textVal))}</div>`
          : '';
        let imgHtml = '';
        if (hasImageOptions) {
          const img = optionImages[i];
          if (img) {
            imgHtml = `<div><img src="/${escapeHtml(img)}" alt="选项${label}" style="max-width:180px;max-height:140px;border:1px solid #ddd;margin-top:6px;"></div>`;
          } else {
            imgHtml = `<div style="color:#666;font-size:12px;margin-top:6px;">[图片选项] 无对应图片</div>`;
          }
        }
        return `
          <div class="option-item">
            <div class="option-label">${label}</div>
            <div class="option-body">
              <input type="text" value="${escapeHtml(textVal !== undefined ? textVal : '')}" onchange="onOptionChange('${q.question_id}', ${i}, this.value)">
              ${optFormulaBlock}
              ${imgHtml}
            </div>
          </div>
        `;
      }).join('') +
      '</div></div>';
  }

  container.innerHTML = `
    <div class="question-card ${isDeleted ? 'error' : ''}" id="card_${q.question_id}">
      <div class="question-header">
        <span class="question-number">题号 ${q.question_number} (${currentIndex + 1} / ${data.questions.length})</span>
        <div class="question-actions">
          <button class="${isDeleted ? 'active' : ''}" onclick="toggleDelete('${q.question_id}')">${isDeleted ? '已删除' : '删除本题'}</button>
          <button onclick="markCorrect('${q.question_id}')">本题正确</button>
        </div>
      </div>
      <div class="field">
        <label>题干内容</label>
        <textarea id="content_${q.question_id}" onchange="onContentChange('${q.question_id}', this.value)">${escapeHtml(corr.content !== undefined ? corr.content : q.content)}</textarea>
        <div id="formula_preview_${q.question_id}" class="formula-preview"></div>
      </div>
      ${tableHtml}
      ${imagesHtml}
      ${optionsHtml}
      <div class="field">
        <label>答案</label>
        <input type="text" value="${escapeHtml(corr.answer !== undefined ? corr.answer : (q.answer || ''))}" onchange="onAnswerChange('${q.question_id}', this.value)">
        <div id="answer_preview_${q.question_id}" class="formula-preview"></div>
      </div>
      <div class="field">
        <label>解析</label>
        <textarea onchange="onSolutionChange('${q.question_id}', this.value)">${escapeHtml(corr.solution !== undefined ? corr.solution : (q.solution || ''))}</textarea>
        <div id="solution_preview_${q.question_id}" class="formula-preview"></div>
      </div>
      <div class="field">
        <label>图片路径 (逗号分隔)</label>
        <input type="text" value="${escapeHtml((corr.images !== undefined ? corr.images : q.images || []).join(', '))}" onchange="onImagesChange('${q.question_id}', this.value)">
        ${(corr.images !== undefined ? corr.images : q.images || []).length ?
          '<div class="image-gallery">' +
          (corr.images !== undefined ? corr.images : q.images || []).map(img =>
            `<img src="/${escapeHtml(img)}" alt="" style="max-width:120px;max-height:120px;border:1px solid #ddd;margin:4px;">`
          ).join('') +
          '</div>' : ''}
      </div>
      <div class="field">
        <label>标签 (逗号分隔)</label>
        <input type="text" value="${escapeHtml((corr.tags !== undefined ? corr.tags : q.tags || []).join(', '))}" onchange="onTagsChange('${q.question_id}', this.value)">
      </div>
    </div>
  `;
  renderFormulaPreview(q.question_id, corr.content !== undefined ? corr.content : q.content);
  renderAnswerPreview(q.question_id, corr.answer !== undefined ? corr.answer : (q.answer || ''));
  renderSolutionPreview(q.question_id, corr.solution !== undefined ? corr.solution : (q.solution || ''));
  renderScore();

  // 渲染选项公式
  if (q.options && q.options.length) {
    q.options.forEach((opt, i) => {
      const textVal = opt.text !== undefined ? opt.text : opt;
      renderKatex(document.getElementById('option_formula_' + q.question_id + '_' + i), textVal);
    });
  }
  } catch (e) {
    console.error('renderQuestion error:', e);
  }
}

function onContentChange(qid, value) {
  console.log('onContentChange', qid);
  try {
    ensureCorrection(qid); review.corrections[qid].content = value; renderFormulaPreview(qid, value); renderScore();
  } catch (e) {
    console.error('onContentChange error:', e);
  }
}
function onAnswerChange(qid, value) { ensureCorrection(qid); review.corrections[qid].answer = value; renderAnswerPreview(qid, value); renderScore(); }

function hasFormula(text) {
  return text && (text.includes('^') || text.includes('~') || text.includes('sqrt(') || text.includes(')/('));
}

function toLatexMath(text) {
  if (!text) return text;
  const placeholders = [];
  const addPlaceholder = (latex) => {
    const key = `__MATH_${placeholders.length}__`;
    placeholders.push(latex);
    return key;
  };

  let changed = true;
  while (changed) {
    changed = false;

    // 分数 (a)/(b)，从内到外处理
    let fi = 0;
    while (fi < text.length) {
      const divIdx = text.indexOf(')/(', fi);
      if (divIdx === -1) break;
      let depth = 0;
      let start = divIdx;
      while (start >= 0) {
        if (text[start] === ')') depth++;
        else if (text[start] === '(') {
          depth--;
          if (depth === 0) break;
        }
        start--;
      }
      if (start < 0) { fi = divIdx + 3; continue; }
      depth = 0;
      let end = divIdx + 2;
      while (end < text.length) {
        if (text[end] === '(') depth++;
        else if (text[end] === ')') {
          depth--;
          if (depth === 0) break;
        }
        end++;
      }
      if (end >= text.length) { fi = divIdx + 3; continue; }
      const num = text.slice(start + 1, divIdx);
      const den = text.slice(divIdx + 3, end);
      if (!num.includes(')/(') && !den.includes(')/(')) {
        text = text.slice(0, start) + addPlaceholder(`\\frac{${num}}{${den}}`) + text.slice(end + 1);
        changed = true;
        fi = start + 1;
      } else {
        fi = end + 1;
      }
    }

    // sqrt(x)，内层优先
    let si = 0;
    while (si < text.length) {
      const idx = text.indexOf('sqrt(', si);
      if (idx === -1) break;
      let depth = 1;
      let j = idx + 5;
      while (j < text.length && depth > 0) {
        if (text[j] === '(') depth++;
        else if (text[j] === ')') depth--;
        j++;
      }
      const inner = text.slice(idx + 5, j - 1);
      if (!inner.includes('sqrt(')) {
        text = text.slice(0, idx) + addPlaceholder(`\\sqrt{${inner}}`) + text.slice(j);
        changed = true;
        si = idx + 1;
      } else {
        si = j;
      }
    }

    // 上标 ^x^，内层优先
    text = text.replace(/([a-zA-Z0-9)\]}′']|\([^()]*\))\^([^^\\n]+)\^/g, (m, base, exp) => {
      changed = true;
      return addPlaceholder(`${base}^{${exp}}`);
    });

    // 下标 ~x~，内层优先
    text = text.replace(/([a-zA-Z0-9)\]}′']|\([^()]*\))\~([^~\\n]+)\~/g, (m, base, sub) => {
      changed = true;
      return addPlaceholder(`${base}_{${sub}}`);
    });
  }

  placeholders.forEach((latex, idx) => {
    text = text.split(`__MATH_${idx}__`).join(`$${latex}$`);
  });
  return text;
}

function renderKatex(el, text) {
  if (!el) return;
  if (!hasFormula(text)) {
    el.innerHTML = '';
    el.style.display = 'none';
    return;
  }
  const latex = toLatexMath(text);
  el.innerHTML = escapeHtml(latex);
  el.style.display = 'block';
  if (typeof renderMathInElement === 'function') {
    try {
      renderMathInElement(el, {
        delimiters: [
          {left: '$$', right: '$$', display: true},
          {left: '$', right: '$', display: false},
        ],
        throwOnError: false,
      });
    } catch (e) {
      console.error('KaTeX render error:', e);
    }
  }
}

function renderFormulaPreview(qid, text) {
  renderKatex(document.getElementById('formula_preview_' + qid), text);
}

function renderAnswerPreview(qid, text) {
  renderKatex(document.getElementById('answer_preview_' + qid), text);
}
function onSolutionChange(qid, value) { ensureCorrection(qid); review.corrections[qid].solution = value; renderSolutionPreview(qid, value); renderScore(); }

function renderSolutionPreview(qid, text) {
  renderKatex(document.getElementById('solution_preview_' + qid), text);
}
function onImagesChange(qid, value) { ensureCorrection(qid); review.corrections[qid].images = value.split(',').map(s => s.trim()).filter(Boolean); renderScore(); }
function onTagsChange(qid, value) { ensureCorrection(qid); review.corrections[qid].tags = value.split(',').map(s => s.trim()).filter(Boolean); renderScore(); }
function onOptionChange(qid, idx, value) {
  ensureCorrection(qid);
  if (!review.corrections[qid].options) {
    const orig = data.questions.find(q => q.question_id === qid).options || [];
    review.corrections[qid].options = JSON.parse(JSON.stringify(orig));
  }
  if (typeof review.corrections[qid].options[idx] === 'string') {
    review.corrections[qid].options[idx] = { label: String.fromCharCode(65 + idx), text: value };
  } else {
    review.corrections[qid].options[idx].text = value;
  }
  renderScore();
}

function ensureCorrection(qid) {
  if (!review.corrections[qid]) review.corrections[qid] = {};
}

function toggleDelete(qid) {
  const idx = review.deleted.indexOf(qid);
  if (idx >= 0) review.deleted.splice(idx, 1);
  else review.deleted.push(qid);
  renderQuestion();
}

function markCorrect(qid) {
  console.log('markCorrect', qid);
  delete review.corrections[qid];
  const idx = review.deleted.indexOf(qid);
  if (idx >= 0) review.deleted.splice(idx, 1);
  renderQuestion();
}

function markAllCorrect() {
  review.corrections = {};
  review.deleted = [];
  renderQuestion();
}

function nextQuestion() {
  console.log('nextQuestion', currentIndex, data.questions.length);
  try {
    if (currentIndex < data.questions.length - 1) { currentIndex++; renderQuestion(); }
  } catch (e) {
    console.error('nextQuestion error:', e);
  }
}
function prevQuestion() { if (currentIndex > 0) { currentIndex--; renderQuestion(); } }

window.onerror = function(msg, url, line) {
  console.error('JS error:', msg, 'line:', line);
};

async function saveReview() {
  const res = await fetch('/api/review', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(review)
  });
  if (res.ok) {
    document.getElementById('saveStatus').textContent = '已保存';
  } else {
    document.getElementById('saveStatus').textContent = '保存失败';
  }
}

async function loadReview() {
  await loadReviewFromServer();
  renderQuestion();
}

function escapeHtml(text) {
  if (text === null || text === undefined) return '';
  return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

document.addEventListener('keydown', e => {
  if (e.key === 'ArrowRight') nextQuestion();
  if (e.key === 'ArrowLeft') prevQuestion();
});

document.addEventListener('click', e => {
  console.log('document click', e.target.tagName, e.target.textContent, e.target.getAttribute('onclick'));
});

init();
</script>
</body>
</html>
"""


def make_response(status: str, body: bytes, content_type: str = "application/json") -> tuple:
    headers = [("Content-Type", content_type)]
    if content_type == "application/json":
        headers.append(("Access-Control-Allow-Origin", "*"))
    return status, headers, body


class ReviewServer:
    """本地人工复核 Web 服务器"""

    def __init__(self, work_dir: Path, port: int = 8765):
        self.work_dir = Path(work_dir).resolve()
        self.port = port
        self.data_dir = self.work_dir / "review_data"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.extracted_file = self.data_dir / "extracted.json"
        self.review_file = self.data_dir / "human_review.json"
        self.score_file = self.data_dir / "machine_score.json"

    def load_extracted(self) -> Dict[str, Any]:
        if self.extracted_file.exists():
            return json.loads(self.extracted_file.read_text(encoding="utf-8"))
        return {"meta": {}, "questions": [], "machine_score": {}}

    def load_review(self) -> Dict[str, Any]:
        if self.review_file.exists():
            return json.loads(self.review_file.read_text(encoding="utf-8"))
        return {"corrections": {}, "deleted": [], "meta_corrections": {}, "human_score": None}

    def save_review(self, review: Dict[str, Any]):
        self.review_file.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")

    def __call__(self, environ, start_response):
        setup_testing_defaults(environ)
        method = environ["REQUEST_METHOD"]
        path = environ["PATH_INFO"]
        query = urllib.parse.parse_qs(environ.get("QUERY_STRING", ""))

        if method == "GET" and path == "/":
            body = HTML_PAGE.encode("utf-8")
            start_response("200 OK", [
                ("Content-Type", "text/html; charset=utf-8"),
                ("Cache-Control", "no-cache, no-store, must-revalidate"),
            ])
            return [body]

        if method == "GET" and path == "/api/data":
            data = self.load_extracted()
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            start_response("200 OK", [("Content-Type", "application/json; charset=utf-8")])
            return [body]

        if method == "GET" and path == "/api/review":
            review = self.load_review()
            body = json.dumps(review, ensure_ascii=False).encode("utf-8")
            start_response("200 OK", [("Content-Type", "application/json; charset=utf-8")])
            return [body]

        if method == "POST" and path == "/api/review":
            try:
                length = int(environ.get("CONTENT_LENGTH", 0))
                payload = environ["wsgi.input"].read(length).decode("utf-8")
                review = json.loads(payload)
                self.save_review(review)
                body = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
                start_response("200 OK", [("Content-Type", "application/json; charset=utf-8")])
                return [body]
            except Exception as e:
                body = json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False).encode("utf-8")
                start_response("400 Bad Request", [("Content-Type", "application/json; charset=utf-8")])
                return [body]

        # 静态图片服务：/images/... -> work_dir/images/...
        if method == "GET" and path.startswith("/images/"):
            # WSGI PATH_INFO 是 latin-1 解码的，中文需重新按 utf-8 解码
            try:
                decoded_path = path.encode("latin-1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                decoded_path = urllib.parse.unquote(path)
            file_path = self.work_dir / decoded_path.lstrip("/")
            if file_path.exists() and file_path.is_file():
                content_type = "image/jpeg"
                if file_path.suffix.lower() == ".png":
                    content_type = "image/png"
                elif file_path.suffix.lower() == ".gif":
                    content_type = "image/gif"
                start_response("200 OK", [("Content-Type", content_type)])
                return [file_path.read_bytes()]

        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"Not Found"]

    def run(self):
        server = make_server("127.0.0.1", self.port, self, server_class=ThreadingWSGIServer)
        print(f"=" * 50)
        print(f"人工复核服务器已启动")
        print(f"请在浏览器打开: http://127.0.0.1:{self.port}")
        print(f"工作目录: {self.work_dir}")
        print(f"按 Ctrl+C 停止")
        print(f"=" * 50)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n服务器已停止")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="人工复核 Web 服务器")
    parser.add_argument("--work-dir", required=True, help="工作目录，包含 extracted.json 和 human_review.json")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    args = parser.parse_args()

    server = ReviewServer(Path(args.work_dir), args.port)
    server.run()


if __name__ == "__main__":
    main()
