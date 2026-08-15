from pathlib import Path
import threading

from indextts_batch_gui.models import AppConfig, SynthesisResult, TaskRecord
from indextts_batch_gui.scheduler import BatchRunner, mark_regen_if_changed, task_signature
from indextts_batch_gui.storage import TaskSetStorage


class FakeClient:
    def __init__(self, config: AppConfig):
        self.config = config

    def synthesize(self, task: TaskRecord) -> SynthesisResult:
        return SynthesisResult(audio_bytes=b"RIFF....WAVE")


def test_batch_runner_state_transition(tmp_path: Path) -> None:
    storage = TaskSetStorage(tmp_path / "set_a")
    storage.bootstrap()

    task = TaskRecord(task_id="t1", text="hello", reference_audio="ref.wav")
    storage.save_task(task)

    updates = []

    def on_progress(updated: TaskRecord) -> None:
        updates.append((updated.status, updated.progress))

    runner = BatchRunner(storage=storage, client=FakeClient(AppConfig()), max_workers=1)
    result = runner.run([task], on_progress)

    assert result[0].status == "done"
    assert Path(result[0].audio_file).exists()
    assert any(status == "queued" for status, _ in updates)
    assert any(status == "generating" for status, _ in updates)
    assert any(status == "done" for status, _ in updates)


def test_mark_regen_if_changed_sets_pending() -> None:
    task = TaskRecord(task_id="t1", text="hello", reference_audio="ref.wav", status="done")
    task.last_generated_signature = task_signature(task)
    task.config["speed"] = 0.9

    mark_regen_if_changed(task)

    assert task.needs_regen is True
    assert task.status == "pending"


def test_same_text_tasks_get_distinct_output_files(tmp_path: Path) -> None:
    storage = TaskSetStorage(tmp_path / "set_a")
    storage.bootstrap()
    tasks = [
        TaskRecord(task_id="t1", text="same", reference_audio="ref.wav"),
        TaskRecord(task_id="t2", text="same", reference_audio="ref.wav"),
    ]
    runner = BatchRunner(storage=storage, client=FakeClient(AppConfig()), max_workers=2)
    results = runner.run(tasks, lambda _task: None)
    assert results[0].audio_file != results[1].audio_file
    assert all(Path(task.audio_file).exists() for task in results)


def test_cancel_request_prevents_queued_task_generation(tmp_path: Path) -> None:
    storage = TaskSetStorage(tmp_path / "set_a")
    storage.bootstrap()
    task = TaskRecord(task_id="t1", text="hello", reference_audio="ref.wav")
    runner = BatchRunner(storage=storage, client=FakeClient(AppConfig()), max_workers=1)
    runner.request_cancel(task.task_id)
    result = runner.run([task], lambda _task: None)
    assert result[0].status == "cancelled"
    assert not result[0].audio_file


def test_pause_keeps_not_yet_started_task_pending(tmp_path: Path) -> None:
    storage = TaskSetStorage(tmp_path / "set_a")
    storage.bootstrap()
    entered = threading.Event()
    release = threading.Event()

    class BlockingClient(FakeClient):
        def synthesize(self, task: TaskRecord) -> SynthesisResult:
            entered.set()
            assert release.wait(2)
            return super().synthesize(task)

    first = TaskRecord(task_id="t1", text="first", reference_audio="ref.wav")
    second = TaskRecord(task_id="t2", text="second", reference_audio="ref.wav")
    runner = BatchRunner(storage=storage, client=BlockingClient(AppConfig()), max_workers=1)
    result_holder: list[TaskRecord] = []

    thread = threading.Thread(target=lambda: result_holder.extend(runner.run([first, second], lambda _task: None)))
    thread.start()
    assert entered.wait(2)
    assert runner.request_pause(second) is True
    release.set()
    thread.join(2)

    assert not thread.is_alive()
    assert [task.status for task in result_holder] == ["done", "pending"]
