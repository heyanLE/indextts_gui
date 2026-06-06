"""文件处理工具 — 文件命名清洗与路径辅助"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


def sanitize_filename(text: str, max_length: int = 50) -> str:
    """清洗文本为安全文件名

    保留中文、英文、数字，其余字符替换为下划线。
    """
    cleaned = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "_", text)
    cleaned = cleaned.strip("_")
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip("_")
    return cleaned or "untitled"


def make_audio_filename(task_id: str, text: str, ext: str = "wav") -> str:
    """根据任务 ID 和文案生成规范的音频文件名"""
    sanitized = sanitize_filename(text)
    return f"{task_id}_{sanitized}.{ext}"


def ensure_dir(path: Path) -> Path:
    """确保目录存在"""
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write(path: Path, content: str, encoding: str = "utf-8") -> None:
    """原子写入：先写临时文件，再 rename，防止写入中断损坏数据"""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding=encoding)
    tmp.replace(path)


def safe_delete(path: Path) -> bool:
    """安全删除文件，返回是否成功"""
    try:
        if path.exists():
            path.unlink()
        return True
    except Exception:
        return False
