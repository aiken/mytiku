#!/usr/bin/env python3
"""
validation_runner.py — 数据抽取验证框架

Issue #16: 数学/物理各 100 套试卷验证

流程:
1. 扫描所有原卷版文件
2. 按年份×地区×考试类型分层抽样
3. 运行 extract_all.py 抽取
4. 运行 tag_quality.py + metadata_validator.py 统计
5. 从数据库直接计算验收指标
6. 生成完整 Markdown 验证报告
"""

import json
import re
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from random import Random
from typing import Dict, List, Tuple, Any

# 分层抽样配置
STRATA_CONFIG = {
    "year": [2019, 2020, 2021, 2022, 2023, 2024],
    "district": ["海淀", "西城", "东城", "朝阳", "丰台", "顺义", "通州", "房山", "门头沟", "其他"],
    "exam_type": ["期中", "期末", "一模", "二模"],
}

SAMPLES_PER_SUBJECT = 100  # 每科抽样数（正式验证）
SEED = 42  # 可复现


def scan_original_files(base_dir: Path) -> List[Dict]:
    """扫描所有原卷版文件，解析元数据，同时查找同目录下的解析版文件"""
    files = []
    # 排除的目录模式
    exclude_dirs = {"reports", ".git", "node_modules", "__pycache__", ".venv", "venv"}
    
    for ext in ["*.docx", "*.doc", "*.pdf"]:
        for path in base_dir.rglob(ext):
            # 跳过排除的目录
            if any(part in exclude_dirs for part in path.parts):
                continue
            
            name = path.name
            if "原卷版" not in name:
                continue
            # 解析学科
            subject = None
            path_str = str(path).lower()
            if "物理" in path_str:
                subject = "physics"
            elif "数学" in path_str:
                subject = "math"
            else:
                continue  # 跳过化学等其他学科

            # 解析年份
            year = None
            for y in range(2015, 2026):
                if str(y) in name or str(y - 1) + "-" + str(y)[-2:] in name:
                    year = y
                    break

            # 解析地区
            district = "其他"
            for d in STRATA_CONFIG["district"][:-1]:
                if d in name:
                    district = d
                    break

            # 解析考试类型
            exam_type = "其他"
            for et in STRATA_CONFIG["exam_type"]:
                if et in name:
                    exam_type = et
                    break

            # 查找同目录下的解析版文件
            answer_path = None
            parent_dir = path.parent
            for ans_ext in ["*.docx", "*.doc", "*.pdf"]:
                for ans_candidate in parent_dir.rglob(ans_ext):
                    if ans_candidate == path:
                        continue
                    ans_name = ans_candidate.name.lower()
                    if any(k in ans_name for k in ["解析版", "答案版", "解答版", "参考答案", "答案解析"]):
                        # 检查是否属于同一套试卷（文件名相似）
                        clean_orig = re.sub(r'（原卷版）|（解析版）|原卷版|解析版|答案版|解答版|参考答案|答案解析|\s+', '', path.stem)
                        clean_ans = re.sub(r'（原卷版）|（解析版）|原卷版|解析版|答案版|解答版|参考答案|答案解析|\s+', '', ans_candidate.stem)
                        if clean_orig == clean_ans or clean_orig in clean_ans or clean_ans in clean_orig:
                            answer_path = str(ans_candidate)
                            break
                if answer_path:
                    break

            files.append({
                "path": str(path),
                "answer_path": answer_path,
                "subject": subject,
                "year": year,
                "district": district,
                "exam_type": exam_type,
                "filename": name,
            })

    # 去重：同一套试卷（按清理后的文件名）只保留一条记录，优先保留有解析版的
    seen = {}
    for f in files:
        clean_name = re.sub(r'（原卷版）|（解析版）|原卷版|解析版|答案版|解答版|参考答案|答案解析|\s+|\(\d+\)', '', f["filename"])
        if clean_name not in seen:
            seen[clean_name] = f
        else:
            # 如果当前记录有解析版而已存在的没有，则替换
            if f.get("answer_path") and not seen[clean_name].get("answer_path"):
                seen[clean_name] = f
    
    return list(seen.values())


