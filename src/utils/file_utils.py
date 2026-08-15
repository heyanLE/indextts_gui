"""文件处理工具 — 文件命名清洗与路径辅助"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path


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
    safe_task_id = sanitize_filename(task_id, max_length=80)
    sanitized = sanitize_filename(text)
    safe_ext = re.sub(r"[^A-Za-z0-9]", "", ext.lstrip(".")) or "wav"
    return f"{safe_task_id}_{sanitized}.{safe_ext.lower()}"


def ensure_dir(path: Path) -> Path:
    """确保目录存在"""
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write(path: Path, content: str, encoding: str = "utf-8") -> None:
    """原子写入，使用同目录唯一临时文件避免并发写入互相覆盖。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass


def safe_delete(path: Path) -> bool:
    """安全删除文件，返回是否成功"""
    try:
        if path.exists() or path.is_symlink():
            path.unlink()
        return True
    except Exception:
        return False
