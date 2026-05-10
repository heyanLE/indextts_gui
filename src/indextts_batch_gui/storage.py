from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Iterable

from .filenames import build_output_audio_path
from .models import TaskRecord, TaskSetDefaults


class TaskSetStorage:
    def __init__(self, task_set_dir: Path):
        self.task_set_dir = task_set_dir
        self.tasks_dir = task_set_dir / "tasks"
        self.outputs_dir = task_set_dir / "outputs"
        self.refs_dir = task_set_dir / "refs"
        self.defaults_path = task_set_dir / "defaults.json"
        self.meta_path = task_set_dir / "set_meta.json"

    def bootstrap(self) -> None:
        self.task_set_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir.mkdir(exist_ok=True)
        self.outputs_dir.mkdir(exist_ok=True)
        self.refs_dir.mkdir(exist_ok=True)
        if not self.meta_path.exists():
            self._atomic_write_json(self.meta_path, {"name": self.task_set_dir.name, "version": 1})
        if not self.defaults_path.exists():
            self.save_defaults(TaskSetDefaults())

    def load_defaults(self) -> TaskSetDefaults:
        if not self.defaults_path.exists():
            return TaskSetDefaults()
        try:
            raw = json.loads(self.defaults_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return TaskSetDefaults()
        return TaskSetDefaults.from_dict(raw)

    def save_defaults(self, defaults: TaskSetDefaults) -> None:
        self._atomic_write_json(self.defaults_path, defaults.to_dict())

    def list_tasks(self) -> list[TaskRecord]:
        tasks: list[TaskRecord] = []
        normalized = False
        for path in sorted(self.tasks_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                task = TaskRecord.from_dict(raw)
                if not task.task_id:
                    task.task_id = path.stem
                if task.order <= 0:
                    task.order = len(tasks) + 1
                    normalized = True
                tasks.append(task)
            except (json.JSONDecodeError, OSError):
                continue
        tasks.sort(key=lambda item: (item.order, item.updated_at, item.task_id))
        if normalized:
            for task in tasks:
                self.save_task(task)
        return tasks

    def save_task(self, task: TaskRecord) -> TaskRecord:
        if not task.task_id:
            task.task_id = uuid.uuid4().hex
        task.ensure_valid()
        path = self.tasks_dir / task.json_filename()
        self._atomic_write_json(path, task.to_dict())
        return task

    def delete_task(self, task: TaskRecord) -> None:
        path = self.tasks_dir / task.json_filename()
        if path.exists():
            path.unlink()

    def save_many(self, tasks: Iterable[TaskRecord]) -> None:
        for task in tasks:
            self.save_task(task)

    def derive_audio_path(self, task_text: str) -> Path:
        return build_output_audio_path(self.outputs_dir, task_text)

    def remove_audio_if_exists(self, task: TaskRecord) -> None:
        if not task.audio_file:
            return
        audio_path = Path(task.audio_file)
        if not audio_path.is_absolute():
            audio_path = self.task_set_dir / audio_path
        if audio_path.exists():
            audio_path.unlink()

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)