def stratified_sample(files: List[Dict], n: int, subject: str) -> List[Dict]:
    """分层抽样: 按年份×地区×考试类型分层，每层至少 1 套"""
    subject_files = [f for f in files if f["subject"] == subject]

    # 按分层分组
    strata = defaultdict(list)
    for f in subject_files:
        key = (f["year"] or "unknown", f["district"], f["exam_type"])
        strata[key].append(f)

    rng = Random(SEED)
    selected = []

    # 每层至少选 1 套
    for key, group in strata.items():
        if group:
            selected.append(rng.choice(group))

    # 剩余名额随机分配
    remaining = n - len(selected)
    if remaining > 0:
        pool = [f for f in subject_files if f not in selected]
        if len(pool) > remaining:
            selected.extend(rng.sample(pool, remaining))
        else:
            selected.extend(pool)

    return selected[:n]


def run_extraction(sample: List[Dict], db_path: str, output_dir: str) -> Dict:
    """运行抽取流程，返回统计"""
    import shutil

    # 创建临时输入目录，复制选中的文件
    temp_input_dir = Path(output_dir) / "temp_input"
    temp_input_dir.mkdir(parents=True, exist_ok=True)

    # 清空临时目录 — 使用更安全的方式：收集后删除
    entries = list(temp_input_dir.iterdir())
    for f in entries:
        if f.is_file():
            f.unlink()
        elif f.is_dir():
            shutil.rmtree(f)

    # 复制文件到临时目录（保持简单，直接复制到根目录）
    for item in sample:
        src = Path(item["path"])
        dst = temp_input_dir / src.name
        if not src.exists():
            print(f"    ⚠️ 源文件不存在，跳过: {src}")
            continue
        shutil.copy2(src, dst)
        # 同时复制解析版文件（如果存在）
        if item.get("answer_path"):
            ans_src = Path(item["answer_path"])
            if ans_src.exists():
                ans_dst = temp_input_dir / ans_src.name
                shutil.copy2(ans_src, ans_dst)
                print(f"    ✓ 复制解析版: {ans_src.name}")

    # 运行抽取
    cmd = [
        sys.executable, "extract/extract_all.py",
        "--input", str(temp_input_dir),
        "--output", output_dir,
        "--max-workers", "4",
    ]
    print(f"Running: {' '.join(cmd)}")
    
    # 清理之前的 checkpoint，确保重新运行
    checkpoint_path = Path(output_dir) / "checkpoint.json"
    if checkpoint_path.exists():
        checkpoint_path.unlink()
        print("  已清理旧 checkpoint，确保重新抽取")
    
    # 清理之前的数据库，确保从头开始
    db_path = Path(output_dir) / "local.sqlite"
    if db_path.exists():
        db_path.unlink()
        print("  已清理旧数据库，确保重新抽取")
    
    # 清理之前的统计文件
    for f in ["extract_stats.json", "failed_files.json", "manual_review.json"]:
        fp = Path(output_dir) / f
        if fp.exists():
            fp.unlink()
            print(f"  已清理旧 {f}")
    
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).parent.parent)

    # extract_all.py 输出数据库为 local.sqlite
    actual_db = Path(output_dir) / "local.sqlite"

    return {
        "returncode": result.returncode,
        "stdout": result.stdout[-3000:] if len(result.stdout) > 3000 else result.stdout,
        "stderr": result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr,
        "db_path": str(actual_db) if actual_db.exists() else None,
    }


def run_tag_quality(db_path: str, output_dir: str) -> Dict:
    """运行标签质量检查，返回终端输出摘要"""
    cmd = [
        sys.executable, "extract/tag_quality.py",
        "--db", db_path,
        "--format", "terminal",
        "--output-dir", output_dir,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).parent.parent)
    return {
        "returncode": result.returncode,
        "stdout": result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout,
    }


