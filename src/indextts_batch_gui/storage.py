from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .filenames import build_output_audio_path
from .models import TaskRecord, TaskSetDefaults


_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_LOCKS_GUARD = threading.Lock()
_DIRECTORY_LOCKS: dict[str, threading.RLock] = {}
_STORAGE_FORMAT = "indextts_batch_gui"
_SCHEMA_VERSION = 2
_WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class TaskSetFormatError(ValueError):
    """Raised when a directory belongs to the incompatible v2 application."""


def _directory_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.resolve()))
    with _LOCKS_GUARD:
        return _DIRECTORY_LOCKS.setdefault(key, threading.RLock())


def _is_valid_task_id(task_id: str) -> bool:
    return bool(
        _TASK_ID_PATTERN.fullmatch(task_id or "")
        and not task_id.endswith(".")
        and task_id.upper() not in _WINDOWS_RESERVED_NAMES
    )


class TaskSetStorage:
    def __init__(self, task_set_dir: Path):
        self.task_set_dir = task_set_dir.resolve()
        self.tasks_dir = self.task_set_dir / "tasks"
        self.outputs_dir = self.task_set_dir / "outputs"
        self.refs_dir = self.task_set_dir / "refs"
        self.defaults_path = self.task_set_dir / "defaults.json"
        self.meta_path = self.task_set_dir / "set_meta.json"
        self._lock = _directory_lock(self.task_set_dir)

    def bootstrap(self) -> None:
        with self._lock:
            self.task_set_dir.mkdir(parents=True, exist_ok=True)
            self._assert_compatible_format()
            self.tasks_dir.mkdir(exist_ok=True)
            self.outputs_dir.mkdir(exist_ok=True)
            self.refs_dir.mkdir(exist_ok=True)
            self._atomic_write_json(
                self.meta_path,
                {
                    "name": self.task_set_dir.name,
                    "format": _STORAGE_FORMAT,
                    "schema_version": _SCHEMA_VERSION,
                },
            )
            if not self.defaults_path.exists():
                self.save_defaults(TaskSetDefaults())

    def _assert_compatible_format(self) -> None:
        v2_meta = self.task_set_dir / "taskset.json"
        if v2_meta.exists():
            raise TaskSetFormatError(
                f"目录 {self.task_set_dir} 是新版 IndexTTS-GUI2 任务集，"
                "不能由旧版批处理 GUI 打开，以免覆盖不兼容的 tasks/*.json"
            )

        if not self.meta_path.exists():
            # Backward compatibility: historical batch task sets had no format marker.
            return
        try:
            raw = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TaskSetFormatError(f"任务集格式标记损坏: {self.meta_path}") from exc
        if not isinstance(raw, dict):
            raise TaskSetFormatError(f"任务集格式标记无效: {self.meta_path}")
        marker = raw.get("format")
        if marker not in {None, _STORAGE_FORMAT}:
            raise TaskSetFormatError(f"不支持的任务集格式: {marker}")
        schema_version = raw.get("schema_version", 1)
        if marker == _STORAGE_FORMAT and isinstance(schema_version, int) and schema_version > _SCHEMA_VERSION:
            raise TaskSetFormatError(f"任务集版本过新: {schema_version}")

    def load_defaults(self) -> TaskSetDefaults:
        with self._lock:
            if not self.defaults_path.exists():
                return TaskSetDefaults()
            try:
                raw = json.loads(self.defaults_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, TypeError):
                return TaskSetDefaults()
            return TaskSetDefaults.from_dict(raw if isinstance(raw, dict) else {})

    def save_defaults(self, defaults: TaskSetDefaults) -> None:
        with self._lock:
            self._atomic_write_json(self.defaults_path, defaults.to_dict())

    def list_tasks(self) -> list[TaskRecord]:
        with self._lock:
            tasks: list[TaskRecord] = []
            seen_ids: set[str] = set()
            for path in sorted(self.tasks_dir.glob("*.json")):
                if not _is_valid_task_id(path.stem):
                    continue
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(raw, dict):
                        continue
                    task = TaskRecord.from_dict(raw)
                    # The containing filename is authoritative and cannot escape tasks_dir.
                    task.task_id = path.stem
                    if task.task_id in seen_ids:
                        continue
                    seen_ids.add(task.task_id)
                    tasks.append(task)
                except (json.JSONDecodeError, OSError, TypeError, ValueError):
                    continue

            tasks.sort(key=lambda item: (item.order if item.order > 0 else 2**31, item.updated_at, item.task_id))
            normalized = any(task.order != index for index, task in enumerate(tasks, start=1))
            if normalized:
                for index, task in enumerate(tasks, start=1):
                    task.order = index
                    self.save_task(task)
            return tasks

    def save_task(self, task: TaskRecord) -> TaskRecord:
        with self._lock:
            if not task.task_id:
                task.task_id = uuid.uuid4().hex
            path = self._task_path(task.task_id)
            task.ensure_valid()
            task.updated_at = datetime.now(timezone.utc).isoformat()
            self._atomic_write_json(path, task.to_dict())
            return task

    def delete_task(self, task: TaskRecord) -> None:
        with self._lock:
            path = self._task_path(task.task_id)
            if path.exists():
                path.unlink()

    def save_many(self, tasks: Iterable[TaskRecord]) -> None:
        for task in tasks:
            self.save_task(task)

    def derive_audio_path(self, task_text: str, task_id: str = "") -> Path:
        base_path = build_output_audio_path(self.outputs_dir, task_text)
        if not task_id:
            return base_path
        identity = task_id if _is_valid_task_id(task_id) else uuid.uuid4().hex
        id_digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
        return base_path.with_name(f"{base_path.stem}_{id_digest}{base_path.suffix}")

    def remove_audio_if_exists(self, task: TaskRecord) -> None:
        with self._lock:
            audio_path = self.resolve_managed_audio_path(task.audio_file)
            if audio_path is not None and audio_path.exists():
                audio_path.unlink()

    def resolve_managed_audio_path(self, audio_file: str) -> Path | None:
        if not audio_file:
            return None
        path = Path(audio_file)
        if not path.is_absolute():
            path = self.task_set_dir / path
        resolved = path.resolve()
        try:
            resolved.relative_to(self.outputs_dir.resolve())
        except ValueError:
            return None
        return resolved

    def write_audio(self, path: Path, payload: bytes) -> None:
        """Atomically replace a managed output so readers never see partial audio."""
        resolved = path.resolve()
        try:
            resolved.relative_to(self.outputs_dir.resolve())
        except ValueError as exc:
            raise ValueError(f"Audio output is outside the task set: {path}") from exc
        with self._lock:
            self._atomic_write_bytes(resolved, payload)

    def _task_path(self, task_id: str) -> Path:
        if not _is_valid_task_id(task_id):
            raise ValueError(f"Invalid task id: {task_id!r}")
        return self.tasks_dir / f"{task_id}.json"

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        TaskSetStorage._atomic_write_bytes(path, data)

    @staticmethod
    def _atomic_write_bytes(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_name = ""
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as fp:
                temp_name = fp.name
                fp.write(payload)
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(temp_name, path)
        finally:
            if temp_name:
                try:
                    Path(temp_name).unlink(missing_ok=True)
                except OSError:
                    pass
