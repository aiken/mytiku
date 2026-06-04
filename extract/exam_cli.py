#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中考题库 CLI 工具
================
与 Cloudflare Workers 部署的题库 API 交互。

命令:
    query    - 按条件查询题目
    generate - 生成试卷
    preview  - 预览已生成试卷（自动打开浏览器）
    config   - 交互式配置 api_base 和 api_key
"""

import os
import sys
import json
import argparse
import getpass
import webbrowser
import urllib.parse
from pathlib import Path

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


# ----------------------------------------------------------------------
# 配置管理
# ----------------------------------------------------------------------
CONFIG_DIR = Path.home() / ".exam-bank"
CONFIG_FILE = CONFIG_DIR / "config.json"
CONFIG_PERMS = 0o600  # 仅所有者可读写


def ensure_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {}


def save_config(cfg: dict):
    ensure_config_dir()
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(CONFIG_FILE, CONFIG_PERMS)


def interactive_config():
    """交互式配置 api_base 和 api_key"""
    print("=== 题库系统配置 ===")
    current = load_config()
    default_base = current.get("api_base", "https://exam-bank.your-subdomain.workers.dev")
    api_base = input(f"API 基础地址 [{default_base}]: ").strip()
    masked = "*" * len(current.get("api_key", "")) if current.get("api_key") else "未设置"
    api_key = getpass.getpass(f"API Key [{masked}]: ").strip()

    if not api_base:
        api_base = default_base
    if not api_key and current.get("api_key"):
        api_key = current.get("api_key")

    cfg = {
        "api_base": api_base.rstrip("/"),
        "api_key": api_key,
    }
    save_config(cfg)
    print(f"配置已保存到 {CONFIG_FILE}")


def get_auth_headers() -> dict:
    cfg = load_config()
    api_key = cfg.get("api_key")
    if not api_key:
        print("错误: 未配置 api_key，请先运行: exam_cli.py config")
        sys.exit(1)
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def get_api_base() -> str:
    cfg = load_config()
    base = cfg.get("api_base")
    if not base:
        print("错误: 未配置 api_base，请先运行: exam_cli.py config")
        sys.exit(1)
    return base.rstrip("/")


# ----------------------------------------------------------------------
# API 调用
# ----------------------------------------------------------------------
def api_query(
    subject: str = None,
    tags: list = None,
    knowledge_tags: list = None,
    ability_tags: list = None,
    feature_tags: list = None,
    method_tags: list = None,
    position_tag: str = None,
    difficulty: int = None,
    position: str = None,
    limit: int = 10,
):
    """
    POST /api/query
    支持多维度标签筛选
    """
    url = f"{get_api_base()}/api/query"
    headers = get_auth_headers()
    payload = {"limit": limit}
    if subject:
        payload["subject"] = subject
    if tags:
        payload["tags"] = tags
    if knowledge_tags:
        payload["knowledge_tags"] = knowledge_tags
    if ability_tags:
        payload["ability_tags"] = ability_tags
    if feature_tags:
        payload["feature_tags"] = feature_tags
    if method_tags:
        payload["method_tags"] = method_tags
    if position_tag:
        payload["position_tag"] = [position_tag]
    if difficulty is not None:
        payload["difficulty"] = difficulty
    if position:
        payload["position"] = position

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        print(f"请求失败: {e}")
        sys.exit(1)


def api_generate(
    subject: str,
    name: str,
    tags: list = None,
    count: int = None,
    difficulty: int = None,
):
    """
    POST /api/generate
    """
    url = f"{get_api_base()}/api/generate"
    headers = get_auth_headers()
    payload = {
        "subject": subject,
        "name": name,
    }
    if tags:
        payload["tags"] = tags
    if count is not None:
        payload["count"] = count
    if difficulty is not None:
        payload["difficulty"] = difficulty

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        print(f"请求失败: {e}")
        sys.exit(1)


def api_preview(gen_id: str):
    """
    GET /api/html/{gen_id}
    """
    base = get_api_base()
    url = f"{base}/api/html/{urllib.parse.quote(gen_id)}"
    return url


# ----------------------------------------------------------------------
# 子命令
# ----------------------------------------------------------------------
def cmd_query(args):
    data = api_query(
        subject=args.subject,
        tags=args.tags,
        knowledge_tags=args.knowledge_tags,
        ability_tags=args.ability_tags,
        feature_tags=args.feature_tags,
        method_tags=args.method_tags,
        position_tag=args.position_tag,
        difficulty=args.difficulty,
        position=args.position,
        limit=args.limit,
    )
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_generate(args):
    data = api_generate(
        subject=args.subject,
        name=args.name,
        tags=args.tags,
        count=args.count,
        difficulty=args.difficulty,
    )
    print(json.dumps(data, ensure_ascii=False, indent=2))
    gen_id = data.get("gen_id") or data.get("id")
    if gen_id:
        print(f"\n生成 ID: {gen_id}")
        print(f"预览地址: {get_api_base()}/api/html/{gen_id}")


def cmd_preview(args):
    gen_id = args.gen_id
    url = api_preview(gen_id)
    print(f"打开预览: {url}")
    webbrowser.open(url)


def cmd_config(args):
    interactive_config()


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def main():
    if not HAS_REQUESTS:
        print("错误: requests 库未安装。请执行: pip install requests")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="中考题库 CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # query
    p_query = subparsers.add_parser("query", help="查询题目（支持多维度标签筛选）")
    p_query.add_argument("--subject", "-s", help="学科: math / physics")
    p_query.add_argument("--tags", "-t", nargs="+", help="通用标签列表")
    p_query.add_argument("--knowledge-tags", "-k", nargs="+", help="知识点标签，如: 圆 二次函数")
    p_query.add_argument("--ability-tags", "-a", nargs="+", help="能力维度标签，如: 逻辑推理 空间想象")
    p_query.add_argument("--feature-tags", "-f", nargs="+", help="题型特征标签，如: 含图表 多步骤")
    p_query.add_argument("--method-tags", "-m", nargs="+", help="解题方法标签，如: 数形结合 分类讨论")
    p_query.add_argument("--position-tag", "-p", help="考试定位标签，如: 基础题/中档题/综合题/压轴题")
    p_query.add_argument("--difficulty", "-d", type=int, help="难度 1-4")
    p_query.add_argument("--position", help="题位: basic/medium/comprehensive/advanced")
    p_query.add_argument("--limit", "-l", type=int, default=10, help="返回条数")
    p_query.set_defaults(func=cmd_query)

    # generate
    p_gen = subparsers.add_parser("generate", help="生成试卷")
    p_gen.add_argument("--subject", "-s", required=True, help="学科")
    p_gen.add_argument("--name", "-n", required=True, help="试卷名称")
    p_gen.add_argument("--tags", "-t", nargs="+", help="标签筛选")
    p_gen.add_argument("--count", "-c", type=int, help="题目数量")
    p_gen.add_argument("--difficulty", "-d", type=int, help="难度筛选")
    p_gen.set_defaults(func=cmd_generate)

    # preview
    p_prev = subparsers.add_parser("preview", help="预览试卷")
    p_prev.add_argument("gen_id", help="生成 ID")
    p_prev.set_defaults(func=cmd_preview)

    # config
    p_cfg = subparsers.add_parser("config", help="配置 API 地址和密钥")
    p_cfg.set_defaults(func=cmd_config)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
