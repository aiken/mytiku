#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cloudflare 上传模块
===================
- D1 数据库：分批执行 SQL 文件（wrangler d1 execute）
- R2 存储桶：上传图片（boto3 S3 兼容 或 wrangler）
- 标签初始化：执行 seed_tags.sql
- 断点续传：upload_checkpoint.json
"""

import os
import sys
import json
import argparse
import logging
import subprocess
import hashlib
from pathlib import Path
from typing import List, Dict, Optional

try:
    import boto3
    from botocore.config import Config
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False


# ----------------------------------------------------------------------
# 日志
# ----------------------------------------------------------------------
def setup_logging(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("upload_to_cf")
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
# 断点续传
# ----------------------------------------------------------------------
CHECKPOINT_FILE = "upload_checkpoint.json"

def load_checkpoint(work_dir: Path) -> Dict:
    cp = work_dir / CHECKPOINT_FILE
    if cp.exists():
        return json.loads(cp.read_text(encoding="utf-8"))
    return {"uploaded_sql": [], "uploaded_images": [], "initialized_tags": False}


def save_checkpoint(work_dir: Path, data: Dict):
    cp = work_dir / CHECKPOINT_FILE
    cp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------
# D1 SQL 上传
# ----------------------------------------------------------------------
def upload_d1_sql(sql_file: Path, db_name: str = "exam-bank-db", logger: Optional[logging.Logger] = None) -> bool:
    """
    使用 wrangler CLI 执行单个 SQL 文件到 D1 数据库。
    """
    if logger is None:
        logger = logging.getLogger("upload_to_cf")

    cmd = [
        "wrangler", "d1", "execute", db_name,
        "--file", str(sql_file),
        "--yes",  # 自动确认
    ]

    logger.info(f"执行 D1 SQL: {sql_file.name}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=300,  # 5 分钟超时
        )
        logger.info(f"  → 成功 ({sql_file.name})")
        if result.stdout:
            logger.debug(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"  → 失败 ({sql_file.name}): {e.stderr}")
        return False
    except subprocess.TimeoutExpired:
        logger.error(f"  → 超时 ({sql_file.name})")
        return False
    except FileNotFoundError:
        logger.error("wrangler 命令未找到，请确保已安装并登录 Cloudflare CLI")
        return False


def batch_upload_d1(
    sql_dir: Path,
    db_name: str,
    checkpoint: Dict,
    logger: logging.Logger,
) -> List[str]:
    """
    批量上传 SQL 文件，支持断点续传。
    返回本次新上传成功的文件列表。
    """
    if not sql_dir.exists():
        logger.warning(f"SQL 目录不存在: {sql_dir}")
        return []

    sql_files = sorted(sql_dir.glob("questions_part_*.sql"))
    if not sql_files:
        logger.warning("未找到 questions_part_*.sql 文件")
        return []

    uploaded_set = set(checkpoint.get("uploaded_sql", []))
    success_new: List[str] = []

    for sql_file in sql_files:
        key = str(sql_file.resolve())
        if key in uploaded_set:
            logger.info(f"跳过（已上传）: {sql_file.name}")
            continue

        ok = upload_d1_sql(sql_file, db_name, logger)
        if ok:
            uploaded_set.add(key)
            success_new.append(key)
            checkpoint["uploaded_sql"] = sorted(uploaded_set)
            # 每成功一个立即保存断点
            save_checkpoint(sql_dir.parent, checkpoint)
        else:
            logger.warning(f"中止后续上传（因 {sql_file.name} 失败）")
            break

    return success_new


# ----------------------------------------------------------------------
# R2 图片上传
# ----------------------------------------------------------------------
def get_r2_client(
    account_id: str,
    access_key_id: str,
    secret_access_key: str,
    endpoint_url: str,
):
    """
    创建 boto3 S3 兼容客户端（用于 Cloudflare R2）。
    """
    if not HAS_BOTO3:
        raise RuntimeError("boto3 未安装，无法使用 R2 上传。请执行: pip install boto3")

    session = boto3.session.Session()
    client = session.client(
        service_name="s3",
        region_name="auto",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        config=Config(signature_version="s3v4"),
    )
    return client


def upload_image_r2(
    local_path: Path,
    bucket: str,
    r2_client,
    remote_prefix: str = "images",
    max_size_kb: int = 50,
    logger: Optional[logging.Logger] = None,
) -> bool:
    """
    上传单张图片到 R2，上传前检查大小。
    """
    if logger is None:
        logger = logging.getLogger("upload_to_cf")

    if not local_path.exists():
        logger.warning(f"图片不存在: {local_path}")
        return False

    size_kb = local_path.stat().st_size / 1024
    if size_kb > max_size_kb:
        logger.warning(
            f"图片过大 ({size_kb:.1f}KB > {max_size_kb}KB)，跳过: {local_path.name}"
        )
        return False

    remote_key = f"{remote_prefix}/{local_path.name}"
    logger.info(f"上传 R2: {local_path.name} -> {remote_key}")

    try:
        r2_client.upload_file(
            str(local_path),
            bucket,
            remote_key,
            ExtraArgs={"ContentType": "image/jpeg"},
        )
        logger.info(f"  → 成功")
        return True
    except Exception as e:
        logger.error(f"  → 失败: {e}")
        return False


def batch_upload_images_r2(
    images_dir: Path,
    bucket: str,
    r2_client,
    checkpoint: Dict,
    logger: logging.Logger,
    max_size_kb: int = 50,
) -> List[str]:
    """
    批量上传图片到 R2，支持断点续传。
    返回本次新上传成功的文件列表。
    """
    if not images_dir.exists():
        logger.warning(f"图片目录不存在: {images_dir}")
        return []

    image_files = list(images_dir.rglob("*.jpg")) + list(images_dir.rglob("*.jpeg"))
    if not image_files:
        logger.warning("未找到 .jpg / .jpeg 图片")
        return []

    uploaded_set = set(checkpoint.get("uploaded_images", []))
    success_new: List[str] = []

    for img_file in image_files:
        key = str(img_file.resolve())
        if key in uploaded_set:
            logger.debug(f"跳过（已上传）: {img_file.name}")
            continue

        ok = upload_image_r2(img_file, bucket, r2_client, max_size_kb=max_size_kb, logger=logger)
        if ok:
            uploaded_set.add(key)
            success_new.append(key)
            checkpoint["uploaded_images"] = sorted(uploaded_set)
            save_checkpoint(images_dir.parent, checkpoint)
        else:
            # 失败不阻断，继续下一张
            pass

    return success_new


def upload_images_wrangler(
    images_dir: Path,
    bucket: str,
    checkpoint: Dict,
    logger: logging.Logger,
    max_size_kb: int = 50,
) -> List[str]:
    """
    使用 wrangler r2 object put 上传图片（备用方案，无需 boto3）。
    """
    if not images_dir.exists():
        logger.warning(f"图片目录不存在: {images_dir}")
        return []

    image_files = list(images_dir.rglob("*.jpg")) + list(images_dir.rglob("*.jpeg"))
    uploaded_set = set(checkpoint.get("uploaded_images", []))
    success_new: List[str] = []

    for img_file in image_files:
        key = str(img_file.resolve())
        if key in uploaded_set:
            logger.debug(f"跳过（已上传）: {img_file.name}")
            continue

        size_kb = img_file.stat().st_size / 1024
        if size_kb > max_size_kb:
            logger.warning(f"图片过大，跳过: {img_file.name}")
            continue

        remote_key = f"images/{img_file.name}"
        cmd = [
            "wrangler", "r2", "object", "put",
            f"{bucket}/{remote_key}",
            "--file", str(img_file),
            "--content-type", "image/jpeg",
        ]

        logger.info(f"上传 R2 (wrangler): {img_file.name}")
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
            logger.info(f"  → 成功")
            uploaded_set.add(key)
            success_new.append(key)
            checkpoint["uploaded_images"] = sorted(uploaded_set)
            save_checkpoint(images_dir.parent, checkpoint)
        except Exception as e:
            logger.error(f"  → 失败: {e}")

    return success_new


# ----------------------------------------------------------------------
# 标签初始化
# ----------------------------------------------------------------------
def init_tags(
    seed_sql_path: Optional[Path],
    db_name: str,
    checkpoint: Dict,
    logger: logging.Logger,
) -> bool:
    """
    执行 seed_tags.sql 初始化标签表。
    """
    if checkpoint.get("initialized_tags"):
        logger.info("标签已初始化，跳过")
        return True

    if seed_sql_path is None or not seed_sql_path.exists():
        logger.warning(f"seed_tags.sql 不存在: {seed_sql_path}")
        return False

    ok = upload_d1_sql(seed_sql_path, db_name, logger)
    if ok:
        checkpoint["initialized_tags"] = True
        save_checkpoint(seed_sql_path.parent, checkpoint)
        logger.info("标签初始化完成")
    return ok


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Cloudflare D1 / R2 批量上传工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--sql-dir", help="batch_sql 目录路径")
    parser.add_argument("--images-dir", help="images 目录路径")
    parser.add_argument("--init-tags", action="store_true", help="执行标签初始化")
    parser.add_argument("--seed-sql", default="seed_tags.sql", help="标签初始化 SQL 文件路径")
    parser.add_argument("--db-name", default="exam-bank-db", help="D1 数据库名称")
    parser.add_argument("--r2-bucket", default="exam-bank-images", help="R2 存储桶名称")
    parser.add_argument("--r2-endpoint", help="R2 S3 兼容 endpoint（如使用 boto3）")
    parser.add_argument("--r2-access-key", help="R2 Access Key ID")
    parser.add_argument("--r2-secret-key", help="R2 Secret Access Key")
    parser.add_argument("--use-wrangler-r2", action="store_true", help="使用 wrangler 而非 boto3 上传 R2")
    parser.add_argument("--max-image-size-kb", type=int, default=50, help="图片大小上限 KB（默认 50）")
    parser.add_argument("--work-dir", help="断点文件所在目录（默认取 sql-dir 或 images-dir 的父目录）")
    parser.add_argument("--verbose", "-v", action="store_true", help="DEBUG 日志")
    args = parser.parse_args()

    logger = setup_logging(logging.DEBUG if args.verbose else logging.INFO)

    # 确定工作目录（断点文件存放位置）
    work_dir = None
    if args.work_dir:
        work_dir = Path(args.work_dir).resolve()
    elif args.sql_dir:
        work_dir = Path(args.sql_dir).resolve().parent
    elif args.images_dir:
        work_dir = Path(args.images_dir).resolve().parent

    if work_dir is None:
        logger.error("请至少指定 --sql-dir 或 --images-dir 或 --work-dir")
        sys.exit(1)

    work_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = load_checkpoint(work_dir)

    # 1. 标签初始化
    if args.init_tags:
        seed_path = Path(args.seed_sql) if args.seed_sql else None
        if seed_path and not seed_path.is_absolute():
            seed_path = work_dir / seed_path
        init_tags(seed_path, args.db_name, checkpoint, logger)

    # 2. D1 SQL 上传
    if args.sql_dir:
        sql_dir = Path(args.sql_dir).resolve()
        uploaded = batch_upload_d1(sql_dir, args.db_name, checkpoint, logger)
        logger.info(f"D1 SQL 本次新上传: {len(uploaded)} 个文件")

    # 3. R2 图片上传
    if args.images_dir:
        images_dir = Path(args.images_dir).resolve()
        if args.use_wrangler_r2:
            uploaded = upload_images_wrangler(
                images_dir, args.r2_bucket, checkpoint, logger, args.max_image_size_kb
            )
        else:
            if not HAS_BOTO3:
                logger.error("boto3 未安装，无法使用 R2 上传。请添加 --use-wrangler-r2 或安装 boto3")
                sys.exit(1)
            if not args.r2_endpoint or not args.r2_access_key or not args.r2_secret_key:
                logger.error("使用 boto3 上传 R2 需要提供 --r2-endpoint, --r2-access-key, --r2-secret-key")
                sys.exit(1)

            r2_client = get_r2_client(
                account_id="",  # R2 不需要 account_id 在 endpoint 中
                access_key_id=args.r2_access_key,
                secret_access_key=args.r2_secret_key,
                endpoint_url=args.r2_endpoint,
            )
            uploaded = batch_upload_images_r2(
                images_dir, args.r2_bucket, r2_client, checkpoint, logger, args.max_image_size_kb
            )
        logger.info(f"R2 图片本次新上传: {len(uploaded)} 个文件")

    logger.info("上传流程结束")


if __name__ == "__main__":
    main()
