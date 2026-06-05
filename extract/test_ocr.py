#!/usr/bin/env python3
"""
test_ocr.py — OCR 引擎测试与准确率评估

Issue #10: 扫描版 PDF OCR 支持

功能:
1. 批量检测 PDF 类型（文本层 vs 扫描版）
2. 测试 OCR 引擎识别准确率
3. 评估扫描版 PDF 的提取质量
4. 生成 OCR 测试报告

用法:
    python extract/test_ocr.py --scan-dir ./北京数学八上 --output ./reports/ocr-test.md
    python extract/test_ocr.py --file ./test.pdf --engine tesseract
    python extract/test_ocr.py --benchmark ./sample_pdfs --ground-truth ./sample_texts
"""

import argparse
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 导入 OCR 引擎
from ocr_engine import (
    detect_pdf_type, is_scan_pdf, PDFType,
    extract_text_from_pdf, OCREngine, TesseractEngine,
    MockOCREngine, OCRResult
)


def scan_directory(base_dir: Path, max_files: int = 50) -> List[Dict]:
    """扫描目录中的所有 PDF，检测类型"""
    results = []
    pdf_files = list(base_dir.rglob("*.pdf"))
    
    for pdf_path in pdf_files[:max_files]:
        try:
            pdf_type = detect_pdf_type(str(pdf_path))
            results.append({
                "path": str(pdf_path),
                "name": pdf_path.name,
                "type": pdf_type.value,
                "needs_ocr": is_scan_pdf(str(pdf_path)),
            })
        except Exception as e:
            results.append({
                "path": str(pdf_path),
                "name": pdf_path.name,
                "type": "error",
                "needs_ocr": False,
                "error": str(e),
            })
    
    return results


def test_ocr_engine(file_path: str, engine_type: str = "auto") -> Dict:
    """测试指定 OCR 引擎，返回结果统计"""
    result = {
        "file": file_path,
        "engine": engine_type,
        "pdf_type": detect_pdf_type(file_path).value,
        "success": False,
        "text_length": 0,
        "confidence": 0.0,
        "page_count": 0,
        "has_formula": False,
        "error": None,
    }
    
    try:
        if engine_type == "auto":
            engine = TesseractEngine()
            if not engine.installed:
                print("⚠️ Tesseract 未安装，使用 Mock 引擎")
                engine = MockOCREngine()
                result["engine"] = "mock"
            else:
                result["engine"] = "tesseract"
        else:
            engine = OCREngine.create(engine_type)
            result["engine"] = engine_type
        
        ocr_result = engine.recognize_pdf(file_path)
        result["success"] = True
        result["text_length"] = len(ocr_result.text)
        result["confidence"] = ocr_result.confidence
        result["page_count"] = ocr_result.page_count
        result["has_formula"] = ocr_result.has_formula
        
        # 质量评估
        text = ocr_result.text
        result["line_count"] = len(text.splitlines())
        result["chinese_char_count"] = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        result["number_count"] = sum(1 for c in text if c.isdigit())
        
        # 启发式：如果中文字符数 > 100 且行数 > 10，认为识别成功
        result["quality_pass"] = result["chinese_char_count"] > 100 and result["line_count"] > 10
        
    except Exception as e:
        result["error"] = str(e)
    
    return result


