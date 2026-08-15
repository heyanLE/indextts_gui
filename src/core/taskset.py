"""TaskSet 数据模型 — 任务集管理"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Optional

from ._persistence import atomic_write_json
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
    _lock: RLock = field(default_factory=RLock, init=False, repr=False, compare=False)
    _reserved_task_ids: set[str] = field(default_factory=set, init=False, repr=False, compare=False)
    _preserved_task_ids: set[str] = field(default_factory=set, init=False, repr=False, compare=False)
    load_warnings: list[str] = field(default_factory=list, init=False, repr=False, compare=False)

    # 固定子目录名
    TASKS_DIR = "tasks"
    OUTPUTS_DIR = "outputs"
    META_FILE = "taskset.json"
    FORMAT = "indextts-gui2-taskset"
    SCHEMA_VERSION = 2
    LEGACY_MARKERS = ("set_meta.json", "defaults.json")

    def __post_init__(self) -> None:
        self.directory = Path(self.directory)
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
        with self._lock:
            if any(existing.id == task.id for existing in self.tasks):
                raise ValueError(f"任务 ID 已存在: {task.id}")
            self.tasks.append(task)
            self._reserved_task_ids.discard(task.id)
            self._touch()

    def remove_task(self, task_id: str) -> Optional[Task]:
        with self._lock:
            for i, task in enumerate(self.tasks):
                if task.id != task_id:
                    continue
                if not task.can_delete():
                    raise RuntimeError(f"任务 {task_id} 处于 {task.status.value}，不允许删除")
                removed = self.tasks.pop(i)
                try:
                    # Keep the historical immediate-delete behaviour.  A later
                    # save also prunes stale files, covering externally replaced lists.
                    (self.tasks_dir / f"{task_id}.json").unlink(missing_ok=True)
                except OSError:
                    self.tasks.insert(i, removed)
                    raise
                self._reserved_task_ids.discard(task_id)
                self._touch()
                return removed
            return None

    def get_task(self, task_id: str) -> Optional[Task]:
        with self._lock:
            for task in self.tasks:
                if task.id == task_id:
                    return task
            return None

    def reorder_tasks(self, ordered_ids: list[str]) -> None:
        """按给定的 task_id 顺序重排任务列表"""
        with self._lock:
            id_to_task = {task.id: task for task in self.tasks}
            new_order: list[Task] = []
            seen: set[str] = set()
            for task_id in ordered_ids:
                if task_id in id_to_task and task_id not in seen:
                    new_order.append(id_to_task[task_id])
                    seen.add(task_id)
            # 追加任何不在排序列表中的任务（保持在末尾）
            new_order.extend(task for task in self.tasks if task.id not in seen)
            self.tasks = new_order
            self._touch()

    def next_task_id(self) -> str:
        """生成唯一的短 UUID 作为任务 ID"""
        with self._lock:
            used = {task.id for task in self.tasks} | self._reserved_task_ids
            while True:
                task_id = str(uuid.uuid4())[:8]
                if task_id not in used:
                    self._reserved_task_ids.add(task_id)
                    return task_id

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def _touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def save(self) -> None:
        """保存任务集元信息和所有任务配置到磁盘"""
        with self._lock:
            self.ensure_dirs()
            task_ids = [task.id for task in self.tasks]
            if len(task_ids) != len(set(task_ids)):
                raise ValueError("任务集中存在重复任务 ID")
            # A successfully loaded/recreated task supersedes a previously
            # preserved corrupt file with the same ID.
            self._preserved_task_ids.difference_update(task_ids)
            committed_ids = task_ids + sorted(self._preserved_task_ids)

            # Write task snapshots first.  task_order in the metadata is the
            # commit record: orphan task files from an interrupted save are ignored.
            for task in self.tasks:
                task.save_to(self.tasks_dir)

            self._touch()
            meta = {
                "format": self.FORMAT,
                "schema_version": self.SCHEMA_VERSION,
                "id": self.id,
                "name": self.name,
                "directory": str(self.directory),
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "task_count": len(committed_ids),
                "task_order": committed_ids,
            }
            atomic_write_json(self.directory / self.META_FILE, meta)

            # Prune only after the metadata commit.  Stale files are harmless
            # before this point because loaders follow task_order.
            expected = {f"{task_id}.json" for task_id in committed_ids}
            for path in self.tasks_dir.glob("*.json"):
                if path.name not in expected:
                    path.unlink(missing_ok=True)

    @classmethod
    def load(cls, directory: Path) -> TaskSet:
        """从目录加载任务集"""
        directory = Path(directory)
        meta_path = directory / cls.META_FILE
        if not meta_path.exists():
            legacy = [name for name in cls.LEGACY_MARKERS if (directory / name).exists()]
            if legacy:
                raise ValueError(
                    f"目录是旧版任务集（发现 {', '.join(legacy)}），"
                    "不能作为 GUI2 任务集打开"
                )
            raise FileNotFoundError(f"任务集元信息不存在: {meta_path}")

        import json

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict) or not meta.get("id") or not isinstance(meta.get("name"), str):
            raise ValueError(f"任务集元信息无效: {meta_path}")
        taskset_format = meta.get("format")
        if taskset_format is not None and taskset_format != cls.FORMAT:
            raise ValueError(f"不支持的任务集格式: {taskset_format!r}")
        schema_version = meta.get("schema_version", 1)
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version < 1
            or schema_version > cls.SCHEMA_VERSION
        ):
            raise ValueError(f"不支持的任务集版本: {schema_version!r}")
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
            files = {path.stem: path for path in tasks_dir.glob("*.json")}
            raw_order = meta.get("task_order")
            if isinstance(raw_order, list):
                ordered_ids = [item for item in raw_order if isinstance(item, str)]
                paths = [files[task_id] for task_id in ordered_ids if task_id in files]
                missing_ids = set(ordered_ids) - set(files)
                ts._preserved_task_ids.update(missing_ids)
                ts.load_warnings.extend(
                    f"{task_id}.json: 元数据已提交但任务文件缺失"
                    for task_id in sorted(missing_ids)
                )
            else:
                # Backward compatibility for tasksets saved before task_order.
                paths = sorted(files.values())

            loaded_ids: set[str] = set()
            for f in paths:
                try:
                    task = Task.load_from(f)
                    if task.id != f.stem:
                        raise ValueError(
                            f"任务文件名 {f.stem!r} 与内部 ID {task.id!r} 不一致"
                        )
                    if task.id in loaded_ids:
                        continue
                    ts.tasks.append(task)
                    loaded_ids.add(task.id)
                except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                    # Keep committed but unreadable files across later saves;
                    # otherwise merely opening and saving a taskset loses evidence.
                    ts._preserved_task_ids.add(f.stem)
                    ts.load_warnings.append(f"{f.name}: {exc}")
        return ts

    @classmethod
    def create(cls, name: str, directory: Path) -> TaskSet:
        """创建新的任务集"""
        directory = Path(directory)
        if (directory / cls.META_FILE).exists():
            raise FileExistsError(f"任务集已存在: {directory / cls.META_FILE}")
        if any((directory / marker).exists() for marker in cls.LEGACY_MARKERS):
            raise ValueError("目标目录已包含旧版任务集，拒绝覆盖")
        if not (directory / cls.META_FILE).exists():
            tasks_dir = directory / cls.TASKS_DIR
            if tasks_dir.exists() and any(tasks_dir.glob("*.json")):
                raise ValueError("目标目录包含无元数据的任务文件，拒绝覆盖")
        directory.mkdir(parents=True, exist_ok=True)
        ts = cls(name=name, directory=directory)
        ts.save()
        return ts
