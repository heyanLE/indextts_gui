from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .api_client import IndexTTSClient, IndexTTSClientError
from .models import TaskRecord
from .storage import TaskSetStorage


logger = logging.getLogger(__name__)

ProgressCallback = Callable[[TaskRecord], None]
ReleaseFileCallback = Callable[[Path], None]


@dataclass
class BatchRunner:
    storage: TaskSetStorage
    client: IndexTTSClient
    max_workers: int = 1
    release_file_callback: ReleaseFileCallback | None = None

    def __post_init__(self) -> None:
        self._cancel_requested_ids: set[str] = set()
        self._cancel_lock = threading.Lock()

    def request_cancel(self, task_id: str) -> None:
        target = (task_id or "").strip()
        if not target:
            return
        with self._cancel_lock:
            self._cancel_requested_ids.add(target)

    def _consume_cancel_request(self, task_id: str) -> bool:
        target = (task_id or "").strip()
        if not target:
            return False
        with self._cancel_lock:
            if target in self._cancel_requested_ids:
                self._cancel_requested_ids.remove(target)
                return True
        return False

    def run(self, tasks: list[TaskRecord], on_progress: ProgressCallback) -> list[TaskRecord]:
        logger.info("Batch run start task_count=%d max_workers=%d mode=sequential", len(tasks), max(1, self.max_workers))
        results: list[TaskRecord] = []
        queued_tasks: list[TaskRecord] = []
        for task in tasks:
            if task.status == "done" and not task.needs_regen:
                continue
            task.status = "queued"
            task.progress = 5
            task.error = ""
            self.storage.save_task(task)
            on_progress(task)
            queued_tasks.append(task)

        for task in queued_tasks:
            if task.status != "queued":
                continue
            if self._consume_cancel_request(task.task_id):
                task.status = "cancelled"
                task.progress = 0
                task.error = "已取消排队"
                self.storage.save_task(task)
                on_progress(task)
                results.append(task)
                continue
            results.append(self._run_one(task, on_progress))
        return results

    def _run_one(self, task: TaskRecord, on_progress: ProgressCallback) -> TaskRecord:
        task.status = "generating"
        task.progress = 30
        self.storage.save_task(task)
        on_progress(task)

        old_audio_file = task.audio_file
        try:
            result = self.client.synthesize(task)
            task.progress = 80
            self.storage.save_task(task)
            on_progress(task)

            output_path = self.storage.derive_audio_path(task.text)
            self._write_audio_with_retry(output_path, result.audio_bytes)
            task.audio_file = str(output_path)

            # Persist the exact set that produced current audio as a second config set.
            task.generated_text = task.text
            task.generated_reference_audio = task.reference_audio
            task.generated_config = dict(task.config or {})

            # Remove old audio only after new audio is written successfully.
            if task.needs_regen and old_audio_file:
                old_audio_path = Path(old_audio_file)
                if not old_audio_path.is_absolute():
                    old_audio_path = self.storage.task_set_dir / old_audio_path
                if old_audio_path.resolve() != output_path.resolve() and old_audio_path.exists():
                    self._remove_audio_with_retry(old_audio_path)

            # Keep task.config as the source-of-truth edited by user and stored in task JSON.
            task.status = "done"
            task.progress = 100
            task.needs_regen = False
            task.last_generated_signature = task_signature(task)
            task.error = ""
        except (IndexTTSClientError, OSError) as exc:
            logger.exception("Task generation failed task_id=%s", task.task_id)
            task.status = "failed"
            task.progress = 100
            task.error = str(exc)

        self.storage.save_task(task)
        on_progress(task)
        return task

    def _write_audio_with_retry(self, output_path: Path, payload: bytes) -> None:
        last_error: OSError | None = None
        for _ in range(6):
            try:
                if self.release_file_callback is not None:
                    self.release_file_callback(output_path)
                output_path.write_bytes(payload)
                return
            except OSError as exc:
                last_error = exc
                time.sleep(0.2)
        if last_error is not None:
            raise last_error

    def _remove_audio_with_retry(self, audio_path: Path) -> None:
        last_error: OSError | None = None
        for _ in range(6):
            try:
                if self.release_file_callback is not None:
                    self.release_file_callback(audio_path)
                if audio_path.exists():
                    audio_path.unlink()
                return
            except OSError as exc:
                last_error = exc
                time.sleep(0.2)
        if last_error is not None:
            raise last_error


def task_signature(task: TaskRecord) -> str:
    snapshot = {
        "text": task.text,
        "reference_audio": task.reference_audio,
        "config": task.config,
    }
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(encoded.encode("utf-8")).hexdigest()


def mark_regen_if_changed(task: TaskRecord) -> TaskRecord:
    current = task_signature(task)
    task.needs_regen = bool(task.last_generated_signature and current != task.last_generated_signature)
    if task.needs_regen and task.status == "done":
        task.status = "pending"
        task.progress = 0
    return task