def run_metadata_validation(db_path: str, output_dir: str) -> Dict:
    """运行元数据校验，返回终端输出摘要"""
    cmd = [
        sys.executable, "extract/metadata_validator.py",
        "--db", db_path,
        "--format", "terminal",
        "--output-dir", output_dir,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).parent.parent)
    return {
        "returncode": result.returncode,
        "stdout": result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout,
    }


def compute_metrics(db_path: str) -> Dict[str, Any]:
    """从 SQLite 数据库直接计算所有验收指标"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    metrics = {}

    # 1. 题目提取成功率 = 有 content 的题目数 / 总题目数
    cursor.execute("SELECT COUNT(*) as total FROM questions")
    total_questions = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) as has_content FROM questions WHERE content IS NOT NULL AND LENGTH(TRIM(content)) > 10")
    has_content = cursor.fetchone()["has_content"]
    metrics["extraction_success_rate"] = round(has_content / total_questions * 100, 1) if total_questions > 0 else 0.0

    # 2. 无标签题目比例 = 5 个维度都为空/未分类/空数组的题目数 / 总题目数
    cursor.execute("""
        SELECT COUNT(*) as untagged FROM questions
        WHERE (knowledge_tags IS NULL OR knowledge_tags = '[]' OR knowledge_tags = '["未分类"]')
          AND (ability_tags IS NULL OR ability_tags = '[]' OR ability_tags = '["未分类"]')
          AND (feature_tags IS NULL OR feature_tags = '[]' OR feature_tags = '["未分类"]')
          AND (method_tags IS NULL OR method_tags = '[]' OR method_tags = '["未分类"]')
          AND (position_tag IS NULL OR position_tag = '' OR position_tag = '未知')
    """)
    untagged = cursor.fetchone()["untagged"]
    metrics["untagged_rate"] = round(untagged / total_questions * 100, 1) if total_questions > 0 else 0.0

    # 3. 每题平均标签数（跨 5 个维度的有效标签总数 / 总题目数）
    cursor.execute("""
        SELECT knowledge_tags, ability_tags, feature_tags, method_tags, position_tag
        FROM questions
    """)
    total_tag_count = 0
    for row in cursor.fetchall():
        for col in ["knowledge_tags", "ability_tags", "feature_tags", "method_tags"]:
            val = row[col]
            if val and val != '[]' and val != '["未分类"]':
                try:
                    tags = json.loads(val)
                    if isinstance(tags, list):
                        total_tag_count += len([t for t in tags if t and t != "未分类"])
                except (json.JSONDecodeError, TypeError):
                    pass
        # position_tag 是单个字符串
        pos = row["position_tag"]
        if pos and pos.strip() and pos != "未知":
            total_tag_count += 1

    metrics["avg_tags_per_question"] = round(total_tag_count / total_questions, 1) if total_questions > 0 else 0.0

    # 4. 图片提取成功率 = 有 images 且不为 "[]" 的题目数 / 总题目数
    cursor.execute("SELECT COUNT(*) as has_image FROM questions WHERE images IS NOT NULL AND images != '[]'")
    has_image = cursor.fetchone()["has_image"]
    metrics["image_extraction_rate"] = round(has_image / total_questions * 100, 1) if total_questions > 0 else 0.0
    
    # 4.1 区分"无图片"和"提取失败"：统计有图片内容的题目（从原卷版中实际包含图片的题目）
    # 由于无法直接从数据库判断原卷版是否有图片，我们使用一个启发式：
    # 如果 content 中包含"如图"、"图像"等关键词，则认为应该有图片
    cursor.execute("""
        SELECT COUNT(*) as likely_has_image FROM questions
        WHERE content LIKE '%如图%' OR content LIKE '%图像%'
          OR content LIKE '%picture%' OR content LIKE '%img%'
    """)
    likely_has_image = cursor.fetchone()["likely_has_image"]
    metrics["likely_has_image"] = likely_has_image
    
    # 实际图片提取成功率（仅针对可能有图片的题目）
    if likely_has_image > 0:
        metrics["image_extraction_rate_adjusted"] = round(min(has_image, likely_has_image) / likely_has_image * 100, 1)
    else:
        metrics["image_extraction_rate_adjusted"] = 0.0

    # 5. 元数据准确率（从 papers 表统计）
    cursor.execute("SELECT COUNT(*) as total FROM papers")
    total_papers = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) as valid FROM papers WHERE district IS NOT NULL AND district != '未知' AND district != ''")
    valid_district = cursor.fetchone()["valid"]

    cursor.execute("SELECT COUNT(*) as valid FROM papers WHERE school IS NOT NULL AND school != '未知' AND school != ''")
    valid_school = cursor.fetchone()["valid"]

    cursor.execute("SELECT COUNT(*) as valid FROM papers WHERE exam_type IS NOT NULL AND exam_type != '未知' AND exam_type != ''")
    valid_exam_type = cursor.fetchone()["valid"]

    # 综合元数据准确率（三个字段都有效）
    cursor.execute("""
        SELECT COUNT(*) as valid FROM papers
        WHERE district IS NOT NULL AND district != '未知' AND district != ''
          AND school IS NOT NULL AND school != '未知' AND school != ''
          AND exam_type IS NOT NULL AND exam_type != '未知' AND exam_type != ''
    """)
    all_valid = cursor.fetchone()["valid"]
    metrics["metadata_accuracy"] = round(all_valid / total_papers * 100, 1) if total_papers > 0 else 0.0
    metrics["metadata_breakdown"] = {
        "district": round(valid_district / total_papers * 100, 1) if total_papers > 0 else 0.0,
        "school": round(valid_school / total_papers * 100, 1) if total_papers > 0 else 0.0,
        "exam_type": round(valid_exam_type / total_papers * 100, 1) if total_papers > 0 else 0.0,
    }

    # 6. 答案/解析匹配覆盖率 = 有 answer 或 solution 的题目数 / 总题目数
    cursor.execute("SELECT COUNT(*) as has_answer FROM questions WHERE answer IS NOT NULL AND LENGTH(TRIM(answer)) > 0")
    has_answer = cursor.fetchone()["has_answer"]

    cursor.execute("SELECT COUNT(*) as has_solution FROM questions WHERE solution IS NOT NULL AND LENGTH(TRIM(solution)) > 0")
    has_solution = cursor.fetchone()["has_solution"]

    # 至少有一个（answer 或 solution）
    cursor.execute("""
        SELECT COUNT(*) as has_either FROM questions
        WHERE (answer IS NOT NULL AND LENGTH(TRIM(answer)) > 0)
           OR (solution IS NOT NULL AND LENGTH(TRIM(solution)) > 0)
    """)
    has_either = cursor.fetchone()["has_either"]
    metrics["answer_coverage"] = round(has_either / total_questions * 100, 1) if total_questions > 0 else 0.0
    metrics["answer_breakdown"] = {
        "has_answer": has_answer,
        "has_solution": has_solution,
        "has_either": has_either,
    }

    # 基础统计
    metrics["total_questions"] = total_questions
    metrics["total_papers"] = total_papers

    conn.close()
    return metrics


def generate_report(
    math_sample: List[Dict],
    physics_sample: List[Dict],
    extraction_result: Dict,
    metrics: Dict[str, Any],
    tag_quality_result: Dict,
    metadata_result: Dict,
    output_path: str,
) -> str:
    """生成完整 Markdown 验证报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    total_sample = len(math_sample) + len(physics_sample)

    # 统计抽样分布
    def sample_stats(sample: List[Dict]) -> str:
        years = Counter(f["year"] for f in sample)
        districts = Counter(f["district"] for f in sample)
        exam_types = Counter(f["exam_type"] for f in sample)

        lines = []
        lines.append("**年份分布**: " + ", ".join(f"{y}: {c}" for y, c in sorted(years.items())))
        lines.append("**地区分布**: " + ", ".join(f"{d}: {c}" for d, c in districts.most_common()))
        lines.append("**考试类型**: " + ", ".join(f"{e}: {c}" for e, c in exam_types.most_common()))
        return "\n".join(lines)

    # 验收指标状态判断
    def status_emoji(value: float, threshold: float, operator: str = ">=") -> str:
        if operator == ">=":
            return "✅ 通过" if value >= threshold else "❌ 未通过"
        else:
            return "✅ 通过" if value < threshold else "❌ 未通过"

    # 标签质量输出摘要
    tag_stdout = tag_quality_result.get("stdout", "")
    tag_summary = "\n".join([f"    {line}" for line in tag_stdout.strip().split("\n")[:30]]) if tag_stdout else "    [tag_quality.py 输出为空]"

    # 元数据校验输出摘要
    meta_stdout = metadata_result.get("stdout", "")
    meta_summary = "\n".join([f"    {line}" for line in meta_stdout.strip().split("\n")[:30]]) if meta_stdout else "    [metadata_validator.py 输出为空]"

    report = f"""# 数据抽取验证报告

**生成时间**: {now}
**验证集规模**: 数学 {len(math_sample)} 套 + 物理 {len(physics_sample)} 套 = {total_sample} 套
**总题目数**: {metrics['total_questions']}
**总试卷数**: {metrics['total_papers']}

---

## 1. 验证集构成

### 数学 ({len(math_sample)} 套)
{sample_stats(math_sample)}

### 物理 ({len(physics_sample)} 套)
{sample_stats(physics_sample)}

---

## 2. 抽取流程执行

- **命令**: `extract_all.py --input <temp_input_dir> --output <output_dir>`
- **返回码**: {extraction_result['returncode']}
- **输出摘要**:
```
{extraction_result['stdout'][:1500]}
```

---

## 3. 质量统计

### 3.1 标签质量检查 (`tag_quality.py`)
```
{tag_summary}
```

### 3.2 元数据校验 (`metadata_validator.py`)
```
{meta_summary}
```

### 3.3 数据库指标汇总

| 指标 | 数值 | 说明 |
|------|------|------|
| 总题目数 | {metrics['total_questions']} | 抽取到的全部题目 |
| 总试卷数 | {metrics['total_papers']} | 抽取到的全部试卷 |
| 题目提取成功率 | {metrics['extraction_success_rate']}% | content 非空且长度 > 10 |
| 无标签题目比例 | {metrics['untagged_rate']}% | 5 维度全部缺失 |
| 每题平均标签数 | {metrics['avg_tags_per_question']} | 跨 5 维度有效标签总数 / 题目数 |
| 图片提取成功率 | {metrics['image_extraction_rate']}% | images 非空且非 [] |
| 元数据准确率 | {metrics['metadata_accuracy']}% | district + school + exam_type 均有效 |
| 答案/解析覆盖率 | {metrics['answer_coverage']}% | answer 或 solution 非空 |

**元数据分项准确率**:
- 区名 (district): {metrics['metadata_breakdown']['district']}%
- 学校 (school): {metrics['metadata_breakdown']['school']}%
- 考试类型 (exam_type): {metrics['metadata_breakdown']['exam_type']}%

**答案/解析分项**:
- 有答案 (answer): {metrics['answer_breakdown']['has_answer']} 题
- 有解析 (solution): {metrics['answer_breakdown']['has_solution']} 题
- 至少一项: {metrics['answer_breakdown']['has_either']} 题

---

## 4. 验收标准检查

| 检查项 | 标准 | 实际 | 状态 |
|--------|------|------|------|
| 题目提取成功率 | ≥ 95% | {metrics['extraction_success_rate']}% | {status_emoji(metrics['extraction_success_rate'], 95.0)} |
| 无标签题目比例 | < 5% | {metrics['untagged_rate']}% | {status_emoji(metrics['untagged_rate'], 5.0, '<')} |
| 每题平均标签数 | ≥ 4 个 | {metrics['avg_tags_per_question']} | {status_emoji(metrics['avg_tags_per_question'], 4.0)} |
| 图片提取成功率 | ≥ 90% | {metrics['image_extraction_rate']}% | {status_emoji(metrics['image_extraction_rate'], 90.0)} |
| 图片提取成功率(调整后) | ≥ 90% | {metrics['image_extraction_rate_adjusted']}% | {status_emoji(metrics['image_extraction_rate_adjusted'], 90.0)} |
| 元数据准确率 | ≥ 80% | {metrics['metadata_accuracy']}% | {status_emoji(metrics['metadata_accuracy'], 80.0)} |
| 答案/解析匹配 | ≥ 90% | {metrics['answer_coverage']}% | {status_emoji(metrics['answer_coverage'], 90.0)} |

---

## 5. 问题与改进建议

### 发现的问题
1. 无标签题目比例 {metrics['untagged_rate']}% — {'符合标准' if metrics['untagged_rate'] < 5 else '需优化标签规则覆盖'}
2. 每题平均标签数 {metrics['avg_tags_per_question']} — {'符合标准' if metrics['avg_tags_per_question'] >= 4 else '需增加标签维度覆盖'}
3. 图片提取成功率 {metrics['image_extraction_rate']}% — {'符合标准' if metrics['image_extraction_rate'] >= 90 else '需检查图片提取逻辑'}
4. 元数据准确率 {metrics['metadata_accuracy']}% — {'符合标准' if metrics['metadata_accuracy'] >= 80 else '需优化元数据解析'}
5. 答案/解析覆盖率 {metrics['answer_coverage']}% — {'符合标准' if metrics['answer_coverage'] >= 90 else '需改进答案提取'}

### 改进建议
1. 针对未通过项，参考 Phase 1 数据质量优化脚本 (`tag_fixer.py`, `metadata_mappings.py`)
2. 对无标签题目运行 `tag_fixer.py --auto-fix` 批量补全
3. 对元数据异常试卷运行 `metadata_validator.py` 查看详细问题列表
4. 人工抽检 20 套（10%）核对提取结果与原始文件

---

*报告由 validation_runner.py 自动生成*
*关联 Issue: #16 [Validation] 数据抽取验证*
"""

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    return output_path


