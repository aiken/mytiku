#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地 SQLite 数据库模块
=====================
提取后的数据先写入本地 SQLite，支持：
- 结构化存储（与 D1 schema 一致）
- 本地查询/统计/验证
- 数据清洗（UPDATE 修正标签等）
- 增量更新（INSERT OR IGNORE 去重）
- 导出为 SQL 文件（供 upload_to_cf.py 使用）
"""

import os
import json
import sqlite3
import logging
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import asdict

logger = logging.getLogger("local_db")


# ----------------------------------------------------------------------
# 建表 SQL（与 schema.sql 完全一致 + 本地优化索引）
# ----------------------------------------------------------------------
INIT_SQL = """
-- 试卷表
CREATE TABLE IF NOT EXISTS papers (
    paper_id TEXT PRIMARY KEY,
    title TEXT,
    subject TEXT CHECK(subject IN ('math','physics')),
    region TEXT,          -- 城市，如"北京"
    district TEXT,        -- 区名，如"海淀"
    school TEXT,          -- 学校名，如"北京四中"
    exam_type TEXT,
    round TEXT,           -- 考试轮次：一模/二模/三模/真题/期中/期末/月考
    year INTEGER,
    total_score INTEGER DEFAULT 100,
    question_count INTEGER,
    metadata TEXT
);

