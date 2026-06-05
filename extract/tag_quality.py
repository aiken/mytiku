#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
标签质量检查脚本
================
监控和评估多维度标签体系的覆盖率和准确性。

用法:
    python extract/tag_quality.py --db ./extract_output/local.sqlite
    python extract/tag_quality.py --db ./extract_output/local.sqlite --subject math --output-dir ./reports
"""

import os
import sys
import json
import csv
import argparse
import sqlite3
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from collections import Counter


# ----------------------------------------------------------------------
# 数据模型
# ----------------------------------------------------------------------
@dataclass
class DimensionCoverage:
    """单个维度的覆盖率统计"""
    dimension: str          # knowledge / ability / feature / method / position
    total: int              # 总题目数
    has_tag: int            # 有标签的题目数
    coverage_rate: float    # 覆盖率 (%)
    empty_count: int        # 空值/空列表数
    uncategorized_count: int  # 标记为"未分类"的数量
    top_tags: List[Dict[str, Any]]  # Top 10 标签分布


@dataclass
class QualityReport:
    """完整质量报告"""
    db_path: str
    subject: Optional[str]
    total_questions: int
    total_papers: int
    dimensions: List[DimensionCoverage]
    problem_questions: List[Dict[str, Any]]  # 需要人工复核的题目
    summary: Dict[str, Any]


# ----------------------------------------------------------------------
# 核心检查逻辑
# ----------------------------------------------------------------------
class TagQualityChecker:
    """标签质量检查器"""

    # 维度字段映射
    DIMENSION_FIELDS = {
        "knowledge": "knowledge_tags",
        "ability": "ability_tags",
        "feature": "feature_tags",
        "method": "method_tags",
        "position": "position_tag",
    }

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"数据库不存在: {db_path}")
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row

    def close(self):
        self.conn.close()

    def _has_tag(self, value: Optional[str], dimension: str) -> bool:
        """判断字段是否有有效标签"""
        if value is None:
            return False
        if dimension == "position":
            # position_tag 是字符串
            return value.strip() != "" and value != "未知"
        else:
            # 其他是 JSON 数组
            try:
                tags = json.loads(value)
                if isinstance(tags, list):
                    return len(tags) > 0 and tags != ["未分类"]
                return False
            except (json.JSONDecodeError, TypeError):
                return False

    def _parse_tags(self, value: Optional[str], dimension: str) -> List[str]:
        """解析标签值为列表"""
        if value is None:
            return []
        if dimension == "position":
            return [value.strip()] if value.strip() else []
        try:
            tags = json.loads(value)
            if isinstance(tags, list):
                return [t for t in tags if t and t != "未分类"]
            return []
        except (json.JSONDecodeError, TypeError):
            return []

    def check_dimension(self, dimension: str, subject: Optional[str] = None) -> DimensionCoverage:
        """检查单个维度的覆盖率"""
        field = self.DIMENSION_FIELDS[dimension]

        # 基础查询
        base_sql = """
            SELECT q.question_id, q.question_number, q.{field}, q.q_type, q.difficulty,
                   q.content, p.subject, p.region, p.exam_type, p.year, p.title as paper_title
            FROM questions q
            JOIN papers p ON q.paper_id = p.paper_id
            WHERE 1=1
        """.format(field=field)
        params = []

        if subject:
            base_sql += " AND p.subject = ?"
            params.append(subject)

        rows = self.conn.execute(base_sql, params).fetchall()
        total = len(rows)

        if total == 0:
            return DimensionCoverage(
                dimension=dimension, total=0, has_tag=0,
                coverage_rate=0.0, empty_count=0, uncategorized_count=0,
                top_tags=[]
            )

        # 统计
        has_tag_count = 0
        empty_count = 0
        uncategorized_count = 0
        tag_counter = Counter()

        for row in rows:
            value = row[field]
            if self._has_tag(value, dimension):
                has_tag_count += 1
                tags = self._parse_tags(value, dimension)
                for t in tags:
                    tag_counter[t] += 1
            else:
                if value is None or value == "" or value == "[]":
                    empty_count += 1
                elif value == '["未分类"]':
                    uncategorized_count += 1
                else:
                    empty_count += 1

        # Top 10 标签
        top_tags = [{"tag": tag, "count": count, "rate": round(count / total * 100, 1)}
                    for tag, count in tag_counter.most_common(10)]

        return DimensionCoverage(
            dimension=dimension,
            total=total,
            has_tag=has_tag_count,
            coverage_rate=round(has_tag_count / total * 100, 1),
            empty_count=empty_count,
            uncategorized_count=uncategorized_count,
            top_tags=top_tags,
        )

    def find_problem_questions(self, subject: Optional[str] = None,
                                min_dimensions: int = 3) -> List[Dict[str, Any]]:
        """
        找出需要人工复核的题目（缺失维度 ≥ min_dimensions 或知识点为未分类）
        """
        sql = """
            SELECT q.question_id, q.question_number, q.q_type, q.difficulty,
                   q.content, q.knowledge_tags, q.ability_tags, q.feature_tags,
                   q.method_tags, q.position_tag, q.tags,
                   p.subject, p.region, p.exam_type, p.year, p.title as paper_title
            FROM questions q
            JOIN papers p ON q.paper_id = p.paper_id
            WHERE 1=1
        """
        params = []
        if subject:
            sql += " AND p.subject = ?"
            params.append(subject)

        rows = self.conn.execute(sql, params).fetchall()

        problems = []
        for row in rows:
            missing_dims = []
            for dim, field in self.DIMENSION_FIELDS.items():
                if not self._has_tag(row[field], dim):
                    missing_dims.append(dim)

            # 条件：缺失 ≥ min_dimensions 个维度，或知识点缺失
            if len(missing_dims) >= min_dimensions or "knowledge" in missing_dims:
                content_preview = (row["content"] or "")[:100] + "..." if (row["content"] or "") else ""
                problems.append({
                    "question_id": row["question_id"],
                    "question_number": row["question_number"],
                    "subject": row["subject"],
                    "q_type": row["q_type"],
                    "difficulty": row["difficulty"],
                    "paper_title": row["paper_title"],
                    "year": row["year"],
                    "region": row["region"],
                    "exam_type": row["exam_type"],
                    "content_preview": content_preview,
                    "missing_dimensions": ", ".join(missing_dims),
                    "missing_count": len(missing_dims),
                    "knowledge_tags": row["knowledge_tags"],
                    "ability_tags": row["ability_tags"],
                    "feature_tags": row["feature_tags"],
                    "method_tags": row["method_tags"],
                    "position_tag": row["position_tag"],
                })

        # 按缺失维度数降序排列
        problems.sort(key=lambda x: (-x["missing_count"], x["question_id"]))
        return problems

    def generate_report(self, subject: Optional[str] = None) -> QualityReport:
        """生成完整质量报告"""
        # 统计试卷数
        paper_sql = "SELECT COUNT(*) FROM papers"
        if subject:
            paper_sql += " WHERE subject = ?"
            paper_count = self.conn.execute(paper_sql, [subject]).fetchone()[0]
        else:
            paper_count = self.conn.execute(paper_sql).fetchone()[0]

        # 统计题目数
        q_sql = """
            SELECT COUNT(*) FROM questions q
            JOIN papers p ON q.paper_id = p.paper_id
            WHERE 1=1
        """
        q_params = []
        if subject:
            q_sql += " AND p.subject = ?"
            q_params.append(subject)
        total_questions = self.conn.execute(q_sql, q_params).fetchone()[0]

        # 各维度检查
        dimensions = []
        for dim in ["knowledge", "ability", "feature", "method", "position"]:
            dim_coverage = self.check_dimension(dim, subject)
            dimensions.append(dim_coverage)

        # 问题题目
        problem_questions = self.find_problem_questions(subject)

        # 汇总
        summary = {
            "pass_knowledge": dimensions[0].coverage_rate >= 85,
            "pass_ability": dimensions[1].coverage_rate >= 70,
            "pass_feature": dimensions[2].coverage_rate >= 70,
            "pass_method": dimensions[3].coverage_rate >= 70,
            "pass_position": dimensions[4].coverage_rate >= 95,
            "problem_count": len(problem_questions),
            "problem_rate": round(len(problem_questions) / total_questions * 100, 1) if total_questions else 0,
        }

        return QualityReport(
            db_path=str(self.db_path),
            subject=subject,
            total_questions=total_questions,
            total_papers=paper_count,
            dimensions=dimensions,
            problem_questions=problem_questions,
            summary=summary,
        )


# ----------------------------------------------------------------------
# 输出格式化
# ----------------------------------------------------------------------
class ReportFormatter:
    """报告格式化输出"""

    @staticmethod
    def terminal(report: QualityReport):
        """终端表格输出"""
        print("=" * 70)
        print(f"  标签质量检查报告")
        print(f"  数据库: {report.db_path}")
        if report.subject:
            print(f"  学科: {report.subject}")
        print(f"  试卷数: {report.total_papers} | 题目数: {report.total_questions}")
        print("=" * 70)

        # 维度覆盖率表格
        print("\n【各维度标签覆盖率】")
        print("-" * 70)
        print(f"{'维度':<12} {'总题数':>8} {'有标签':>8} {'覆盖率':>8} {'空值':>8} {'未分类':>8}")
        print("-" * 70)
        for dim in report.dimensions:
            status = "✅" if dim.coverage_rate >= 70 else "⚠️" if dim.coverage_rate >= 50 else "❌"
            print(f"{dim.dimension:<12} {dim.total:>8} {dim.has_tag:>8} "
                  f"{dim.coverage_rate:>7.1f}% {dim.empty_count:>8} {dim.uncategorized_count:>8} {status}")
        print("-" * 70)

        # Top 标签
        print("\n【各维度 Top 5 标签】")
        for dim in report.dimensions:
            if dim.top_tags:
                print(f"\n  {dim.dimension}:")
                for tag_info in dim.top_tags[:5]:
                    print(f"    {tag_info['tag']:<20} {tag_info['count']:>5} 题 ({tag_info['rate']:>5.1f}%)")

        # 问题题目摘要
        print("\n【需要人工复核的题目】")
        print(f"  总数: {len(report.problem_questions)} 题 "
              f"({report.summary['problem_rate']:.1f}%)")
        if report.problem_questions:
            print(f"\n  {'题号':<6} {'类型':<8} {'学科':<6} {'缺失维度':<20} {'内容预览'}")
            print("  " + "-" * 60)
            for q in report.problem_questions[:10]:
                print(f"  {q['question_number']:<6} {q['q_type']:<8} {q['subject']:<6} "
                      f"{q['missing_dimensions']:<20} {q['content_preview'][:40]}")
            if len(report.problem_questions) > 10:
                print(f"  ... 还有 {len(report.problem_questions) - 10} 题")

        # 验收标准
        print("\n【验收标准检查】")
        checks = [
            ("知识点覆盖率 ≥ 85%", report.summary["pass_knowledge"]),
            ("能力维度覆盖率 ≥ 70%", report.summary["pass_ability"]),
            ("题型特征覆盖率 ≥ 70%", report.summary["pass_feature"]),
            ("解题方法覆盖率 ≥ 70%", report.summary["pass_method"]),
            ("考试定位覆盖率 ≥ 95%", report.summary["pass_position"]),
        ]
        for check, passed in checks:
            status = "✅ 通过" if passed else "❌ 未通过"
            print(f"  {check:<30} {status}")

        print("\n" + "=" * 70)

    @staticmethod
    def csv_report(report: QualityReport, output_path: str):
        """输出 CSV 问题题目列表"""
        if not report.problem_questions:
            print(f"无问题题目，跳过 CSV 输出")
            return

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "question_id", "question_number", "subject", "q_type",
                "difficulty", "paper_title", "year", "region", "exam_type",
                "missing_dimensions", "missing_count", "content_preview",
                "knowledge_tags", "ability_tags", "feature_tags",
                "method_tags", "position_tag",
            ])
            writer.writeheader()
            writer.writerows(report.problem_questions)
        print(f"CSV 报告已保存: {output_path}")

    @staticmethod
    def html_report(report: QualityReport, output_path: str):
        """输出 HTML 可视化报告"""
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>标签质量检查报告</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; background: #f8fafc; }}
h1 {{ color: #1e3a5f; border-bottom: 3px solid #1e3a5f; padding-bottom: 10px; }}
h2 {{ color: #334155; margin-top: 30px; }}
.summary {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 20px; }}
table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
th {{ background: #1e3a5f; color: white; padding: 12px; text-align: left; }}
td {{ padding: 10px 12px; border-bottom: 1px solid #e2e8f0; }}
tr:hover {{ background: #f1f5f9; }}
.pass {{ color: #16a34a; font-weight: bold; }}
.fail {{ color: #dc2626; font-weight: bold; }}
.warn {{ color: #d97706; font-weight: bold; }}
.tag-bar {{ display: inline-block; height: 20px; background: #3b82f6; border-radius: 4px; }}
.meter {{ width: 100%; height: 24px; background: #e2e8f0; border-radius: 12px; overflow: hidden; }}
.meter-fill {{ height: 100%; background: linear-gradient(90deg, #3b82f6, #8b5cf6); border-radius: 12px; transition: width 0.3s; }}
</style>
</head>
<body>
<h1>🏷️ 标签质量检查报告</h1>
<div class="summary">
  <p><strong>数据库:</strong> {report.db_path}</p>
  <p><strong>学科:</strong> {report.subject or "全部"}</p>
  <p><strong>试卷数:</strong> {report.total_papers} | <strong>题目数:</strong> {report.total_questions}</p>
  <p><strong>问题题目:</strong> {len(report.problem_questions)} 题 ({report.summary['problem_rate']:.1f}%)</p>
</div>

<h2>📊 各维度覆盖率</h2>
<table>
<tr><th>维度</th><th>总题数</th><th>有标签</th><th>覆盖率</th><th>可视化</th><th>空值</th><th>未分类</th><th>状态</th></tr>
"""
        for dim in report.dimensions:
            status_class = "pass" if dim.coverage_rate >= 70 else "warn" if dim.coverage_rate >= 50 else "fail"
            status_text = "✅ 通过" if dim.coverage_rate >= 70 else "⚠️ 警告" if dim.coverage_rate >= 50 else "❌ 未通过"
            html += f"""
<tr>
  <td><strong>{dim.dimension}</strong></td>
  <td>{dim.total}</td>
  <td>{dim.has_tag}</td>
  <td>{dim.coverage_rate:.1f}%</td>
  <td><div class="meter"><div class="meter-fill" style="width: {dim.coverage_rate}%"></div></div></td>
  <td>{dim.empty_count}</td>
  <td>{dim.uncategorized_count}</td>
  <td class="{status_class}">{status_text}</td>
</tr>
"""
        html += "</table>"

        # Top 标签
        html += "<h2>🏷️ 各维度 Top 10 标签</h2>"
        for dim in report.dimensions:
            if dim.top_tags:
                html += f"<h3>{dim.dimension}</h3><table><tr><th>标签</th><th>数量</th><th>占比</th></tr>"
                for tag in dim.top_tags:
                    html += f"<tr><td>{tag['tag']}</td><td>{tag['count']}</td><td>{tag['rate']:.1f}%</td></tr>"
                html += "</table>"

        # 问题题目
        if report.problem_questions:
            html += f"""
<h2>⚠️ 需要人工复核的题目（前 50 题）</h2>
<table>
<tr><th>题号</th><th>类型</th><th>学科</th><th>难度</th><th>缺失维度</th><th>内容预览</th></tr>
"""
            for q in report.problem_questions[:50]:
                html += f"""
<tr>
  <td>{q['question_number']}</td>
  <td>{q['q_type']}</td>
  <td>{q['subject']}</td>
  <td>{q['difficulty']}</td>
  <td class="fail">{q['missing_dimensions']}</td>
  <td>{q['content_preview'][:80]}</td>
</tr>
"""
            html += "</table>"

        html += """
</body>
</html>
"""
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"HTML 报告已保存: {output_path}")


