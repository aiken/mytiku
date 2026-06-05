#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量标签修正工具
================
支持按内容模式批量修正题目标签，减少人工干预成本。

用法:
    # 预览模式（dry-run）
    python extract/tag_fixer.py --db ./extract_output/local.sqlite --rules ./rules.yaml --dry-run

    # 执行修正
    python extract/tag_fixer.py --db ./extract_output/local.sqlite --rules ./rules.yaml

    # 回滚上次操作
    python extract/tag_fixer.py --db ./extract_output/local.sqlite --rollback ./logs/tag_fix_20240605_120000.json
"""

import os
import sys
import json
import re
import argparse
import sqlite3
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime


# ----------------------------------------------------------------------
# 数据模型
# ----------------------------------------------------------------------
@dataclass
class TagChange:
    """单个题目的标签变更记录"""
    question_id: str
    question_number: str
    subject: str
    content_preview: str
    field: str           # knowledge_tags / ability_tags / feature_tags / method_tags / position_tag
    old_value: str
    new_value: str
    rule_name: str


@dataclass
class FixRule:
    """单条修正规则"""
    name: str
    pattern: str
    subject: Optional[str] = None
    add_tags: Dict[str, List[str]] = None
    remove_tags: Dict[str, List[str]] = None
    compiled_pattern: re.Pattern = None

    def __post_init__(self):
        if self.compiled_pattern is None and self.pattern:
            self.compiled_pattern = re.compile(self.pattern, re.DOTALL)


@dataclass
class FixLog:
    """修正操作日志（用于回滚）"""
    timestamp: str
    db_path: str
    rules_file: str
    total_matched: int
    total_changed: int
    changes: List[Dict[str, Any]]


# ----------------------------------------------------------------------
# 核心修正引擎
# ----------------------------------------------------------------------
class TagFixer:
    """标签修正引擎"""

    DIMENSION_FIELDS = {
        "knowledge_tags": "knowledge_tags",
        "ability_tags": "ability_tags",
        "feature_tags": "feature_tags",
        "method_tags": "method_tags",
        "position_tag": "position_tag",
    }

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"数据库不存在: {db_path}")
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row

    def close(self):
        self.conn.close()

    def load_rules(self, rules_path: str) -> List[FixRule]:
        """从 YAML/JSON 文件加载规则"""
        path = Path(rules_path)
        if not path.exists():
            raise FileNotFoundError(f"规则文件不存在: {rules_path}")

        with open(path, "r", encoding="utf-8") as f:
            if path.suffix in (".yaml", ".yml"):
                try:
                    import yaml
                    data = yaml.safe_load(f)
                except ImportError:
                    print("错误: 需要 PyYAML 库解析 YAML 文件。请执行: pip install pyyaml")
                    sys.exit(1)
            else:
                data = json.load(f)

        rules = []
        for rule_data in data.get("rules", []):
            rule = FixRule(
                name=rule_data.get("name", "未命名规则"),
                pattern=rule_data.get("pattern", ""),
                subject=rule_data.get("subject"),
                add_tags=rule_data.get("add_tags", {}),
                remove_tags=rule_data.get("remove_tags", {}),
            )
            rules.append(rule)

        return rules

    def _parse_tags(self, value: Optional[str], field: str) -> List[str]:
        """解析标签值为列表"""
        if value is None:
            return []
        if field == "position_tag":
            return [value.strip()] if value.strip() else []
        try:
            tags = json.loads(value)
            if isinstance(tags, list):
                return [t for t in tags if t]
            return []
        except (json.JSONDecodeError, TypeError):
            return []

    def _serialize_tags(self, tags: List[str], field: str) -> str:
        """序列化标签值为字符串"""
        if field == "position_tag":
            return tags[0] if tags else ""
        return json.dumps(tags, ensure_ascii=False)

    def _apply_rule(self, row: sqlite3.Row, rule: FixRule) -> Optional[TagChange]:
        """
        对单条题目应用规则，返回变更记录（如有变更）。
        返回 None 表示无变更或不符合条件。
        """
        content = row["content"] or ""

        # 学科筛选
        if rule.subject and row["subject"] != rule.subject:
            return None

        # 内容匹配
        if not rule.compiled_pattern.search(content):
            return None

        # 应用标签变更
        changes = []
        for field, field_name in self.DIMENSION_FIELDS.items():
            old_value = row[field_name]
            old_tags = self._parse_tags(old_value, field_name)
            new_tags = list(old_tags)  # 复制

            # 添加标签
            if rule.add_tags and field in rule.add_tags:
                for tag in rule.add_tags[field]:
                    if tag not in new_tags:
                        new_tags.append(tag)

            # 移除标签
            if rule.remove_tags and field in rule.remove_tags:
                for tag in rule.remove_tags[field]:
                    if tag in new_tags:
                        new_tags.remove(tag)

            # 如果有变更
            if new_tags != old_tags:
                new_value = self._serialize_tags(new_tags, field_name)
                changes.append({
                    "field": field_name,
                    "old_value": old_value or "",
                    "new_value": new_value,
                })

        if not changes:
            return None

        # 返回第一条变更（记录用）
        first_change = changes[0]
        return TagChange(
            question_id=row["question_id"],
            question_number=row["question_number"],
            subject=row["subject"],
            content_preview=(content[:80] + "...") if len(content) > 80 else content,
            field=first_change["field"],
            old_value=first_change["old_value"],
            new_value=first_change["new_value"],
            rule_name=rule.name,
        )

    def preview(self, rules: List[FixRule]) -> List[TagChange]:
        """预览模式：返回所有变更记录，不执行 UPDATE"""
        all_changes = []

        for rule in rules:
            print(f"\n规则: {rule.name}")
            print(f"  模式: {rule.pattern}")

            # 查询所有题目
            sql = """
                SELECT q.question_id, q.question_number, q.content,
                       q.knowledge_tags, q.ability_tags, q.feature_tags,
                       q.method_tags, q.position_tag, p.subject
                FROM questions q
                JOIN papers p ON q.paper_id = p.paper_id
            """
            rows = self.conn.execute(sql).fetchall()

            matched = 0
            changed = 0
            for row in rows:
                change = self._apply_rule(row, rule)
                if change:
                    matched += 1
                    all_changes.append(change)
                    if changed < 5:  # 只显示前5条
                        print(f"    ✓ 题号{change.question_number} ({change.subject}): "
                              f"{change.field} {change.old_value} → {change.new_value}")
                    changed += 1

            if changed > 5:
                print(f"    ... 还有 {changed - 5} 题")
            print(f"  匹配: {matched} 题 | 变更: {changed} 题")

        return all_changes

    def execute(self, rules: List[FixRule], log_dir: str = "./logs") -> str:
        """
        执行修正，返回日志文件路径。
        使用事务保证安全。
        """
        all_changes = []
        total_matched = 0
        total_changed = 0

        # 开始事务
        self.conn.execute("BEGIN TRANSACTION")

        try:
            for rule in rules:
                print(f"\n执行规则: {rule.name}")

                # 查询所有题目
                sql = """
                    SELECT q.question_id, q.question_number, q.content,
                           q.knowledge_tags, q.ability_tags, q.feature_tags,
                           q.method_tags, q.position_tag, p.subject
                    FROM questions q
                    JOIN papers p ON q.paper_id = p.paper_id
                """
                rows = self.conn.execute(sql).fetchall()

                matched = 0
                changed = 0
                for row in rows:
                    content = row["content"] or ""

                    # 学科筛选
                    if rule.subject and row["subject"] != rule.subject:
                        continue

                    # 内容匹配
                    if not rule.compiled_pattern.search(content):
                        continue

                    matched += 1
                    question_id = row["question_id"]

                    # 对每个维度应用变更
                    for field, field_name in self.DIMENSION_FIELDS.items():
                        old_value = row[field_name]
                        old_tags = self._parse_tags(old_value, field_name)
                        new_tags = list(old_tags)

                        # 添加标签
                        if rule.add_tags and field in rule.add_tags:
                            for tag in rule.add_tags[field]:
                                if tag not in new_tags:
                                    new_tags.append(tag)

                        # 移除标签
                        if rule.remove_tags and field in rule.remove_tags:
                            for tag in rule.remove_tags[field]:
                                if tag in new_tags:
                                    new_tags.remove(tag)

                        # 如果有变更，执行 UPDATE
                        if new_tags != old_tags:
                            new_value = self._serialize_tags(new_tags, field_name)
                            self.conn.execute(
                                f"UPDATE questions SET {field_name} = ? WHERE question_id = ?",
                                (new_value, question_id)
                            )

                            all_changes.append({
                                "question_id": question_id,
                                "field": field_name,
                                "old_value": old_value or "",
                                "new_value": new_value,
                                "rule_name": rule.name,
                            })
                            changed += 1

                print(f"  匹配: {matched} 题 | 变更: {changed} 题")
                total_matched += matched
                total_changed += changed

            # 提交事务
            self.conn.commit()
            print(f"\n✅ 事务已提交，共变更 {total_changed} 题")

        except Exception as e:
            self.conn.rollback()
            print(f"\n❌ 执行失败，事务已回滚: {e}")
            raise

        # 保存日志
        log = FixLog(
            timestamp=datetime.now().isoformat(),
            db_path=str(self.db_path),
            rules_file="",
            total_matched=total_matched,
            total_changed=total_changed,
            changes=all_changes,
        )

        log_dir_path = Path(log_dir)
        log_dir_path.mkdir(parents=True, exist_ok=True)
        log_file = log_dir_path / f"tag_fix_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(asdict(log), f, ensure_ascii=False, indent=2)

        print(f"📝 日志已保存: {log_file}")
        return str(log_file)

    def rollback(self, log_file: str):
        """根据日志文件回滚操作"""
        log_path = Path(log_file)
        if not log_path.exists():
            raise FileNotFoundError(f"日志文件不存在: {log_file}")

        with open(log_path, "r", encoding="utf-8") as f:
            log_data = json.load(f)

        changes = log_data.get("changes", [])
        if not changes:
            print("日志中无变更记录，无需回滚")
            return

        print(f"\n回滚操作: {len(changes)} 条变更")
        print(f"原操作时间: {log_data.get('timestamp', '未知')}")

        # 开始事务
        self.conn.execute("BEGIN TRANSACTION")

        try:
            for change in changes:
                question_id = change["question_id"]
                field = change["field"]
                old_value = change["old_value"]

                self.conn.execute(
                    f"UPDATE questions SET {field} = ? WHERE question_id = ?",
                    (old_value, question_id)
                )

            self.conn.commit()
            print(f"✅ 回滚完成，已恢复 {len(changes)} 条记录")

        except Exception as e:
            self.conn.rollback()
            print(f"❌ 回滚失败，事务已回滚: {e}")
            raise


# ----------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="批量标签修正工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", required=True, help="SQLite 数据库路径")
    parser.add_argument("--rules", help="规则文件路径（YAML/JSON）")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不执行 UPDATE")
    parser.add_argument("--rollback", help="回滚指定日志文件")
    parser.add_argument("--log-dir", default="./logs", help="日志输出目录")
    args = parser.parse_args()

    # 检查数据库
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"错误: 数据库不存在: {db_path}")
        sys.exit(1)

    fixer = TagFixer(str(db_path))

    try:
        if args.rollback:
            # 回滚模式
            fixer.rollback(args.rollback)

        elif args.rules:
            # 加载规则
            rules = fixer.load_rules(args.rules)
            if not rules:
                print("错误: 规则文件为空或格式不正确")
                sys.exit(1)

            print(f"已加载 {len(rules)} 条规则")

            if args.dry_run:
                # 预览模式
                print("\n=== 预览模式（不执行实际修改）===")
                changes = fixer.preview(rules)
                print(f"\n总计: {len(changes)} 题将发生变更")
                print("\n提示: 去掉 --dry-run 参数执行实际修改")
            else:
                # 执行模式
                print("\n=== 执行模式 ===")
                confirm = input("确认执行修正操作? [y/N]: ")
                if confirm.lower() != "y":
                    print("已取消")
                    sys.exit(0)

                log_file = fixer.execute(rules, log_dir=args.log_dir)
                print(f"\n如需回滚，执行:")
                print(f"  python extract/tag_fixer.py --db {args.db} --rollback {log_file}")

        else:
            print("错误: 请指定 --rules 或 --rollback 参数")
            sys.exit(1)

    finally:
        fixer.close()


if __name__ == "__main__":
    main()
