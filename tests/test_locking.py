"""任务锁定状态功能测试 (UT-06)"""

import pytest
from src.core.task import Task, TaskStatus


class TestTaskLockedField:
    """Task.locked 字段行为"""

    def test_default_is_false(self):
        task = Task(id="t1", text="测试", engine="indextts")
        assert task.locked is False

    def test_explicit_true(self):
        task = Task(id="t2", text="测试", engine="indextts", locked=True)
        assert task.locked is True

    def test_from_dict_defaults_to_false(self):
        """旧数据无 locked 字段 → 默认 False"""
        data = {
            "id": "t_old",
            "text": "旧任务",
            "engine": "indextts",
            "status": "生成完成",
        }
        task = Task.from_dict(data)
        assert task.locked is False

    def test_from_dict_preserves_locked_true(self):
        data = {
            "id": "t_new",
            "text": "新任务",
            "engine": "indextts",
            "status": "生成完成",
            "locked": True,
        }
        task = Task.from_dict(data)
        assert task.locked is True

    def test_to_dict_includes_locked(self):
        task = Task(id="t3", text="测试", engine="indextts", locked=True)
        data = task.to_dict()
        assert data["locked"] is True

    def test_to_dict_false_included(self):
        task = Task(id="t4", text="测试", engine="indextts")
        data = task.to_dict()
        assert "locked" in data
        assert data["locked"] is False


class TestLockedRoundtrip:
    """锁定状态序列化往返"""

    def test_roundtrip_locked(self):
        task = Task(id="t5", text="锁了", engine="indextts", locked=True)
        data = task.to_dict()
        restored = Task.from_dict(data)
        assert restored.locked is True
        assert restored.id == task.id


class TestLockedWithCanEdit:
    """locked 与 can_edit 的关系（锁定不改变状态机规则）"""

    def test_completed_locked_still_can_edit(self):
        """完成态锁定时 can_edit 仍为 True（由 UI 层根据 locked 禁用控件）"""
        task = Task(id="t6", text="完成且锁定", engine="indextts",
                    status=TaskStatus.COMPLETED, locked=True)
        assert task.can_edit() is True  # 状态机不感知 locked

    def test_completed_unlocked_can_edit(self):
        task = Task(id="t7", text="完成未锁", engine="indextts",
                    status=TaskStatus.COMPLETED, locked=False)
        assert task.can_edit() is True

    def test_generating_cannot_edit_regardless_of_lock(self):
        """生成中无论是否锁定都不能编辑"""
        for locked in (True, False):
            task = Task(id="t8", text="生成中", engine="indextts",
                        status=TaskStatus.GENERATING, locked=locked)
            assert task.can_edit() is False

    def test_queued_cannot_edit_regardless_of_lock(self):
        for locked in (True, False):
            task = Task(id="t9", text="队列中", engine="indextts",
                        status=TaskStatus.QUEUED, locked=locked)
            assert task.can_edit() is False


class TestLockedWithCanDelete:
    """locked 与 can_delete 的关系"""

    def test_completed_locked_can_delete(self):
        """锁定不影响删除权限"""
        task = Task(id="t10", text="完成锁定", engine="indextts",
                    status=TaskStatus.COMPLETED, locked=True)
        assert task.can_delete() is True

    def test_generating_locked_cannot_delete(self):
        """生成中不可删除"""
        task = Task(id="t11", text="生成中锁定", engine="indextts",
                    status=TaskStatus.GENERATING, locked=True)
        assert task.can_delete() is False