# ----------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="标签质量检查脚本 — 评估多维度标签覆盖率",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", required=True, help="SQLite 数据库路径")
    parser.add_argument("--subject", choices=["math", "physics"], help="按学科筛选")
    parser.add_argument("--output-dir", default="./reports", help="输出目录（默认 ./reports）")
    parser.add_argument("--format", choices=["terminal", "csv", "html", "all"], default="all",
                        help="输出格式（默认 all）")
    parser.add_argument("--min-dimensions", type=int, default=3,
                        help="问题题目判定阈值：缺失维度数（默认 3）")
    args = parser.parse_args()

    # 检查数据库
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"错误: 数据库不存在: {db_path}")
        sys.exit(1)

    # 生成报告
    checker = TagQualityChecker(str(db_path))
    try:
        report = checker.generate_report(subject=args.subject)
    finally:
        checker.close()

    # 输出
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    subject_suffix = f"_{args.subject}" if args.subject else ""

    if args.format in ("terminal", "all"):
        ReportFormatter.terminal(report)

    if args.format in ("csv", "all"):
        csv_path = output_dir / f"tag_quality_problems{subject_suffix}.csv"
        ReportFormatter.csv_report(report, str(csv_path))

    if args.format in ("html", "all"):
        html_path = output_dir / f"tag_quality_report{subject_suffix}.html"
        ReportFormatter.html_report(report, str(html_path))

    # 返回码：有未通过的检查则返回 1
    all_passed = all([
        report.summary["pass_knowledge"],
        report.summary["pass_ability"],
        report.summary["pass_feature"],
        report.summary["pass_method"],
        report.summary["pass_position"],
    ])
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
