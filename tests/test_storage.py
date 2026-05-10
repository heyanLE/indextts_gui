from pathlib import Path

from indextts_batch_gui.models import TaskRecord, TaskSetDefaults
from indextts_batch_gui.storage import TaskSetStorage


def test_bootstrap_and_defaults_roundtrip(tmp_path: Path) -> None:
    storage = TaskSetStorage(tmp_path / "set_a")
    storage.bootstrap()

    defaults = TaskSetDefaults(reference_audio="refs/a.wav", config={"temperature": 0.7})
    storage.save_defaults(defaults)
    loaded = storage.load_defaults()

    assert loaded.reference_audio == "refs/a.wav"
    assert loaded.config["temperature"] == 0.7


def test_task_json_persistence_and_atomic_write(tmp_path: Path) -> None:
    storage = TaskSetStorage(tmp_path / "set_a")
    storage.bootstrap()

    task = TaskRecord(task_id="t1", text="abc", reference_audio="ref.wav", config={"speed": 1.0})
    storage.save_task(task)

    loaded = storage.list_tasks()
    assert len(loaded) == 1
    assert loaded[0].task_id == "t1"
    assert loaded[0].config["speed"] == 1.0


def test_regeneration_replaces_old_audio_file(tmp_path: Path) -> None:
    storage = TaskSetStorage(tmp_path / "set_a")
    storage.bootstrap()

    old_file = storage.outputs_dir / "old.wav"
    old_file.write_bytes(b"old")

    task = TaskRecord(task_id="t1", text="abc", reference_audio="ref.wav", audio_file=str(old_file))
    storage.remove_audio_if_exists(task)

    assert not old_file.exists()


def test_task_order_persistence_and_sorting(tmp_path: Path) -> None:
    storage = TaskSetStorage(tmp_path / "set_a")
    storage.bootstrap()

    first = TaskRecord(task_id="t1", text="first", reference_audio="ref.wav", order=2)
    second = TaskRecord(task_id="t2", text="second", reference_audio="ref.wav", order=1)
    storage.save_task(first)
    storage.save_task(second)

    loaded = storage.list_tasks()
    assert [task.task_id for task in loaded] == ["t2", "t1"]
    assert [task.order for task in loaded] == [1, 2]


def test_task_final_note_field_roundtrip(tmp_path: Path) -> None:
    storage = TaskSetStorage(tmp_path / "set_a")
    storage.bootstrap()

    task = TaskRecord(task_id="t1", text="abc", reference_audio="ref.wav", is_final=True)
    storage.save_task(task)

    loaded = storage.list_tasks()
    assert len(loaded) == 1
    assert loaded[0].is_final is True


def test_task_final_note_false_roundtrip(tmp_path: Path) -> None:
    storage = TaskSetStorage(tmp_path / "set_a")
    storage.bootstrap()

    task = TaskRecord(task_id="t1", text="abc", reference_audio="ref.wav", is_final=False)
    storage.save_task(task)

    loaded = storage.list_tasks()
    assert len(loaded) == 1
    assert loaded[0].is_final is False