def benchmark_ocr(pdf_dir: Path, ground_truth_dir: Optional[Path] = None) -> Dict:
    """
    OCR 基准测试：对比 OCR 结果与 ground truth（如果有）。
    
    如果没有 ground truth，则使用启发式评估：
    - 中文字符数 > 100
    - 行数 > 10
    - 包含数字（题号）
    """
    scan_results = scan_directory(pdf_dir, max_files=20)
    scan_pdfs = [r for r in scan_results if r["needs_ocr"]]
    
    if not scan_pdfs:
        return {
            "total_scanned": len(scan_results),
            "scan_pdfs_found": 0,
            "message": "未找到扫描版 PDF",
        }
    
    test_results = []
    for pdf_info in scan_pdfs[:5]:  # 最多测试 5 个扫描版 PDF
        print(f"  测试: {pdf_info['name']}")
        result = test_ocr_engine(pdf_info["path"], "auto")
        test_results.append(result)
    
    # 统计
    success_count = sum(1 for r in test_results if r["success"])
    quality_pass_count = sum(1 for r in test_results if r.get("quality_pass"))
    
    return {
        "total_scanned": len(scan_results),
        "scan_pdfs_found": len(scan_pdfs),
        "tested": len(test_results),
        "success": success_count,
        "quality_pass": quality_pass_count,
        "success_rate": round(success_count / len(test_results) * 100, 1) if test_results else 0,
        "quality_rate": round(quality_pass_count / len(test_results) * 100, 1) if test_results else 0,
        "details": test_results,
    }


