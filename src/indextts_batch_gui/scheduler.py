from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
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

    def request_cancel(self, task_id: str, task: TaskRecord | None = None) -> bool:
        target = (task_id or "").strip()
        if not target:
            return False
        with self._cancel_lock:
            if task is not None and task.status not in {"queued", "generating"}:
                return False
            self._cancel_requested_ids.add(target)
            return True

    def request_pause(self, task: TaskRecord) -> bool:
        """Atomically return a queued task to pending before a worker claims it."""
        with self._cancel_lock:
            if task.status != "queued":
                return False
            self._cancel_requested_ids.discard(task.task_id)
            task.transition_to("pending", error="")
            return True

    def _consume_cancel_request(self, task_id: str) -> bool:
        target = (task_id or "").strip()
        if not target:
            return False
        with self._cancel_lock:
            if target in self._cancel_requested_ids:
                self._cancel_requested_ids.remove(target)
                return True
        return False

    def _notify(self, callback: ProgressCallback, task: TaskRecord) -> None:
        try:
            callback(TaskRecord.from_dict(task.to_dict()))
        except Exception:
            # UI/reporting failures must not corrupt the persisted task lifecycle.
            logger.exception("Task progress callback failed task_id=%s", task.task_id)

    def run(self, tasks: list[TaskRecord], on_progress: ProgressCallback) -> list[TaskRecord]:
        worker_count = max(1, int(self.max_workers))
        logger.info("Batch run start task_count=%d max_workers=%d", len(tasks), worker_count)
        queued_tasks: list[TaskRecord] = []
        seen_ids: set[str] = set()
        for task in tasks:
            if not task.task_id or task.task_id in seen_ids:
                logger.warning("Skip task with empty/duplicate id task_id=%r", task.task_id)
                continue
            seen_ids.add(task.task_id)
            if task.status == "done" and not task.needs_regen:
                continue
            if task.status == "generating":
                task.transition_to("pending", error="上次生成未正常结束，已重新排队")
            if task.status == "queued":
                task.transition_to("pending", error="")
            if task.status != "pending":
                task.transition_to("pending", error="")
            task.transition_to("queued", error="")
            self.storage.save_task(task)
            self._notify(on_progress, task)
            queued_tasks.append(task)

        if not queued_tasks:
            return []

        completed_by_id: dict[str, TaskRecord] = {}
        with ThreadPoolExecutor(max_workers=min(worker_count, len(queued_tasks)), thread_name_prefix="tts-batch") as pool:
            futures: dict[Future[TaskRecord], str] = {
                pool.submit(self._run_one, task, on_progress): task.task_id for task in queued_tasks
            }
            for future in as_completed(futures):
                task_id = futures[future]
                try:
                    completed_by_id[task_id] = future.result()
                except Exception as exc:  # Defensive boundary around one task.
                    task = next(item for item in queued_tasks if item.task_id == task_id)
                    logger.exception("Unhandled task failure task_id=%s", task_id)
                    if task.status == "queued":
                        task.transition_to("generating")
                    task.transition_to("failed", error=str(exc))
                    self.storage.save_task(task)
                    self._notify(on_progress, task)
                    completed_by_id[task_id] = task

        return [completed_by_id[task.task_id] for task in queued_tasks]

    def _run_one(self, task: TaskRecord, on_progress: ProgressCallback) -> TaskRecord:
        with self._cancel_lock:
            cancel_requested = task.task_id in self._cancel_requested_ids
            self._cancel_requested_ids.discard(task.task_id)
            if task.status == "pending":
                # The UI uses pending to represent a queued task that was paused.
                terminal_before_start = True
            elif task.status == "cancelled" or cancel_requested:
                if task.status != "cancelled":
                    task.transition_to("cancelled", error="已取消排队")
                terminal_before_start = True
            else:
                # Serialize this transition with request_cancel(), preventing a
                # queued -> cancelled race from becoming an invalid -> generating move.
                task.transition_to("generating", error="")
                terminal_before_start = False

        if terminal_before_start:
            self.storage.save_task(task)
            self._notify(on_progress, task)
            return task

        self.storage.save_task(task)
        self._notify(on_progress, task)

        old_audio_file = task.audio_file
        try:
            result = self.client.synthesize(task)
            if self._consume_cancel_request(task.task_id):
                task.transition_to("cancelled", error="生成请求完成后已取消，未保存输出")
                self.storage.save_task(task)
                self._notify(on_progress, task)
                return task
            if not isinstance(result.audio_bytes, bytes) or not result.audio_bytes:
                raise IndexTTSClientError("后端返回了空音频")
            task.progress = 80
            self.storage.save_task(task)
            self._notify(on_progress, task)

            output_path = self.storage.derive_audio_path(task.text, task.task_id)
            self._write_audio_with_retry(output_path, result.audio_bytes)
            task.audio_file = str(output_path)

            # Persist the exact set that produced current audio as a second config set.
            task.generated_text = task.text
            task.generated_reference_audio = task.reference_audio
            task.generated_config = dict(task.config or {})

            # Remove old audio only after new audio is written successfully.
            if task.needs_regen and old_audio_file:
                old_audio_path = self.storage.resolve_managed_audio_path(old_audio_file)
                if old_audio_path is not None and old_audio_path != output_path.resolve() and old_audio_path.exists():
                    try:
                        self._remove_audio_with_retry(old_audio_path)
                    except OSError:
                        # The new output is already durable. Stale-file cleanup must
                        # not turn a successful generation into a contradictory failure.
                        logger.warning("Unable to remove stale audio path=%s", old_audio_path, exc_info=True)

            # Keep task.config as the source-of-truth edited by user and stored in task JSON.
            # Commit under the cancellation lock so a last-moment UI request cannot
            # leak into a later run. Once the atomic output exists, completion wins.
            with self._cancel_lock:
                late_cancel = task.task_id in self._cancel_requested_ids
                self._cancel_requested_ids.discard(task.task_id)
                task.transition_to("done", error="")
                task.needs_regen = False
                task.last_generated_signature = task_signature(task)
            if late_cancel:
                logger.info("Cancellation arrived after output commit task_id=%s", task.task_id)
        except Exception as exc:
            logger.exception("Task generation failed task_id=%s", task.task_id)
            with self._cancel_lock:
                self._cancel_requested_ids.discard(task.task_id)
                if task.status == "generating":
                    task.transition_to("failed", error=str(exc))

        self.storage.save_task(task)
        self._notify(on_progress, task)
        return task

    def _write_audio_with_retry(self, output_path: Path, payload: bytes) -> None:
        last_error: OSError | None = None
        for _ in range(6):
            try:
                if self.release_file_callback is not None:
                    self.release_file_callback(output_path)
                self.storage.write_audio(output_path, payload)
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
        task.transition_to("pending")
    return task
