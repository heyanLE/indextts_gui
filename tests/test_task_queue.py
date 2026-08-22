"""TaskQueue lifecycle and snapshot consistency tests."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from src.core.config_manager import GlobalSettings
from src.core._persistence import atomic_write_bytes as real_atomic_write_bytes
from src.core.task import Task, TaskStatus
from src.core.task_queue import TaskQueue
from src.core.taskset import TaskSet


class _Config:
    settings = GlobalSettings(queue_interval=0)

    @staticmethod
    def get_engine_url(engine_id: str) -> str:
        return "http://engine.test" if engine_id == "fake" else ""


class _MutatingEngine:
    def __init__(self, task: Task) -> None:
        self.task = task
        self.params = None

    @staticmethod
    def validate_params(params: dict) -> list[str]:
        return []

    async def generate(self, url: str, params: dict, *, timeout: float | None = None) -> bytes:
        self.params = params
        assert timeout == 120
        # Simulate an external, incorrectly timed edit while the request is in flight.
        self.task.text = "changed later"
        self.task.engine_params["speed"] = 9
        return b"RIFF-test"


class _BlockingEngine:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    @staticmethod
    def validate_params(params: dict) -> list[str]:
        return []

    async def generate(
        self, url: str, params: dict, *, timeout: float | None = None
    ) -> bytes:
        self.started.set()
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        return b"RIFF-blocked"


def test_queue_naturally_finishes_and_uses_one_request_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    taskset = TaskSet.create("queue", tmp_path / "queue")
    task = Task(
        id="task1",
        text="original text",
        engine="fake",
        engine_params={"speed": 1},
    )
    taskset.add_task(task)
    taskset.save()
    engine = _MutatingEngine(task)
    monkeypatch.setattr("src.core.task_queue.engine_registry.get", lambda engine_id: engine)

    queue = TaskQueue(taskset, _Config())
    done = []
    queue.all_done.connect(lambda: done.append(True))
    queue.add_tasks([task])
    queue.run()

    assert done == [True]
    assert task.status == TaskStatus.COMPLETED
    assert engine.params == {"speed": 1, "text": "original text"}
    assert task.generation_config == {
        "text": "original text",
        "engine": "fake",
        "engine_params": {"speed": 1},
    }
    assert Path(task.output_audio_path).name == "task1_original_text.wav"
    assert Path(task.output_audio_path).read_bytes() == b"RIFF-test"


def test_drain_queued_retry_restores_completed_and_counts_once(tmp_path: Path) -> None:
    taskset = TaskSet.create("drain", tmp_path / "drain")
    task = Task(
        id="task1",
        text="retry",
        engine="fake",
        status=TaskStatus.COMPLETED,
        output_audio_path=str(taskset.outputs_dir / "old.wav"),
    )
    Path(task.output_audio_path).write_bytes(b"old")
    taskset.add_task(task)
    queue = TaskQueue(taskset, _Config())
    queue.add_tasks([task])

    assert queue.drain_queue(taskset) == 1
    assert task.status == TaskStatus.COMPLETED
    assert queue.queue_size() == 0


def test_drain_missing_previous_output_returns_pending(tmp_path: Path) -> None:
    taskset = TaskSet.create("missing", tmp_path / "missing")
    task = Task(
        id="task1",
        text="retry",
        engine="fake",
        status=TaskStatus.COMPLETED,
        output_audio_path=str(taskset.outputs_dir / "missing.wav"),
    )
    taskset.add_task(task)
    queue = TaskQueue(taskset, _Config())
    queue.add_tasks([task])
    queue.drain_queue(taskset)

    assert task.status == TaskStatus.PENDING
    assert task.output_audio_path is None


def test_stop_before_run_leaves_no_queued_state(tmp_path: Path) -> None:
    taskset = TaskSet.create("stop", tmp_path / "stop")
    task = Task(id="task1", text="stop", engine="fake")
    taskset.add_task(task)
    queue = TaskQueue(taskset, _Config())
    queue.add_tasks([task])
    queue.stop()
    queue.run()
    queue.drain_queue(taskset)

    assert task.status == TaskStatus.PENDING
    assert queue.queue_size() == 0


def test_successful_regeneration_removes_superseded_managed_audio(
    tmp_path: Path, monkeypatch
) -> None:
    taskset = TaskSet.create("regen", tmp_path / "regen")
    old_path = taskset.outputs_dir / "old.wav"
    old_path.write_bytes(b"old")
    task = Task(
        id="task1",
        text="new name",
        engine="fake",
        status=TaskStatus.COMPLETED,
        output_audio_path=str(old_path),
    )
    taskset.add_task(task)
    engine = _MutatingEngine(task)
    monkeypatch.setattr("src.core.task_queue.engine_registry.get", lambda engine_id: engine)
    queue = TaskQueue(taskset, _Config())
    queue.add_tasks([task])
    queue.run()

    assert task.status == TaskStatus.COMPLETED
    assert not old_path.exists()
    assert Path(task.output_audio_path).exists()


def test_regeneration_uses_new_file_when_current_output_is_locked(
    tmp_path: Path, monkeypatch
) -> None:
    taskset = TaskSet.create("locked-output", tmp_path / "locked-output")
    output_path = taskset.outputs_dir / "task1_same_text.wav"
    output_path.write_bytes(b"old")
    task = Task(
        id="task1",
        text="same text",
        engine="fake",
        status=TaskStatus.COMPLETED,
        output_audio_path=str(output_path),
    )
    taskset.add_task(task)
    engine = _MutatingEngine(task)
    monkeypatch.setattr("src.core.task_queue.engine_registry.get", lambda engine_id: engine)

    attempted_paths: list[Path] = []

    def locked_first_write(path: Path, content: bytes) -> None:
        attempted_paths.append(path)
        if len(attempted_paths) == 1:
            raise PermissionError(5, "Access is denied", str(path))
        real_atomic_write_bytes(path, content)

    monkeypatch.setattr("src.core.task_queue.atomic_write_bytes", locked_first_write)
    queue = TaskQueue(taskset, _Config())
    queue.add_tasks([task])
    queue.run()

    assert task.status == TaskStatus.COMPLETED
    assert Path(task.output_audio_path).read_bytes() == b"RIFF-test"
    assert Path(task.output_audio_path) != output_path


def test_metadata_commit_failure_does_not_leave_completed_in_memory(
    tmp_path: Path, monkeypatch
) -> None:
    taskset = TaskSet.create("commit-failure", tmp_path / "commit-failure")
    task = Task(id="task1", text="text", engine="fake")
    taskset.add_task(task)
    real_save = taskset.save
    save_calls = 0

    def flaky_save() -> None:
        nonlocal save_calls
        save_calls += 1
        if save_calls == 2:  # publishing COMPLETED fails once
            raise OSError("disk temporarily unavailable")
        real_save()

    taskset.save = flaky_save  # type: ignore[method-assign]
    engine = _MutatingEngine(task)
    monkeypatch.setattr("src.core.task_queue.engine_registry.get", lambda engine_id: engine)
    queue = TaskQueue(taskset, _Config())
    queue.add_tasks([task])
    queue.run()

    assert task.status == TaskStatus.FAILED
    assert "disk temporarily unavailable" in (task.error_message or "")
    assert TaskSet.load(taskset.directory).get_task(task.id).status == TaskStatus.FAILED


def test_stop_finishes_current_and_drain_reverts_remaining(
    tmp_path: Path, monkeypatch
) -> None:
    taskset = TaskSet.create("threaded", tmp_path / "threaded")
    first = Task(id="first", text="first", engine="fake")
    second = Task(id="second", text="second", engine="fake")
    taskset.add_task(first)
    taskset.add_task(second)
    engine = _BlockingEngine()
    monkeypatch.setattr("src.core.task_queue.engine_registry.get", lambda engine_id: engine)
    queue = TaskQueue(taskset, _Config())
    queue.add_tasks([first, second])
    queue.start()
    assert engine.started.wait(timeout=2)
    queue.stop()
    engine.release.set()
    assert queue.wait(3000)

    assert first.status == TaskStatus.COMPLETED
    assert second.status == TaskStatus.QUEUED
    assert queue.drain_queue(taskset) == 1
    assert second.status == TaskStatus.PENDING
