#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
问题分类器 (Issue Classifier)
==============================

根据单卷评分结果，把质量问题归因到具体修复模块，
并输出结构化的问题分类报告。

用法:
    from issue_classifier import classify_paper_issues, classify_all_issues
"""

import json
from pathlib import Path
from typing import Dict, List, Any
from collections import Counter

from paper_scorer import PaperScore


ISSUE_TYPES = {
    "QUESTION_NUMBER_LOSS": {
        "name": "题号识别失败/漏抽",
        "check": lambda d, i: d.get("extraction_success", 100) < 80 or d.get("number_recall", 100) < 80 or "题号" in " ".join(i),
        "fix_module": "extract_all.py: 题号正则、分卷结构解析",
    },
    "NOISE_POLLUTION": {
        "name": "内容被噪声污染",
        "check": lambda d, i: d.get("content_integrity", 100) < 80 or "噪声" in " ".join(i),
        "fix_module": "extract_all.py: NOISE_TEXT_PATTERNS 内容清洗",
    },
    "IMAGE_MISPLACEMENT": {
        "name": "图片归属错误/缺失",
        "check": lambda d, i: d.get("image_placement", 100) < 80 or "图片" in " ".join(i),
        "fix_module": "extract_all.py: 图片 bbox 空间匹配",
    },
    "METADATA_MISSING": {
        "name": "元数据缺失/错误",
        "check": lambda d, i: d.get("metadata_accuracy", 100) < 80 or "元数据" in " ".join(i),
        "fix_module": "metadata_mappings.py: 区名/学校/考试类型别名",
    },
    "ANSWER_MISSING": {
        "name": "答案/解析缺失",
        "check": lambda d, i: d.get("answer_coverage", 100) < 80 or d.get("answer_accuracy", 100) < 80 or "答案" in " ".join(i),
        "fix_module": "extract_all.py: 解析版配对、答案提取",
    },
    "TAG_MISSING": {
        "name": "标签缺失/未分类",
        "check": lambda d, i: "标签" in " ".join(i),
        "fix_module": "tag_rules.py: 知识/能力/方法规则补全",
    },
    "TAG_INFLATION": {
        "name": "标签膨胀",
        "check": lambda d, i: "膨胀" in " ".join(i),
        "fix_module": "tag_rules.py: 互斥与抑制规则",
    },
    "LOW_OVERALL": {
        "name": "整体质量低",
        "check": lambda d, i: True,  # 兜底类型
        "fix_module": "综合排查",
    },
}


def classify_paper_issues(score: PaperScore) -> List[Dict[str, str]]:
    """
    对单张试卷的评分结果进行问题分类。
    返回 [{"type": "QUESTION_NUMBER_LOSS", "name": "...", "fix_module": "..."}, ...]
    """
    issues = []
    dimensions = score.dimensions
    issue_texts = score.issues

    matched_any = False
    for code, info in ISSUE_TYPES.items():
        if code == "LOW_OVERALL":
            continue
        if info["check"](dimensions, issue_texts):
            issues.append({
                "type": code,
                "name": info["name"],
                "fix_module": info["fix_module"],
            })
            matched_any = True

    # 如果没有任何具体类型匹配，但分数未通过，归到整体质量低
    if not matched_any and not score.passed:
        issues.append({
            "type": "LOW_OVERALL",
            "name": ISSUE_TYPES["LOW_OVERALL"]["name"],
            "fix_module": ISSUE_TYPES["LOW_OVERALL"]["fix_module"],
        })

    return issues


def classify_all_issues(scores: List[PaperScore]) -> Dict[str, Any]:
    """
    对所有试卷评分结果进行分类汇总。
    """
    all_issues = []
    failed_papers = []

    for score in scores:
        if score.passed:
            continue
        paper_issues = classify_paper_issues(score)
        all_issues.extend([i["type"] for i in paper_issues])
        failed_papers.append({
            "paper_id": score.paper_id,
            "score": score.score,
            "issues": paper_issues,
            "dimensions": score.dimensions,
        })

    issue_counter = Counter(all_issues)
    top_issues = [
        {
            "type": t,
            "name": ISSUE_TYPES[t]["name"],
            "count": c,
            "fix_module": ISSUE_TYPES[t]["fix_module"],
        }
        for t, c in issue_counter.most_common()
    ]

    return {
        "total_papers": len(scores),
        "failed_papers": len(failed_papers),
        "pass_rate": round(
            (len(scores) - len(failed_papers)) / len(scores) * 100, 1
        ) if scores else 0.0,
        "top_issues": top_issues,
        "failed_paper_details": failed_papers,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="问题分类器")
    parser.add_argument("--score-file", required=True, help="paper_scorer 输出的 JSON 文件")
    parser.add_argument("--output", help="输出 JSON 文件路径")
    args = parser.parse_args()

    data = json.loads(Path(args.score_file).read_text(encoding="utf-8"))
    scores = [PaperScore(**s) for s in data["papers"]]
    report = classify_all_issues(scores)

    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"分类报告已保存: {args.output}")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
