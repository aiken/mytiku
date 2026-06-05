#!/usr/bin/env python3
"""
ocr_engine.py — 扫描版 PDF OCR 引擎

Issue #10: 扫描版 PDF OCR 支持

功能:
1. PDF 类型检测（文本层 vs 纯图片）
2. OCR 引擎抽象接口（支持 Tesseract / 云端 API）
3. 图片预处理（去噪、二值化、倾斜校正）
4. 中文识别 + 数学公式保留
5. 与 extract_all.py 集成

用法:
    from ocr_engine import detect_pdf_type, OCREngine
    
    pdf_type = detect_pdf_type("scan.pdf")
    if pdf_type == "image_only":
        engine = OCREngine.create("tesseract")
        text = engine.recognize("scan.pdf")
"""

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

# 可选依赖（运行时检查）
try:
    from PIL import Image, ImageEnhance, ImageFilter
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False


class PDFType(Enum):
    """PDF 类型"""
    TEXT = "text"           # 包含文本层，可直接提取
    IMAGE_ONLY = "image"    # 纯图片，需要 OCR
    MIXED = "mixed"         # 混合类型（部分页面有文本，部分没有）


@dataclass
class OCRResult:
    """OCR 识别结果"""
    text: str               # 识别出的文本
    confidence: float       # 平均置信度 (0-100)
    page_count: int         # 处理的页数
    has_formula: bool     # 是否检测到数学公式区域
    engine: str             # 使用的引擎名称


# =============================================================================
# PDF 类型检测
# =============================================================================

def detect_pdf_type(file_path: str) -> PDFType:
    """
    检测 PDF 类型：检查是否包含文本层。
    
    策略:
    1. 尝试用 PyMuPDF 提取每页文本
    2. 如果所有页面文本量 < 阈值，判定为 image_only
    3. 如果部分页面有文本，判定为 mixed
    4. 如果大部分页面有文本，判定为 text
    """
    if not HAS_FITZ:
        # 无法检测，保守返回 image（需要 OCR）
        return PDFType.IMAGE_ONLY
    
    doc = fitz.open(file_path)
    text_pages = 0
    image_pages = 0
    TEXT_THRESHOLD = 50  # 字符数阈值
    
    for page in doc:
        text = page.get_text("text").strip()
        if len(text) >= TEXT_THRESHOLD:
            text_pages += 1
        else:
            image_pages += 1
    
    doc.close()
    
    total = text_pages + image_pages
    if total == 0:
        return PDFType.IMAGE_ONLY
    
    text_ratio = text_pages / total
    if text_ratio >= 0.8:
        return PDFType.TEXT
    elif text_ratio <= 0.2:
        return PDFType.IMAGE_ONLY
    else:
        return PDFType.MIXED


def is_scan_pdf(file_path: str) -> bool:
    """快捷判断：是否为扫描版 PDF（需要 OCR）"""
    pdf_type = detect_pdf_type(file_path)
    return pdf_type in (PDFType.IMAGE_ONLY, PDFType.MIXED)


# =============================================================================
# 图片预处理
# =============================================================================

def preprocess_image(image_path: str, output_path: Optional[str] = None) -> str:
    """
    图片预处理：去噪、二值化、倾斜校正。
    
    返回处理后的图片路径。
    """
    if not HAS_PIL:
        return image_path  # 无法处理，返回原图
    
    img = Image.open(image_path)
    
    # 1. 转换为灰度
    if img.mode != 'L':
        img = img.convert('L')
    
    # 2. 增强对比度
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)
    
    # 3. 去噪（中值滤波）
    img = img.filter(ImageFilter.MedianFilter(size=3))
    
    # 4. 二值化（自适应阈值简化版）
    threshold = 128
    img = img.point(lambda x: 0 if x < threshold else 255, '1')
    
    # 保存
    if output_path is None:
        output_path = str(Path(image_path).with_suffix('.preprocessed.png'))
    img.save(output_path, 'PNG')
    
    return output_path


# =============================================================================
# OCR 引擎抽象接口
# =============================================================================

class OCREngine:
    """OCR 引擎抽象基类"""
    
    def __init__(self, lang: str = "chi_sim+eng"):
        self.lang = lang
    
    def recognize(self, image_path: str) -> OCRResult:
        """识别单张图片，返回 OCRResult"""
        raise NotImplementedError
    
    def recognize_pdf(self, pdf_path: str, preprocess: bool = True) -> OCRResult:
        """识别 PDF 所有页面，合并文本"""
        raise NotImplementedError
    
    @classmethod
    def create(cls, engine_type: str, **kwargs) -> "OCREngine":
        """工厂方法：创建指定类型的 OCR 引擎"""
        if engine_type == "tesseract":
            return TesseractEngine(**kwargs)
        elif engine_type == "baidu":
            return BaiduOCREngine(**kwargs)
        elif engine_type == "mock":
            return MockOCREngine(**kwargs)
        else:
            raise ValueError(f"不支持的 OCR 引擎: {engine_type}")


