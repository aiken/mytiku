#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
试卷元数据校验脚本
==================
校验和评估试卷元数据（district/school/round/exam_type）的准确率。

用法:
    python extract/metadata_validator.py --db ./extract_output/local.sqlite
    python extract/metadata_validator.py --db ./extract_output/local.sqlite --subject math --output-dir ./reports
"""

import os
import sys
import json
import csv
import argparse
import sqlite3
from pathlib import Path
from typing import Dict, List, Any, Optional
from collections import Counter

from metadata_mappings import (
    DISTRICT_STANDARDIZATION, 
    SCHOOL_ALIASES, 
    EXAM_TYPE_STANDARDIZATION,
    standardize_metadata
)


# ----------------------------------------------------------------------
# 校验逻辑
# ----------------------------------------------------------------------
class MetadataValidator:
    """元数据校验器"""

    # 北京市标准行政区划
    VALID_DISTRICTS = set(DISTRICT_STANDARDIZATION.values())
    
    # 标准考试类型
    VALID_EXAM_TYPES = {"期中", "期末", "月考", "一模", "二模", "三模", "中考模拟", "真题", "段考", "未知"}
    
    # 标准学校名（从映射表提取）
    VALID_SCHOOLS = set(SCHOOL_ALIASES.values())

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"数据库不存在: {db_path}")
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row

    def close(self):
        self.conn.close()

    def validate_district(self, district: str) -> bool:
        """校验区名是否标准"""
        if not district or district == "未知":
            return False
        return district in self.VALID_DISTRICTS

    def validate_school(self, school: str) -> bool:
        """校验学校名是否标准"""
        if not school or school == "未知":
            return False
        # 检查是否是标准名，或包含标准关键词
        for standard in self.VALID_SCHOOLS:
            if standard in school or school in standard:
                return True
        # 检查是否是"区名+统考"格式
        if "统考" in school:
            district_part = school.replace("统考", "").strip()
            if district_part + "区" in self.VALID_DISTRICTS:
                return True
        return False

    def validate_exam_type(self, exam_type: str) -> bool:
        """校验考试类型是否标准"""
        if not exam_type or exam_type == "未知":
            return False
        return exam_type in self.VALID_EXAM_TYPES

    def validate_round(self, round: str) -> bool:
        """校验考试轮次是否标准"""
        valid_rounds = {"一模", "二模", "三模", "四模", "真题", "期中", "期末", "月考", "段考", "模拟", "未知"}
        if not round or round == "未知":
            return False
        return round in valid_rounds

    def check_all(self, subject: Optional[str] = None) -> Dict[str, Any]:
        """执行完整校验"""
        # 检查 papers 表是否有 round 列
        has_round = False
        try:
            self.conn.execute("SELECT round FROM papers LIMIT 1")
            has_round = True
        except sqlite3.OperationalError:
            pass

        sql = """
            SELECT p.paper_id, p.title, p.subject, p.district, p.school, 
                   p.exam_type, p.year, p.region,
                   COUNT(q.question_id) as question_count
            FROM papers p
            LEFT JOIN questions q ON p.paper_id = q.paper_id
            WHERE 1=1
        """
        if has_round:
            # 需要重新构建 SQL，包含 round 列
            sql = """
                SELECT p.paper_id, p.title, p.subject, p.district, p.school, 
                       p.exam_type, p.year, p.region, p.round,
                       COUNT(q.question_id) as question_count
                FROM papers p
                LEFT JOIN questions q ON p.paper_id = q.paper_id
                WHERE 1=1
            """
        params = []
        if subject:
            sql += " AND p.subject = ?"
            params.append(subject)
        sql += " GROUP BY p.paper_id"

        rows = self.conn.execute(sql, params).fetchall()
        total = len(rows)

        if total == 0:
            return {"total": 0, "message": "无试卷数据"}

        # 统计
        district_valid = 0
        school_valid = 0
        exam_type_valid = 0
        round_valid = 0
        all_valid = 0

        district_issues = []
        school_issues = []
        exam_type_issues = []
        round_issues = []

        district_counter = Counter()
        school_counter = Counter()
        exam_type_counter = Counter()
        round_counter = Counter()

        for row in rows:
            d_ok = self.validate_district(row["district"])
            s_ok = self.validate_school(row["school"])
            e_ok = self.validate_exam_type(row["exam_type"])
            r_ok = True  # 默认通过，如果没有 round 列
            if has_round:
                r_ok = self.validate_round(row["round"])

            if d_ok:
                district_valid += 1
            else:
                district_issues.append({
                    "paper_id": row["paper_id"],
                    "title": row["title"],
                    "field": "district",
                    "value": row["district"],
                    "year": row["year"],
                })

            if s_ok:
                school_valid += 1
            else:
                school_issues.append({
                    "paper_id": row["paper_id"],
                    "title": row["title"],
                    "field": "school",
                    "value": row["school"],
                    "year": row["year"],
                })

            if e_ok:
                exam_type_valid += 1
            else:
                exam_type_issues.append({
                    "paper_id": row["paper_id"],
                    "title": row["title"],
                    "field": "exam_type",
                    "value": row["exam_type"],
                    "year": row["year"],
                })

            if has_round:
                if r_ok:
                    round_valid += 1
                else:
                    round_issues.append({
                        "paper_id": row["paper_id"],
                        "title": row["title"],
                        "field": "round",
                        "value": row["round"],
                        "year": row["year"],
                    })

            if d_ok and s_ok and e_ok and r_ok:
                all_valid += 1

            district_counter[row["district"]] += 1
            school_counter[row["school"]] += 1
            exam_type_counter[row["exam_type"]] += 1
            if has_round:
                round_counter[row["round"]] += 1

        result = {
            "total": total,
            "district": {
                "valid": district_valid,
                "rate": round(district_valid / total * 100, 1),
                "issues": district_issues,
                "distribution": [{"value": k, "count": v} for k, v in district_counter.most_common(20)],
            },
            "school": {
                "valid": school_valid,
                "rate": round(school_valid / total * 100, 1),
                "issues": school_issues,
                "distribution": [{"value": k, "count": v} for k, v in school_counter.most_common(20)],
            },
            "exam_type": {
                "valid": exam_type_valid,
                "rate": round(exam_type_valid / total * 100, 1),
                "issues": exam_type_issues,
                "distribution": [{"value": k, "count": v} for k, v in exam_type_counter.most_common(20)],
            },
            "all_valid": {
                "count": all_valid,
                "rate": round(all_valid / total * 100, 1),
            },
        }

        if has_round:
            result["round"] = {
                "valid": round_valid,
                "rate": round(round_valid / total * 100, 1),
                "issues": round_issues,
                "distribution": [{"value": k, "count": v} for k, v in round_counter.most_common(20)],
            }

        return result


# ----------------------------------------------------------------------
# 输出格式化
# ----------------------------------------------------------------------
def terminal_report(result: Dict[str, Any], subject: Optional[str] = None):
    """终端报告输出"""
    total = result["total"]
    if total == 0:
        print("无试卷数据")
        return

    print("=" * 70)
    print(f"  试卷元数据校验报告")
    if subject:
        print(f"  学科: {subject}")
    print(f"  试卷总数: {total}")
    print("=" * 70)

    # 各字段准确率
    print("\n【各字段准确率】")
    print("-" * 70)
    print(f"{'字段':<12} {'有效数':>8} {'准确率':>10} {'状态'}")
    print("-" * 70)

    fields = [
        ("区名", result["district"]),
        ("学校", result["school"]),
        ("考试类型", result["exam_type"]),
    ]
    if "round" in result:
        fields.append(("考试轮次", result["round"]))

    for name, data in fields:
        status = "✅" if data["rate"] >= 80 else "⚠️" if data["rate"] >= 60 else "❌"
        print(f"{name:<12} {data['valid']:>8} {data['rate']:>9.1f}% {status}")

    print("-" * 70)
    all_rate = result["all_valid"]["rate"]
    all_status = "✅" if all_rate >= 80 else "⚠️" if all_rate >= 60 else "❌"
    print(f"{'全部有效':<12} {result['all_valid']['count']:>8} {all_rate:>9.1f}% {all_status}")
    print("-" * 70)

    # 分布统计
    print("\n【区名分布 Top 10】")
    for item in result["district"]["distribution"][:10]:
        print(f"  {item['value']:<15} {item['count']:>4} 份")

    print("\n【考试类型分布 Top 10】")
    for item in result["exam_type"]["distribution"][:10]:
        print(f"  {item['value']:<15} {item['count']:>4} 份")

    # 问题列表
    print("\n【问题试卷列表】")
    all_issues = (
        result["district"]["issues"] +
        result["school"]["issues"] +
        result["exam_type"]["issues"]
    )
    if "round" in result:
        all_issues += result["round"]["issues"]

    if all_issues:
        # 按试卷分组
        paper_issues = {}
        for issue in all_issues:
            pid = issue["paper_id"]
            if pid not in paper_issues:
                paper_issues[pid] = {
                    "title": issue["title"],
                    "year": issue["year"],
                    "fields": [],
                }
            paper_issues[pid]["fields"].append(f"{issue['field']}={issue['value']}")

        print(f"  共 {len(paper_issues)} 份试卷存在元数据问题")
        print(f"\n  {'试卷标题':<40} {'年份':<6} {'问题字段'}")
        print("  " + "-" * 80)
        for pid, info in list(paper_issues.items())[:10]:
            title = info["title"][:35] + "..." if len(info["title"]) > 35 else info["title"]
            print(f"  {title:<40} {info['year']:<6} {', '.join(info['fields'])}")
        if len(paper_issues) > 10:
            print(f"  ... 还有 {len(paper_issues) - 10} 份")
    else:
        print("  ✅ 所有试卷元数据均有效")

    # 验收标准
    print("\n【验收标准检查】")
    checks = [
        ("区名准确率 ≥ 80%", result["district"]["rate"] >= 80),
        ("学校准确率 ≥ 80%", result["school"]["rate"] >= 80),
        ("考试类型准确率 ≥ 80%", result["exam_type"]["rate"] >= 80),
    ]
    if "round" in result:
        checks.append(("轮次准确率 ≥ 80%", result["round"]["rate"] >= 80))
    for check, passed in checks:
        status = "✅ 通过" if passed else "❌ 未通过"
        print(f"  {check:<30} {status}")

    print("\n" + "=" * 70)


def csv_report(result: Dict[str, Any], output_path: str):
    """输出 CSV 问题列表"""
    all_issues = (
        result["district"]["issues"] +
        result["school"]["issues"] +
        result["exam_type"]["issues"]
    )
    if "round" in result:
        all_issues += result["round"]["issues"]

    if not all_issues:
        print("无问题试卷，跳过 CSV 输出")
        return

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["paper_id", "title", "field", "value", "year"])
        writer.writeheader()
        writer.writerows(all_issues)
    print(f"CSV 报告已保存: {output_path}")


def html_report(result: Dict[str, Any], output_path: str, subject: Optional[str] = None):
    """输出 HTML 可视化报告"""
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>试卷元数据校验报告</title>
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
.meter {{ width: 100%; height: 24px; background: #e2e8f0; border-radius: 12px; overflow: hidden; }}
.meter-fill {{ height: 100%; background: linear-gradient(90deg, #3b82f6, #8b5cf6); border-radius: 12px; }}
</style>
</head>
<body>
<h1>📋 试卷元数据校验报告</h1>
<div class="summary">
  <p><strong>学科:</strong> {subject or "全部"}</p>
  <p><strong>试卷总数:</strong> {result['total']}</p>
  <p><strong>全部有效:</strong> {result['all_valid']['count']} 份 ({result['all_valid']['rate']:.1f}%)</p>
</div>

<h2>📊 各字段准确率</h2>
<table>
<tr><th>字段</th><th>有效数</th><th>准确率</th><th>可视化</th><th>状态</th></tr>
"""
    fields = [
        ("区名", result["district"]),
        ("学校", result["school"]),
        ("考试类型", result["exam_type"]),
    ]
    if "round" in result:
        fields.append(("考试轮次", result["round"]))
    for name, data in fields:
        status_class = "pass" if data["rate"] >= 80 else "warn" if data["rate"] >= 60 else "fail"
        status_text = "✅ 通过" if data["rate"] >= 80 else "⚠️ 警告" if data["rate"] >= 60 else "❌ 未通过"
        html += f"""
<tr>
  <td><strong>{name}</strong></td>
  <td>{data['valid']}/{result['total']}</td>
  <td>{data['rate']:.1f}%</td>
  <td><div class="meter"><div class="meter-fill" style="width: {data['rate']}%"></div></div></td>
  <td class="{status_class}">{status_text}</td>
</tr>
"""
    html += "</table>"

    # 分布统计
    for name, key in [("区名", "district"), ("考试类型", "exam_type"), ("学校", "school")]:
        html += f"<h2>🏷️ {name}分布 Top 10</h2><table><tr><th>{name}</th><th>数量</th></tr>"
        for item in result[key]["distribution"][:10]:
            html += f"<tr><td>{item['value']}</td><td>{item['count']}</td></tr>"
        html += "</table>"
    if "round" in result:
        html += f"<h2>🏷️ 考试轮次分布 Top 10</h2><table><tr><th>考试轮次</th><th>数量</th></tr>"
        for item in result["round"]["distribution"][:10]:
            html += f"<tr><td>{item['value']}</td><td>{item['count']}</td></tr>"
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
        description="试卷元数据校验脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", required=True, help="SQLite 数据库路径")
    parser.add_argument("--subject", choices=["math", "physics"], help="按学科筛选")
    parser.add_argument("--output-dir", default="./reports", help="输出目录")
    parser.add_argument("--format", choices=["terminal", "csv", "html", "all"], default="all")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"错误: 数据库不存在: {db_path}")
        sys.exit(1)

    validator = MetadataValidator(str(db_path))
    try:
        result = validator.check_all(subject=args.subject)
    finally:
        validator.close()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    subject_suffix = f"_{args.subject}" if args.subject else ""

    if args.format in ("terminal", "all"):
        terminal_report(result, args.subject)

    if args.format in ("csv", "all"):
        csv_path = output_dir / f"metadata_issues{subject_suffix}.csv"
        csv_report(result, str(csv_path))

    if args.format in ("html", "all"):
        html_path = output_dir / f"metadata_report{subject_suffix}.html"
        html_report(result, str(html_path), args.subject)

    # 返回码
    checks = [
        result["district"]["rate"] >= 80,
        result["school"]["rate"] >= 80,
        result["exam_type"]["rate"] >= 80,
    ]
    if "round" in result:
        checks.append(result["round"]["rate"] >= 80)
    all_pass = all(checks)
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