-- 题目表（核心）
CREATE TABLE IF NOT EXISTS questions (
    question_id TEXT PRIMARY KEY,
    paper_id TEXT REFERENCES papers(paper_id),
    question_number TEXT NOT NULL,
    q_type TEXT CHECK(q_type IN ('choice','fill','calculation','proof','experiment','reading','comprehensive')),
    position TEXT CHECK(position IN ('basic','medium','comprehensive','advanced')),
    score INTEGER DEFAULT 0,
    difficulty INTEGER CHECK(difficulty BETWEEN 1 AND 5),
    content TEXT,
    options TEXT,
    answer TEXT,
    solution TEXT,
    tags TEXT,           -- JSON 数组: ["圆","几何","逻辑推理","多步骤","数形结合","压轴题"]
    images TEXT,
    data_table TEXT,
    estimated_time INTEGER,
    -- 本地扩展字段
    knowledge_tags TEXT,   -- 知识点标签 JSON
    ability_tags TEXT,     -- 能力维度标签 JSON
    feature_tags TEXT,     -- 题型特征标签 JSON
    method_tags TEXT,      -- 解题方法标签 JSON
    position_tag TEXT,     -- 考试定位标签
    source_tags TEXT,      -- 来源信息标签 JSON: ["年份:2023","学校:北京四中","区:海淀"]
    tag_count INTEGER DEFAULT 0,  -- 标签总数（用于质量检查）
    has_image INTEGER DEFAULT 0,  -- 是否有图片
    word_count INTEGER DEFAULT 0, -- 题干字数
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 本地优化索引
CREATE UNIQUE INDEX IF NOT EXISTS idx_q_paper_num ON questions(paper_id, question_number);
CREATE INDEX IF NOT EXISTS idx_q_subject ON questions(paper_id);
CREATE INDEX IF NOT EXISTS idx_q_difficulty ON questions(difficulty);
CREATE INDEX IF NOT EXISTS idx_q_position ON questions(position);
CREATE INDEX IF NOT EXISTS idx_q_type ON questions(q_type);
CREATE INDEX IF NOT EXISTS idx_q_tags ON questions(tags);

-- 标签统计表（本地分析用）
CREATE TABLE IF NOT EXISTS tag_stats (
    tag_name TEXT PRIMARY KEY,
    tag_dimension TEXT,  -- knowledge/ability/feature/method/position
    subject TEXT,
    count INTEGER DEFAULT 0,
    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 提取日志表
CREATE TABLE IF NOT EXISTS extract_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT,
    file_name TEXT,
    subject TEXT,
    year INTEGER,
    region TEXT,
    exam_type TEXT,
    question_count INTEGER,
    image_count INTEGER,
    status TEXT,  -- success/failed/manual_review
    error_msg TEXT,
    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


class LocalDB:
    """本地 SQLite 数据库管理器"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        # WAL 模式：支持读写并发，崩溃安全
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA cache_size=-64000")  # 64MB 缓存
        self._init_tables()
        logger.info(f"本地数据库已连接: {self.db_path}")

    def _init_tables(self):
        """初始化表结构"""
        self.conn.executescript(INIT_SQL)
        self.conn.commit()

    # ------------------------------------------------------------------
    # 写入接口
    # ------------------------------------------------------------------

    def insert_paper(self, paper: Dict[str, Any]) -> bool:
        """插入/更新试卷"""
        try:
            self.conn.execute("""
                INSERT OR REPLACE INTO papers 
                (paper_id, title, subject, region, district, school, exam_type, round, year, total_score, question_count, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                paper.get("paper_id"),
                paper.get("title", ""),
                paper.get("subject", "math"),
                paper.get("region", ""),
                paper.get("district", ""),
                paper.get("school", ""),
                paper.get("exam_type", ""),
                paper.get("round", ""),
                paper.get("year", 0),
                paper.get("total_score", 100),
                paper.get("question_count", 0),
                json.dumps(paper.get("metadata", {}), ensure_ascii=False) if isinstance(paper.get("metadata"), dict) else paper.get("metadata", "{}"),
            ))
            return True
        except Exception as e:
            logger.warning(f"插入试卷失败 {paper.get('paper_id')}: {e}")
            return False

    def insert_question(self, q: Dict[str, Any]) -> bool:
        """插入/更新题目（INSERT OR IGNORE 去重）"""
        try:
            # 解析多维度标签
            tags = q.get("tags", [])
            if isinstance(tags, str):
                tags = json.loads(tags)
            
            knowledge_tags = q.get("knowledge_tags", [])
            ability_tags = q.get("ability_tags", [])
            feature_tags = q.get("feature_tags", [])
            method_tags = q.get("method_tags", [])
            position_tag = q.get("position_tag", "")
            
            # 来源信息标签
            source_tags = q.get("source_tags", [])
            if isinstance(source_tags, str):
                source_tags = json.loads(source_tags)
            
            # 合并为统一 tags 字段（包含来源信息）
            all_tags = list(set(tags + knowledge_tags + ability_tags + feature_tags + method_tags + source_tags))
            if position_tag:
                all_tags.append(position_tag)
            
            # 计算字数
            content = q.get("content", "")
            word_count = len(content) if content else 0
            
            # 是否有图片
            images = q.get("images", [])
            has_image = 1 if (images and len(images) > 0) else 0
            
            self.conn.execute("""
                INSERT OR IGNORE INTO questions 
                (question_id, paper_id, question_number, q_type, position, score, difficulty,
                 content, options, answer, solution, tags, images, data_table, estimated_time,
                 knowledge_tags, ability_tags, feature_tags, method_tags, position_tag, source_tags,
                 tag_count, has_image, word_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                q.get("question_id"),
                q.get("paper_id"),
                q.get("question_number", ""),
                q.get("q_type", "comprehensive"),
                q.get("position", "medium"),
                q.get("score", 0),
                q.get("difficulty", 2),
                content,
                json.dumps(q.get("options"), ensure_ascii=False) if q.get("options") else None,
                q.get("answer"),
                q.get("solution"),
                json.dumps(all_tags, ensure_ascii=False),
                json.dumps(images, ensure_ascii=False) if images else "[]",
                json.dumps(q.get("data_table"), ensure_ascii=False) if q.get("data_table") else None,
                q.get("estimated_time", 5),
                json.dumps(knowledge_tags, ensure_ascii=False),
                json.dumps(ability_tags, ensure_ascii=False),
                json.dumps(feature_tags, ensure_ascii=False),
                json.dumps(method_tags, ensure_ascii=False),
                position_tag,
                json.dumps(source_tags, ensure_ascii=False),
                len(all_tags),
                has_image,
                word_count,
            ))
            return True
        except Exception as e:
            logger.warning(f"插入题目失败 {q.get('question_id')}: {e}")
            return False

    def insert_extract_log(self, log: Dict[str, Any]):
        """记录提取日志"""
        self.conn.execute("""
            INSERT INTO extract_log 
            (file_path, file_name, subject, year, region, exam_type, question_count, image_count, status, error_msg)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            log.get("file_path", ""),
            log.get("file_name", ""),
            log.get("subject", ""),
            log.get("year", 0),
            log.get("region", ""),
            log.get("exam_type", ""),
            log.get("question_count", 0),
            log.get("image_count", 0),
            log.get("status", "success"),
            log.get("error_msg", ""),
        ))

    def commit(self):
        """提交事务"""
        self.conn.commit()

    def close(self):
        """关闭连接"""
        self.conn.close()

    # ------------------------------------------------------------------
    # 查询接口（数据验证 & 质量检查）
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """获取数据库统计概览"""
        cur = self.conn.cursor()
        
        # 试卷统计
        paper_count = cur.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        
        # 题目统计
        q_count = cur.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        
        # 按科目统计
        subject_stats = cur.execute("""
            SELECT p.subject, COUNT(*) as q_count, COUNT(DISTINCT q.paper_id) as p_count
            FROM questions q JOIN papers p ON q.paper_id = p.paper_id
            GROUP BY p.subject
        """).fetchall()
        
        # 按难度统计
        diff_stats = cur.execute("""
            SELECT difficulty, COUNT(*) FROM questions GROUP BY difficulty ORDER BY difficulty
        """).fetchall()
        
        # 按题型统计
        type_stats = cur.execute("""
            SELECT q_type, COUNT(*) FROM questions GROUP BY q_type ORDER BY COUNT(*) DESC
        """).fetchall()
        
        # 无标签题目
        untagged = cur.execute("""
            SELECT COUNT(*) FROM questions 
            WHERE tags = '["未分类"]' OR tags = '[]' OR tags IS NULL
        """).fetchone()[0]
        
        # 无图片题目
        no_image = cur.execute("""
            SELECT COUNT(*) FROM questions WHERE has_image = 0
        """).fetchone()[0]
        
        # 标签分布（Top 20）
        tag_dist = self._get_tag_distribution()
        
        return {
            "paper_count": paper_count,
            "question_count": q_count,
            "subject_stats": [{"subject": r["subject"], "questions": r["q_count"], "papers": r["p_count"]} for r in subject_stats],
            "difficulty_stats": [{"difficulty": r[0], "count": r[1]} for r in diff_stats],
            "type_stats": [{"type": r[0], "count": r[1]} for r in type_stats],
            "untagged_count": untagged,
            "untagged_rate": round(untagged / q_count * 100, 1) if q_count else 0,
            "no_image_count": no_image,
            "no_image_rate": round(no_image / q_count * 100, 1) if q_count else 0,
            "top_tags": tag_dist[:20],
        }

    def _get_tag_distribution(self) -> List[Dict]:
        """获取标签分布统计"""
        cur = self.conn.cursor()
        rows = cur.execute("SELECT tags FROM questions WHERE tags IS NOT NULL").fetchall()
        
        from collections import Counter
        tag_counter = Counter()
        for row in rows:
            try:
                tags = json.loads(row[0])
                if isinstance(tags, list):
                    for t in tags:
                        tag_counter[t] += 1
            except:
                pass
        
        return [{"tag": tag, "count": count} for tag, count in tag_counter.most_common()]

    def query_questions(self, filters: Dict[str, Any]) -> List[Dict]:
        """灵活查询题目"""
        sql = """
            SELECT q.*, p.subject, p.region, p.exam_type, p.year, p.title
            FROM questions q
            JOIN papers p ON q.paper_id = p.paper_id
            WHERE 1=1
        """
        params = []
        
        if "subject" in filters:
            sql += " AND p.subject = ?"
            params.append(filters["subject"])
        if "difficulty" in filters:
            sql += " AND q.difficulty = ?"
            params.append(filters["difficulty"])
        if "q_type" in filters:
            sql += " AND q.q_type = ?"
            params.append(filters["q_type"])
        if "position" in filters:
            sql += " AND q.position = ?"
            params.append(filters["position"])
        if "tag" in filters:
            sql += " AND q.tags LIKE ?"
            params.append(f'%"{filters["tag"]}"%')
        if "min_score" in filters:
            sql += " AND q.score >= ?"
            params.append(filters["min_score"])
        if "has_image" in filters:
            sql += " AND q.has_image = ?"
            params.append(1 if filters["has_image"] else 0)
        if "year" in filters:
            sql += " AND p.year = ?"
            params.append(filters["year"])
        
        sql += " ORDER BY RANDOM()"
        if "limit" in filters:
            sql += " LIMIT ?"
            params.append(filters["limit"])
        
        cur = self.conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    def get_questions_by_tag_dimension(self, dimension: str, subject: Optional[str] = None) -> List[Dict]:
        """按标签维度查询分布"""
        column = {
            "knowledge": "knowledge_tags",
            "ability": "ability_tags",
            "feature": "feature_tags",
            "method": "method_tags",
            "position": "position_tag",
        }.get(dimension, "tags")
        
        sql = f"SELECT {column} FROM questions q JOIN papers p ON q.paper_id = p.paper_id WHERE {column} IS NOT NULL"
        params = []
        if subject:
            sql += " AND p.subject = ?"
            params.append(subject)
        
        rows = self.conn.execute(sql, params).fetchall()
        
        from collections import Counter
        counter = Counter()
        for row in rows:
            try:
                val = row[0]
                if dimension == "position":
                    if val:
                        counter[val] += 1
                else:
                    tags = json.loads(val) if val else []
                    if isinstance(tags, list):
                        for t in tags:
                            counter[t] += 1
            except:
                pass
        
        return [{"tag": tag, "count": count} for tag, count in counter.most_common()]

    # ------------------------------------------------------------------
    # 数据清洗接口
    # ------------------------------------------------------------------

    def update_question_tags(self, question_id: str, tags: List[str]) -> bool:
        """更新题目标签（用于人工修正）"""
        try:
            self.conn.execute("""
                UPDATE questions SET tags = ?, tag_count = ? WHERE question_id = ?
            """, (json.dumps(tags, ensure_ascii=False), len(tags), question_id))
            self.commit()
            return True
        except Exception as e:
            logger.warning(f"更新标签失败 {question_id}: {e}")
            return False

    def batch_update_tags_by_content(self, pattern: str, new_tags: List[str], subject: Optional[str] = None) -> int:
        """按内容模式批量更新标签"""
        sql = "UPDATE questions SET tags = ? WHERE content LIKE ?"
        params = [json.dumps(new_tags, ensure_ascii=False), f"%{pattern}%"]
        
        if subject:
            sql = """
                UPDATE questions SET tags = ? 
                WHERE question_id IN (
                    SELECT q.question_id FROM questions q 
                    JOIN papers p ON q.paper_id = p.paper_id 
                    WHERE q.content LIKE ? AND p.subject = ?
                )
            """
            params = [json.dumps(new_tags, ensure_ascii=False), f"%{pattern}%", subject]
        
        cur = self.conn.execute(sql, params)
        self.commit()
        return cur.rowcount

    def delete_questions_by_paper(self, paper_id: str) -> int:
        """删除某试卷的所有题目（用于重新提取）"""
        cur = self.conn.execute("DELETE FROM questions WHERE paper_id = ?", (paper_id,))
        self.commit()
        return cur.rowcount

    # ------------------------------------------------------------------
    # 导出接口（生成 SQL 文件供 upload_to_cf.py 使用）
    # ------------------------------------------------------------------

    def export_to_sql(self, output_dir: Path, batch_size: int = 5000) -> List[Path]:
        """导出为 questions_part_NNN.sql 文件"""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # 获取所有题目
        cur = self.conn.execute("""
            SELECT q.*, p.subject, p.region, p.exam_type, p.year
            FROM questions q
            JOIN papers p ON q.paper_id = p.paper_id
            ORDER BY q.question_id
        """)
        
        written_files: List[Path] = []
        batch: List[str] = []
        part_index = 1
        
        # 先收集所有 papers（去重）
        papers_sql = []
        paper_rows = self.conn.execute("SELECT * FROM papers").fetchall()
        for row in paper_rows:
            papers_sql.append(self._paper_to_insert_sql(dict(row)))
        
        for row in cur:
            q = dict(row)
            if len(batch) >= batch_size:
                self._write_batch(output_dir, part_index, papers_sql, batch)
                written_files.append(output_dir / f"questions_part_{part_index:03d}.sql")
                part_index += 1
                batch = []
            
            batch.append(self._question_to_insert_sql(q))
        
        if batch:
            self._write_batch(output_dir, part_index, papers_sql, batch)
            written_files.append(output_dir / f"questions_part_{part_index:03d}.sql")
        
        logger.info(f"导出完成: {len(written_files)} 个 SQL 文件, 共 {len(paper_rows)} 份试卷")
        return written_files

    def _paper_to_insert_sql(self, p: Dict) -> str:
        """将 paper 字典转为 INSERT SQL"""
        def esc(s):
            if s is None:
                return "NULL"
            return "'" + str(s).replace("'", "''") + "'"
        
        return (
            f"INSERT OR IGNORE INTO papers ("
            f"paper_id, title, subject, region, exam_type, year, total_score, question_count, metadata"
            f") VALUES ("
            f"{esc(p.get('paper_id'))}, {esc(p.get('title'))}, {esc(p.get('subject'))}, "
            f"{esc(p.get('region'))}, {esc(p.get('exam_type'))}, {p.get('year', 0)}, "
            f"{p.get('total_score', 100)}, {p.get('question_count', 0)}, {esc(p.get('metadata', '{}'))}"
            f");"
        )

    def _question_to_insert_sql(self, q: Dict) -> str:
        """将 question 字典转为 INSERT SQL（包含多维度标签）"""
        def esc(s):
            if s is None:
                return "NULL"
            return "'" + str(s).replace("'", "''") + "'"
        
        return (
            f"INSERT INTO questions ("
            f"question_id, paper_id, question_number, q_type, position, score, difficulty, "
            f"content, options, answer, solution, tags, images, data_table, estimated_time, "
            f"knowledge_tags, ability_tags, feature_tags, method_tags, position_tag"
            f") VALUES ("
            f"{esc(q.get('question_id'))}, {esc(q.get('paper_id'))}, {esc(q.get('question_number'))}, "
            f"{esc(q.get('q_type'))}, {esc(q.get('position'))}, {q.get('score', 0)}, {q.get('difficulty', 2)}, "
            f"{esc(q.get('content'))}, {esc(q.get('options'))}, {esc(q.get('answer'))}, "
            f"{esc(q.get('solution'))}, {esc(q.get('tags'))}, {esc(q.get('images'))}, "
            f"{esc(q.get('data_table'))}, {q.get('estimated_time', 5)}, "
            f"{esc(q.get('knowledge_tags'))}, {esc(q.get('ability_tags'))}, "
            f"{esc(q.get('feature_tags'))}, {esc(q.get('method_tags'))}, "
            f"{esc(q.get('position_tag'))}"
            f");"
        )

    def _write_batch(self, output_dir: Path, part_index: int, papers_sql: List[str], questions_sql: List[str]):
        """写入一批 SQL"""
        filepath = output_dir / f"questions_part_{part_index:03d}.sql"
        content = "BEGIN TRANSACTION;\n"
        # papers 只写在第一批
        if part_index == 1 and papers_sql:
            content += "\n".join(papers_sql) + "\n"
        content += "\n".join(questions_sql) + "\nCOMMIT;"
        filepath.write_text(content, encoding="utf-8")

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