class TesseractEngine(OCREngine):
    """Tesseract OCR 引擎（本地）"""
    
    def __init__(self, lang: str = "chi_sim+eng", tesseract_cmd: str = "tesseract"):
        super().__init__(lang)
        self.tesseract_cmd = tesseract_cmd
        self._check_installation()
    
    def _check_installation(self):
        """检查 Tesseract 是否已安装"""
        try:
            result = subprocess.run(
                [self.tesseract_cmd, "--version"],
                capture_output=True, text=True, timeout=5
            )
            self.installed = result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self.installed = False
    
    def recognize(self, image_path: str) -> OCRResult:
        """使用 Tesseract 识别图片"""
        if not self.installed:
            raise RuntimeError(
                "Tesseract 未安装。请执行:\n"
                "  macOS: brew install tesseract tesseract-lang\n"
                "  Ubuntu: sudo apt install tesseract-ocr tesseract-ocr-chi-sim\n"
                "  或选择云端 OCR 引擎: OCREngine.create('baidu', api_key=..., secret_key=...)"
            )
        
        # 预处理
        processed_path = preprocess_image(image_path)
        
        # 运行 Tesseract
        # 输出到 stdout，使用 -c tessedit_create_txt=1
        result = subprocess.run(
            [self.tesseract_cmd, processed_path, "stdout", "-l", self.lang],
            capture_output=True, text=True, encoding="utf-8"
        )
        
        text = result.stdout.strip()
        # Tesseract stdout 模式不输出置信度，默认返回 0
        confidence = 0.0
        
        # 检测数学公式（简单启发式：查找 $...$ 或特殊符号）
        has_formula = bool(re.search(r'[∫∑∏√∞∂∆παβγδεθλμσφω]|\$[^$]+\$', text))
        
        return OCRResult(
            text=text,
            confidence=confidence,
            page_count=1,
            has_formula=has_formula,
            engine="tesseract"
        )
    
    def recognize_pdf(self, pdf_path: str, preprocess: bool = True) -> OCRResult:
        """将 PDF 转为图片后逐页识别"""
        if not HAS_FITZ:
            raise RuntimeError("需要 PyMuPDF (fitz) 将 PDF 转为图片")
        
        doc = fitz.open(pdf_path)
        all_texts = []
        total_confidence = 0.0
        has_formula = False
        
        with tempfile.TemporaryDirectory() as tmpdir:
            for page_num, page in enumerate(doc):
                # PDF 页面转为图片
                pix = page.get_pixmap(dpi=300)
                img_path = Path(tmpdir) / f"page_{page_num:03d}.png"
                pix.save(str(img_path))
                
                # OCR 识别
                ocr_result = self.recognize(str(img_path))
                all_texts.append(ocr_result.text)
                total_confidence += ocr_result.confidence
                if ocr_result.has_formula:
                    has_formula = True
        
        doc.close()
        
        merged_text = "\n\n".join(all_texts)
        avg_confidence = total_confidence / len(all_texts) if all_texts else 0
        
        return OCRResult(
            text=merged_text,
            confidence=avg_confidence,
            page_count=len(all_texts),
            has_formula=has_formula,
            engine="tesseract"
        )


class BaiduOCREngine(OCREngine):
    """百度 OCR API 引擎（云端）"""
    
    def __init__(self, api_key: str = "", secret_key: str = "", **kwargs):
        super().__init__()
        self.api_key = api_key
        self.secret_key = secret_key
        self.access_token = None
    
    def _get_access_token(self) -> str:
        """获取百度 API access token"""
        if self.access_token:
            return self.access_token
        
        import urllib.request
        import json
        
        url = f"https://aip.baidubce.com/oauth/2.0/token?grant_type=client_credentials&client_id={self.api_key}&client_secret={self.secret_key}"
        
        req = urllib.request.Request(url, method="POST")
        req.add_header("Content-Type", "application/json")
        
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            self.access_token = data.get("access_token")
            return self.access_token
    
    def recognize(self, image_path: str) -> OCRResult:
        """使用百度通用文字识别 API"""
        import urllib.request
        import json
        import base64
        
        token = self._get_access_token()
        url = f"https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic?access_token={token}"
        
        with open(image_path, "rb") as f:
            img_data = base64.b64encode(f.read()).decode("utf-8")
        
        params = {"image": img_data, "language_type": "CHN_ENG"}
        data = urllib.parse.urlencode(params).encode("utf-8")
        
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
        
        words = result.get("words_result", [])
        text = "\n".join(w.get("words", "") for w in words)
        avg_confidence = sum(w.get("probability", {}).get("average", 0) for w in words) / len(words) * 100 if words else 0
        
        return OCRResult(
            text=text,
            confidence=avg_confidence,
            page_count=1,
            has_formula=False,  # 百度通用 OCR 不专门识别公式
            engine="baidu"
        )
    
    def recognize_pdf(self, pdf_path: str, preprocess: bool = True) -> OCRResult:
        """PDF 逐页识别"""
        if not HAS_FITZ:
            raise RuntimeError("需要 PyMuPDF (fitz) 将 PDF 转为图片")
        
        doc = fitz.open(pdf_path)
        all_texts = []
        total_confidence = 0.0
        
        with tempfile.TemporaryDirectory() as tmpdir:
            for page_num, page in enumerate(doc):
                pix = page.get_pixmap(dpi=300)
                img_path = Path(tmpdir) / f"page_{page_num:03d}.png"
                pix.save(str(img_path))
                
                ocr_result = self.recognize(str(img_path))
                all_texts.append(ocr_result.text)
                total_confidence += ocr_result.confidence
        
        doc.close()
        
        merged_text = "\n\n".join(all_texts)
        avg_confidence = total_confidence / len(all_texts) if all_texts else 0
        
        return OCRResult(
            text=merged_text,
            confidence=avg_confidence,
            page_count=len(all_texts),
            has_formula=False,
            engine="baidu"
        )


