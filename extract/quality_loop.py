#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最小数据提取质量循环控制器 (Quality Loop)
==========================================

提供三种模式:
1. audit:  对已有提取结果做质检，输出单卷评分与问题分类报告。
2. iterate: 小批量提取-评分-修复循环，直到达标或达到最大迭代次数。
3. benchmark: 将提取结果与 Ground Truth 对比，输出真实指标。

用法:
    python extract/quality_loop.py audit --db ./extract_output/local.sqlite --output ./report/
    python extract/quality_loop.py iterate --input ./raw_papers/ --sample 20 --output ./loop/
    python extract/quality_loop.py benchmark --ground-truth ./gt/ --db ./extract_output/local.sqlite
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Any, Optional

from paper_scorer import score_all_papers, score_paper_from_db, PaperScore
from issue_classifier import classify_all_issues, classify_paper_issues
from fix_rule_generator import generate_all_rules, save_rules


def run_audit(db_path: str, output_dir: Path, gt_dir: Optional[str] = None,
              threshold: float = 80.0) -> Dict[str, Any]:
    """
    对数据库中所有试卷评分并分类问题。
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    scores = score_all_papers(db_path, gt_dir, threshold)
    issue_report = classify_all_issues(scores)

    score_output = {
        "threshold": threshold,
        "total": len(scores),
        "passed": sum(1 for s in scores if s.passed),
        "failed": sum(1 for s in scores if not s.passed),
        "average_score": round(sum(s.score for s in scores) / len(scores), 1) if scores else 0,
        "papers": [{
            "paper_id": s.paper_id,
            "score": s.score,
            "passed": s.passed,
            "threshold": s.threshold,
            "dimensions": s.dimensions,
            "issues": s.issues,
            "details": s.details,
        } for s in scores],
    }

    score_path = output_dir / "paper_scores.json"
    score_path.write_text(json.dumps(score_output, ensure_ascii=False, indent=2), encoding="utf-8")

    issue_path = output_dir / "issue_report.json"
    issue_path.write_text(json.dumps(issue_report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 生成修复规则候选
    rules = generate_all_rules(issue_report)
    rules_dir = output_dir / "fix_rules"
    save_rules(rules, rules_dir)

    print(f"=== Audit 完成 ===")
    print(f"  总试卷: {score_output['total']}")
    print(f"  通过: {score_output['passed']}")
    print(f"  未通过: {score_output['failed']}")
    print(f"  平均分: {score_output['average_score']}")
    print(f"  Top 问题:")
    for issue in issue_report["top_issues"][:5]:
        print(f"    - {issue['name']}: {issue['count']} 次")
    print(f"  输出目录: {output_dir}")

    return {
        "score_output": score_output,
        "issue_report": issue_report,
        "rules_dir": str(rules_dir),
    }


def run_benchmark(gt_dir: str, db_path: str, output_dir: Path,
                  threshold: float = 80.0) -> Dict[str, Any]:
    """
    将提取结果与 Ground Truth 做详细对比。
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    gt_root = Path(gt_dir)
    gt_paper_ids = set()

    for subject_dir in sorted(gt_root.iterdir()):
        if not subject_dir.is_dir():
            continue
        for paper_dir in sorted(subject_dir.iterdir()):
            if not paper_dir.is_dir():
                continue
            meta = json.loads((paper_dir / "meta.json").read_text(encoding="utf-8"))
            gt_paper_ids.add(meta["paper_id"])

    scores = []
    for paper_id in gt_paper_ids:
        score = score_paper_from_db(db_path, paper_id, gt_dir, threshold)
        if score:
            scores.append(score)

    issue_report = classify_all_issues(scores)

    benchmark_output = {
        "threshold": threshold,
        "gt_paper_count": len(gt_paper_ids),
        "matched_paper_count": len(scores),
        "average_score": round(sum(s.score for s in scores) / len(scores), 1) if scores else 0,
        "passed": sum(1 for s in scores if s.passed),
        "failed": sum(1 for s in scores if not s.passed),
        "papers": [{
            "paper_id": s.paper_id,
            "score": s.score,
            "passed": s.passed,
            "dimensions": s.dimensions,
            "issues": s.issues,
            "details": s.details,
        } for s in scores],
    }

    output_path = output_dir / "benchmark_report.json"
    output_path.write_text(json.dumps(benchmark_output, ensure_ascii=False, indent=2), encoding="utf-8")

    issue_path = output_dir / "benchmark_issues.json"
    issue_path.write_text(json.dumps(issue_report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"=== Benchmark 完成 ===")
    print(f"  GT 试卷数: {benchmark_output['gt_paper_count']}")
    print(f"  匹配到数据库: {benchmark_output['matched_paper_count']}")
    print(f"  平均分: {benchmark_output['average_score']}")
    print(f"  通过: {benchmark_output['passed']}")
    print(f"  未通过: {benchmark_output['failed']}")

    return benchmark_output


def run_iterate(input_dir: Path, sample: int, output_dir: Path,
                max_iterations: int = 5, threshold: float = 80.0,
                gt_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    小批量提取-评分-修复循环。

    由于自动修复需要人工确认规则，iterate 模式目前执行：
    1. 随机/分层抽取 sample 套试卷到临时目录。
    2. 运行 extract_all.py 提取。
    3. 评分并生成分类报告 + 修复规则候选。
    4. 输出结果，提示人工确认规则后再次运行。

    未来可扩展 --auto-apply 自动应用低风险规则。
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # 扫描文件
    all_files = []
    for ext in ["*.pdf", "*.docx", "*.doc"]:
        all_files.extend(input_dir.rglob(ext))

    if len(all_files) < sample:
        sample = len(all_files)

    # 简单随机抽样（后续可改为分层抽样）
    import random
    random.seed(42)
    selected = random.sample(all_files, sample)

    iteration_results = []

    for iteration in range(1, max_iterations + 1):
        iter_dir = output_dir / f"iteration_{iteration}"
        if iter_dir.exists():
            shutil.rmtree(iter_dir)
        iter_dir.mkdir(parents=True)

        temp_input = iter_dir / "temp_input"
        temp_input.mkdir()
        for f in selected:
            shutil.copy2(f, temp_input / f.name)

        temp_output = iter_dir / "extract_output"

        # 运行 extract_all.py
        cmd = [
            sys.executable, "extract/extract_all.py",
            "--input", str(temp_input),
            "--output", str(temp_output),
            "--max-workers", "2",
        ]
        print(f"\n[迭代 {iteration}/{max_iterations}] 运行提取...")
        try:
            subprocess.run(cmd, check=True, cwd=Path(__file__).parent.parent)
        except subprocess.CalledProcessError as e:
            print(f"提取失败: {e}")
            break

        db_path = temp_output / "local.sqlite"
        if not db_path.exists():
            print(f"数据库未生成: {db_path}")
            break

        # 评分
        result = run_audit(str(db_path), iter_dir / "audit", gt_dir, threshold)
        iteration_results.append(result)

        avg_score = result["score_output"]["average_score"]
        failed = result["score_output"]["failed"]

        print(f"[迭代 {iteration}] 平均分: {avg_score}, 未通过: {failed}")

        if failed == 0:
            print(f"\n✓ 所有 {sample} 套试卷在迭代 {iteration} 达标！")
            break

        print(f"\n请查看修复规则候选并调整代码/规则: {iter_dir / 'audit' / 'fix_rules'}")
        if iteration < max_iterations:
            print("确认规则后再次运行 iterate 模式。\n")

    return {
        "iterations": iteration_results,
        "sample_files": [str(f) for f in selected],
    }


def main():
    parser = argparse.ArgumentParser(description="最小数据提取质量循环")
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit_parser = subparsers.add_parser("audit", help="对已有提取结果做质检")
    audit_parser.add_argument("--db", required=True, help="SQLite 数据库路径")
    audit_parser.add_argument("--output", required=True, help="输出目录")
    audit_parser.add_argument("--gt-dir", help="GT 根目录（可选）")
    audit_parser.add_argument("--threshold", type=float, default=80.0, help="通过阈值")

    benchmark_parser = subparsers.add_parser("benchmark", help="与 GT 对比")
    benchmark_parser.add_argument("--ground-truth", required=True, help="GT 根目录")
    benchmark_parser.add_argument("--db", required=True, help="SQLite 数据库路径")
    benchmark_parser.add_argument("--output", default="./benchmark_report", help="输出目录")
    benchmark_parser.add_argument("--threshold", type=float, default=80.0, help="通过阈值")

    iterate_parser = subparsers.add_parser("iterate", help="小批量迭代循环")
    iterate_parser.add_argument("--input", required=True, help="原始试卷目录")
    iterate_parser.add_argument("--output", required=True, help="循环输出目录")
    iterate_parser.add_argument("--sample", type=int, default=20, help="每轮抽样套数")
    iterate_parser.add_argument("--max-iterations", type=int, default=5, help="最大迭代次数")
    iterate_parser.add_argument("--gt-dir", help="GT 根目录（可选）")
    iterate_parser.add_argument("--threshold", type=float, default=80.0, help="通过阈值")

    args = parser.parse_args()

    if args.command == "audit":
        run_audit(args.db, Path(args.output), args.gt_dir, args.threshold)
    elif args.command == "benchmark":
        run_benchmark(args.ground_truth, args.db, Path(args.output), args.threshold)
    elif args.command == "iterate":
        run_iterate(Path(args.input), args.sample, Path(args.output),
                    args.max_iterations, args.threshold, args.gt_dir)


if __name__ == "__main__":
    main()
