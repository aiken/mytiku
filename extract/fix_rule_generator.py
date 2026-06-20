#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复规则生成器 (Fix Rule Generator)
====================================

根据问题分类报告，自动生成修复规则候选文件：
- tag_fix_rules.yaml        → 供 tag_fixer.py 使用
- metadata_alias_candidates.yaml
- noise_patterns.yaml       → 供 extract_all.py 使用

用法:
    python extract/fix_rule_generator.py --issue-file issues.json --output-dir ./rules/
"""

import json
from pathlib import Path
from typing import Dict, List, Any

try:
    import yaml
except ImportError:
    yaml = None


DEFAULT_TAG_FIX_RULES = [
    {
        "name": "兜底知识标签：含图几何题",
        "pattern": "如图.*(圆|三角形|四边形|正方形|矩形|菱形)",
        "subject": "math",
        "add_tags": {"knowledge": ["几何"]},
        "priority": "low",
    },
    {
        "name": "兜底方法标签：存在性/最值",
        "pattern": "(最大值|最小值|取值范围|恒成立)",
        "subject": "math",
        "add_tags": {"method": ["分类讨论"]},
        "priority": "low",
    },
]

DEFAULT_METADATA_CANDIDATES = {
    "school_aliases": {},
    "district_aliases": {},
    "exam_type_aliases": {},
}

DEFAULT_NOISE_PATTERNS = [
    "本试卷共\\s*\\d+\\s*页",
    "考试时间\\s*\\d+\\s*分钟",
    "满分\\s*\\d+\\s*分",
    "姓名[：:]\\s*\\S*\\s*班级[：:]\\s*\\S*\\s*考号[：:]",
    "准考证号|密封线|装订线|答题卡|注意事项",
]


def generate_tag_rules(top_issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """根据问题类型生成标签修复规则候选"""
    rules = []

    issue_types = {i["type"] for i in top_issues}

    if "TAG_MISSING" in issue_types:
        rules.extend([
            {
                "name": "数学未分类题兜底：函数/方程关键词",
                "pattern": "(y\\s*=|函数|抛物线|双曲线|坐标系)",
                "subject": "math",
                "add_tags": {"knowledge": ["函数", "函数图像"]},
                "priority": "medium",
            },
            {
                "name": "数学未分类题兜底：方程不等式关键词",
                "pattern": "(方程|解方程|不等式|解集)",
                "subject": "math",
                "add_tags": {"knowledge": ["方程不等式"]},
                "priority": "medium",
            },
            {
                "name": "物理未分类题兜底：力学",
                "pattern": "(力|重力|弹力|摩擦力|压强|浮力|杠杆|滑轮)",
                "subject": "physics",
                "add_tags": {"knowledge": ["力学"]},
                "priority": "medium",
            },
            {
                "name": "物理未分类题兜底：电学",
                "pattern": "(电流|电压|电阻|电路|电功率|欧姆定律)",
                "subject": "physics",
                "add_tags": {"knowledge": ["电学"]},
                "priority": "medium",
            },
        ])

    if "TAG_INFLATION" in issue_types:
        rules.append({
            "name": "抑制过度标签：仅如图不打空间想象",
            "pattern": "^(?!.*(圆|三角形|四边形|函数|坐标|几何)).*如图.*$",
            "subject": "math",
            "remove_tags": {"ability": ["空间想象"]},
            "priority": "high",
        })

    return rules + DEFAULT_TAG_FIX_RULES


def generate_metadata_candidates(failed_papers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """根据失败试卷的元数据问题，生成别名候选"""
    candidates = {
        "school_aliases": {},
        "district_aliases": {},
        "exam_type_aliases": {},
    }

    # 从 paper_id 或文件名推断可能的缺失别名
    for paper in failed_papers:
        paper_id = paper.get("paper_id", "")
        # 简单启发：如果 school 未知且 paper_id 含学校关键词，提出候选
        # 实际使用时需要人工确认
        for keyword in ["附中", "中学", "学校"]:
            if keyword in paper_id:
                # 提取 paper_id 中可能的学校名片段（简化示例）
                candidates["school_aliases"][paper_id] = "待人工确认"
                break

    return candidates


def generate_noise_patterns(top_issues: List[Dict[str, Any]]) -> List[str]:
    """根据噪声污染问题生成噪声模式候选"""
    if any(i["type"] == "NOISE_POLLUTION" for i in top_issues):
        return DEFAULT_NOISE_PATTERNS
    return []


def generate_all_rules(issue_report: Dict[str, Any]) -> Dict[str, Any]:
    """根据问题分类报告生成所有修复规则候选"""
    top_issues = issue_report.get("top_issues", [])
    failed_papers = issue_report.get("failed_paper_details", [])

    return {
        "tag_fix_rules": generate_tag_rules(top_issues),
        "metadata_alias_candidates": generate_metadata_candidates(failed_papers),
        "noise_patterns": generate_noise_patterns(top_issues),
    }


def save_rules(rules: Dict[str, Any], output_dir: Path) -> None:
    """保存规则候选到 output_dir"""
    output_dir.mkdir(parents=True, exist_ok=True)

    if yaml is None:
        # 无 PyYAML 时回退到 JSON
        (output_dir / "tag_fix_rules.json").write_text(
            json.dumps(rules["tag_fix_rules"], ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        (output_dir / "metadata_alias_candidates.json").write_text(
            json.dumps(rules["metadata_alias_candidates"], ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        (output_dir / "noise_patterns.json").write_text(
            json.dumps(rules["noise_patterns"], ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    else:
        (output_dir / "tag_fix_rules.yaml").write_text(
            yaml.dump(rules["tag_fix_rules"], allow_unicode=True, sort_keys=False),
            encoding="utf-8"
        )
        (output_dir / "metadata_alias_candidates.yaml").write_text(
            yaml.dump(rules["metadata_alias_candidates"], allow_unicode=True, sort_keys=False),
            encoding="utf-8"
        )
        (output_dir / "noise_patterns.yaml").write_text(
            yaml.dump(rules["noise_patterns"], allow_unicode=True, sort_keys=False),
            encoding="utf-8"
        )


def main():
    import argparse
    parser = argparse.ArgumentParser(description="修复规则生成器")
    parser.add_argument("--issue-file", required=True, help="issue_classifier 输出的 JSON 文件")
    parser.add_argument("--output-dir", required=True, help="规则候选输出目录")
    args = parser.parse_args()

    issue_report = json.loads(Path(args.issue_file).read_text(encoding="utf-8"))
    rules = generate_all_rules(issue_report)
    save_rules(rules, Path(args.output_dir))

    ext = "json" if yaml is None else "yaml"
    print(f"规则候选已生成: {args.output_dir}")
    print(f"  - tag_fix_rules.{ext}")
    print(f"  - metadata_alias_candidates.{ext}")
    print(f"  - noise_patterns.{ext}")


if __name__ == "__main__":
    main()
