"""Task 数据模型单元测试"""

import pytest
from src.core.task import Task, TaskStatus, sanitize_filename


class TestTaskStatus:
    """TaskStatus 枚举"""

    def test_all_statuses_exist(self):
        assert TaskStatus.PENDING.value == "未开始"
        assert TaskStatus.QUEUED.value == "队列中"
        assert TaskStatus.GENERATING.value == "生成中"
        assert TaskStatus.COMPLETED.value == "生成完成"
        assert TaskStatus.FAILED.value == "生成失败"


class TestTaskCreation:
    """Task 创建"""

    def test_create_default_status(self):
        task = Task(id="task_001", text="你好", engine="indextts")
        assert task.status == TaskStatus.PENDING
        assert task.text == "你好"
        assert task.engine == "indextts"

    def test_create_with_explicit_status(self):
        task = Task(
            id="task_002", text="测试",
            engine="indextts", status=TaskStatus.COMPLETED
        )
        assert task.status == TaskStatus.COMPLETED

    def test_auto_timestamps(self):
        task = Task(id="task_003", text="时间戳", engine="indextts")
        assert task.created_at
        assert task.updated_at
        assert "T" in task.created_at  # ISO 8601


class TestTaskStateMachine:
    """状态机流转"""

    def test_pending_to_queued(self):
        task = Task(id="t1", text="x", engine="indextts")
        task.transition_to(TaskStatus.QUEUED)
        assert task.status == TaskStatus.QUEUED

    def test_queued_to_generating(self):
        task = Task(id="t2", text="x", engine="indextts", status=TaskStatus.QUEUED)
        task.transition_to(TaskStatus.GENERATING)
        assert task.status == TaskStatus.GENERATING

    def test_generating_to_completed(self):
        task = Task(id="t3", text="x", engine="indextts", status=TaskStatus.GENERATING)
        task.transition_to(TaskStatus.COMPLETED)
        assert task.status == TaskStatus.COMPLETED
        assert task.generation_config is None  # 外部设置

    def test_generating_to_failed(self):
        task = Task(id="t4", text="x", engine="indextts", status=TaskStatus.GENERATING)
        task.transition_to(TaskStatus.FAILED)
        assert task.status == TaskStatus.FAILED

    def test_failed_to_queued_retry(self):
        task = Task(id="t5", text="x", engine="indextts", status=TaskStatus.FAILED)
        task.transition_to(TaskStatus.QUEUED)
        assert task.status == TaskStatus.QUEUED

    def test_completed_to_queued_retry(self):
        task = Task(id="t6", text="x", engine="indextts", status=TaskStatus.COMPLETED)
        task.transition_to(TaskStatus.QUEUED)
        assert task.status == TaskStatus.QUEUED

    def test_invalid_transition_raises(self):
        task = Task(id="t7", text="x", engine="indextts")  # PENDING
        with pytest.raises(ValueError):
            task.transition_to(TaskStatus.COMPLETED)  # 不能跳过队列

    def test_completed_to_pending_raises(self):
        task = Task(id="t8", text="x", engine="indextts", status=TaskStatus.COMPLETED)
        with pytest.raises(ValueError):
            task.transition_to(TaskStatus.PENDING)

    def test_updated_at_changes_on_transition(self):
        task = Task(id="t9", text="x", engine="indextts")
        old = task.updated_at
        task.transition_to(TaskStatus.QUEUED)
        assert task.updated_at != old


class TestTaskEditPermissions:
    """编辑/删除权限"""

    def test_pending_can_edit(self):
        task = Task(id="t1", text="x", engine="indextts")
        assert task.can_edit() is True

    def test_queued_cannot_edit(self):
        task = Task(id="t2", text="x", engine="indextts", status=TaskStatus.QUEUED)
        assert task.can_edit() is False

    def test_generating_cannot_edit(self):
        task = Task(id="t3", text="x", engine="indextts", status=TaskStatus.GENERATING)
        assert task.can_edit() is False

    def test_completed_can_edit(self):
        task = Task(id="t4", text="x", engine="indextts", status=TaskStatus.COMPLETED)
        assert task.can_edit() is True

    def test_failed_can_edit(self):
        task = Task(id="t5", text="x", engine="indextts", status=TaskStatus.FAILED)
        assert task.can_edit() is True

    def test_queued_cannot_delete(self):
        task = Task(id="t6", text="x", engine="indextts", status=TaskStatus.QUEUED)
        assert task.can_delete() is False

    def test_generating_cannot_delete(self):
        task = Task(id="t7", text="x", engine="indextts", status=TaskStatus.GENERATING)
        assert task.can_delete() is False


class TestTaskSerialization:
    """序列化"""

    def test_to_dict_and_from_dict(self):
        task = Task(id="task_001", text="你好世界", engine="indextts")
        data = task.to_dict()
        assert data["status"] == "未开始"

        restored = Task.from_dict(data)
        assert restored.id == task.id
        assert restored.text == task.text
        assert restored.engine == task.engine
        assert restored.status == task.status

    def test_from_dict_compatibility(self):
        """兼容中文状态值"""
        data = {
            "id": "task_099",
            "text": "测试",
            "engine": "indextts",
            "status": "生成完成",
        }
        task = Task.from_dict(data)
        assert task.status == TaskStatus.COMPLETED

    def test_queued_not_persisted_in_to_dict(self):
        """QUEUED 纯运行时 → to_dict 输出 PENDING"""
        task = Task(id="t_q", text="队列任务", engine="indextts",
                    status=TaskStatus.QUEUED)
        data = task.to_dict()
        assert data["status"] == "未开始"  # 不是 "队列中"

    def test_queued_not_restored_from_dict(self):
        """磁盘上的 QUEUED 安全兜底为 PENDING"""
        data = {
            "id": "old_q",
            "text": "旧数据",
            "engine": "indextts",
            "status": "队列中",
        }
        task = Task.from_dict(data)
        assert task.status == TaskStatus.PENDING

    def test_generating_not_restored_from_dict(self):
        """磁盘上的 GENERATING（崩溃残留）安全兜底为 PENDING"""
        data = {
            "id": "old_g",
            "text": "生成中残留",
            "engine": "indextts",
            "status": "生成中",
        }
        task = Task.from_dict(data)
        assert task.status == TaskStatus.PENDING


class TestSanitizeFilename:
    """文件命名清洗"""

    def test_simple_chinese(self):
        result = sanitize_filename("你好世界")
        assert result == "你好世界"

    def test_with_special_chars(self):
        result = sanitize_filename("Hello, 你好!")
        assert "," not in result
        assert "!" not in result

    def test_spaces_to_underscores(self):
        result = sanitize_filename("hello world")
        assert " " not in result

    def test_max_length_truncation(self):
        long_text = "这是一段非常长的文本用于测试截断功能" * 3
        result = sanitize_filename(long_text, max_length=20)
        assert len(result) <= 20
        assert not result.endswith("_")

    def test_all_special_chars(self):
        result = sanitize_filename("@#$%^&*()")
        assert result == "untitled"

    def test_empty_string(self):
        result = sanitize_filename("")
        assert result == "untitled"


class TestTaskAudioFilename:
    """音频文件名生成"""

    def test_basic(self):
        task = Task(id="task_001", text="你好世界", engine="indextts")
        name = task.audio_filename()
        assert name.startswith("task_001_")
        assert name.endswith(".wav")

    def test_with_extension(self):
        task = Task(id="task_005", text="测试", engine="indextts")
        name = task.audio_filename("mp3")
        assert name.endswith(".mp3")
