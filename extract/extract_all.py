#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中考题库提取器
===============
从 PDF / Word 试卷中自动提取题目、图片、元数据，
生成批量 SQL INSERT 语句，支持断点续传与多进程。
"""

import os
import sys
import json
import re
import argparse
import logging
import hashlib
import subprocess
import multiprocessing
from pathlib import Path
from time import time, sleep
from tempfile import TemporaryDirectory
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass, field, asdict
from concurrent.futures import ProcessPoolExecutor, as_completed

# 第三方库
try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    from docx import Document
except ImportError:
    Document = None

try:
    from PIL import Image
except ImportError:
    Image = None

from tag_rules import auto_tag, merge_tags, get_tag_summary
from local_db import LocalDB


# ----------------------------------------------------------------------
# 常量与配置
# ----------------------------------------------------------------------
CHECKPOINT_FILE = "checkpoint.json"
FAILED_FILE = "failed_files.json"
MANUAL_REVIEW_FILE = "manual_review.json"
SQL_DIR = "batch_sql"
IMAGE_DIR = "images"

# 题号正则：支持 "1. ", "1．", "1、", "(1)", "（1）", "第1题" 等
QUESTION_NUMBER_RE = re.compile(
    r'^\s*(?:'
    r'(\d+)[\.．、]\s*'           # 1.  1．  1、
    r'|'
    r'第\s*(\d+)\s*题'           # 第1题
    r')',
    re.MULTILINE
)

# 子题号正则：(1), （1）, ①, (i), （i）等
SUB_QUESTION_RE = re.compile(
    r'^\s*(?:'
    r'[\(（]([a-zA-Z0-9]+)[\)）]'  # (1), （a）
    r'|'
    r'([①②③④⑤⑥⑦⑧⑨⑩])'         # ①
    r'|'
    r'([ⅰⅱⅲⅳⅴⅵⅶⅷⅸⅹ])'             # ⅰ
    r')\s*',
    re.MULTILINE
)

# 支持的文件扩展名
SUPPORTED_EXTS = {".pdf", ".docx", ".doc"}


# ----------------------------------------------------------------------
# 数据模型
# ----------------------------------------------------------------------
@dataclass
class ExtractedQuestion:
    """提取出的单道题目（与 schema.sql 字段对齐 + 多维度标签）"""
    question_id: str
    paper_id: str
    question_number: str          # 字符串，支持子题如 "17(1)"
    q_type: str = "comprehensive" # choice/fill/calculation/proof/experiment/reading/comprehensive
    position: str = "medium"      # basic/medium/comprehensive/advanced
    score: int = 0
    difficulty: int = 2
    content: str = ""
    options: Optional[str] = None # JSON 字符串
    answer: Optional[str] = None
    solution: Optional[str] = None
    tags: List[str] = field(default_factory=list)  # 合并后的所有标签
    images: List[str] = field(default_factory=list)
    data_table: Optional[str] = None
    estimated_time: int = 5
    # 多维度标签（本地 SQLite 使用）
    knowledge_tags: List[str] = field(default_factory=list)
    ability_tags: List[str] = field(default_factory=list)
    feature_tags: List[str] = field(default_factory=list)
    method_tags: List[str] = field(default_factory=list)
    position_tag: str = "中档题"
    round_tag: str = "未知"       # 考试轮次：一模/二模/三模/真题/期中/期末/月考
    source_tags: List[str] = field(default_factory=list)  # 来源信息: ["年份:2023","学校:北京四中","区:海淀"]


@dataclass
class PaperMeta:
    """试卷元数据（含来源信息）"""
    paper_id: str
    subject: str
    year: int
    region: str           # 城市，如"北京"
    district: str         # 区名，如"海淀"
    school: str           # 学校名，如"北京四中"
    exam_type: str
    round: str            # 考试轮次：一模/二模/三模/真题/期中/期末/月考
    title: str = ""
    total_score: int = 100
    question_count: int = 0
    metadata: str = ""


# 全局：已插入的 paper_id 集合（去重）
_inserted_papers: set = set()


# ----------------------------------------------------------------------
# 日志
# ----------------------------------------------------------------------
def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """配置日志输出"""
    logger = logging.getLogger("extract_all")
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


# ----------------------------------------------------------------------
# 辅助函数
# ----------------------------------------------------------------------
def filename_hash(filepath: str, length: int = 8) -> str:
    """对文件路径取短哈希"""
    return hashlib.sha256(filepath.encode("utf-8")).hexdigest()[:length]


def infer_subject(path_str: str) -> str:
    """根据路径推断学科"""
    p = path_str.lower()
    if any(k in p for k in ["物理", "physics", "八九物理"]):
        return "physics"
    if any(k in p for k in ["化学", "chemistry"]):
        return "chemistry"
    return "math"


def infer_year(path_str: str) -> int:
    """从路径中推断年份，返回起始年份整数"""
    # 优先匹配 2019-2020 格式，返回起始年
    m = re.search(r'(20\d{2})[-–—](20\d{2})', path_str)
    if m:
        return int(m.group(1))
    # 单独匹配 20xx
    m = re.search(r'(20\d{2})', path_str)
    if m:
        return int(m.group(1))
    return 0


def infer_region(path_str: str) -> str:
    """从路径中推断地区（城市级）"""
    p = path_str.lower()
    if "北京" in p or "beijing" in p or "b京" in p:
        return "北京"
    return "全国"


def infer_district(path_str: str) -> str:
    """从文件名中提取区名，如'海淀''朝阳''西城'等"""
    # 匹配模式：北京市xx区 / 北京xx区
    m = re.search(r'北京(?:市)?([\u4e00-\u9fa5]{2,4}区)', path_str)
    if m:
        return m.group(1)
    # 备选：直接从路径中的目录名提取
    districts = [
        "海淀", "朝阳", "西城", "东城", "丰台", "大兴", "昌平",
        "通州", "顺义", "房山", "石景山", "门头沟", "怀柔",
        "平谷", "密云", "延庆", "燕山"
    ]
    for d in districts:
        if d in path_str:
            return d + "区"
    return "未知"


def infer_school(path_str: str) -> str:
    """从文件名中提取学校名称"""
    stem = Path(path_str).stem
    
    # 1. 附中模式（优先级最高）
   附中_match = re.search(r'(清华|北大|人大|北师大|首师大|理工|交大|铁二|铁路二|民大|师大)附?中', stem)
    if 附中_match:
        school = 附中_match.group(1) + "附中"
        # 统一别名
        if school == "师大附中":
            school = "北师大附中"
        if school == "铁路二中":
            school = "铁二中"
        return school
    
    # 2. 一零一中学
    if re.search(r'一零一|101', stem):
        return "北京一零一中"
    
    # 3. 北京+数字+中 / 数字+中
    m = re.search(r'北京([一二三四五六七八九十零〇百]+)中(?:学|(?:\d+)?)', stem)
    if m:
        return "北京" + m.group(1) + "中"
    
    m = re.search(r'([一二三四五六七八九十零〇百]+)中(?:学|(?:\d+)?)', stem)
    if m and "北京" in stem:
        return "北京" + m.group(1) + "中"
    
    # 4. 知名学校
    known_schools = [
        "育才学校", "育英学校", "景山学校", "景山中学", "育英中学",
        "汇文中学", "广渠门中学", "陈经纶中学", "日坛中学",
        "和平街一中", "东直门中学", "一七一中学", "回民学校",
        "外国语学校", "外语学校", "国际学校", "实验中学",
        "三帆中学", "十三中分校", "三十五中", "大峪中学",
        "北京交通大学附属中学", "北京理工大学附属中学",
        "首都师大附中", "首师大附中"
    ]
    for school in known_schools:
        if school in stem:
            # 去掉"分校"后缀
            return school.replace("分校", "").strip()
    
    # 5. 如果都没匹配到，返回区名作为学校（区统考卷）
    district = infer_district(path_str)
    if district != "未知":
        return district + "统考"
    
    return "未知"


def infer_exam_type(path_str: str) -> str:
    """推断考试类型"""
    p = path_str.lower()
    if "期中" in p:
        return "期中"
    if "期末" in p:
        return "期末"
    if "月考" in p:
        return "月考"
    if "模拟" in p or "中考" in p:
        return "中考模拟"
    return "未知"


def infer_round(path_str: str) -> str:
    """推断考试轮次：一模/二模/三模/真题/期中/期末/月考"""
    p = path_str.lower()
    # 模拟轮次
    if "一模" in p or "第一次模拟" in p:
        return "一模"
    if "二模" in p or "第二次模拟" in p:
        return "二模"
    if "三模" in p or "第三次模拟" in p:
        return "三模"
    if "四模" in p or "第四次模拟" in p:
        return "四模"
    # 真题
    if "真题" in p or "中考试题" in p or "中考卷" in p:
        return "真题"
    # 常规考试
    if "期中" in p:
        return "期中"
    if "期末" in p:
        return "期末"
    if "月考" in p:
        # 提取第几次月考
        m = re.search(r'(\d+)[\s]*月', p)
        if m:
            return f"{m.group(1)}月月考"
        return "月考"
    if "段考" in p or "单元测" in p or "周测" in p:
        return "段考"
    # 默认：如果含"模拟"但无具体轮次
    if "模拟" in p:
        return "模拟"
    return "未知"


def infer_difficulty(question_number: int) -> int:
    """根据题号推断难度"""
    if 1 <= question_number <= 8:
        return 1
    if 9 <= question_number <= 15:
        return 2
    if question_number == 16:
        return 3
    if 17 <= question_number <= 22:
        return 2
    if 23 <= question_number <= 26:
        return 3
    if 27 <= question_number <= 28:
        return 4
    return 2  # 默认中等


def infer_q_type(text: str) -> str:
    """推断题目类型：choice/fill/calculation/proof/experiment/reading/comprehensive"""
    if not text:
        return "comprehensive"
    t = text.strip()
    # 选择题：有 A/B/C/D 选项（优先检测）
    opts = re.findall(r'[A-D][\.．、]\s*\S', t)
    if len(opts) >= 2 and re.search(r'[A-D][\.．、].*[A-D][\.．、]', t):
        return "choice"
    # 填空题：有"____"或明确说"填空"
    if re.search(r'_{2,}|填空', t):
        return "fill"
    # 证明题
    if "证明" in t:
        return "proof"
    # 实验探究
    if any(k in t for k in ["实验", "探究", "测量", "操作"]):
        return "experiment"
    # 阅读题
    if any(k in t for k in ["阅读", "材料", "科普"]):
        return "reading"
    # 计算题（默认）
    return "calculation"


def infer_position(question_number: int) -> str:
    """根据题号推断位置/阶段"""
    if 1 <= question_number <= 15:
        return "basic"
    if 16 <= question_number <= 22:
        return "medium"
    if 23 <= question_number <= 26:
        return "comprehensive"
    if question_number >= 27:
        return "advanced"
    return "medium"


def extract_options(text: str) -> Optional[str]:
    """提取选择题选项，返回 JSON 字符串"""
    # 先移除答案/解析区域，避免误匹配
    clean_text = re.sub(r'【答案】.*', '', text, flags=re.DOTALL)
    clean_text = re.sub(r'【解析】.*', '', clean_text, flags=re.DOTALL)
    clean_text = re.sub(r'【分析】.*', '', clean_text, flags=re.DOTALL)
    clean_text = re.sub(r'【详解】.*', '', clean_text, flags=re.DOTALL)
    clean_text = re.sub(r'【点睛】.*', '', clean_text, flags=re.DOTALL)
    
    opts = []
    for m in re.finditer(r'([A-D])[\.．、]\s*([^\nA-D【]{1,200})', clean_text):
        label = m.group(1)
        opt_text = m.group(2).strip()
        # 过滤掉过短或纯标点的选项
        if opt_text and len(opt_text) > 1 and not re.match(r'^[\s\.．、]+$', opt_text):
            opts.append({"label": label, "text": opt_text})
    if len(opts) >= 2:
        return json.dumps(opts, ensure_ascii=False)
    return None


def extract_answer(text: str) -> Optional[str]:
    """从文本中提取答案（【答案】标记）"""
    m = re.search(r'【答案】\s*([^\n【]+)', text)
    if m:
        return m.group(1).strip()
    # 备选："答案："格式
    m = re.search(r'答案[：:]\s*([^\n]+)', text)
    if m:
        return m.group(1).strip()
    return None


def infer_score(question_number: int, q_type: str) -> int:
    """根据题号和类型推断分值"""
    if q_type == "choice":
        return 3 if question_number <= 8 else 2
    if q_type == "fill":
        return 2
    if 17 <= question_number <= 22:
        return 5
    if 23 <= question_number <= 26:
        return 6
    if question_number >= 27:
        return 7
    return 3


def build_paper_id(subject: str, year: str, region: str, exam_type: str, fhash: str) -> str:
    """生成试卷唯一标识"""
    safe = lambda s: re.sub(r'[^a-zA-Z0-9_\u4e00-\u9fff]', '_', str(s))
    return f"{safe(subject)}_{safe(year)}_{safe(region)}_{safe(exam_type)}_{fhash}"


def build_question_id(paper_id: str, question_number: str) -> str:
    """生成题目唯一标识"""
    return f"q_{paper_id}_{question_number}"


def sanitize_sql(text: str) -> str:
    """对文本进行 SQL 转义，防止注入"""
    if not text:
        return ""
    return text.replace("'", "''").replace("\\", "\\\\")


# ----------------------------------------------------------------------
# 图片处理
# ----------------------------------------------------------------------
def process_image(
    img_bytes: bytes,
    max_width: int = 1200,
    quality: int = 75,
    max_size_kb: int = 50,
    fallback_width: int = 800,
    fallback_quality: int = 60,
) -> Optional[bytes]:
    """
    处理图片：转 RGB、限制宽度、JPEG 压缩。
    若仍大于 max_size_kb，则二次压缩。
    """
    if Image is None:
        logging.getLogger("extract_all").warning("PIL 未安装，跳过图片处理")
        return None
    try:
        from io import BytesIO
        img = Image.open(BytesIO(img_bytes))
        
        # 过滤过小的图片（公式符号、小图标等）
        w, h = img.size
        if w < 50 or h < 50:
            return None
        
        # 转 RGB（去除透明通道）
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # 限制宽度
        if w > max_width:
            ratio = max_width / w
            img = img.resize((max_width, int(h * ratio)), Image.LANCZOS)

        # 首次压缩
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        data = buf.getvalue()

        # 若仍过大，二次压缩
        if len(data) > max_size_kb * 1024:
            ratio = fallback_width / max_width
            new_w = int(img.width * ratio)
            new_h = int(img.height * ratio)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=fallback_quality, optimize=True)
            data = buf.getvalue()

        return data
    except Exception as e:
        logging.getLogger("extract_all").warning(f"图片处理失败: {e}")
        return None


def save_image(
    img_bytes: bytes,
    out_dir: Path,
    subject: str,
    paper_id: str,
    question_number: int,
    fig_index: int,
) -> str:
    """
    保存处理后的图片，返回相对路径。
    命名规则: {subject}_{paper_id}_q{question_number}_fig{fig_index}.jpg
    """
    img_dir = out_dir / IMAGE_DIR / subject
    img_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{subject}_{paper_id}_q{question_number}_fig{fig_index}.jpg"
    filepath = img_dir / filename
    filepath.write_bytes(img_bytes)
    return str(filepath.relative_to(out_dir))


# ----------------------------------------------------------------------
# PDF 提取
# ----------------------------------------------------------------------
def extract_pdf_questions(
    pdf_path: str,
    paper_meta: PaperMeta,
    out_dir: Path,
    max_image_width: int = 1200,
    compress_quality: int = 75,
) -> Tuple[List[ExtractedQuestion], List[str]]:
    """
    使用 PyMuPDF 逐页提取 PDF 内容，按题号拆分题目并提取图片。
    返回 (题目列表, 图片路径列表)。
    """
    questions: List[ExtractedQuestion] = []
    image_paths: List[str] = []
    logger = logging.getLogger("extract_all")

    if fitz is None:
        logger.error("PyMuPDF (fitz) 未安装，无法处理 PDF")
        return questions, image_paths

    doc = fitz.open(pdf_path)
    all_text_blocks: List[Tuple[int, str]] = []  # (page_number, text)

    # 1. 逐页提取文本
    for page_idx in range(len(doc)):
        page = doc.load_page(page_idx)
        text = page.get_text("text")
        all_text_blocks.append((page_idx + 1, text))

    # 2. 合并全文，按题号拆分
    full_text = "\n".join(t for _, t in all_text_blocks)
    splits = split_by_question_number(full_text)

    # 3. 逐题处理
    for q_num, q_text in splits:
        difficulty = infer_difficulty(q_num)
        q_type = infer_q_type(q_text)
        position = infer_position(q_num)
        options = extract_options(q_text)
        answer = extract_answer(q_text)
        score = infer_score(q_num, q_type)
        
        # 多维度自动标签
        tag_result = auto_tag(q_text, paper_meta.subject, q_num, difficulty, q_type)
        all_tags = merge_tags(tag_result)

        q_obj = ExtractedQuestion(
            question_id=build_question_id(paper_meta.paper_id, str(q_num)),
            paper_id=paper_meta.paper_id,
            question_number=str(q_num),
            q_type=q_type,
            position=position,
            score=score,
            difficulty=difficulty,
            content=q_text,
            options=options,
            answer=answer,
            tags=all_tags,
            images=[],
            estimated_time=score,
            knowledge_tags=tag_result["knowledge"],
            ability_tags=tag_result["ability"],
            feature_tags=tag_result["feature"],
            method_tags=tag_result["method"],
            position_tag=tag_result["position"],
        )
        questions.append(q_obj)

    # 4. 提取图片（按页扫描，关联到最近题号）
    fig_counter: Dict[int, int] = {}  # question_number -> next_fig_index
    for page_idx in range(len(doc)):
        page = doc.load_page(page_idx)
        image_list = page.get_images(full=True)
        for img_index, img in enumerate(image_list, start=1):
            xref = img[0]
            try:
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]
                ext = base_image["ext"]
            except Exception as e:
                logger.warning(f"提取图片失败 page={page_idx+1} xref={xref}: {e}")
                continue

            # 处理图片
            processed = process_image(
                image_bytes,
                max_width=max_image_width,
                quality=compress_quality,
            )
            if processed is None:
                continue

            # 关联到该页最近题号
            page_text = all_text_blocks[page_idx][1] if page_idx < len(all_text_blocks) else ""
            nearest_q = find_nearest_question_on_page(page_text, questions)
            if nearest_q is None:
                nearest_q = 1  # 默认归到第1题

            fig_idx = fig_counter.get(nearest_q, 1)
            fig_counter[nearest_q] = fig_idx + 1

            rel_path = save_image(
                processed,
                out_dir,
                paper_meta.subject,
                paper_meta.paper_id,
                nearest_q,
                fig_idx,
            )
            image_paths.append(rel_path)

            # 将图片路径附加到对应题目
            for q in questions:
                if q.question_number == nearest_q:
                    q.images.append(rel_path)
                    if q.page_number == 0:
                        q.page_number = page_idx + 1
                    break

    doc.close()
    return questions, image_paths


def split_by_question_number(text: str) -> List[Tuple[int, str]]:
    """
    按题号正则拆分全文，返回 [(题号, 题目文本), ...]。
    """
    matches = list(QUESTION_NUMBER_RE.finditer(text))
    if not matches:
        return [(1, text.strip())]

    result: List[Tuple[int, str]] = []
    for i, m in enumerate(matches):
        # 提取题号（两个捕获组只有一个有值）
        q_num = int(m.group(1) or m.group(2))
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        segment = text[start:end].strip()
        result.append((q_num, segment))
    return result


def split_sub_questions(text: str) -> List[Dict]:
    """
    在一道题目内部按子题号拆分。
    返回 [{"sub_number": "1", "content": "..."}, ...]
    """
    matches = list(SUB_QUESTION_RE.finditer(text))
    if len(matches) <= 1:
        return []

    subs: List[Dict] = []
    for i, m in enumerate(matches):
        sub_num = m.group(1) or m.group(2) or m.group(3)
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        segment = text[start:end].strip()
        subs.append({"sub_number": sub_num, "content": segment})
    return subs


def find_nearest_question_on_page(page_text: str, questions: List[ExtractedQuestion]) -> Optional[int]:
    """
    根据页面文本中出现的题号，找到最可能关联的题目。
    """
    found_nums = set()
    for m in QUESTION_NUMBER_RE.finditer(page_text):
        num = int(m.group(1) or m.group(2))
        found_nums.add(num)

    if not found_nums:
        return None

    # 取页面中最小的题号作为关联（通常图片在题目附近）
    return min(found_nums)


# ----------------------------------------------------------------------
# Word 提取
# ----------------------------------------------------------------------
def extract_docx_questions(
    docx_path: str,
    paper_meta: PaperMeta,
    out_dir: Path,
    max_image_width: int = 1200,
    compress_quality: int = 75,
) -> Tuple[List[ExtractedQuestion], List[str]]:
    """
    使用 python-docx 提取 Word 段落，按题号拆分题目。
    返回 (题目列表, 图片路径列表)。
    """
    questions: List[ExtractedQuestion] = []
    image_paths: List[str] = []
    logger = logging.getLogger("extract_all")

    if Document is None:
        logger.error("python-docx 未安装，无法处理 Word")
        return questions, image_paths

    try:
        doc = Document(docx_path)
    except Exception as e:
        logger.error(f"打开 Word 文件失败 {docx_path}: {e}")
        return questions, image_paths

    # 提取所有段落文本
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    full_text = "\n".join(paragraphs)

    # 按题号拆分
    splits = split_by_question_number(full_text)

    for q_num, q_text in splits:
        difficulty = infer_difficulty(q_num)
        q_type = infer_q_type(q_text)
        position = infer_position(q_num)
        options = extract_options(q_text)
        answer = extract_answer(q_text)
        score = infer_score(q_num, q_type)
        
        # 多维度自动标签
        tag_result = auto_tag(q_text, paper_meta.subject, q_num, difficulty, q_type)
        all_tags = merge_tags(tag_result)

        q_obj = ExtractedQuestion(
            question_id=build_question_id(paper_meta.paper_id, str(q_num)),
            paper_id=paper_meta.paper_id,
            question_number=str(q_num),
            q_type=q_type,
            position=position,
            score=score,
            difficulty=difficulty,
            content=q_text,
            options=options,
            answer=answer,
            tags=all_tags,
            images=[],
            estimated_time=score,
            knowledge_tags=tag_result["knowledge"],
            ability_tags=tag_result["ability"],
            feature_tags=tag_result["feature"],
            method_tags=tag_result["method"],
            position_tag=tag_result["position"],
        )
        questions.append(q_obj)

    # Word 图片提取（python-docx 原生支持有限，尝试提取内嵌图）
    fig_counter: Dict[int, int] = {}
    try:
        from docx.oxml import parse_xml
        from docx.opc.constants import RELATIONSHIP_TYPE as RT
        rels = doc.part.rels
        for rel in rels.values():
            if "image" in rel.reltype:
                # 尝试获取图片数据
                try:
                    image_part = rel.target_part
                    image_bytes = image_part.blob
                except Exception:
                    continue

                processed = process_image(
                    image_bytes,
                    max_width=max_image_width,
                    quality=compress_quality,
                )
                if processed is None:
                    continue

                # Word 图片难以精确定位题号，默认归到第1题或轮询
                # 简单策略：按出现顺序分配给已有题目
                target_q = 1
                if questions:
                    # 轮询分配
                    idx = len(image_paths) % len(questions)
                    target_q = questions[idx].question_number

                fig_idx = fig_counter.get(target_q, 1)
                fig_counter[target_q] = fig_idx + 1

                rel_path = save_image(
                    processed,
                    out_dir,
                    paper_meta.subject,
                    paper_meta.paper_id,
                    target_q,
                    fig_idx,
                )
                image_paths.append(rel_path)

                for q in questions:
                    if q.question_number == target_q:
                        q.images.append(rel_path)
                        break
    except Exception as e:
        logger.warning(f"Word 图片提取异常: {e}")

    return questions, image_paths


# ----------------------------------------------------------------------
# SQL 生成（与 schema.sql 严格对齐）
# ----------------------------------------------------------------------
def generate_sql_inserts(
    papers: List[PaperMeta],
    questions: List[ExtractedQuestion],
    batch_size: int = 5000,
) -> List[str]:
    """
    生成批量 SQL INSERT 语句。
    先插入 papers，再插入 questions。每批最多 batch_size 条，每批一个事务。
    """
    if not questions:
        return []

    sql_batches: List[str] = []
    current_batch: List[str] = []

    # 1. 插入 papers（去重）
    for p in papers:
        if p.paper_id in _inserted_papers:
            continue
        _inserted_papers.add(p.paper_id)
        stmt = (
            f"INSERT OR IGNORE INTO papers ("
            f"paper_id, title, subject, region, district, school, exam_type, year, total_score, question_count, metadata"
            f") VALUES ("
            f"'{sanitize_sql(p.paper_id)}', "
            f"'{sanitize_sql(p.title)}', "
            f"'{sanitize_sql(p.subject)}', "
            f"'{sanitize_sql(p.region)}', "
            f"'{sanitize_sql(p.district)}', "
            f"'{sanitize_sql(p.school)}', "
            f"'{sanitize_sql(p.exam_type)}', "
            f"{p.year}, "
            f"{p.total_score}, "
            f"{p.question_count}, "
            f"'{sanitize_sql(p.metadata)}'"
            f");"
        )
        current_batch.append(stmt)

    # 2. 插入 questions
    for q in questions:
        tags_json = json.dumps(q.tags, ensure_ascii=False) if q.tags else "[]"
        images_json = json.dumps(q.images, ensure_ascii=False) if q.images else "[]"
        options_val = f"'{sanitize_sql(q.options)}'" if q.options else "NULL"
        answer_val = f"'{sanitize_sql(q.answer)}'" if q.answer else "NULL"

        stmt = (
            f"INSERT INTO questions ("
            f"question_id, paper_id, question_number, q_type, position, score, "
            f"difficulty, content, options, answer, tags, images, estimated_time"
            f") VALUES ("
            f"'{sanitize_sql(q.question_id)}', "
            f"'{sanitize_sql(q.paper_id)}', "
            f"'{sanitize_sql(q.question_number)}', "
            f"'{sanitize_sql(q.q_type)}', "
            f"'{sanitize_sql(q.position)}', "
            f"{q.score}, "
            f"{q.difficulty}, "
            f"'{sanitize_sql(q.content)}', "
            f"{options_val}, "
            f"{answer_val}, "
            f"'{sanitize_sql(tags_json)}', "
            f"'{sanitize_sql(images_json)}', "
            f"{q.estimated_time}"
            f");"
        )
        current_batch.append(stmt)

        if len(current_batch) >= batch_size:
            batch_sql = "BEGIN TRANSACTION;\n" + "\n".join(current_batch) + "\nCOMMIT;"
            sql_batches.append(batch_sql)
            current_batch = []

    if current_batch:
        batch_sql = "BEGIN TRANSACTION;\n" + "\n".join(current_batch) + "\nCOMMIT;"
        sql_batches.append(batch_sql)

    return sql_batches


def write_sql_batches(
    sql_batches: List[str],
    out_dir: Path,
    part_start: int = 1,
) -> List[str]:
    """
    将 SQL 批次写入文件，返回写入的文件路径列表。
    """
    sql_dir = out_dir / SQL_DIR
    sql_dir.mkdir(parents=True, exist_ok=True)

    written: List[str] = []
    for i, batch in enumerate(sql_batches, start=part_start):
        filepath = sql_dir / f"questions_part_{i:03d}.sql"
        filepath.write_text(batch, encoding="utf-8")
        written.append(str(filepath))
    return written


# ----------------------------------------------------------------------
# 单文件处理 Worker
# ----------------------------------------------------------------------
def process_single_file(
    file_path: str,
    out_dir: Path,
    max_image_width: int,
    compress_quality: int,
) -> Dict[str, Any]:
    """
    处理单个文件，返回结果字典。
    该函数设计为可被多进程调用（因此内部重新初始化日志）。
    """
    result = {
        "file": file_path,
        "success": False,
        "questions": [],
        "images": [],
        "error": None,
    }

    # 多进程环境下重新配置日志
    logger = logging.getLogger("extract_all")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s"))
        logger.addHandler(handler)

    path_obj = Path(file_path)
    ext = path_obj.suffix.lower()

    # 跳过解析版/答案版文件（只提取原卷）
    stem_lower = path_obj.stem.lower()
    if any(k in stem_lower for k in ["解析版", "答案版", "解答版", "参考答案", "答案解析"]):
        result["error"] = "跳过解析版文件"
        return result

    if ext not in SUPPORTED_EXTS:
        result["error"] = f"不支持的文件类型: {ext}"
        return result

    # 构建元数据
    subject = infer_subject(file_path)
    year = infer_year(file_path)
    region = infer_region(file_path)
    district = infer_district(file_path)
    school = infer_school(file_path)
    exam_type = infer_exam_type(file_path)
    fhash = filename_hash(file_path)
    paper_id = build_paper_id(subject, str(year), region, exam_type, fhash)

    paper_meta = PaperMeta(
        paper_id=paper_id,
        subject=subject,
        year=year,
        region=region,
        district=district,
        school=school,
        exam_type=exam_type,
        round=infer_round(file_path),
        title=path_obj.stem,
        total_score=100,
        question_count=0,
        metadata=json.dumps({"source": file_path, "district": district, "school": school}, ensure_ascii=False),
    )

    try:
        if ext == ".pdf":
            qs, imgs = extract_pdf_questions(
                file_path, paper_meta, out_dir, max_image_width, compress_quality
            )
        elif ext in (".docx", ".doc"):
            qs, imgs = extract_docx_questions(
                file_path, paper_meta, out_dir, max_image_width, compress_quality
            )
        else:
            result["error"] = f"未处理的类型: {ext}"
            return result

        result["success"] = True
        result["questions"] = [asdict(q) for q in qs]
        result["images"] = imgs
        result["paper"] = asdict(paper_meta)
        result["paper"]["question_count"] = len(qs)

    except Exception as e:
        logger.exception(f"处理文件失败: {file_path}")
        result["error"] = str(e)

    return result


# ----------------------------------------------------------------------
# 断点续传
# ----------------------------------------------------------------------
def load_checkpoint(out_dir: Path) -> Dict:
    """加载断点记录"""
    cp = out_dir / CHECKPOINT_FILE
    if cp.exists():
        return json.loads(cp.read_text(encoding="utf-8"))
    return {"processed": [], "failed": [], "manual_review": [], "sql_part_index": 1}


def save_checkpoint(out_dir: Path, data: Dict):
    """保存断点记录"""
    cp = out_dir / CHECKPOINT_FILE
    cp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_failed_files(out_dir: Path, failed: List[Dict]):
    """保存失败文件列表"""
    fp = out_dir / FAILED_FILE
    fp.write_text(json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8")


def save_manual_review(out_dir: Path, reviews: List[Dict]):
    """保存需人工复核列表"""
    fp = out_dir / MANUAL_REVIEW_FILE
    fp.write_text(json.dumps(reviews, ensure_ascii=False, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="中考题库自动提取器 — 从 PDF/Word 试卷中提取题目、图片并生成 SQL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", "-i", required=True, help="输入目录（支持嵌套子目录）")
    parser.add_argument("--output", "-o", required=True, help="输出目录")
    parser.add_argument("--batch-size", type=int, default=5000, help="每批 SQL 最大条数（默认 5000）")
    parser.add_argument("--max-workers", type=int, default=4, help="多进程并发数（默认 4）")
    parser.add_argument("--compress-quality", type=int, default=75, help="JPEG 压缩质量（默认 75）")
    parser.add_argument("--max-image-width", type=int, default=1200, help="图片最大宽度（默认 1200）")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出 DEBUG 级别日志")
    args = parser.parse_args()

    in_dir = Path(args.input).resolve()
    out_dir = Path(args.output).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    logger = setup_logging(logging.DEBUG if args.verbose else logging.INFO)

    if not in_dir.exists():
        logger.error(f"输入目录不存在: {in_dir}")
        sys.exit(1)

    # 检查依赖
    if fitz is None:
        logger.warning("PyMuPDF (fitz) 未安装，PDF 处理将被跳过。请执行: pip install PyMuPDF")
    if Document is None:
        logger.warning("python-docx 未安装，Word 处理将被跳过。请执行: pip install python-docx")
    if Image is None:
        logger.warning("PIL (Pillow) 未安装，图片处理将被跳过。请执行: pip install Pillow")

    # 扫描所有文件
    all_files = []
    for ext in SUPPORTED_EXTS:
        all_files.extend(in_dir.rglob(f"*{ext}"))
    all_files = [str(p.resolve()) for p in all_files]
    logger.info(f"共发现 {len(all_files)} 个待处理文件")

    if not all_files:
        logger.info("没有待处理文件，退出")
        sys.exit(0)

    # 加载断点
    checkpoint = load_checkpoint(out_dir)
    processed_set = set(checkpoint.get("processed", []))
    pending_files = [f for f in all_files if f not in processed_set]
    logger.info(f"已处理 {len(processed_set)} 个，待处理 {len(pending_files)} 个")

    if not pending_files:
        logger.info("所有文件已处理完毕")
        sys.exit(0)

    # 初始化本地 SQLite 数据库
    db = LocalDB(out_dir / "local.sqlite")
    logger.info(f"本地数据库已初始化: {out_dir / 'local.sqlite'}")

    # 结果聚合
    all_failed: List[Dict] = []
    all_manual: List[Dict] = []

    # 多进程处理
    max_workers = min(args.max_workers, multiprocessing.cpu_count())
    logger.info(f"启动 {max_workers} 个 worker 进程")

    processed_since_checkpoint = 0
    CHECKPOINT_INTERVAL = 100

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_to_file = {
            executor.submit(
                process_single_file,
                f,
                out_dir,
                args.max_image_width,
                args.compress_quality,
            ): f
            for f in pending_files
        }

        for future in as_completed(future_to_file):
            file_path = future_to_file[future]
            try:
                res = future.result()
            except Exception as exc:
                logger.exception(f"Worker 异常: {file_path}")
                all_failed.append({"file": file_path, "error": str(exc)})
                processed_set.add(file_path)
                processed_since_checkpoint += 1
                # 记录失败日志到 SQLite
                db.insert_extract_log({
                    "file_path": file_path,
                    "file_name": Path(file_path).name,
                    "status": "failed",
                    "error_msg": str(exc),
                })
                continue

            if res["success"]:
                processed_set.add(file_path)
                processed_since_checkpoint += 1
                
                # 写入 SQLite
                if res.get("paper"):
                    db.insert_paper(res["paper"])
                for q in res.get("questions", []):
                    db.insert_question(q)
                db.commit()
                
                # 记录成功日志
                db.insert_extract_log({
                    "file_path": file_path,
                    "file_name": Path(file_path).name,
                    "subject": res.get("paper", {}).get("subject", ""),
                    "year": res.get("paper", {}).get("year", 0),
                    "region": res.get("paper", {}).get("region", ""),
                    "exam_type": res.get("paper", {}).get("exam_type", ""),
                    "question_count": len(res.get("questions", [])),
                    "image_count": len(res.get("images", [])),
                    "status": "success",
                })
                db.commit()
                
                logger.info(
                    f"✓ {file_path} | 提取 {len(res['questions'])} 题, "
                    f"{len(res['images'])} 张图 | 已写入 SQLite"
                )

                # 若题目数异常（如0题或过多），标记人工复核
                q_count = len(res["questions"])
                if q_count == 0 or q_count > 50:
                    all_manual.append({
                        "file": file_path,
                        "reason": f"题目数量异常: {q_count}",
                        "questions": q_count,
                    })
            else:
                all_failed.append({"file": file_path, "error": res.get("error")})
                processed_set.add(file_path)
                processed_since_checkpoint += 1
                logger.warning(f"✗ {file_path} | {res.get('error')}")
                # 记录失败日志
                db.insert_extract_log({
                    "file_path": file_path,
                    "file_name": Path(file_path).name,
                    "status": "failed",
                    "error_msg": res.get("error", ""),
                })
                db.commit()

            # 每 100 文件保存断点
            if processed_since_checkpoint >= CHECKPOINT_INTERVAL:
                checkpoint["processed"] = sorted(processed_set)
                save_checkpoint(out_dir, checkpoint)
                save_failed_files(out_dir, all_failed)
                save_manual_review(out_dir, all_manual)
                logger.info(f"断点已保存（已处理 {len(processed_set)} 个文件）")
                processed_since_checkpoint = 0

    # 最终：从 SQLite 导出 SQL 文件
    logger.info("正在从 SQLite 导出 SQL 文件...")
    sql_files = db.export_to_sql(out_dir / "batch_sql", batch_size=args.batch_size)
    logger.info(f"导出完成: {len(sql_files)} 个 SQL 文件")

    # 输出标签统计
    logger.info("正在生成标签分布统计...")
    stats = db.get_stats()
    logger.info(f"数据库统计: {stats['paper_count']} 份试卷, {stats['question_count']} 道题")
    logger.info(f"无标签题目: {stats['untagged_count']} 道 ({stats['untagged_rate']}%)")
    logger.info(f"无图片题目: {stats['no_image_count']} 道 ({stats['no_image_rate']}%)")
    logger.info("Top 10 标签:")
    for tag_info in stats['top_tags'][:10]:
        logger.info(f"  {tag_info['tag']}: {tag_info['count']} 题")

    # 输出各维度标签分布
    for dimension in ["knowledge", "ability", "feature", "method"]:
        dim_stats = db.get_questions_by_tag_dimension(dimension)
        if dim_stats:
            logger.info(f"{dimension} 维度 Top 5:")
            for t in dim_stats[:5]:
                logger.info(f"  {t['tag']}: {t['count']} 题")

    # 保存最终断点与统计
    checkpoint["processed"] = sorted(processed_set)
    save_checkpoint(out_dir, checkpoint)
    save_failed_files(out_dir, all_failed)
    save_manual_review(out_dir, all_manual)

    # 保存统计到 JSON
    stats_path = out_dir / "extract_stats.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"统计已保存: {stats_path}")

    total_ok = len(processed_set) - len(all_failed)
    logger.info("=" * 50)
    logger.info("处理完成")
    logger.info(f"成功: {total_ok} | 失败: {len(all_failed)} | 需复核: {len(all_manual)}")
    logger.info(f"输出目录: {out_dir}")
    logger.info(f"数据库: {out_dir / 'local.sqlite'}")
    logger.info("=" * 50)

    db.close()


if __name__ == "__main__":
    main()
