from pathlib import Path

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
