#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
单卷评分器 (Paper Scorer)
==========================

对单张试卷的提取结果进行 0-100 质量评分，支持：
1. 启发式评分（无 GT）：基于提取结果自身的完整性、连续性、覆盖率等。
2. GT 对比评分（有 GT）：与人工校对数据对比，计算题号、内容、答案、元数据等匹配度。

用法:
    from paper_scorer import score_paper, score_against_gt
"""

import json
import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict


NOISE_KEYWORDS = [
    "本试卷共", "考试时间", "满分", "姓名", "班级", "考号",
    "准考证号", "密封线", "装订线", "答题卡", "注意事项",
    "学校", "第.*页.*共.*页"
]


@dataclass
class PaperScore:
    """单卷评分结果"""
    paper_id: str
    score: float
    passed: bool
    threshold: float
    dimensions: Dict[str, float]
    issues: List[str]
    details: Dict[str, Any]


def _is_noise_content(content: str) -> bool:
    """判断内容是否为噪声（考试说明/页眉页脚等）"""
    if not content:
        return True
    text = content.strip()
    for kw in NOISE_KEYWORDS:
        if re.search(kw, text):
            return True
    return False


def _count_number_jumps(question_numbers: List[str]) -> int:
    """计算主题号跳号处数"""
    main_nums = []
    for qn in question_numbers:
        try:
            main_nums.append(int(str(qn).split("(")[0].split("_")[0]))
        except (ValueError, TypeError):
            continue
    main_nums = sorted(set(main_nums))
    if len(main_nums) < 2:
        return 0
    jumps = 0
    for i in range(1, len(main_nums)):
        if main_nums[i] - main_nums[i - 1] > 1:
            jumps += 1
    return jumps


def _expected_question_count(question_numbers: List[str]) -> int:
    """根据最大题号估算预期题数"""
    main_nums = []
    for qn in question_numbers:
        try:
            main_nums.append(int(str(qn).split("(")[0].split("_")[0]))
        except (ValueError, TypeError):
            continue
    return max(main_nums) if main_nums else 0


def heuristic_score(paper: Dict[str, Any],
                    questions: List[Dict[str, Any]],
                    threshold: float = 80.0) -> PaperScore:
    """
    启发式评分：无需 GT，仅根据提取结果自身质量评分。

    维度与权重:
    - extraction_success (25%): content 有效题目 / 预期题数
    - number_continuity (20%): 主题号连续性
    - content_integrity (15%): 非噪声/非空内容比例
    - answer_coverage (15%): 有 answer 或 solution 的题目比例
    - metadata_accuracy (15%): district/school/exam_type 均非未知
    - image_placement (10%): 含“如图”题目中有图片的比例
    """
    total_q = len(questions)
    expected = _expected_question_count([q.get("question_number", "") for q in questions])
    expected = max(expected, total_q)

    # extraction_success
    # 主题号内容长度映射，用于子题有效性判断
    parent_content_lengths = {}
    for q in questions:
        qn = str(q.get("question_number", ""))
        if "(" not in qn and "_" not in qn:
            parent_content_lengths[qn] = len(str(q.get("content", "")).strip())

    def _is_valid_content(q: Dict[str, Any]) -> bool:
        content = str(q.get("content", "")).strip()
        if len(content) >= 20:
            return True
        qn = str(q.get("question_number", ""))
        # 子题可放宽：父题内容有效且子题不少于 10 字符
        parent_qn = qn.split("(")[0].split("_")[0]
        if parent_qn != qn and parent_content_lengths.get(parent_qn, 0) >= 20 and len(content) >= 10:
            return True
        return False

    valid_content = sum(1 for q in questions if _is_valid_content(q))
    extraction_success = (valid_content / expected * 100) if expected > 0 else 0.0

    # number_continuity
    jumps = _count_number_jumps([q.get("question_number", "") for q in questions])
    number_continuity = max(0, 100 - (jumps * 20))  # 每处跳号扣 20 分

    # content_integrity
    noise_count = sum(1 for q in questions if _is_noise_content(q.get("content", "")))
    empty_count = sum(1 for q in questions if not q.get("content") or len(str(q.get("content", "")).strip()) < 10)
    bad_content = noise_count + empty_count
    content_integrity = (1 - bad_content / max(total_q, 1)) * 100

    # answer_coverage
    has_answer = sum(1 for q in questions if q.get("answer") or q.get("solution"))
    answer_coverage = (has_answer / total_q * 100) if total_q > 0 else 0.0

    # metadata_accuracy
    # district/exam_type 必须有效；school 可为具体学校或区统考（当 district 有效时）
    meta_fields = ["district", "school", "exam_type"]
    meta_ok = 0
    for f in meta_fields:
        v = paper.get(f)
        if v and v != "未知":
            meta_ok += 1
    # school 为区统考且 district 有效时，算作半有效（避免虚高也不完全丢分）
    school = paper.get("school", "")
    district = paper.get("district", "")
    if school and "统考" in school and district and district != "未知":
        meta_ok += 0.5
    metadata_accuracy = min(100.0, meta_ok / len(meta_fields) * 100)

    # image_placement
    has_figure_questions = sum(1 for q in questions if "如图" in str(q.get("content", "")))
    figure_with_image = sum(
        1 for q in questions
        if "如图" in str(q.get("content", "")) and q.get("images")
    )
    image_placement = (figure_with_image / has_figure_questions * 100) if has_figure_questions > 0 else 100.0

    dimensions = {
        "extraction_success": round(extraction_success, 1),
        "number_continuity": round(number_continuity, 1),
        "content_integrity": round(content_integrity, 1),
        "answer_coverage": round(answer_coverage, 1),
        "metadata_accuracy": round(metadata_accuracy, 1),
        "image_placement": round(image_placement, 1),
    }

    weights = {
        "extraction_success": 0.25,
        "number_continuity": 0.20,
        "content_integrity": 0.15,
        "answer_coverage": 0.15,
        "metadata_accuracy": 0.15,
        "image_placement": 0.10,
    }

    score = sum(dimensions[k] * weights[k] for k in dimensions)

    issues = []
    if dimensions["extraction_success"] < 80:
        issues.append("提取成功率低")
    if dimensions["number_continuity"] < 80:
        issues.append("题号跳号过多")
    if dimensions["content_integrity"] < 80:
        issues.append("内容被噪声污染或空内容过多")
    if dimensions["answer_coverage"] < 80:
        issues.append("答案/解析覆盖率低")
    if dimensions["metadata_accuracy"] < 80:
        issues.append("元数据缺失")
    if dimensions["image_placement"] < 80:
        issues.append("图片归属不合理")

    return PaperScore(
        paper_id=paper.get("paper_id", ""),
        score=round(score, 1),
        passed=score >= threshold,
        threshold=threshold,
        dimensions=dimensions,
        issues=issues,
        details={
            "total_questions": total_q,
            "expected_questions": expected,
            "valid_content": valid_content,
            "noise_count": noise_count,
            "empty_count": empty_count,
            "jumps": jumps,
            "has_answer": has_answer,
            "has_figure_questions": has_figure_questions,
            "figure_with_image": figure_with_image,
        }
    )


def score_against_gt(paper: Dict[str, Any],
                     questions: List[Dict[str, Any]],
                     gt_meta: Dict[str, Any],
                     gt_questions: List[Dict[str, Any]],
                     threshold: float = 80.0) -> PaperScore:
    """
    基于 Ground Truth 的评分。
    """
    # 元数据对比
    meta_fields = ["subject", "year", "region", "district", "school", "exam_type", "round"]
    meta_ok = sum(1 for f in meta_fields if str(paper.get(f, "")) == str(gt_meta.get(f, "")))
    metadata_accuracy = meta_ok / len(meta_fields) * 100

    # 题号召回与精确率
    gt_numbers = {str(q["question_number"]) for q in gt_questions}
    extracted_numbers = {str(q.get("question_number", "")) for q in questions}
    true_positives = len(gt_numbers & extracted_numbers)
    recall = (true_positives / len(gt_numbers) * 100) if gt_numbers else 0.0
    precision = (true_positives / len(extracted_numbers) * 100) if extracted_numbers else 0.0

    # 内容对比：仅对比共有的题号
    content_matches = 0
    content_total = 0
    answer_matches = 0
    answer_total = 0

    gt_map = {str(q["question_number"]): q for q in gt_questions}
    for q in questions:
        qn = str(q.get("question_number", ""))
        if qn not in gt_map:
            continue
        gt_q = gt_map[qn]
        content_total += 1
        # 简单相似度：长度差异 < 30% 且共同中文字符比例 > 50%
        gt_content = str(gt_q.get("content", ""))
        ex_content = str(q.get("content", ""))
        if _content_similar(gt_content, ex_content):
            content_matches += 1

        gt_ans = str(gt_q.get("answer", "")).strip()
        ex_ans = str(q.get("answer", "")).strip()
        answer_total += 1
        if gt_ans and ex_ans and (gt_ans in ex_ans or ex_ans in gt_ans):
            answer_matches += 1

    content_accuracy = (content_matches / content_total * 100) if content_total > 0 else 0.0
    answer_accuracy = (answer_matches / answer_total * 100) if answer_total > 0 else 0.0

    dimensions = {
        "metadata_accuracy": round(metadata_accuracy, 1),
        "number_recall": round(recall, 1),
        "number_precision": round(precision, 1),
        "content_accuracy": round(content_accuracy, 1),
        "answer_accuracy": round(answer_accuracy, 1),
    }

    weights = {
        "metadata_accuracy": 0.20,
        "number_recall": 0.25,
        "number_precision": 0.15,
        "content_accuracy": 0.25,
        "answer_accuracy": 0.15,
    }

    score = sum(dimensions[k] * weights[k] for k in dimensions)

    issues = []
    if dimensions["metadata_accuracy"] < 80:
        issues.append("元数据与 GT 不符")
    if dimensions["number_recall"] < 80:
        issues.append("题号召回率低（漏抽）")
    if dimensions["number_precision"] < 80:
        issues.append("题号精确率低（多抽/误抽）")
    if dimensions["content_accuracy"] < 80:
        issues.append("内容提取不准确")
    if dimensions["answer_accuracy"] < 80:
        issues.append("答案提取不准确")

    return PaperScore(
        paper_id=paper.get("paper_id", ""),
        score=round(score, 1),
        passed=score >= threshold,
        threshold=threshold,
        dimensions=dimensions,
        issues=issues,
        details={
            "gt_question_count": len(gt_questions),
            "extracted_question_count": len(questions),
            "true_positives": true_positives,
            "content_total": content_total,
            "content_matches": content_matches,
            "answer_total": answer_total,
            "answer_matches": answer_matches,
        }
    )


def _content_similar(gt: str, ex: str) -> bool:
    """简单内容相似度判断"""
    if not gt or not ex:
        return False
    # 长度差异不能太大
    len_ratio = min(len(gt), len(ex)) / max(len(gt), len(ex))
    if len_ratio < 0.5:
        return False
    # 中文字符交集比例
    gt_chars = set(c for c in gt if '\u4e00' <= c <= '\u9fff')
    ex_chars = set(c for c in ex if '\u4e00' <= c <= '\u9fff')
    if not gt_chars:
        return len(gt) > 0 and len(ex) > 0 and abs(len(gt) - len(ex)) / max(len(gt), 1) < 0.3
    overlap = len(gt_chars & ex_chars) / len(gt_chars)
    return overlap >= 0.5


def _find_gt_for_paper(paper_id: str, gt_dir: str) -> Optional[tuple]:
    """
    在 GT 目录中查找与 paper_id 匹配的 meta.json + questions.json。
    支持目录结构与 paper_id 不一致的情况。
    """
    gt_root = Path(gt_dir)
    for subject_dir in gt_root.iterdir():
        if not subject_dir.is_dir():
            continue
        for paper_dir in subject_dir.iterdir():
            if not paper_dir.is_dir():
                continue
            meta_path = paper_dir / "meta.json"
            questions_path = paper_dir / "questions.json"
            if not meta_path.exists() or not questions_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("paper_id") == paper_id:
                    questions = json.loads(questions_path.read_text(encoding="utf-8"))
                    return meta, questions
            except Exception:
                continue
    return None


def score_paper_from_db(db_path: str, paper_id: str,
                        gt_dir: Optional[str] = None,
                        threshold: float = 80.0) -> Optional[PaperScore]:
    """
    从 SQLite 数据库读取试卷提取结果并评分。
    如果提供 gt_dir，则使用 GT 对比评分；否则使用启发式评分。
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    paper_row = conn.execute(
        "SELECT * FROM papers WHERE paper_id = ?", (paper_id,)
    ).fetchone()
    if not paper_row:
        conn.close()
        return None

    paper = dict(paper_row)
    question_rows = conn.execute(
        "SELECT * FROM questions WHERE paper_id = ? ORDER BY question_number", (paper_id,)
    ).fetchall()
    questions = [dict(r) for r in question_rows]
    conn.close()

    if gt_dir:
        gt_pair = _find_gt_for_paper(paper_id, gt_dir)
        if gt_pair:
            gt_meta, gt_questions = gt_pair
            return score_against_gt(paper, questions, gt_meta, gt_questions, threshold)

    return heuristic_score(paper, questions, threshold)


