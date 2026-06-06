"""TaskSet 加载/保存测试"""

import json
import tempfile
from pathlib import Path

import pytest
from src.core.task import Task, TaskStatus
from src.core.taskset import TaskSet


class TestTaskSetCreation:
    """TaskSet 创建"""

    def test_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test_project"
            ts = TaskSet.create("测试项目", path)
            assert ts.name == "测试项目"
            assert ts.directory == path
            assert path.exists()
            assert ts.tasks_dir.exists()
            assert ts.outputs_dir.exists()
            assert (path / "taskset.json").exists()

    def test_auto_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "auto_id"
            ts = TaskSet(name="test", directory=path)
            assert ts.id
            assert len(ts.id) == 8

    def test_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timestamps"
            ts = TaskSet(name="test", directory=path)
            assert ts.created_at
            assert ts.updated_at
            assert "T" in ts.created_at


class TestTaskSetLoad:
    """TaskSet 加载"""

    def test_load_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "existing"
            TaskSet.create("已存在", path)
            ts = TaskSet.load(path)
            assert ts.name == "已存在"
            assert ts.directory == path

    def test_load_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "non_existent"
            with pytest.raises(FileNotFoundError):
                TaskSet.load(path)

    def test_load_with_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "with_tasks"
            ts = TaskSet.create("任务项目", path)

            task1 = Task(id="task_001", text="你好", engine="indextts")
            task2 = Task(id="task_002", text="世界", engine="indextts",
                         status=TaskStatus.COMPLETED)
            ts.tasks = [task1, task2]
            ts.save()

            loaded = TaskSet.load(path)
            assert len(loaded.tasks) == 2
            assert loaded.tasks[0].id == "task_001"
            assert loaded.tasks[1].status == TaskStatus.COMPLETED


class TestTaskSetCRUD:
    """任务 CRUD"""

    def test_add_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "add"
            ts = TaskSet.create("add", path)
            task = Task(id="task_001", text="新增", engine="indextts")
            ts.add_task(task)
            assert len(ts.tasks) == 1
            assert ts.tasks[0].id == "task_001"

    def test_remove_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "remove"
            ts = TaskSet.create("remove", path)
            task = Task(id="task_001", text="删除", engine="indextts")
            ts.add_task(task)
            ts.save()

            removed = ts.remove_task("task_001")
            assert removed is not None
            assert len(ts.tasks) == 0
            assert not ts.tasks_dir.joinpath("task_001.json").exists()

    def test_remove_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "no_remove"
            ts = TaskSet.create("no_remove", path)
            result = ts.remove_task("not_there")
            assert result is None

    def test_get_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "get"
            ts = TaskSet.create("get", path)
            task = Task(id="task_001", text="查找", engine="indextts")
            ts.add_task(task)

            found = ts.get_task("task_001")
            assert found is not None
            assert found.text == "查找"

            not_found = ts.get_task("task_999")
            assert not_found is None

    def test_next_task_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "next_id"
            ts = TaskSet.create("next_id", path)
            tid1 = ts.next_task_id()
            # ID 应为 8 字符短 UUID，不再是 task_001
            assert isinstance(tid1, str)
            assert len(tid1) == 8
            assert not tid1.startswith("task_")

            # 添加第一个任务后再生成，应不重复
            ts.add_task(Task(id=tid1, text="1", engine="indextts"))
            tid2 = ts.next_task_id()
            assert tid2 != tid1
            assert len(tid2) == 8

    def test_save_and_reload(self):
        """完整保存和重载流程"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reload"
            ts = TaskSet.create("重载测试", path)

            for i in range(5):
                ts.add_task(Task(
                    id=f"task_{i+1:03d}",
                    text=f"文案_{i+1}",
                    engine="indextts",
                    engine_params={"ref": f"audio_{i+1}.wav"},
                ))

            ts.save()

            loaded = TaskSet.load(path)
            assert len(loaded.tasks) == 5
            assert loaded.tasks[0].engine_params["ref"] == "audio_1.wav"
            assert loaded.name == "重载测试"
