"""TaskSet 数据模型 — 任务集管理"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .task import Task


@dataclass
class TaskSet:
    """一个项目/视频的语音合成任务集合"""

    name: str
    directory: Path
    id: str = ""
    tasks: list[Task] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    # 固定子目录名
    TASKS_DIR = "tasks"
    OUTPUTS_DIR = "outputs"
    META_FILE = "taskset.json"

    def __post_init__(self) -> None:
        if not self.id:
            self.id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    # ------------------------------------------------------------------
    # 目录结构
    # ------------------------------------------------------------------
    @property
    def tasks_dir(self) -> Path:
        return self.directory / self.TASKS_DIR

    @property
    def outputs_dir(self) -> Path:
        return self.directory / self.OUTPUTS_DIR

    def ensure_dirs(self) -> None:
        """确保所有子目录存在"""
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 任务 CRUD
    # ------------------------------------------------------------------
    def add_task(self, task: Task) -> None:
        self.tasks.append(task)
        self._touch()

    def remove_task(self, task_id: str) -> Optional[Task]:
        for i, t in enumerate(self.tasks):
            if t.id == task_id:
                removed = self.tasks.pop(i)
                self._touch()
                # 删除磁盘上的任务文件
                task_file = self.tasks_dir / f"{task_id}.json"
                if task_file.exists():
                    task_file.unlink()
                return removed
        return None

    def get_task(self, task_id: str) -> Optional[Task]:
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None

    def reorder_tasks(self, ordered_ids: list[str]) -> None:
        """按给定的 task_id 顺序重排任务列表"""
        id_to_task = {t.id: t for t in self.tasks}
        new_order = []
        for tid in ordered_ids:
            if tid in id_to_task:
                new_order.append(id_to_task[tid])
        # 追加任何不在排序列表中的任务（保持在末尾）
        seen = set(ordered_ids)
        for t in self.tasks:
            if t.id not in seen:
                new_order.append(t)
        self.tasks = new_order
        self._touch()

    def next_task_id(self) -> str:
        """生成唯一的短 UUID 作为任务 ID"""
        while True:
            tid = str(uuid.uuid4())[:8]
            if not any(t.id == tid for t in self.tasks):
                return tid

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def _touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def save(self) -> None:
        """保存任务集元信息和所有任务配置到磁盘"""
        self.ensure_dirs()

        # 保存任务集元信息
        meta = {
            "id": self.id,
            "name": self.name,
            "directory": str(self.directory),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "task_count": len(self.tasks),
        }
        meta_path = self.directory / self.META_FILE
        tmp = meta_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(meta_path)

        # 保存每个任务
        for task in self.tasks:
            task.save_to(self.tasks_dir)

    @classmethod
    def load(cls, directory: Path) -> TaskSet:
        """从目录加载任务集"""
        meta_path = directory / cls.META_FILE
        if not meta_path.exists():
            raise FileNotFoundError(f"任务集元信息不存在: {meta_path}")

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        ts = cls(
            id=meta["id"],
            name=meta["name"],
            directory=directory,
            created_at=meta.get("created_at", ""),
            updated_at=meta.get("updated_at", ""),
        )

        # 加载所有任务（ID 为 UUID，因此不再用 task_ 前缀过滤）
        ts.ensure_dirs()
        tasks_dir = ts.tasks_dir
        if tasks_dir.exists():
            for f in sorted(tasks_dir.glob("*.json")):
                try:
                    ts.tasks.append(Task.load_from(f))
                except Exception:
                    # 损坏的任务文件跳过，不阻塞加载
                    pass
        return ts

    @classmethod
    def create(cls, name: str, directory: Path) -> TaskSet:
        """创建新的任务集"""
        directory.mkdir(parents=True, exist_ok=True)
        ts = cls(name=name, directory=directory)
        ts.save()
        return ts
