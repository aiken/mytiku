#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
单张卷子人机协作质量循环
==========================

分步骤执行，方便人工参与:

    # 1. 提取 + 启动复核服务器
    python extract/single_paper_loop.py extract \
        --input ./raw_papers/2023海淀一模数学.pdf \
        --output ./loop/single/001/

    # 2. 浏览器访问 http://127.0.0.1:8765 完成复核，点击保存

    # 3. 计算人机 diff
    python extract/single_paper_loop.py diff --output ./loop/single/001/

    # 4. 生成修复规则候选
    python extract/single_paper_loop.py rules --output ./loop/single/001/

    # 5. 人工检查并合并规则到代码后，重跑验证
    python extract/single_paper_loop.py verify \
        --input ./raw_papers/2023海淀一模数学.pdf \
        --output ./loop/single/001/

也支持完整自动流程:
    python extract/single_paper_loop.py full \
        --input ./raw_papers/2023海淀一模数学.pdf \
        --output ./loop/single/001/
"""

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Any, Optional

from paper_scorer import heuristic_score, score_against_gt, PaperScore
from human_review import HumanReview, compute_diff, classify_diff, generate_fix_rules_from_diff, compute_human_machine_agreement


def extract_single_paper(input_file: Path, output_dir: Path, max_workers: int = 2) -> bool:
    """运行 extract_all.py 提取单张卷子"""
    input_dir = output_dir / "_temp_input"
    input_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(input_file, input_dir / input_file.name)

    cmd = [
        sys.executable, "extract/extract_all.py",
        "--input", str(input_dir),
        "--output", str(output_dir),
        "--max-workers", str(max_workers),
    ]
    print(f"\n[提取] 运行: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True, cwd=Path(__file__).parent.parent)
        return True
    except subprocess.CalledProcessError as e:
        print(f"[提取失败] {e}")
        return False
    finally:
        if input_dir.exists():
            shutil.rmtree(input_dir)


def load_extracted_from_db(db_path: Path) -> Optional[Dict[str, Any]]:
    """从 local.sqlite 读取第一张试卷的提取结果"""
    try:
        import sqlite3
    except ImportError:
        return None

    if not db_path.exists():
        return None

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    paper_row = conn.execute("SELECT * FROM papers LIMIT 1").fetchone()
    if not paper_row:
        conn.close()
        return None

    paper = dict(paper_row)
    question_rows = conn.execute(
        "SELECT * FROM questions WHERE paper_id = ?",
        (paper["paper_id"],)
    ).fetchall()

    json_fields = [
        "options", "tags", "images", "data_table",
        "knowledge_tags", "ability_tags", "feature_tags", "method_tags",
        "source_tags", "metadata",
    ]

    def _parse_json(value):
        if value is None or value == "":
            return None
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        return value

    def _question_sort_key(qn: str):
        """按题号数值排序：1, 2, ..., 10, 17(1), 17(2), ..."""
        import re
        qn = str(qn)
        # 主题号
        m = re.match(r'(\d+)', qn)
        main = int(m.group(1)) if m else 9999
        # 子题号：括号内数字或 ①②③
        sub_match = re.search(r'[（(](\d+)[）)]', qn)
        if sub_match:
            sub = int(sub_match.group(1))
        elif '①' in qn:
            sub = 1
        elif '②' in qn:
            sub = 2
        elif '③' in qn:
            sub = 3
        else:
            sub = 0
        return (main, sub)

    questions = []
    for r in question_rows:
        q = dict(r)
        for f in json_fields:
            if f in q:
                q[f] = _parse_json(q[f])
        questions.append(q)

    questions.sort(key=lambda q: _question_sort_key(q.get("question_number", "")))

    if "metadata" in paper and isinstance(paper["metadata"], str):
        try:
            paper["metadata"] = json.loads(paper["metadata"])
        except (json.JSONDecodeError, TypeError):
            pass

    conn.close()

    return {"meta": paper, "questions": questions}


def save_extracted_json(extracted: Dict[str, Any], work_dir: Path):
    """保存提取结果为 extracted.json"""
    data_dir = work_dir / "review_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "extracted.json").write_text(
        json.dumps(extracted, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def score_extracted(extracted: Dict[str, Any], gt_dir: Optional[Path] = None) -> PaperScore:
    """对提取结果评分"""
    if gt_dir:
        for subject_dir in sorted(gt_dir.iterdir()):
            if not subject_dir.is_dir():
                continue
            for paper_dir in sorted(subject_dir.iterdir()):
                if not paper_dir.is_dir():
                    continue
                meta_path = paper_dir / "meta.json"
                questions_path = paper_dir / "questions.json"
                if not meta_path.exists() or not questions_path.exists():
                    continue
                try:
                    gt_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    if gt_meta.get("paper_id") == extracted["meta"].get("paper_id"):
                        gt_questions = json.loads(questions_path.read_text(encoding="utf-8"))
                        return score_against_gt(extracted["meta"], extracted["questions"], gt_meta, gt_questions)
                except Exception:
                    continue

    return heuristic_score(extracted["meta"], extracted["questions"])


def start_review_server(work_dir: Path, port: int = 8765):
    """启动人工复核 Web 服务器"""
    from review_server import ReviewServer
    server = ReviewServer(work_dir, port)
    server.run()


def cmd_extract(args):
    """提取并启动复核服务器"""
    input_file = Path(args.input)
    work_dir = Path(args.output)
    work_dir.mkdir(parents=True, exist_ok=True)

    if args.skip_extract:
        print("[跳过提取] 加载已有 extracted.json")
        extracted = json.loads(Path(args.input).read_text(encoding="utf-8"))
    else:
        print("[提取] 开始提取单张卷子...")
        if not extract_single_paper(input_file, work_dir):
            print("提取失败，退出")
            return 1
        extracted = load_extracted_from_db(work_dir / "local.sqlite")
        if not extracted:
            print("无法读取提取结果")
            return 1
        save_extracted_json(extracted, work_dir)

    # 自动评分
    gt_dir = Path(args.gt_dir) if args.gt_dir and Path(args.gt_dir).exists() else None
    score_obj = score_extracted(extracted, gt_dir)
    print(f"\n[机器评分] {score_obj.score} / 100")
    print(f"  通过: {score_obj.passed}")
    print(f"  维度: {score_obj.dimensions}")
    print(f"  问题: {score_obj.issues}")

    data_dir = work_dir / "review_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    machine_score = {
        "paper_id": score_obj.paper_id,
        "score": score_obj.score,
        "passed": score_obj.passed,
        "threshold": score_obj.threshold,
        "dimensions": score_obj.dimensions,
        "issues": score_obj.issues,
        "details": score_obj.details,
    }
    (data_dir / "machine_score.json").write_text(
        json.dumps(machine_score, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # 同时写入 extracted.json，供复核界面直接展示
    extracted["machine_score"] = machine_score
    save_extracted_json(extracted, work_dir)

    if args.no_server:
        print(f"\n[跳过服务器] 请在浏览器中人工复核后保存到:")
        print(f"  {data_dir / 'human_review.json'}")
        print(f"然后运行: python extract/single_paper_loop.py diff --output {work_dir}")
        return 0

    print(f"\n[启动复核服务器] http://127.0.0.1:{args.port}")
    print("请在浏览器中打开，完成复核后点击'保存复核结果'")
    print("关闭服务器后，请运行 diff 子命令继续")
    start_review_server(work_dir, args.port)
    return 0


def cmd_diff(args):
    """计算人机 diff"""
    work_dir = Path(args.output)
    data_dir = work_dir / "review_data"

    if not (data_dir / "extracted.json").exists():
        print(f"未找到 {data_dir / 'extracted.json'}，请先运行 extract")
        return 1
    if not (data_dir / "human_review.json").exists():
        print(f"未找到 {data_dir / 'human_review.json'}，请先完成人工复核")
        return 1

    extracted = json.loads((data_dir / "extracted.json").read_text(encoding="utf-8"))
    review = HumanReview(data_dir / "human_review.json")

    diff = compute_diff(extracted, review)
    diff["issue_types"] = classify_diff(diff)
    diff["human_machine_agreement"] = compute_human_machine_agreement(diff)

    (data_dir / "human_machine_diff.json").write_text(
        json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n[人机 diff 报告]")
    print(f"  一致率: {diff['human_machine_agreement']}%")
    print(f"  机器题数: {diff['machine_question_count']}, 人工题数: {diff['human_question_count']}")
    print(f"  问题类型: {diff['issue_types']}")
    print(f"  字段差异: {len(diff['question_diffs'])}")
    print(f"  人工删除: {len(diff['deleted_by_human'])}")
    print(f"  人工新增: {len(diff['added_by_human'])}")
    print(f"\n报告已保存: {data_dir / 'human_machine_diff.json'}")
    return 0


def cmd_rules(args):
    """生成修复规则候选"""
    work_dir = Path(args.output)
    data_dir = work_dir / "review_data"

    if not (data_dir / "human_machine_diff.json").exists():
        print(f"未找到 {data_dir / 'human_machine_diff.json'}，请先运行 diff")
        return 1

    diff = json.loads((data_dir / "human_machine_diff.json").read_text(encoding="utf-8"))
    rules = generate_fix_rules_from_diff(diff)
    rules_dir = work_dir / "fix_rules"
    rules_dir.mkdir(parents=True, exist_ok=True)

    (rules_dir / "tag_fix_rules.json").write_text(
        json.dumps(rules["tag_fix_rules"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (rules_dir / "metadata_alias_candidates.json").write_text(
        json.dumps(rules["metadata_alias_candidates"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (rules_dir / "noise_patterns.json").write_text(
        json.dumps(rules["noise_patterns"], ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n[修复规则候选] 已生成: {rules_dir}")
    print("  - tag_fix_rules.json")
    print("  - metadata_alias_candidates.json")
    print("  - noise_patterns.json")
    print("\n请人工检查并合并到代码中:")
    print("  - tag_fix_rules.json → extract/tag_rules.py")
    print("  - metadata_alias_candidates.json → extract/metadata_mappings.py")
    print("  - noise_patterns.json → extract/extract_all.py NOISE_TEXT_PATTERNS")
    return 0


def cmd_verify(args):
    """重跑验证"""
    input_file = Path(args.input)
    work_dir = Path(args.output)

    print("\n[重跑验证] 清空 checkpoint 和数据库...")
    db_path = work_dir / "local.sqlite"
    checkpoint = work_dir / "checkpoint.json"
    if db_path.exists():
        db_path.unlink()
    if checkpoint.exists():
        checkpoint.unlink()

    old_score = 0.0
    machine_score_file = work_dir / "review_data" / "machine_score.json"
    if machine_score_file.exists():
        old_score = json.loads(machine_score_file.read_text(encoding="utf-8")).get("score", 0.0)

    if not extract_single_paper(input_file, work_dir):
        return 1

    extracted = load_extracted_from_db(work_dir / "local.sqlite")
    if not extracted:
        print("无法读取重跑后的数据库")
        return 1

    new_score_obj = heuristic_score(extracted["meta"], extracted["questions"])
    print(f"\n[重跑结果]")
    print(f"  原分数: {old_score}")
    print(f"  新分数: {new_score_obj.score}")
    print(f"  提升: {round(new_score_obj.score - old_score, 1)}")
    print(f"  是否通过 80 分阈值: {new_score_obj.passed}")

    # Gate 0 判定
    gate_passed = new_score_obj.passed or (new_score_obj.score - old_score) >= 10
    if gate_passed:
        print(f"\n✓ Gate 0 通过")
        # 保存为 GT
        gt_dir = Path(args.gt_dir)
        review_file = work_dir / "review_data" / "human_review.json"
        if review_file.exists():
            review = HumanReview(review_file)
            save_as_gt(extracted, review, gt_dir)
            print("🎉 已保存为 GT")
        return 0
    else:
        print(f"\n✗ Gate 0 未通过: 分数未达 85 且提升不足 10 分")
        print("请继续修复规则后重跑")
        return 1


def save_as_gt(extracted: Dict[str, Any], review: HumanReview, gt_dir: Path) -> Path:
    """将通过 Gate 0 的卷子保存为 GT"""
    meta = review.get_corrected_meta(extracted["meta"])
    questions = review.get_corrected_questions(extracted["questions"])

    subject = meta.get("subject", "unknown")
    paper_id = meta.get("paper_id", "unknown")
    safe_id = re.sub(r'[^a-zA-Z0-9_\-\u4e00-\u9fff]', '_', paper_id)

    paper_dir = gt_dir / subject / safe_id
    paper_dir.mkdir(parents=True, exist_ok=True)

    meta["expected_question_count"] = len(questions)
    (paper_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (paper_dir / "questions.json").write_text(
        json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n[GT 保存] {paper_dir}")
    return paper_dir


def cmd_full(args):
    """完整流程（阻塞式）"""
    print("完整流程将启动 Web 服务器，请在浏览器中复核后关闭服务器继续。")
    ret = cmd_extract(args)
    if ret != 0:
        return ret

    work_dir = Path(args.output)
    print("\n等待人工复核文件...")
    review_file = work_dir / "review_data" / "human_review.json"
    start = time.time()
    while time.time() - start < args.wait_timeout:
        if review_file.exists():
            print("检测到复核文件，继续...")
            break
        time.sleep(2)
    else:
        print("超时，未检测到复核文件")
        return 1

    ret = cmd_diff(args)
    if ret != 0:
        return ret

    ret = cmd_rules(args)
    if ret != 0:
        return ret

    input("\n请检查并合并修复规则，按 Enter 继续重跑验证...")
    return cmd_verify(args)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="单张卷子人机协作质量循环")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # extract
    extract_parser = subparsers.add_parser("extract", help="提取并启动复核服务器")
    extract_parser.add_argument("--input", required=True, help="原始试卷文件路径")
    extract_parser.add_argument("--output", required=True, help="循环工作目录")
    extract_parser.add_argument("--gt-dir", default="./gt", help="GT 目录")
    extract_parser.add_argument("--port", type=int, default=8765, help="复核服务器端口")
    extract_parser.add_argument("--skip-extract", action="store_true", help="跳过提取，加载已有 extracted.json")
    extract_parser.add_argument("--no-server", action="store_true", help="不启动服务器，只生成文件")

    # diff
    diff_parser = subparsers.add_parser("diff", help="计算人机 diff")
    diff_parser.add_argument("--output", required=True, help="循环工作目录")

    # rules
    rules_parser = subparsers.add_parser("rules", help="生成修复规则候选")
    rules_parser.add_argument("--output", required=True, help="循环工作目录")

    # verify
    verify_parser = subparsers.add_parser("verify", help="重跑验证")
    verify_parser.add_argument("--input", required=True, help="原始试卷文件路径")
    verify_parser.add_argument("--output", required=True, help="循环工作目录")
    verify_parser.add_argument("--gt-dir", default="./gt", help="GT 保存目录")

    # full
    full_parser = subparsers.add_parser("full", help="完整流程（阻塞式）")
    full_parser.add_argument("--input", required=True, help="原始试卷文件路径")
    full_parser.add_argument("--output", required=True, help="循环工作目录")
    full_parser.add_argument("--gt-dir", default="./gt", help="GT 保存目录")
    full_parser.add_argument("--port", type=int, default=8765, help="复核服务器端口")
    full_parser.add_argument("--wait-timeout", type=int, default=3600, help="等待人工复核超时秒数")

    args = parser.parse_args()

    if args.command == "extract":
        return cmd_extract(args)
    elif args.command == "diff":
        return cmd_diff(args)
    elif args.command == "rules":
        return cmd_rules(args)
    elif args.command == "verify":
        return cmd_verify(args)
    elif args.command == "full":
        return cmd_full(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