def main():
    base_dir = Path(__file__).parent.parent
    output_dir = base_dir / "reports" / "validation"
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(output_dir / "validation.sqlite")

    print("=" * 60)
    print("数据抽取验证框架 (Issue #16)")
    print(f"每科抽样数: {SAMPLES_PER_SUBJECT}")
    print("=" * 60)

    # 1. 扫描文件
    print("\n[1/6] 扫描试卷文件...")
    all_files = scan_original_files(base_dir)
    print(f"  找到 {len(all_files)} 份原卷版文件")

    math_count = sum(1 for f in all_files if f["subject"] == "math")
    physics_count = sum(1 for f in all_files if f["subject"] == "physics")
    print(f"  数学: {math_count}, 物理: {physics_count}")

    if math_count < SAMPLES_PER_SUBJECT or physics_count < SAMPLES_PER_SUBJECT:
        print(f"  ⚠️ 警告: 可用文件不足 {SAMPLES_PER_SUBJECT} 套，将使用全部可用文件")

    # 2. 分层抽样
    print(f"\n[2/6] 分层抽样 (每科 {SAMPLES_PER_SUBJECT} 套)...")
    math_sample = stratified_sample(all_files, SAMPLES_PER_SUBJECT, "math")
    physics_sample = stratified_sample(all_files, SAMPLES_PER_SUBJECT, "physics")
    print(f"  数学选中: {len(math_sample)} 套")
    print(f"  物理选中: {len(physics_sample)} 套")

    # 保存验证集清单
    validation_set = {
        "math": [{k: v for k, v in s.items() if k != "path"} for s in math_sample],
        "physics": [{k: v for k, v in s.items() if k != "path"} for s in physics_sample],
    }
    with open(output_dir / "validation_set.json", "w", encoding="utf-8") as f:
        json.dump(validation_set, f, ensure_ascii=False, indent=2)

    # 3. 运行抽取
    print(f"\n[3/6] 运行抽取流程...")
    all_sample = math_sample + physics_sample
    extraction_result = run_extraction(all_sample, db_path, str(output_dir))
    print(f"  返回码: {extraction_result['returncode']}")
    if extraction_result["stderr"]:
        print(f"  错误输出: {extraction_result['stderr'][:500]}")
    actual_db = extraction_result.get("db_path", db_path)

    # 4. 质量检查 — tag_quality
    print(f"\n[4/6] 运行标签质量检查...")
    tag_quality_result = run_tag_quality(actual_db, str(output_dir))
    print(f"  返回码: {tag_quality_result['returncode']}")

    # 5. 质量检查 — metadata_validator
    print(f"\n[5/6] 运行元数据校验...")
    metadata_result = run_metadata_validation(actual_db, str(output_dir))
    print(f"  返回码: {metadata_result['returncode']}")

    # 6. 从数据库计算验收指标
    print(f"\n[6/6] 计算验收指标...")
    if actual_db and Path(actual_db).exists():
        metrics = compute_metrics(actual_db)
        print(f"  题目提取成功率: {metrics['extraction_success_rate']}%")
        print(f"  无标签比例: {metrics['untagged_rate']}%")
        print(f"  平均标签数: {metrics['avg_tags_per_question']}")
        print(f"  图片提取率: {metrics['image_extraction_rate']}%")
        print(f"  元数据准确率: {metrics['metadata_accuracy']}%")
        print(f"  答案覆盖率: {metrics['answer_coverage']}%")
    else:
        print(f"  ⚠️ 数据库不存在: {actual_db}")
        metrics = {k: 0.0 for k in [
            "extraction_success_rate", "untagged_rate", "avg_tags_per_question",
            "image_extraction_rate", "metadata_accuracy", "answer_coverage",
            "total_questions", "total_papers",
        ]}
        metrics["metadata_breakdown"] = {"district": 0.0, "school": 0.0, "exam_type": 0.0}
        metrics["answer_breakdown"] = {"has_answer": 0, "has_solution": 0, "has_either": 0}

    # 7. 生成报告
    print(f"\n[7/7] 生成验证报告...")
    report_path = output_dir / f"validation-report-{datetime.now().strftime('%Y%m%d')}.md"
    generate_report(
        math_sample, physics_sample,
        extraction_result, metrics,
        tag_quality_result, metadata_result,
        str(report_path),
    )
    print(f"  报告: {report_path}")

    # 打印验收结果摘要
    print("\n" + "=" * 60)
    print("验收标准检查摘要")
    print("=" * 60)
    checks = [
        ("题目提取成功率", metrics["extraction_success_rate"], 95.0, ">="),
        ("无标签题目比例", metrics["untagged_rate"], 5.0, "<"),
        ("每题平均标签数", metrics["avg_tags_per_question"], 4.0, ">="),
        ("图片提取成功率", metrics["image_extraction_rate"], 90.0, ">="),
        ("元数据准确率", metrics["metadata_accuracy"], 80.0, ">="),
        ("答案/解析匹配", metrics["answer_coverage"], 90.0, ">="),
    ]
    all_pass = True
    for name, value, threshold, op in checks:
        if op == ">=":
            passed = value >= threshold
        else:
            passed = value < threshold
        status = "✅" if passed else "❌"
        print(f"  {status} {name}: {value} (标准: {op} {threshold})")
        if not passed:
            all_pass = False

    print("=" * 60)
    if all_pass:
        print("🎉 所有验收标准通过！")
    else:
        print("⚠️ 部分验收标准未通过，请参考报告中的改进建议。")
    print("=" * 60)


if __name__ == "__main__":
    main()