def generate_report(results: Dict, output_path: str):
    """生成 Markdown 测试报告"""
    lines = [
        "# OCR 引擎测试报告",
        "",
        f"**测试时间**: 2026-06-05",
        f"**扫描目录**: {results.get('scan_dir', 'N/A')}",
        "",
        "## 1. PDF 类型扫描统计",
        "",
    ]
    
    if "type_distribution" in results:
        lines.append("| 类型 | 数量 | 比例 |")
        lines.append("|------|------|------|")
        for t, count in results["type_distribution"].items():
            pct = round(count / results["total_scanned"] * 100, 1)
            lines.append(f"| {t} | {count} | {pct}% |")
        lines.append("")
    
    lines.extend([
        "## 2. OCR 测试结果",
        "",
        f"- **扫描版 PDF 数量**: {results.get('scan_pdfs_found', 0)}",
        f"- **实际测试数量**: {results.get('tested', 0)}",
        f"- **OCR 成功**: {results.get('success', 0)}/{results.get('tested', 0)}",
        f"- **质量通过**: {results.get('quality_pass', 0)}/{results.get('tested', 0)}",
        f"- **成功率**: {results.get('success_rate', 0)}%",
        f"- **质量通过率**: {results.get('quality_rate', 0)}%",
        "",
        "## 3. 详细结果",
        "",
    ])
    
    for detail in results.get("details", []):
        # 处理两种不同格式的 detail
        if isinstance(detail, dict):
            if "file" in detail:
                file_name = detail["file"]
            elif "name" in detail:
                file_name = detail["name"]
            elif "path" in detail:
                file_name = detail["path"]
            else:
                file_name = "unknown"
            
            status = "✅" if detail.get("quality_pass") else "❌"
            lines.append(f"### {status} {file_name}")
            
            if "engine" in detail:
                lines.append(f"- **引擎**: {detail['engine']}")
            if "success" in detail:
                lines.append(f"- **成功**: {detail['success']}")
            if "text_length" in detail:
                lines.append(f"- **文本长度**: {detail['text_length']} 字符")
            if "chinese_char_count" in detail:
                lines.append(f"- **中文字符**: {detail.get('chinese_char_count', 0)}")
            if "line_count" in detail:
                lines.append(f"- **行数**: {detail.get('line_count', 0)}")
            if "confidence" in detail:
                lines.append(f"- **置信度**: {detail.get('confidence', 0):.1f}%")
            if "page_count" in detail:
                lines.append(f"- **页数**: {detail['page_count']}")
            if "type" in detail:
                lines.append(f"- **PDF 类型**: {detail['type']}")
            if "needs_ocr" in detail:
                lines.append(f"- **需要 OCR**: {detail['needs_ocr']}")
            if detail.get("error"):
                lines.append(f"- **错误**: {detail['error']}")
            lines.append("")
    
    lines.extend([
        "---",
        "*报告由 test_ocr.py 自动生成*",
        "*关联 Issue: #10 [Extraction] 扫描版 PDF OCR 支持*",
    ])
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    
    print(f"报告已保存: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="OCR 引擎测试与评估")
    parser.add_argument("--scan-dir", help="扫描目录，检测所有 PDF 类型")
    parser.add_argument("--file", help="测试单个 PDF 文件")
    parser.add_argument("--engine", choices=["tesseract", "baidu", "mock", "auto"], default="auto")
    parser.add_argument("--output", default="./reports/ocr-test-report.md", help="报告输出路径")
    parser.add_argument("--benchmark", help="基准测试目录（包含扫描版 PDF）")
    parser.add_argument("--ground-truth", help="Ground truth 文本目录（可选）")
    args = parser.parse_args()
    
    if args.file:
        print(f"测试文件: {args.file}")
        print(f"PDF 类型: {detect_pdf_type(args.file).value}")
        print(f"需要 OCR: {is_scan_pdf(args.file)}")
        
        if is_scan_pdf(args.file):
            result = test_ocr_engine(args.file, args.engine)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("该文件为文本层 PDF，无需 OCR")
    
    elif args.scan_dir:
        base_dir = Path(args.scan_dir)
        print(f"扫描目录: {base_dir}")
        
        results = scan_directory(base_dir)
        type_dist = Counter(r["type"] for r in results)
        scan_count = sum(1 for r in results if r["needs_ocr"])
        
        print(f"\n扫描结果:")
        print(f"  总 PDF 数: {len(results)}")
        print(f"  扫描版: {scan_count}")
        print(f"  文本层: {len(results) - scan_count}")
        print(f"  类型分布: {dict(type_dist)}")
        
        # 保存扫描结果
        scan_output = Path(args.output).parent / "ocr-scan-results.json"
        scan_output.parent.mkdir(parents=True, exist_ok=True)
        with open(scan_output, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"扫描结果已保存: {scan_output}")
    
    elif args.benchmark:
        pdf_dir = Path(args.benchmark)
        gt_dir = Path(args.ground_truth) if args.ground_truth else None
        
        print(f"基准测试目录: {pdf_dir}")
        results = benchmark_ocr(pdf_dir, gt_dir)
        results["scan_dir"] = str(pdf_dir)
        
        print(f"\n基准测试结果:")
        print(f"  扫描版 PDF: {results['scan_pdfs_found']}")
        print(f"  测试数量: {results['tested']}")
        print(f"  成功率: {results['success_rate']}%")
        print(f"  质量通过率: {results['quality_rate']}%")
        
        generate_report(results, args.output)
    
    else:
        # 默认：运行项目目录的扫描测试
        base_dir = Path(__file__).parent.parent
        print(f"默认扫描项目目录: {base_dir}")
        
        results = scan_directory(base_dir, max_files=100)
        type_dist = Counter(r["type"] for r in results)
        scan_count = sum(1 for r in results if r["needs_ocr"])
        
        print(f"\n扫描结果:")
        print(f"  总 PDF 数: {len(results)}")
        print(f"  扫描版: {scan_count}")
        print(f"  文本层: {len(results) - scan_count}")
        print(f"  类型分布: {dict(type_dist)}")
        
        # 如果有扫描版 PDF，运行 OCR 测试
        if scan_count > 0:
            scan_pdfs = [r for r in results if r["needs_ocr"]]
            print(f"\n对 {min(3, len(scan_pdfs))} 个扫描版 PDF 运行 OCR 测试...")
            test_results = []
            for pdf_info in scan_pdfs[:3]:
                result = test_ocr_engine(pdf_info["path"], "auto")
                test_results.append(result)
                status = "✅" if result.get("quality_pass") else "❌"
                print(f"  {status} {pdf_info['name']}: {result['text_length']} 字符, 引擎={result['engine']}")
        
        # 保存报告
        report_data = {
            "total_scanned": len(results),
            "scan_pdfs_found": scan_count,
            "type_distribution": dict(type_dist),
            "details": results,
        }
        generate_report(report_data, args.output)


if __name__ == "__main__":
    main()
