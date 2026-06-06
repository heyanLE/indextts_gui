"""Task 数据模型 — 单条语音生成任务"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class TaskStatus(str, Enum):
    """任务状态枚举"""

    PENDING = "未开始"
    QUEUED = "队列中"
    GENERATING = "生成中"
    COMPLETED = "生成完成"
    FAILED = "生成失败"


# 合法的状态转换映射
_VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.QUEUED, TaskStatus.GENERATING},
    TaskStatus.QUEUED: {TaskStatus.GENERATING, TaskStatus.PENDING},  # 清空队列回退
    TaskStatus.GENERATING: {TaskStatus.COMPLETED, TaskStatus.FAILED},
    TaskStatus.COMPLETED: {TaskStatus.QUEUED},  # 重新生成
    TaskStatus.FAILED: {TaskStatus.QUEUED},  # 重新生成
}


def sanitize_filename(text: str, max_length: int = 50) -> str:
    """清洗文案为安全文件名

    保留中文、英文、数字，其余替换为下划线，限制长度。
    """
    cleaned = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "_", text)
    cleaned = cleaned.strip("_")
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip("_")
    return cleaned or "untitled"


@dataclass
class Task:
    """单条语音生成任务"""

    id: str  # 唯一短 UUID，如 "a1b2c3d4"
    text: str  # 目标文案
    engine: str  # 引擎标识: "indextts"
    status: TaskStatus = TaskStatus.PENDING
    engine_params: dict[str, Any] = field(default_factory=dict)
    output_audio_path: Optional[str] = None
    generation_config: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    locked: bool = False  # 锁定后详情面板所有编辑控件只读（仅完成态生效）
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    # ------------------------------------------------------------------
    # 状态机
    # ------------------------------------------------------------------
    def transition_to(self, new_status: TaskStatus) -> None:
        """安全地执行状态转换

        Raises:
            ValueError: 非法状态转换
        """
        if new_status not in _VALID_TRANSITIONS.get(self.status, set()):
            raise ValueError(
                f"非法状态转换: {self.status.value} → {new_status.value}"
            )
        self.status = new_status
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def can_edit(self) -> bool:
        """任务是否允许编辑"""
        return self.status in (TaskStatus.PENDING, TaskStatus.FAILED, TaskStatus.COMPLETED)

    def can_delete(self) -> bool:
        """任务是否允许删除"""
        return self.status in (
            TaskStatus.PENDING,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
        )

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # QUEUED 是纯运行时状态，不持久化 → 回退为 PENDING
        status = self.status
        if status == TaskStatus.QUEUED:
            status = TaskStatus.PENDING
        data["status"] = status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        data = dict(data)
        raw_status = data.pop("status", "未开始")
        # 兼容旧数据无 locked 字段
        data.setdefault("locked", False)
        # 兼容状态值（中文）或 key 名
        status_map: dict[str, TaskStatus] = {s.value: s for s in TaskStatus}
        status = status_map.get(raw_status, TaskStatus.PENDING)
        # 安全兜底：运行时状态不应出现在磁盘上 → 回退为未开始
        # - QUEUED：纯运行时，不应持久化
        # - GENERATING：崩溃中断，无法恢复生成进程
        if status in (TaskStatus.QUEUED, TaskStatus.GENERATING):
            status = TaskStatus.PENDING
        return cls(status=status, **data)

    # ------------------------------------------------------------------
    # 音频文件命名
    # ------------------------------------------------------------------
    def audio_filename(self, ext: str = "wav") -> str:
        sanitized = sanitize_filename(self.text)
        return f"{self.id}_{sanitized}.{ext}"

    # ------------------------------------------------------------------
    # 文件 I/O helpers（委托给 TaskSet 管理，这里仅提供便捷方法）
    # ------------------------------------------------------------------
    def save_to(self, directory: Path) -> None:
        """保存任务 JSON 到指定目录下"""
        directory.mkdir(parents=True, exist_ok=True)
        filepath = directory / f"{self.id}.json"
        data = self.to_dict()
        tmp = filepath.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(filepath)

    @classmethod
    def load_from(cls, filepath: Path) -> Task:
        data = json.loads(filepath.read_text(encoding="utf-8"))
        return cls.from_dict(data)