def score_all_papers(db_path: str,
                     gt_dir: Optional[str] = None,
                     threshold: float = 80.0) -> List[PaperScore]:
    """对数据库中所有试卷评分"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    paper_rows = conn.execute("SELECT paper_id FROM papers").fetchall()
    conn.close()

    results = []
    for row in paper_rows:
        score = score_paper_from_db(db_path, row["paper_id"], gt_dir, threshold)
        if score:
            results.append(score)
    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="单卷评分器")
    parser.add_argument("--db", required=True, help="SQLite 数据库路径")
    parser.add_argument("--gt-dir", help="GT 根目录（可选）")
    parser.add_argument("--paper-id", help="指定试卷 ID")
    parser.add_argument("--threshold", type=float, default=80.0, help="通过阈值")
    parser.add_argument("--output", help="输出 JSON 文件路径")
    args = parser.parse_args()

    if args.paper_id:
        score = score_paper_from_db(args.db, args.paper_id, args.gt_dir, args.threshold)
        if not score:
            print(f"未找到试卷: {args.paper_id}")
            return
        scores = [score]
    else:
        scores = score_all_papers(args.db, args.gt_dir, args.threshold)

    output = {
        "threshold": args.threshold,
        "total": len(scores),
        "passed": sum(1 for s in scores if s.passed),
        "failed": sum(1 for s in scores if not s.passed),
        "average_score": round(sum(s.score for s in scores) / len(scores), 1) if scores else 0,
        "papers": [asdict(s) for s in scores],
    }

    if args.output:
        Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结果已保存: {args.output}")
    else:
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
