#!/usr/bin/env python3
"""
validation_runner.py — 数据抽取验证框架

Issue #16: 数学/物理各 100 套试卷验证

流程:
1. 扫描所有原卷版文件
2. 按年份×地区×考试类型分层抽样
3. 运行 extract_all.py 抽取
4. 运行 tag_quality.py + metadata_validator.py 统计
5. 生成 Markdown 验证报告
"""

import json
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from random import Random
from typing import Dict, List, Tuple

# 分层抽样配置
STRATA_CONFIG = {
    "year": [2019, 2020, 2021, 2022, 2023, 2024],
    "district": ["海淀", "西城", "东城", "朝阳", "丰台", "顺义", "通州", "房山", "门头沟", "其他"],
    "exam_type": ["期中", "期末", "一模", "二模"],
}

SAMPLES_PER_SUBJECT = 20  # 每科抽样数（测试用，正式运行改为 100）
SEED = 42  # 可复现


def scan_original_files(base_dir: Path) -> List[Dict]:
    """扫描所有原卷版文件，解析元数据"""
    files = []
    for ext in ["*.docx", "*.doc", "*.pdf"]:
        for path in base_dir.rglob(ext):
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

            files.append({
                "path": str(path),
                "subject": subject,
                "year": year,
                "district": district,
                "exam_type": exam_type,
                "filename": name,
            })

    return files


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

    # 清空临时目录
    for f in temp_input_dir.iterdir():
        if f.is_file():
            f.unlink()
        elif f.is_dir():
            shutil.rmtree(f)

    # 复制文件到临时目录（保持简单，直接复制到根目录）
    for item in sample:
        src = Path(item["path"])
        dst = temp_input_dir / src.name
        shutil.copy2(src, dst)

    # 运行抽取
    cmd = [
        sys.executable, "extract/extract_all.py",
        "--input", str(temp_input_dir),
        "--output", output_dir,
        "--max-workers", "4",
    ]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).parent.parent)

    # extract_all.py 输出数据库为 local.sqlite
    actual_db = Path(output_dir) / "local.sqlite"

    return {
        "returncode": result.returncode,
        "stdout": result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout,
        "stderr": result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr,
        "db_path": str(actual_db) if actual_db.exists() else None,
    }


def run_tag_quality(db_path: str, output_dir: str) -> Dict:
    """运行标签质量检查"""
    cmd = [
        sys.executable, "extract/tag_quality.py",
        "--db", db_path,
        "--format", "json",
        "--output", str(Path(output_dir) / "tag_quality.json"),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).parent.parent)

    # 读取 JSON 结果
    json_path = Path(output_dir) / "tag_quality.json"
    if json_path.exists():
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def run_metadata_validation(db_path: str, output_dir: str) -> Dict:
    """运行元数据校验"""
    cmd = [
        sys.executable, "extract/metadata_validator.py",
        "--db", db_path,
        "--format", "json",
        "--output-dir", output_dir,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).parent.parent)

    # 读取结果
    # metadata_validator 目前不支持 json 输出，需要修改
    # 暂时从 stdout 解析
    return {"returncode": result.returncode}


def generate_report(
    math_sample: List[Dict],
    physics_sample: List[Dict],
    extraction_result: Dict,
    tag_quality: Dict,
    output_path: str,
) -> str:
    """生成 Markdown 验证报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

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

    # 标签质量统计
    tag_stats = ""
    if tag_quality:
        tag_stats = f"""
### 标签覆盖率
| 维度 | 覆盖率 |
|------|--------|
| 知识点 | {tag_quality.get('knowledge', {}).get('coverage', 'N/A')} |
| 能力 | {tag_quality.get('ability', {}).get('coverage', 'N/A')} |
| 特征 | {tag_quality.get('feature', {}).get('coverage', 'N/A')} |
| 方法 | {tag_quality.get('method', {}).get('coverage', 'N/A')} |
| 定位 | {tag_quality.get('position', {}).get('coverage', 'N/A')} |
"""

    report = f"""# 数据抽取验证报告

**生成时间**: {now}
**验证集规模**: 数学 {len(math_sample)} 套 + 物理 {len(physics_sample)} 套 = {len(math_sample) + len(physics_sample)} 套

---

## 1. 验证集构成

### 数学 ({len(math_sample)} 套)
{sample_stats(math_sample)}

### 物理 ({len(physics_sample)} 套)
{sample_stats(physics_sample)}

---

## 2. 抽取流程执行

- **命令**: `extract_all.py --input-list validation_file_list.txt`
- **返回码**: {extraction_result['returncode']}
- **输出摘要**:
```
{extraction_result['stdout'][:1000]}
```

---

## 3. 质量统计

{tag_stats}

### 元数据准确率
> 运行 `metadata_validator.py` 生成，见 `reports/` 目录

---

## 4. 问题与改进建议

### 发现的问题
1. [待填写]

### 改进建议
1. [待填写]

---

## 5. 验收标准检查

| 检查项 | 标准 | 实际 | 状态 |
|--------|------|------|------|
| 题目提取成功率 | ≥ 95% | [待测量] | ⬜ |
| 无标签题目比例 | < 5% | [待测量] | ⬜ |
| 每题平均标签数 | ≥ 4 个 | [待测量] | ⬜ |
| 图片提取成功率 | ≥ 90% | [待测量] | ⬜ |
| 元数据准确率 | ≥ 80% | [待测量] | ⬜ |
| 答案/解析匹配 | ≥ 90% | [待测量] | ⬜ |

---

*报告由 validation_runner.py 自动生成*
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
    print("=" * 60)

    # 1. 扫描文件
    print("\n[1/5] 扫描试卷文件...")
    all_files = scan_original_files(base_dir)
    print(f"  找到 {len(all_files)} 份原卷版文件")

    math_count = sum(1 for f in all_files if f["subject"] == "math")
    physics_count = sum(1 for f in all_files if f["subject"] == "physics")
    print(f"  数学: {math_count}, 物理: {physics_count}")

    # 2. 分层抽样
    print(f"\n[2/5] 分层抽样 (每科 {SAMPLES_PER_SUBJECT} 套)...")
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
    print(f"\n[3/5] 运行抽取流程...")
    all_sample = math_sample + physics_sample
    extraction_result = run_extraction(all_sample, db_path, str(output_dir))
    print(f"  返回码: {extraction_result['returncode']}")
    actual_db = extraction_result.get("db_path", db_path)

    # 4. 质量检查
    print(f"\n[4/5] 运行质量检查...")
    tag_quality = run_tag_quality(actual_db, str(output_dir))
    print(f"  标签质量: {len(tag_quality)} 个维度")

    # 5. 生成报告
    print(f"\n[5/5] 生成验证报告...")
    report_path = output_dir / f"validation-report-{datetime.now().strftime('%Y%m%d')}.md"
    generate_report(math_sample, physics_sample, extraction_result, tag_quality, str(report_path))
    print(f"  报告: {report_path}")

    print("\n" + "=" * 60)
    print("验证完成!")
    print(f"输出目录: {output_dir}")
    print("=" * 60)


if __name__ == "__main__":
    main()
