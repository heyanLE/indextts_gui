"""任务列表 Tab 页 — 左右分栏布局"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QPushButton, QLabel, QMessageBox,
)

from src.core.recipe import RecipeManager
from src.core.task import Task, TaskStatus
from src.core.taskset import TaskSet
from src.core.popup_suppressor import suppress_popups, restore_popups
from src.ui.task_table import TaskTableWidget
from src.ui.task_detail_panel import TaskDetailPanel
from src.ui.batch_import_dialog import BatchImportDialog


class TaskListTab(QWidget):
    """任务列表 Tab"""

    # 信号
    batch_generate = Signal(list)   # list[Task]
    tasks_deleted = Signal()        # 通知外部刷新
    play_audio = Signal(str)        # 播放音频 (file_path)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._taskset: TaskSet | None = None
        self._recipe_manager: RecipeManager | None = None

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ─── 工具栏 ───
        toolbar = QWidget()
        toolbar.setObjectName("taskToolbar")
        tl = QHBoxLayout(toolbar)
        tl.setContentsMargins(8, 6, 8, 6)

        self._batch_gen_btn = QPushButton("⚡ 批量生成")
        self._batch_gen_btn.setObjectName("primaryBtn")
        self._batch_gen_btn.clicked.connect(self._on_batch_generate)

        self._batch_del_btn = QPushButton("🗑 批量删除")
        self._batch_del_btn.setObjectName("dangerBtn")
        self._batch_del_btn.clicked.connect(self._on_batch_delete)

        self._new_task_btn = QPushButton("＋ 新建任务")
        self._new_task_btn.setObjectName("successBtn")
        self._new_task_btn.clicked.connect(self._on_new_task)

        self._batch_import_btn = QPushButton("📥 批量导入")
        self._batch_import_btn.setObjectName("successBtn")
        self._batch_import_btn.clicked.connect(self._on_batch_import)

        self._select_all_btn = QPushButton("全选")
        self._select_all_btn.clicked.connect(self._on_select_all)

        self._deselect_btn = QPushButton("取消全选")
        self._deselect_btn.clicked.connect(self._on_deselect_all)

        self._count_label = QLabel("共 0 个任务")
        self._count_label.setObjectName("countLabel")

        tl.addWidget(self._batch_gen_btn)
        tl.addWidget(self._batch_del_btn)
        tl.addWidget(self._select_all_btn)
        tl.addWidget(self._deselect_btn)
        tl.addWidget(self._new_task_btn)
        tl.addWidget(self._batch_import_btn)
        tl.addStretch()
        tl.addWidget(self._count_label)

        layout.addWidget(toolbar)

        # ─── 左右分栏（QSplitter） ───
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("taskSplitter")

        # 左侧：任务表格
        self._task_table = TaskTableWidget()
        splitter.addWidget(self._task_table)

        # 右侧：任务详情
        self._detail_panel = TaskDetailPanel()
        splitter.addWidget(self._detail_panel)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([300, 500])
        # 禁止两侧完全折叠，防止拖到极限后无法恢复
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)

        layout.addWidget(splitter, 1)

    def _connect_signals(self) -> None:
        """连接内部信号"""
        self._task_table.task_selected.connect(self._on_task_selected)
        self._task_table.play_audio_requested.connect(self._on_play_task)
        self._task_table.task_generate_requested.connect(self._on_single_generate)
        self._task_table.tasks_reordered.connect(self._on_tasks_reordered)
        # 🔒 表格内锁定按钮
        self._task_table.lock_toggled.connect(self._on_lock_toggled)
        # 🔊 表格内试听按钮
        self._task_table.play_ref_requested.connect(self._on_play_ref_from_table)
        self._task_table.play_output_requested.connect(self._on_play_output_from_table)
        # 详情面板
        self._detail_panel.task_saved.connect(self._on_task_saved)
        self._detail_panel.task_deleted.connect(self._on_task_deleted)
        self._detail_panel.play_ref_audio.connect(self._on_play_ref)
        self._detail_panel.play_output.connect(self._on_play_output)

    def set_recipe_manager(self, manager: RecipeManager) -> None:
        """设置配方管理器引用"""
        self._recipe_manager = manager

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------
    def set_task_set(self, taskset: TaskSet) -> None:
        """设置当前任务集并加载任务"""
        # Persist any pending debounced edit against the old task set before
        # replacing the owner reference.
        self._detail_panel.flush_pending_save()
        self._taskset = taskset
        # 🔇 加载期间全局抑制所有弹窗 + 全程阻断引擎 combo 信号
        suppress_popups()
        self._detail_panel._engine_combo.blockSignals(True)
        try:
            self._detail_panel.init_engines()
            self._task_table.load_tasks(taskset.tasks)
            self._detail_panel.clear()
            self._count_label.setText(f"共 {len(taskset.tasks)} 个任务")
        finally:
            self._detail_panel._engine_combo.blockSignals(False)
            restore_popups()

    def refresh_task(self, task_id: str) -> None:
        """刷新单个任务显示"""
        if not self._taskset:
            return
        task = self._taskset.get_task(task_id)
        if task:
            self._task_table.refresh_task(task)
            # 如果是当前选中的任务，刷新详情
            if self._detail_panel._current_task and self._detail_panel._current_task.id == task_id:
                self._detail_panel.load_task(task)

    def refresh_all(self) -> None:
        """刷新全部任务列表"""
        if self._taskset:
            self._task_table.load_tasks(self._taskset.tasks)
            self._count_label.setText(f"共 {len(self._taskset.tasks)} 个任务")

    def checked_tasks(self) -> list[Task]:
        """获取所有勾选的任务对象"""
        if not self._taskset:
            return []
        ids = self._task_table.checked_task_ids()
        return [t for t in self._taskset.tasks if t.id in ids]

    def flush_pending_save(self) -> None:
        """Persist edits still waiting in the detail panel's debounce timer."""
        self._detail_panel.flush_pending_save()
        # Qt does not propagate exceptions raised by a slot back through
        # Signal.emit reliably. Save once at this lifecycle boundary so callers
        # can cancel a switch/close when persistence actually failed.
        if self._taskset:
            self._taskset.save()

    # ------------------------------------------------------------------
    # 内部事件
    # ------------------------------------------------------------------
    def _on_task_selected(self, task_id: str) -> None:
        if not self._taskset or not task_id:
            self._detail_panel.load_task(None)
            return
        task = self._taskset.get_task(task_id)
        self._detail_panel.load_task(task)

    def _on_task_saved(self, task: Task) -> None:
        if not self._taskset:
            return
        # 更新 taskset 中的任务（替换原对象以保留新字段）
        existing = self._taskset.get_task(task.id)
        if existing:
            idx = self._taskset.tasks.index(existing)
            self._taskset.tasks[idx] = task
        else:
            self._taskset.add_task(task)
        self._taskset.save()
        self._task_table.refresh_task(task)
        self._count_label.setText(f"共 {len(self._taskset.tasks)} 个任务")

    def _on_task_deleted(self, task_id: str) -> None:
        if not self._taskset:
            return
        self._taskset.remove_task(task_id)
        self._taskset.save()
        self._detail_panel.clear()
        self.refresh_all()
        self.tasks_deleted.emit()

    def _on_new_task(self) -> None:
        if not self._taskset:
            QMessageBox.warning(self, "提示", "请先在配置页面选择或创建任务集")
            return

        task = Task(
            id=self._taskset.next_task_id(),
            text="",
            engine="indextts",
        )
        self._taskset.add_task(task)
        self._taskset.save()
        self.refresh_all()
        # 自动选中新任务
        self._detail_panel.load_task(task)

    def _on_batch_import(self) -> None:
        """批量导入：弹出对话框，输入多行文案和共用参数，一行生成一个任务"""
        if not self._taskset:
            QMessageBox.warning(self, "提示", "请先在配置页面选择或创建任务集")
            return

        dialog = BatchImportDialog(self)
        if self._recipe_manager:
            dialog.set_recipe_manager(self._recipe_manager)
        if dialog.exec() != BatchImportDialog.DialogCode.Accepted:
            return

        tasks = dialog.get_tasks(self._taskset.next_task_id)
        if not tasks:
            return

        for task in tasks:
            self._taskset.add_task(task)

        self._taskset.save()
        self.refresh_all()
        QMessageBox.information(
            self, "导入完成",
            f"成功导入 {len(tasks)} 个任务。\n\n"
            "可在任务列表中勾选后点击「批量生成」加入队列。",
        )

    def _on_batch_generate(self) -> None:
        """批量生成：必须勾选任务，将选中的可生成任务加入队列"""
        # Commit any detail edits before status changes to QUEUED. Once queued,
        # can_edit() becomes false and the debounce callback intentionally skips.
        self._detail_panel.flush_pending_save()
        if not self._taskset:
            QMessageBox.information(self, "提示", "没有任务可生成")
            return

        tasks = self.checked_tasks()
        if not tasks:
            QMessageBox.information(self, "提示", "请先勾选要生成的任务")
            return

        pending = [t for t in tasks if t.status in (TaskStatus.PENDING, TaskStatus.FAILED, TaskStatus.COMPLETED)]
        if not pending:
            QMessageBox.information(self, "提示", "勾选的任务中没有可加入队列的任务（仅未开始、失败或已完成的任务可加入）")
            return

        self.batch_generate.emit(pending)

    def _on_single_generate(self, task_id: str) -> None:
        """单个任务加入生成队列（支持已完成任务重新生成）"""
        self._detail_panel.flush_pending_save()
        if not self._taskset:
            return
        task = self._taskset.get_task(task_id)
        if not task:
            return
        if task.status not in (TaskStatus.PENDING, TaskStatus.FAILED, TaskStatus.COMPLETED):
            QMessageBox.information(self, "提示", f"任务 {task_id} 当前状态不允许加入队列")
            return
        self.batch_generate.emit([task])

    def _on_batch_delete(self) -> None:
        tasks = self.checked_tasks()
        if not tasks:
            QMessageBox.information(self, "提示", "请先勾选要删除的任务")
            return

        deletable = [t for t in tasks if t.can_delete()]
        locked = [t for t in tasks if not t.can_delete()]

        msg = f"确定删除 {len(deletable)} 个任务吗？此操作不可撤销。"
        if locked:
            msg += f"\n\n{len(locked)} 个任务正在队列/生成中，无法删除。"

        ret = QMessageBox.question(self, "确认批量删除", msg)
        if ret != QMessageBox.StandardButton.Yes:
            return

        for task in deletable:
            self._taskset.remove_task(task.id) if self._taskset else None

        if self._taskset:
            self._taskset.save()
        self._detail_panel.clear()
        self.refresh_all()
        self.tasks_deleted.emit()

    def _on_play_task(self, task_id: str) -> None:
        """双击任务行播放"""
        if not self._taskset:
            return
        task = self._taskset.get_task(task_id)
        if task and task.output_audio_path:
            self.play_audio.emit(task.output_audio_path)

    # ------------------------------------------------------------------
    # 🔒 表格锁定按钮处理
    # ------------------------------------------------------------------
    def _on_lock_toggled(self, task_id: str, locked: bool) -> None:
        """表格中锁定按钮切换 → 更新 task.locked 并联动详情面板"""
        if not self._taskset:
            return
        task = self._taskset.get_task(task_id)
        if not task:
            return
        if task.status != TaskStatus.COMPLETED:
            # Locking is only meaningful for completed results. Ignore stale
            # button events from a row that changed status concurrently.
            self._task_table.refresh_task(task)
            return
        task.locked = locked
        self._taskset.save()

        # 如果是当前详情面板打开的任务，只刷新状态显示（不重建整个引擎表单）
        if (self._detail_panel._current_task
                and self._detail_panel._current_task.id == task_id):
            self._detail_panel._update_status_display()

        # 同时刷新表格的锁定列显示（🔒/🔓 切换由 refresh_task 处理）
        self._task_table.refresh_task(task)

    # ------------------------------------------------------------------
    # 🔊 表格试听按钮处理
    # ------------------------------------------------------------------
    def _on_play_ref_from_table(self, task_id: str) -> None:
        """表格中点击 ▶ 试听参考音频"""
        if not self._taskset:
            return
        task = self._taskset.get_task(task_id)
        if not task:
            return
        ref = task.engine_params.get("reference_audio") or task.engine_params.get("emotion_audio")
        if ref:
            self.play_audio.emit(ref)

    def _on_play_output_from_table(self, task_id: str) -> None:
        """表格中点击 ♫ 试听输出音频"""
        if not self._taskset:
            return
        task = self._taskset.get_task(task_id)
        if task and task.output_audio_path:
            self.play_audio.emit(task.output_audio_path)

    def _on_play_ref(self, path: str) -> None:
        """播放参考音频"""
        if path:
            self.play_audio.emit(path)

    def _on_play_output(self, path: str) -> None:
        """播放输出音频"""
        if path:
            self.play_audio.emit(path)

    def _on_select_all(self) -> None:
        self._task_table.select_all()

    def _on_deselect_all(self) -> None:
        self._task_table.deselect_all()

    def _on_tasks_reordered(self, ordered_ids: list[str]) -> None:
        """拖拽排序后持久化新顺序"""
        if self._taskset:
            self._taskset.reorder_tasks(ordered_ids)
            self._taskset.save()
            self._count_label.setText(f"共 {len(self._taskset.tasks)} 个任务")
