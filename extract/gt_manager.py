#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ground Truth 管理工具
=====================

用于创建、校验、统计人工校对基准数据。

用法:
    python extract/gt_manager.py validate gt/
    python extract/gt_manager.py stats gt/
    python extract/gt_manager.py create-template gt/math/paper_099 --subject math --year 2023 --district 海淀区
"""

import json
import argparse
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Any
from datetime import datetime


GT_SCHEMA_REQUIRED = {
    "paper_id", "subject", "year", "region", "district", "school",
    "exam_type", "round", "title"
}

GT_QUESTION_REQUIRED = {"question_number", "content"}

VALID_SUBJECTS = {"math", "physics"}
VALID_EXAM_TYPES = {"期中", "期末", "月考", "模拟", "真题", "未知"}
VALID_ROUNDS = {"一模", "二模", "三模", "四模", "真题", "期中", "期末", "月考", "段考", "模拟", "未知"}
VALID_Q_TYPES = {"choice", "fill", "calculation", "proof", "experiment", "reading", "comprehensive"}


def load_json(path: Path) -> Any:
    """加载 JSON 文件"""
    return json.loads(path.read_text(encoding="utf-8"))


def validate_paper_dir(paper_dir: Path) -> Tuple[bool, List[str]]:
    """
    校验单个 paper 目录的 GT 数据。
    返回 (是否通过, 错误列表)。
    """
    errors: List[str] = []

    meta_path = paper_dir / "meta.json"
    questions_path = paper_dir / "questions.json"

    if not meta_path.exists():
        errors.append(f"缺少 meta.json")
        return False, errors
    if not questions_path.exists():
        errors.append(f"缺少 questions.json")
        return False, errors

    try:
        meta = load_json(meta_path)
    except Exception as e:
        errors.append(f"meta.json 解析失败: {e}")
        return False, errors

    try:
        questions = load_json(questions_path)
    except Exception as e:
        errors.append(f"questions.json 解析失败: {e}")
        return False, errors

    # 校验 meta 必填字段
    missing = GT_SCHEMA_REQUIRED - set(meta.keys())
    if missing:
        errors.append(f"meta.json 缺少字段: {sorted(missing)}")

    # 校验枚举值
    if meta.get("subject") not in VALID_SUBJECTS:
        errors.append(f"subject 必须是 math/physics，当前: {meta.get('subject')}")
    if meta.get("exam_type") not in VALID_EXAM_TYPES:
        errors.append(f"exam_type 不在允许列表内: {meta.get('exam_type')}")
    if meta.get("round") not in VALID_ROUNDS:
        errors.append(f"round 不在允许列表内: {meta.get('round')}")

    # 校验 questions 是数组
    if not isinstance(questions, list):
        errors.append("questions.json 必须是数组")
        return False, errors

    if not questions:
        errors.append("questions.json 为空数组")

    # 校验每道题
    for idx, q in enumerate(questions):
        prefix = f"questions[{idx}]"
        q_missing = GT_QUESTION_REQUIRED - set(q.keys())
        if q_missing:
            errors.append(f"{prefix} 缺少字段: {sorted(q_missing)}")
            continue
        if q.get("q_type") and q.get("q_type") not in VALID_Q_TYPES:
            errors.append(f"{prefix} q_type 无效: {q.get('q_type')}")
        if q.get("options"):
            labels = [opt.get("label") for opt in q["options"]]
            if len(labels) != len(set(labels)):
                errors.append(f"{prefix} 选项 label 重复")

    # 校验 expected_question_count 与实际一致（如果提供）
    if "expected_question_count" in meta and isinstance(questions, list):
        if meta["expected_question_count"] != len(questions):
            errors.append(
                f"expected_question_count ({meta['expected_question_count']}) "
                f"与实际题目数 ({len(questions)}) 不一致"
            )

    # 如果 meta 中包含 questions，警告建议使用独立文件
    if "questions" in meta:
        errors.append("建议将 questions 从 meta.json 移到独立的 questions.json")

    return len(errors) == 0, errors


def validate_gt_dir(gt_dir: Path) -> Dict[str, Any]:
    """
    校验整个 GT 目录。
    返回统计与错误信息字典。
    """
    if not gt_dir.exists():
        return {"error": f"GT 目录不存在: {gt_dir}"}

    results = {
        "total_papers": 0,
        "valid_papers": 0,
        "invalid_papers": 0,
        "subject_counts": {},
        "total_questions": 0,
        "errors": [],
    }

    for subject_dir in sorted(gt_dir.iterdir()):
        if not subject_dir.is_dir():
            continue
        subject = subject_dir.name
        for paper_dir in sorted(subject_dir.iterdir()):
            if not paper_dir.is_dir():
                continue
            results["total_papers"] += 1
            ok, errs = validate_paper_dir(paper_dir)
            if ok:
                results["valid_papers"] += 1
                meta = load_json(paper_dir / "meta.json")
                results["subject_counts"][subject] = results["subject_counts"].get(subject, 0) + 1
                questions = load_json(paper_dir / "questions.json")
                results["total_questions"] += len(questions)
            else:
                results["invalid_papers"] += 1
                for e in errs:
                    results["errors"].append(f"{paper_dir}: {e}")

    return results


def stats_gt_dir(gt_dir: Path) -> Dict[str, Any]:
    """统计 GT 目录信息"""
    results = validate_gt_dir(gt_dir)
    if "error" in results:
        return results

    stats = {
        "total_papers": results["total_papers"],
        "valid_papers": results["valid_papers"],
        "invalid_papers": results["invalid_papers"],
        "subject_counts": results["subject_counts"],
        "total_questions": results["total_questions"],
        "avg_questions_per_paper": round(
            results["total_questions"] / max(results["valid_papers"], 1), 1
        ),
    }

    # 按学科统计题目类型分布
    type_counts: Dict[str, Dict[str, int]] = {}
    for subject_dir in sorted(gt_dir.iterdir()):
        if not subject_dir.is_dir():
            continue
        subject = subject_dir.name
        type_counts[subject] = {}
        for paper_dir in sorted(subject_dir.iterdir()):
            if not paper_dir.is_dir():
                continue
            ok, _ = validate_paper_dir(paper_dir)
            if not ok:
                continue
            questions = load_json(paper_dir / "questions.json")
            for q in questions:
                q_type = q.get("q_type", "unknown")
                type_counts[subject][q_type] = type_counts[subject].get(q_type, 0) + 1

    stats["type_distribution"] = type_counts
    return stats


def create_template(paper_dir: Path, subject: str, year: int, district: str,
                    school: str = "未知", exam_type: str = "未知",
                    round_tag: str = "未知", title: str = "") -> None:
    """创建 GT 模板目录和文件"""
    paper_dir.mkdir(parents=True, exist_ok=True)
    (paper_dir / "images").mkdir(exist_ok=True)

    meta = {
        "paper_id": f"{subject}_{year}_{district}_{exam_type}_{paper_dir.name}",
        "subject": subject,
        "year": year,
        "region": "北京",
        "district": district,
        "school": school,
        "exam_type": exam_type,
        "round": round_tag,
        "title": title or paper_dir.name,
        "source_file": "",
        "total_score": 100,
        "expected_question_count": 0,
        "annotator": "",
        "annotated_at": datetime.now().isoformat(),
        "notes": "",
        "questions": []
    }

    (paper_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (paper_dir / "questions.json").write_text(
        json.dumps([], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"已创建 GT 模板: {paper_dir}")


def main():
    parser = argparse.ArgumentParser(description="Ground Truth 管理工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="校验 GT 目录")
    validate_parser.add_argument("gt_dir", help="GT 根目录")

    stats_parser = subparsers.add_parser("stats", help="统计 GT 目录")
    stats_parser.add_argument("gt_dir", help="GT 根目录")

    create_parser = subparsers.add_parser("create-template", help="创建 GT 模板")
    create_parser.add_argument("paper_dir", help="paper 目录路径")
    create_parser.add_argument("--subject", required=True, choices=["math", "physics"])
    create_parser.add_argument("--year", required=True, type=int)
    create_parser.add_argument("--district", required=True)
    create_parser.add_argument("--school", default="未知")
    create_parser.add_argument("--exam-type", default="未知")
    create_parser.add_argument("--round", default="未知")
    create_parser.add_argument("--title", default="")

    args = parser.parse_args()

    if args.command == "validate":
        gt_dir = Path(args.gt_dir)
        results = validate_gt_dir(gt_dir)
        if "error" in results:
            print(f"错误: {results['error']}")
            sys.exit(1)

        print(f"GT 校验结果:")
        print(f"  总试卷数: {results['total_papers']}")
        print(f"  有效: {results['valid_papers']}")
        print(f"  无效: {results['invalid_papers']}")
        print(f"  学科分布: {results['subject_counts']}")
        print(f"  总题数: {results['total_questions']}")
        if results["errors"]:
            print("\n错误详情:")
            for e in results["errors"]:
                print(f"  - {e}")
            sys.exit(1)
        else:
            print("\n✓ 所有 GT 数据校验通过")

    elif args.command == "stats":
        gt_dir = Path(args.gt_dir)
        stats = stats_gt_dir(gt_dir)
        if "error" in stats:
            print(f"错误: {stats['error']}")
            sys.exit(1)

        print(json.dumps(stats, ensure_ascii=False, indent=2))

    elif args.command == "create-template":
        create_template(
            Path(args.paper_dir),
            args.subject,
            args.year,
            args.district,
            args.school,
            args.exam_type,
            args.round,
            args.title,
        )


if __name__ == "__main__":
    main()
