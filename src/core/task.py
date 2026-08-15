"""Task 数据模型 — 单条语音生成任务"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any, Optional

from ._persistence import atomic_write_json


class TaskStatus(str, Enum):
    """任务状态枚举"""

    PENDING = "未开始"
    QUEUED = "队列中"
    GENERATING = "生成中"
    COMPLETED = "生成完成"
    FAILED = "生成失败"


# 合法的状态转换映射
_VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.QUEUED},
    # A completed task may retain its old audio while queued for regeneration.
    # Cancelling that queued retry therefore restores COMPLETED rather than PENDING.
    TaskStatus.QUEUED: {
        TaskStatus.GENERATING,
        TaskStatus.PENDING,
        TaskStatus.COMPLETED,
    },
    TaskStatus.GENERATING: {TaskStatus.COMPLETED, TaskStatus.FAILED},
    # A durable/output validation failure may invalidate a just-completed task.
    TaskStatus.COMPLETED: {TaskStatus.QUEUED, TaskStatus.FAILED},
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
    _lock: RLock = field(default_factory=RLock, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.status, TaskStatus):
            try:
                self.status = TaskStatus(self.status)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"无效任务状态: {self.status!r}") from exc
        if not isinstance(self.id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", self.id):
            raise ValueError(f"无效任务 ID: {self.id!r}")
        if not isinstance(self.text, str):
            raise TypeError("Task.text 必须是字符串")
        if not isinstance(self.engine, str) or not self.engine:
            raise ValueError("Task.engine 不能为空")
        if not isinstance(self.engine_params, dict):
            raise TypeError("Task.engine_params 必须是字典")
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
        if not isinstance(new_status, TaskStatus):
            raise TypeError("new_status 必须是 TaskStatus")
        with self._lock:
            if new_status not in _VALID_TRANSITIONS.get(self.status, set()):
                raise ValueError(
                    f"非法状态转换: {self.status.value} → {new_status.value}"
                )
            self.status = new_status
            self.updated_at = datetime.now(timezone.utc).isoformat()

    def can_edit(self) -> bool:
        """任务是否允许编辑"""
        with self._lock:
            return self.status in (TaskStatus.PENDING, TaskStatus.FAILED, TaskStatus.COMPLETED)

    def can_delete(self) -> bool:
        """任务是否允许删除"""
        with self._lock:
            return self.status in (
                TaskStatus.PENDING,
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
            )

    def generation_snapshot(self) -> tuple[str, str, dict[str, Any]]:
        """Return one coherent request snapshot under the task lock."""
        with self._lock:
            return self.text, self.engine, deepcopy(self.engine_params)

    def output_path_snapshot(self) -> Optional[str]:
        with self._lock:
            return self.output_audio_path

    def has_valid_output(self) -> bool:
        with self._lock:
            return bool(
                self.output_audio_path
                and Path(self.output_audio_path).is_file()
            )

    def revert_queued(self) -> None:
        """Cancel queued work and restore the last valid durable state."""
        with self._lock:
            if self.status != TaskStatus.QUEUED:
                raise ValueError(f"任务 {self.id} 不在队列中")
            if self.has_valid_output():
                self.transition_to(TaskStatus.COMPLETED)
            else:
                self.output_audio_path = None
                self.generation_config = None
                self.error_message = None
                self.transition_to(TaskStatus.PENDING)

    def complete_generation(
        self,
        output_audio_path: str,
        generation_config: dict[str, Any],
    ) -> None:
        """Publish all successful-generation fields as one state change."""
        with self._lock:
            if self.status != TaskStatus.GENERATING:
                raise ValueError(f"任务 {self.id} 不在生成中")
            self.output_audio_path = output_audio_path
            self.generation_config = deepcopy(generation_config)
            self.error_message = None
            self.transition_to(TaskStatus.COMPLETED)

    def fail_generation(self, error_message: str) -> None:
        """Publish a generation error and its terminal state atomically."""
        with self._lock:
            if self.status not in (TaskStatus.GENERATING, TaskStatus.COMPLETED):
                raise ValueError(f"任务 {self.id} 不在生成或结果提交阶段")
            self.error_message = error_message
            self.transition_to(TaskStatus.FAILED)

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            # QUEUED is runtime-only.  Preserve COMPLETED when a generated task
            # is waiting for regeneration, otherwise restore PENDING on restart.
            status = self.status
            output_audio_path = self.output_audio_path
            generation_config = deepcopy(self.generation_config)
            error_message = self.error_message
            if status == TaskStatus.QUEUED:
                if self.has_valid_output():
                    status = TaskStatus.COMPLETED
                else:
                    status = TaskStatus.PENDING
                    output_audio_path = None
                    generation_config = None
                    error_message = None
            return {
                "id": self.id,
                "text": self.text,
                "engine": self.engine,
                "status": status.value,
                "engine_params": deepcopy(self.engine_params),
                "output_audio_path": output_audio_path,
                "generation_config": generation_config,
                "error_message": error_message,
                "locked": self.locked,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
            }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        data = dict(data)
        raw_status = data.pop("status", None)
        # 兼容旧数据无 locked 字段
        data.setdefault("locked", False)
        # 兼容状态值（中文）或 key 名
        status_map: dict[str, TaskStatus] = {
            key: status
            for status in TaskStatus
            for key in (status.value, status.name)
        }
        if raw_status is None:
            status = TaskStatus.PENDING
        else:
            status = status_map.get(raw_status)
            if status is None:
                raise ValueError(f"未知任务状态: {raw_status!r}")
        # 安全兜底：运行时状态不应出现在磁盘上 → 回退为未开始
        # - QUEUED：纯运行时，不应持久化
        # - GENERATING：崩溃中断，无法恢复生成进程
        if status in (TaskStatus.QUEUED, TaskStatus.GENERATING):
            status = (
                TaskStatus.COMPLETED
                if data.get("output_audio_path")
                else TaskStatus.PENDING
            )
        return cls(status=status, **data)

    # ------------------------------------------------------------------
    # 音频文件命名
    # ------------------------------------------------------------------
    def audio_filename(self, ext: str = "wav") -> str:
        ext = ext.lstrip(".").lower()
        if not re.fullmatch(r"[a-z0-9]+", ext):
            raise ValueError(f"无效音频扩展名: {ext!r}")
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
        if data["output_audio_path"]:
            output_path = Path(data["output_audio_path"])
            if output_path.is_absolute():
                try:
                    data["output_audio_path"] = output_path.resolve().relative_to(
                        directory.parent.resolve()
                    ).as_posix()
                except ValueError:
                    # External output paths remain absolute for compatibility.
                    pass
        atomic_write_json(filepath, data)

    @classmethod
    def load_from(cls, filepath: Path) -> Task:
        import json

        data = json.loads(filepath.read_text(encoding="utf-8"))
        task = cls.from_dict(data)
        if task.output_audio_path:
            stored_path = Path(task.output_audio_path)
            taskset_dir = filepath.parent.parent
            if stored_path.is_absolute():
                resolved = stored_path
            else:
                resolved = (taskset_dir / stored_path).resolve(strict=False)
                try:
                    resolved.relative_to(taskset_dir.resolve())
                except ValueError as exc:
                    raise ValueError(f"任务输出路径越界: {stored_path}") from exc
            if not resolved.exists() and stored_path.is_absolute():
                relocated = taskset_dir / "outputs" / stored_path.name
                if relocated.exists():
                    resolved = relocated
            task.output_audio_path = str(resolved.resolve(strict=False))
            if task.status == TaskStatus.COMPLETED and not resolved.exists():
                task.status = TaskStatus.FAILED
                task.error_message = f"输出音频不存在: {resolved}"
        return task
