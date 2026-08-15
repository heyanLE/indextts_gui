"""Regression tests for legacy PySide UI state synchronization."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from src.core.config_manager import ConfigManager
from src.core.task import Task, TaskStatus
from src.core.taskset import TaskSet
from src.ui.config_tab import ConfigTab
from src.ui.engine_config_widget import EngineConfigWidget
from src.ui.queue_visualizer import QueueVisualizer
from src.ui.task_list_tab import TaskListTab
from src.engines.base_engine import ParamField


def test_taskset_request_does_not_commit_before_mainwindow_accepts(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(ConfigManager, "_resolve_config_dir", lambda self: tmp_path / "config")
    config = ConfigManager()
    tab = ConfigTab(config)
    requested: list[str] = []
    tab.task_set_changed.connect(requested.append)

    candidate = str(tmp_path / "candidate")
    tab._open_taskset_by_path(candidate)

    assert requested == [candidate]
    assert config.current_task_set_path is None
    assert tab.get_current_taskset_path() == ""

    tab.commit_task_set(candidate)
    assert config.current_task_set_path == candidate
    assert tab.get_current_taskset_path() == candidate


def test_single_generate_flushes_debounced_detail_edits(qapp, tmp_path):
    taskset = TaskSet.create("project", tmp_path / "project")
    task = Task(id="task1", text="old", engine="indextts")
    taskset.add_task(task)
    taskset.save()

    tab = TaskListTab()
    tab.set_task_set(taskset)
    tab._detail_panel.load_task(task)
    tab._detail_panel._text_edit.setPlainText("new text")

    emitted: list[list[Task]] = []
    tab.batch_generate.connect(emitted.append)
    tab._on_single_generate(task.id)

    assert emitted == [[task]]
    assert task.text == "new text"
    assert TaskSet.load(taskset.directory).get_task(task.id).text == "new text"


def test_lifecycle_flush_propagates_persistence_failure(
    qapp, tmp_path, monkeypatch
):
    taskset = TaskSet.create("project", tmp_path / "project")
    tab = TaskListTab()
    tab.set_task_set(taskset)

    def fail_save() -> None:
        raise OSError("disk full")

    monkeypatch.setattr(taskset, "save", fail_save)
    with pytest.raises(OSError, match="disk full"):
        tab.flush_pending_save()


def test_completed_only_lock_control(qapp, tmp_path):
    taskset = TaskSet.create("project", tmp_path / "project")
    pending = Task(id="pending", text="p", engine="indextts")
    completed = Task(
        id="completed",
        text="c",
        engine="indextts",
        status=TaskStatus.COMPLETED,
    )
    taskset.add_task(pending)
    taskset.add_task(completed)

    tab = TaskListTab()
    tab.set_task_set(taskset)
    pending_lock = tab._task_table._table.cellWidget(0, 4)
    completed_lock = tab._task_table._table.cellWidget(1, 4)

    assert pending_lock.isHidden()
    assert not completed_lock.isHidden()
    tab._on_lock_toggled(pending.id, True)
    assert pending.locked is False


def test_stale_queue_remove_cannot_delete_requeued_capsule(qapp):
    visualizer = QueueVisualizer()
    visualizer.add_task("same", "first", TaskStatus.QUEUED)
    old_capsule = visualizer._capsules["same"]
    visualizer.remove_task("same")
    visualizer.clear()
    visualizer.add_task("same", "second", TaskStatus.QUEUED)
    new_capsule = visualizer._capsules["same"]

    visualizer._do_remove("same", old_capsule)

    assert visualizer._capsules["same"] is new_capsule


def test_invalid_stored_slider_value_falls_back_to_default(qapp):
    widget = EngineConfigWidget()
    widget.set_schema([
        ParamField(
            name="weight",
            label="Weight",
            field_type="slider",
            default=0.65,
            min_val=0.0,
            max_val=1.0,
        )
    ])

    widget.set_params({"weight": "not-a-number"})

    assert widget.get_params()["weight"] == 0.65