class MockOCREngine(OCREngine):
    """Mock OCR 引擎（用于测试，不实际识别）"""
    
    def recognize(self, image_path: str) -> OCRResult:
        return OCRResult(
            text=f"[Mock OCR] 图片: {image_path}\n（实际使用需安装 Tesseract 或配置云端 API）",
            confidence=0.0,
            page_count=1,
            has_formula=False,
            engine="mock"
        )
    
    def recognize_pdf(self, pdf_path: str, preprocess: bool = True) -> OCRResult:
        return OCRResult(
            text=f"[Mock OCR] PDF: {pdf_path}\n（实际使用需安装 Tesseract 或配置云端 API）",
            confidence=0.0,
            page_count=1,
            has_formula=False,
            engine="mock"
        )


# =============================================================================
# 与 extract_all.py 集成接口
# =============================================================================

def extract_text_from_pdf(file_path: str, engine_type: str = "auto") -> str:
    """
    从 PDF 提取文本（自动检测类型，扫描版自动 OCR）。
    
    Args:
        file_path: PDF 文件路径
        engine_type: OCR 引擎类型 ('auto', 'tesseract', 'baidu', 'mock')
    
    Returns:
        提取的文本内容
    """
    pdf_type = detect_pdf_type(file_path)
    
    if pdf_type == PDFType.TEXT:
        # 文本层 PDF，直接用 PyMuPDF 提取
        if HAS_FITZ:
            doc = fitz.open(file_path)
            text = "\n".join(page.get_text("text") for page in doc)
            doc.close()
            return text
        else:
            raise RuntimeError("需要 PyMuPDF 提取文本层 PDF")
    
    # 扫描版或混合版，需要 OCR
    if engine_type == "auto":
        # 优先尝试 Tesseract，未安装则使用 Mock
        engine = TesseractEngine()
        if not engine.installed:
            print("警告: Tesseract 未安装，使用 Mock 引擎（无实际识别）")
            engine = MockOCREngine()
    else:
        engine = OCREngine.create(engine_type)
    
    result = engine.recognize_pdf(file_path)
    return result.text


# =============================================================================
# CLI 测试
# =============================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="OCR 引擎测试")
    parser.add_argument("file", help="PDF 或图片文件路径")
    parser.add_argument("--engine", choices=["tesseract", "baidu", "mock", "auto"], default="auto",
                        help="OCR 引擎类型")
    parser.add_argument("--detect-only", action="store_true", help="仅检测 PDF 类型")
    parser.add_argument("--api-key", help="百度 OCR API Key")
    parser.add_argument("--secret-key", help="百度 OCR Secret Key")
    args = parser.parse_args()
    
    if args.detect_only:
        pdf_type = detect_pdf_type(args.file)
        print(f"PDF 类型: {pdf_type.value}")
        print(f"需要 OCR: {is_scan_pdf(args.file)}")
    else:
        if args.engine == "baidu" and (not args.api_key or not args.secret_key):
            print("错误: 使用百度 OCR 需要提供 --api-key 和 --secret-key")
            sys.exit(1)
        
        kwargs = {}
        if args.api_key:
            kwargs["api_key"] = args.api_key
        if args.secret_key:
            kwargs["secret_key"] = args.secret_key
        
        text = extract_text_from_pdf(args.file, args.engine)
        print("=" * 60)
        print("OCR 结果")
        print("=" * 60)
        print(text[:2000])
        if len(text) > 2000:
            print(f"\n... (共 {len(text)} 字符)")
