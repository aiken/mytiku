#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
人工复核数据模型与 diff 计算
=============================

将人工复核结果（human_review.json）与机器提取结果（extracted.json）对比，
计算人机差异，并生成修复规则候选。

用法:
    from human_review import HumanReview, compute_diff, generate_fix_rules_from_diff
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from collections import Counter


class HumanReview:
    """人工复核结果"""

    def __init__(self, review_path: Path):
        self.review_path = Path(review_path)
        if self.review_path.exists():
            self.data = json.loads(self.review_path.read_text(encoding="utf-8"))
        else:
            self.data = {"corrections": {}, "deleted": [], "meta_corrections": {}}

    def save(self):
        self.review_path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def get_corrected_question(self, q: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """获取人工修正后的单题数据；如果该题被删除，返回 None。"""
        qid = q.get("question_id")
        if qid in self.data.get("deleted", []):
            return None
        corr = self.data.get("corrections", {}).get(qid, {})
        if not corr:
            return q
        merged = dict(q)
        merged.update(corr)
        return merged

    def get_corrected_meta(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        """获取人工修正后的元数据"""
        merged = dict(meta)
        merged.update(self.data.get("meta_corrections", {}))
        return merged

    def get_corrected_questions(self, questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """获取人工修正后的题目列表"""
        result = []
        for q in questions:
            corrected = self.get_corrected_question(q)
            if corrected:
                result.append(corrected)
        return result


def _content_similar(a: str, b: str) -> bool:
    """简单内容相似度"""
    if not a or not b:
        return a == b
    a_text = str(a).strip()
    b_text = str(b).strip()
    len_ratio = min(len(a_text), len(b_text)) / max(len(a_text), len(b_text))
    if len_ratio < 0.5:
        return False
    a_chars = set(c for c in a_text if '\u4e00' <= c <= '\u9fff')
    b_chars = set(c for c in b_text if '\u4e00' <= c <= '\u9fff')
    if not a_chars:
        return abs(len(a_text) - len(b_text)) / max(len(a_text), 1) < 0.3
    overlap = len(a_chars & b_chars) / len(a_chars)
    return overlap >= 0.5


def _answer_similar(a: str, b: str) -> bool:
    """答案相似度"""
    a_text = str(a).strip()
    b_text = str(b).strip()
    if not a_text or not b_text:
        return False
    return a_text in b_text or b_text in a_text or a_text == b_text


def compute_diff(extracted: Dict[str, Any], review: HumanReview) -> Dict[str, Any]:
    """
    计算机器提取结果与人工复核结果的差异。
    """
    meta = extracted.get("meta", {})
    questions = extracted.get("questions", [])

    corrected_meta = review.get_corrected_meta(meta)
    corrected_questions = review.get_corrected_questions(questions)

    # 元数据差异
    meta_diff = {}
    for key in ["district", "school", "exam_type", "round", "year"]:
        if str(meta.get(key, "")) != str(corrected_meta.get(key, "")):
            meta_diff[key] = {
                "machine": meta.get(key),
                "human": corrected_meta.get(key),
            }

    # 题目集合差异
    machine_map = {str(q.get("question_id")): q for q in questions}
    human_map = {str(q.get("question_id")): q for q in corrected_questions}

    deleted_by_human = set(machine_map.keys()) - set(human_map.keys())
    added_by_human = set(human_map.keys()) - set(machine_map.keys())

    # 逐题差异
    question_diffs = []
    for qid, machine_q in machine_map.items():
        if qid in deleted_by_human:
            question_diffs.append({
                "question_id": qid,
                "question_number": machine_q.get("question_number"),
                "type": "DELETED_BY_HUMAN",
                "message": "人工判定为噪声/误抽并删除",
            })
            continue

        human_q = human_map[qid]
        diffs = []

        if not _content_similar(machine_q.get("content", ""), human_q.get("content", "")):
            diffs.append({
                "field": "content",
                "type": "CONTENT_MISMATCH",
                "machine_preview": str(machine_q.get("content", ""))[:80],
                "human_preview": str(human_q.get("content", ""))[:80],
            })

        if not _answer_similar(machine_q.get("answer", ""), human_q.get("answer", "")):
            diffs.append({
                "field": "answer",
                "type": "ANSWER_MISMATCH",
                "machine": machine_q.get("answer"),
                "human": human_q.get("answer"),
            })

        if not _answer_similar(machine_q.get("solution", ""), human_q.get("solution", "")):
            diffs.append({
                "field": "solution",
                "type": "SOLUTION_MISMATCH",
                "machine": str(machine_q.get("solution", ""))[:80],
                "human": str(human_q.get("solution", ""))[:80],
            })

        machine_images = set(machine_q.get("images", []) or [])
        human_images = set(human_q.get("images", []) or [])
        if machine_images != human_images:
            diffs.append({
                "field": "images",
                "type": "IMAGE_MISMATCH",
                "machine": sorted(machine_images),
                "human": sorted(human_images),
            })

        machine_tags = set(machine_q.get("tags", []) or [])
        human_tags = set(human_q.get("tags", []) or [])
        if machine_tags != human_tags:
            diffs.append({
                "field": "tags",
                "type": "TAG_MISMATCH",
                "machine": sorted(machine_tags),
                "human": sorted(human_tags),
            })

        if diffs:
            question_diffs.append({
                "question_id": qid,
                "question_number": machine_q.get("question_number"),
                "type": "QUESTION_MISMATCH",
                "diffs": diffs,
            })

    return {
        "paper_id": meta.get("paper_id", ""),
        "meta_diff": meta_diff,
        "deleted_by_human": sorted(deleted_by_human),
        "added_by_human": sorted(added_by_human),
        "question_diffs": question_diffs,
        "machine_question_count": len(questions),
        "human_question_count": len(corrected_questions),
    }


def classify_diff(diff: Dict[str, Any]) -> List[str]:
    """根据 diff 类型归类问题"""
    issue_types = set()

    if diff.get("deleted_by_human"):
        # 人工删除的题目很可能是噪声或误抽
        issue_types.add("NOISE_POLLUTION")
        issue_types.add("QUESTION_NUMBER_LOSS")

    if diff.get("added_by_human"):
        issue_types.add("QUESTION_NUMBER_LOSS")

    for d in diff.get("question_diffs", []):
        t = d.get("type")
        if t == "DELETED_BY_HUMAN":
            issue_types.add("NOISE_POLLUTION")
        elif t == "QUESTION_MISMATCH":
            for sub in d.get("diffs", []):
                st = sub.get("type")
                if st == "CONTENT_MISMATCH":
                    issue_types.add("NOISE_POLLUTION")
                elif st in ("ANSWER_MISMATCH", "SOLUTION_MISMATCH"):
                    issue_types.add("ANSWER_MISSING")
                elif st == "IMAGE_MISMATCH":
                    issue_types.add("IMAGE_MISPLACEMENT")
                elif st == "TAG_MISMATCH":
                    issue_types.add("TAG_MISSING")

    if diff.get("meta_diff"):
        issue_types.add("METADATA_MISSING")

    return sorted(issue_types)


def generate_fix_rules_from_diff(diff: Dict[str, Any]) -> Dict[str, Any]:
    """
    根据 diff 生成修复规则候选。
    """
    rules = {
        "tag_fix_rules": [],
        "metadata_alias_candidates": {"school_aliases": {}, "district_aliases": {}, "exam_type_aliases": {}},
        "noise_patterns": [],
    }

    # 元数据别名候选
    for field, values in diff.get("meta_diff", {}).items():
        machine_val = str(values.get("machine", ""))
        human_val = str(values.get("human", ""))
        if field == "school" and machine_val == "未知" and human_val != "未知":
            rules["metadata_alias_candidates"]["school_aliases"][human_val] = human_val
        elif field == "district" and machine_val == "未知" and human_val != "未知":
            rules["metadata_alias_candidates"]["district_aliases"][human_val] = human_val
        elif field == "exam_type" and machine_val != human_val:
            rules["metadata_alias_candidates"]["exam_type_aliases"][machine_val] = human_val

    # 噪声模式候选：从人工删除的题目内容中提取高频噪声词
    deleted_samples = []
    for d in diff.get("question_diffs", []):
        if d.get("type") == "DELETED_BY_HUMAN":
            content = d.get("machine_preview", "")
            if content:
                deleted_samples.append(content[:100])
    if deleted_samples:
        rules["noise_patterns"] = ["请根据以下样本人工补充正则:", *deleted_samples[:5]]

    # 标签修复候选：如果机器标签缺失，人类补充了标签
    for d in diff.get("question_diffs", []):
        if d.get("type") != "QUESTION_MISMATCH":
            continue
        for sub in d.get("diffs", []):
            if sub.get("type") == "TAG_MISMATCH":
                machine_tags = set(sub.get("machine", []))
                human_tags = set(sub.get("human", []))
                missing = human_tags - machine_tags
                if missing:
                    rules["tag_fix_rules"].append({
                        "name": "人工补充标签候选",
                        "pattern": "请根据题目内容补充正则",
                        "add_tags": {"knowledge": sorted(missing)},
                        "priority": "low",
                    })

    return rules


def compute_human_machine_agreement(diff: Dict[str, Any]) -> float:
    """计算人机一致率（基于题目集合和字段匹配）"""
    machine_count = diff.get("machine_question_count", 0)
    human_count = diff.get("human_question_count", 0)
    if machine_count == 0 and human_count == 0:
        return 100.0

    # 一致题数 = 共同题号数 - 有字段差异的题数
    common = min(machine_count, human_count)
    mismatches = len(diff.get("question_diffs", []))
    # 删除也算不一致
    deleted = len(diff.get("deleted_by_human", []))
    added = len(diff.get("added_by_human", []))

    max_count = max(machine_count, human_count)
    agreement = (max_count - mismatches - deleted - added) / max_count * 100
    return max(0, round(agreement, 1))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="人机 diff 计算")
    parser.add_argument("--extracted", required=True, help="extracted.json 路径")
    parser.add_argument("--review", required=True, help="human_review.json 路径")
    parser.add_argument("--output", help="输出 diff JSON 路径")
    parser.add_argument("--rules-output", help="输出修复规则候选目录")
    args = parser.parse_args()

    extracted = json.loads(Path(args.extracted).read_text(encoding="utf-8"))
    review = HumanReview(Path(args.review))
    diff = compute_diff(extracted, review)
    diff["issue_types"] = classify_diff(diff)
    diff["human_machine_agreement"] = compute_human_machine_agreement(diff)

    print(f"人机一致率: {diff['human_machine_agreement']}%")
    print(f"机器题数: {diff['machine_question_count']}, 人工题数: {diff['human_question_count']}")
    print(f"问题类型: {diff['issue_types']}")
    print(f"字段差异题数: {len(diff['question_diffs'])}")
    print(f"人工删除题数: {len(diff['deleted_by_human'])}")

    if args.output:
        Path(args.output).write_text(json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Diff 已保存: {args.output}")

    if args.rules_output:
        rules = generate_fix_rules_from_diff(diff)
        out_dir = Path(args.rules_output)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "tag_fix_rules.json").write_text(
            json.dumps(rules["tag_fix_rules"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "metadata_alias_candidates.json").write_text(
            json.dumps(rules["metadata_alias_candidates"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "noise_patterns.json").write_text(
            json.dumps(rules["noise_patterns"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"规则候选已保存: {out_dir}")


if __name__ == "__main__":
    main()
