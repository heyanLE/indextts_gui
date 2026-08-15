"""Regression tests for core state/persistence invariants."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.config_manager import ConfigManager, GlobalSettings
from src.core.recipe import Recipe, RecipeManager
from src.core.task import Task, TaskStatus
from src.core.taskset import TaskSet


def test_cancel_queued_regeneration_restores_completed() -> None:
    task = Task(
        id="done1",
        text="old output",
        engine="indextts",
        status=TaskStatus.COMPLETED,
        output_audio_path="/tmp/old.wav",
    )
    task.transition_to(TaskStatus.QUEUED)
    task.transition_to(TaskStatus.COMPLETED)
    assert task.status == TaskStatus.COMPLETED


def test_runtime_state_recovery_preserves_existing_output() -> None:
    for status in (TaskStatus.QUEUED, TaskStatus.GENERATING):
        task = Task.from_dict(
            {
                "id": "recover1",
                "text": "text",
                "engine": "indextts",
                "status": status.value,
                "output_audio_path": "/tmp/previous.wav",
            }
        )
        assert task.status == TaskStatus.COMPLETED


def test_taskset_order_is_persisted_and_stale_files_are_pruned(tmp_path: Path) -> None:
    taskset = TaskSet.create("ordered", tmp_path / "ordered")
    taskset.add_task(Task(id="z_task", text="z", engine="indextts"))
    taskset.add_task(Task(id="a_task", text="a", engine="indextts"))
    taskset.save()
    taskset.reorder_tasks(["z_task", "a_task"])
    stale = taskset.tasks_dir / "stale.json"
    stale.write_text("{}", encoding="utf-8")
    taskset.save()

    loaded = TaskSet.load(taskset.directory)
    assert [task.id for task in loaded.tasks] == ["z_task", "a_task"]
    assert not stale.exists()
    meta = json.loads((taskset.directory / "taskset.json").read_text(encoding="utf-8"))
    assert meta["format"] == TaskSet.FORMAT
    assert meta["schema_version"] == TaskSet.SCHEMA_VERSION


def test_taskset_rejects_legacy_directory(tmp_path: Path) -> None:
    directory = tmp_path / "legacy"
    directory.mkdir()
    (directory / "set_meta.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="旧版"):
        TaskSet.load(directory)
    with pytest.raises(ValueError, match="旧版"):
        TaskSet.create("legacy", directory)


def test_taskset_save_preserves_committed_unreadable_task(tmp_path: Path) -> None:
    taskset = TaskSet.create("corrupt", tmp_path / "corrupt")
    bad_path = taskset.tasks_dir / "bad.json"
    bad_path.write_text("{broken", encoding="utf-8")
    meta_path = taskset.directory / "taskset.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["task_order"] = ["bad"]
    meta["task_count"] = 1
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    loaded = TaskSet.load(taskset.directory)
    assert loaded.tasks == []
    assert loaded.load_warnings
    loaded.save()
    assert bad_path.read_text(encoding="utf-8") == "{broken"
    saved_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert saved_meta["task_order"] == ["bad"]


def test_task_output_path_is_portable_when_taskset_moves(tmp_path: Path) -> None:
    original = TaskSet.create("portable", tmp_path / "original")
    output = original.outputs_dir / "audio.wav"
    output.write_bytes(b"audio")
    original.add_task(
        Task(
            id="portable1",
            text="portable",
            engine="indextts",
            status=TaskStatus.COMPLETED,
            output_audio_path=str(output),
        )
    )
    original.save()
    raw = json.loads((original.tasks_dir / "portable1.json").read_text(encoding="utf-8"))
    assert raw["output_audio_path"] == "outputs/audio.wav"

    moved = tmp_path / "moved"
    original.directory.rename(moved)
    loaded = TaskSet.load(moved)
    assert loaded.tasks[0].status == TaskStatus.COMPLETED
    assert loaded.tasks[0].output_audio_path == str((moved / "outputs/audio.wav").resolve())


def test_recipe_manager_does_not_leak_mutable_objects(tmp_path: Path) -> None:
    manager = RecipeManager(tmp_path)
    source = Recipe(id="r1", name="one", engine="indextts", engine_params={"nested": [1]})
    manager.add(source)
    source.engine_params["nested"].append(2)
    fetched = manager.get("r1")
    assert fetched is not None
    assert fetched.engine_params == {"nested": [1]}

    fetched.name = "mutated without update"
    assert manager.get("r1").name == "one"


def test_recipe_upsert_keeps_key_and_object_id_aligned(tmp_path: Path) -> None:
    manager = RecipeManager(tmp_path)
    manager.add(Recipe(id="old", name="same", engine="indextts"))
    manager.upsert(Recipe(id="new", name="same", engine="indextts", engine_params={"x": 1}))

    assert manager.get("old").id == "old"
    assert manager.get("old").engine_params == {"x": 1}
    assert manager.get("new") is None
    reloaded = RecipeManager(tmp_path)
    assert reloaded.get("old").id == "old"


def test_recipe_write_archives_unreadable_source(tmp_path: Path) -> None:
    filepath = tmp_path / "recipes.json"
    filepath.write_text("{broken", encoding="utf-8")
    manager = RecipeManager(tmp_path)
    assert manager.load_error is not None
    manager.add(Recipe(id="new", name="recovered", engine="indextts"))

    backups = list(tmp_path.glob("recipes.corrupt-*.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{broken"
    assert RecipeManager(tmp_path).get("new") is not None


def test_config_load_is_transactional_and_validates_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ConfigManager, "_resolve_config_dir", staticmethod(lambda: tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "engines": {"indextts": {"engine_id": "wrong", "url": "http://ok"}},
                "settings": {"queue_interval": "broken"},
            }
        ),
        encoding="utf-8",
    )
    config = ConfigManager()
    assert config.engines == {}
    assert config.settings == GlobalSettings()

    with pytest.raises(ValueError, match="queue_interval"):
        config.update_settings(queue_interval=-1)
    with pytest.raises(ValueError, match="未知"):
        config.update_settings(does_not_exist=True)


def test_config_activate_taskset_rolls_back_on_save_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ConfigManager, "_resolve_config_dir", staticmethod(lambda: tmp_path))
    config = ConfigManager()
    config.recent_task_sets = ["/previous"]
    config.current_task_set_path = "/previous"

    def fail_save() -> None:
        raise OSError("full")

    monkeypatch.setattr(config, "save", fail_save)

    with pytest.raises(OSError, match="full"):
        config.activate_task_set(str(tmp_path / "next"))

    assert config.recent_task_sets == ["/previous"]
    assert config.current_task_set_path == "/previous"


def test_config_mutation_rolls_back_when_save_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ConfigManager, "_resolve_config_dir", staticmethod(lambda: tmp_path))
    config = ConfigManager()
    previous = config.settings

    def fail_save() -> None:
        raise OSError("disk full")

    monkeypatch.setattr(config, "save", fail_save)
    with pytest.raises(OSError, match="disk full"):
        config.update_settings(queue_interval=9)
    assert config.settings is previous
    assert config.settings.queue_interval == 2
