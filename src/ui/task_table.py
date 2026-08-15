"""任务列表表格 — 左侧任务列表（QTableWidget）"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QEvent, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QPushButton, QLabel, QMenu,
)
from PySide6.QtGui import QColor, QBrush, QFont, QAction

from src.core.task import Task, TaskStatus
from src.ui.spinner_widget import SpinnerWidget


# 状态颜色映射
STATUS_COLORS: dict[TaskStatus, QColor] = {
    TaskStatus.PENDING: QColor("#6A6E73"),
    TaskStatus.QUEUED: QColor("#0066CC"),
    TaskStatus.GENERATING: QColor("#EE0000"),
    TaskStatus.COMPLETED: QColor("#3E8635"),
    TaskStatus.FAILED: QColor("#C9190B"),
}

# ── 列索引常量 ──
COL_CHECK = 0
COL_SUMMARY = 1
COL_STATUS = 2
COL_AUDITION = 3
COL_LOCK = 4
COL_ACTION = 5
COLUMN_COUNT = 6


# ======================================================================
#  单元格辅助 Widget
# ======================================================================

class _StatusCell(QWidget):
    """状态列单元格：非 GENERATING 显示文字，GENERATING 显示 Spinner + 文字"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        # ★ 传入 self 防止子 widget 成为顶级窗口（Windows HWND 闪烁）
        self._spinner = SpinnerWidget(size=16, color="#EE0000", line_width=2, parent=self)
        self._spinner.setVisible(False)
        layout.addWidget(self._spinner)

        self._label = QLabel(self)
        self._label.setObjectName("statusCellLabel")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._label)

        # 默认居中
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_status(self, status: TaskStatus) -> None:
        color = STATUS_COLORS.get(status, QColor("#9E9E9E"))
        self._label.setText(status.value)
        self._label.setStyleSheet(
            f"color: {color.name()}; font-weight: bold; font-size: 12px;"
            "background: transparent; border: none;"
        )

        if status == TaskStatus.GENERATING:
            self._spinner.setVisible(True)
            self._spinner.start()
        else:
            self._spinner.setVisible(False)
            self._spinner.stop()


class _AuditionCell(QWidget):
    """试听列单元格：▶ 参考音频 + ▶ 输出音频"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ★ 传入 self 防止子 widget 成为顶级窗口（Windows HWND 闪烁）
        self._ref_btn = QPushButton("♫", self)
        self._ref_btn.setObjectName("auditionBtn")
        self._ref_btn.setFixedSize(26, 24)
        self._ref_btn.setToolTip("试听参考音频")
        self._ref_btn.setStyleSheet(
            "#auditionBtn {"
            " background-color: #F0F0F0; color: #0066CC; border: none;"
            " border-radius: 4px; padding: 0px; font-size: 12px;"
            "}"
            "#auditionBtn:hover { background-color: #0066CC; color: white; }"
            "#auditionBtn:disabled { background-color: #F5F5F5; color: #B0B0B0; }"
        )

        self._out_btn = QPushButton("▶", self)
        self._out_btn.setObjectName("auditionBtn")
        self._out_btn.setFixedSize(26, 24)
        self._out_btn.setToolTip("试听输出音频")
        self._out_btn.setStyleSheet(
            "#auditionBtn {"
            " background-color: #F0F0F0; color: #3E8635; border: none;"
            " border-radius: 4px; padding: 0px; font-size: 12px;"
            "}"
            "#auditionBtn:hover { background-color: #3E8635; color: white; }"
            "#auditionBtn:disabled { background-color: #F5F5F5; color: #B0B0B0; }"
        )

        layout.addWidget(self._ref_btn)
        layout.addWidget(self._out_btn)

    def configure(self, task: Task) -> None:
        """根据任务状态配置按钮可见性和启用状态"""
        # 参考音频：从 engine_params 中查找
        ref_path = (
            task.engine_params.get("reference_audio")
            or task.engine_params.get("emotion_audio")
        )
        self._ref_btn.setVisible(bool(ref_path))
        self._ref_btn.setEnabled(bool(ref_path))

        # 输出音频：只要文件存在就显示按钮，不限制 COMPLETED 状态
        # （已完成任务重新入队后 status=QUEUED，但音频文件仍在）
        has_output = bool(task.output_audio_path)
        self._out_btn.setVisible(has_output)
        self._out_btn.setEnabled(has_output)

    def set_ref_callback(self, cb) -> None:
        _safe_disconnect(self._ref_btn.clicked)
        self._ref_btn.clicked.connect(cb)

    def set_out_callback(self, cb) -> None:
        _safe_disconnect(self._out_btn.clicked)
        self._out_btn.clicked.connect(cb)


def _safe_disconnect(signal) -> None:
    """安全断开信号的所有连接（不依赖 isSignalConnected）"""
    import warnings
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            signal.disconnect()
    except (TypeError, RuntimeError):
        pass


# ======================================================================
#  TaskTableWidget
# ======================================================================

class TaskTableWidget(QWidget):
    """任务列表表格 — 支持拖拽排序"""

    # ── 信号 ──
    task_selected = Signal(str)          # 选中的 task_id（空串表示取消选中）
    tasks_checked = Signal(list)         # 被勾选的 task_id 列表
    play_audio_requested = Signal(str)   # 请求播放音频（task_id，双击行）
    task_generate_requested = Signal(str)  # 单任务生成请求（task_id）
    tasks_reordered = Signal(list)        # 拖拽排序后发出新顺序的 task_id 列表

    # 🔒 锁定切换 → 表格内直接操作
    lock_toggled = Signal(str, bool)      # task_id, locked
    # 🔊 试听按钮
    play_ref_requested = Signal(str)      # task_id — 播放参考音频
    play_output_requested = Signal(str)   # task_id — 播放输出音频

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tasks: list[Task] = []
        self._block_check_signal: bool = False

        self._setup_ui()

    # ------------------------------------------------------------------
    # UI 搭建
    # ------------------------------------------------------------------
    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget()
        self._table.setColumnCount(COLUMN_COUNT)
        self._table.setHorizontalHeaderLabels(
            ["", "ID / 文案", "状态", "试听", "锁定", "操作"]
        )

        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COL_CHECK, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_SUMMARY, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_AUDITION, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_LOCK, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_ACTION, QHeaderView.ResizeMode.Fixed)

        self._table.setColumnWidth(COL_CHECK, 40)
        self._table.setColumnWidth(COL_STATUS, 90)
        self._table.setColumnWidth(COL_AUDITION, 64)
        self._table.setColumnWidth(COL_LOCK, 50)
        self._table.setColumnWidth(COL_ACTION, 72)

        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.setObjectName("taskTable")
        self._table.setAlternatingRowColors(False)

        # ── 拖拽排序 ──
        self._table.setDragEnabled(True)
        self._table.setAcceptDrops(True)
        self._table.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._table.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._table.setDropIndicatorShown(True)
        self._table.viewport().installEventFilter(self)

        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.itemDoubleClicked.connect(self._on_double_clicked)
        self._table.cellClicked.connect(self._on_cell_clicked)

        layout.addWidget(self._table)

    # ------------------------------------------------------------------
    # 数据加载
    # ------------------------------------------------------------------
    def load_tasks(self, tasks: list[Task]) -> None:
        """加载任务列表到表格（智能增量更新行数，避免全量销毁重建）"""
        self._tasks = tasks
        self._block_check_signal = True
        # 彻底阻断表格所有信号（itemSelectionChanged、cellClicked 等），
        # 防止在批量构建 UI 时触发级联信号导致弹窗。
        # save/restore 模式：兼容外层已 blockSignals 的调用（如拖拽排序后重建）
        was_blocked = self._table.signalsBlocked()
        self._table.blockSignals(True)

        # ⏸ 冻结表格重绘：大批量任务时避免逐行 repaint
        self._table.setUpdatesEnabled(False)

        # ── 智能行数调整（新增/删除差值，不全量销毁） ──
        current = self._table.rowCount()
        target = len(tasks)
        if target != current:
            self._table.setRowCount(target)

        for row, task in enumerate(tasks):
            # ── Col 0: 勾选框 ──
            existing_cb = self._table.cellWidget(row, COL_CHECK)
            if existing_cb is not None:
                # 复用已有 widget：更新 callback
                btn = existing_cb.findChild(QPushButton)
                if btn:
                    _safe_disconnect(btn.toggled)
                    btn.setProperty("task_id", task.id)
                    btn.setChecked(False)
                    btn.toggled.connect(self._on_check_changed)
            else:
                cb_widget = QWidget(self._table.viewport())
                cb_widget.setObjectName("checkCell")
                cb_layout = QVBoxLayout(cb_widget)
                cb_layout.setContentsMargins(4, 0, 4, 0)
                cb_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
                # ★ 传入 cb_widget 防止顶级窗口闪烁
                cb = QPushButton("✓", cb_widget)
                cb.setObjectName("checkBtn")
                cb.setCheckable(True)
                cb.setFixedSize(22, 22)
                cb.setProperty("task_id", task.id)
                cb.toggled.connect(self._on_check_changed)
                cb_layout.addWidget(cb)
                self._table.setCellWidget(row, COL_CHECK, cb_widget)

            # ── Col 1: ID + 文案摘要 ──
            engine_label = "IDXT" if task.engine == "indextts" else "GSV"
            text_preview = (task.text[:30] + "…") if len(task.text) > 30 else task.text
            lock_prefix = "🔒 " if task.locked else ""
            display = f"{lock_prefix}[{task.id}] [{engine_label}] {text_preview}"
            item = QTableWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, task.id)
            self._table.setItem(row, COL_SUMMARY, item)

            # ── Col 2: 状态（含 Spinner） ──
            existing_st = self._table.cellWidget(row, COL_STATUS)
            if isinstance(existing_st, _StatusCell):
                existing_st.set_status(task.status)
            else:
                # ★ 传入 viewport 作为 parent，防止无 parent 时成为顶级窗口导致闪烁
                status_cell = _StatusCell(self._table.viewport())
                status_cell.set_status(task.status)
                self._table.setCellWidget(row, COL_STATUS, status_cell)

            # ── Col 3: 试听按钮 ──
            existing_au = self._table.cellWidget(row, COL_AUDITION)
            if isinstance(existing_au, _AuditionCell):
                existing_au.configure(task)
                existing_au.set_ref_callback(self._make_play_ref_callback(task.id))
                existing_au.set_out_callback(self._make_play_output_callback(task.id))
            else:
                # ★ 传入 viewport 作为 parent，防止无 parent 时成为顶级窗口导致闪烁
                audition_cell = _AuditionCell(self._table.viewport())
                audition_cell.configure(task)
                audition_cell.set_ref_callback(self._make_play_ref_callback(task.id))
                audition_cell.set_out_callback(self._make_play_output_callback(task.id))
                self._table.setCellWidget(row, COL_AUDITION, audition_cell)

            # ── Col 4: 锁定按钮 ──
            existing_lk = self._table.cellWidget(row, COL_LOCK)
            if isinstance(existing_lk, QPushButton):
                _safe_disconnect(existing_lk.clicked)
                existing_lk.setProperty("task_id", task.id)
                existing_lk.setChecked(task.locked)
                existing_lk.setText("🔒" if task.locked else "🔓")
                existing_lk.setToolTip("锁定任务（锁定后无法编辑）" if not task.locked else "解锁任务")
                existing_lk.setVisible(task.status == TaskStatus.COMPLETED)
                existing_lk.setEnabled(task.status == TaskStatus.COMPLETED)
                existing_lk.clicked.connect(self._on_lock_clicked)
            else:
                lock_btn = QPushButton("🔒" if task.locked else "🔓", self._table.viewport())
                lock_btn.setObjectName("tableLockBtn")
                lock_btn.setCheckable(True)
                lock_btn.setChecked(task.locked)
                lock_btn.setFixedSize(32, 26)
                lock_btn.setProperty("task_id", task.id)
                lock_btn.setToolTip("锁定任务（锁定后无法编辑）" if not task.locked else "解锁任务")
                lock_btn.clicked.connect(self._on_lock_clicked)
                lock_btn.setVisible(task.status == TaskStatus.COMPLETED)
                lock_btn.setEnabled(task.status == TaskStatus.COMPLETED)
                lock_btn.setStyleSheet(
                    "#tableLockBtn {"
                    " background-color: transparent; border: 1px solid #DADFE6;"
                    " border-radius: 3px; padding: 0px; font-size: 14px;"
                    "}"
                    "#tableLockBtn:hover { background-color: #F0F1F5; }"
                    "#tableLockBtn:disabled { border-color: #E0E3E8; }"
                )
                self._table.setCellWidget(row, COL_LOCK, lock_btn)
                # QTableWidget shows a widget when it takes ownership, so apply
                # state-dependent visibility after insertion.
                lock_btn.setVisible(task.status == TaskStatus.COMPLETED)
                lock_btn.setEnabled(task.status == TaskStatus.COMPLETED)

            # ── Col 5: 生成按钮 ──
            existing_gen = self._table.cellWidget(row, COL_ACTION)
            if existing_gen is not None:
                btn = existing_gen.findChild(QPushButton)
                if btn:
                    _safe_disconnect(btn.clicked)
                    btn.setProperty("task_id", task.id)
                    btn.setVisible(
                        task.status in (TaskStatus.PENDING, TaskStatus.FAILED, TaskStatus.COMPLETED)
                    )
                    btn.clicked.connect(self._on_generate_clicked)
            else:
                btn_widget = QWidget(self._table.viewport())
                btn_widget.setStyleSheet("background: transparent;")
                btn_layout = QHBoxLayout(btn_widget)
                btn_layout.setContentsMargins(4, 0, 4, 0)
                btn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

                # ★ 传入 btn_widget 防止顶级窗口闪烁
                gen_btn = QPushButton("生成", btn_widget)
                gen_btn.setObjectName("genTaskBtn")
                gen_btn.setFixedSize(56, 26)
                gen_btn.setProperty("task_id", task.id)
                gen_btn.setStyleSheet(
                    "QPushButton#genTaskBtn {"
                    " background-color: #EE0000; color: white; border: none;"
                    " border-radius: 4px; padding: 2px 4px; font-size: 12px; font-weight: 500;"
                    "}"
                    "QPushButton#genTaskBtn:hover { background-color: #D10000; }"
                    "QPushButton#genTaskBtn:pressed { background-color: #B80000; }"
                )
                gen_btn.clicked.connect(self._on_generate_clicked)
                gen_btn.setVisible(
                    task.status in (TaskStatus.PENDING, TaskStatus.FAILED, TaskStatus.COMPLETED)
                )
                btn_layout.addWidget(gen_btn)
                self._table.setCellWidget(row, COL_ACTION, btn_widget)

            # 行高
            self._table.setRowHeight(row, 46)

        # ▶ 先恢复表格信号 → 再刷新重绘 → 最后释放复选框锁
        self._table.blockSignals(was_blocked)
        self._table.setUpdatesEnabled(True)
        self._block_check_signal = False

    # ------------------------------------------------------------------
    # 局部刷新（状态变更时轻量更新）
    # ------------------------------------------------------------------
    def refresh_status(self, task_id: str, status: TaskStatus) -> None:
        """刷新单个任务的状态列 + 操作按钮可见性 + 试听按钮"""
        for row in range(self._table.rowCount()):
            item = self._table.item(row, COL_SUMMARY)
            if not item or item.data(Qt.ItemDataRole.UserRole) != task_id:
                continue

            # 更新状态单元格
            status_cell = self._table.cellWidget(row, COL_STATUS)
            if isinstance(status_cell, _StatusCell):
                status_cell.set_status(status)

            # 更新试听按钮
            audition_cell = self._table.cellWidget(row, COL_AUDITION)
            if isinstance(audition_cell, _AuditionCell):
                # 从内部列表取 task
                task = self._find_task(task_id)
                if task:
                    audition_cell.configure(task)

            # 更新锁定按钮可用性
            lock_btn = self._table.cellWidget(row, COL_LOCK)
            if isinstance(lock_btn, QPushButton):
                task = self._find_task(task_id)
                if task:
                    lock_btn.setVisible(task.status == TaskStatus.COMPLETED)
                    lock_btn.setEnabled(task.status == TaskStatus.COMPLETED)

            # 更新生成按钮可见性
            self._set_gen_button_visible(row, status)
            break

    def refresh_task(self, task: Task) -> None:
        """刷新单个任务的所有显示"""
        for row in range(self._table.rowCount()):
            item = self._table.item(row, COL_SUMMARY)
            if not item or item.data(Qt.ItemDataRole.UserRole) != task.id:
                continue

            # Col 1 文案
            engine_label = "IDXT" if task.engine == "indextts" else "GSV"
            text_preview = (task.text[:30] + "…") if len(task.text) > 30 else task.text
            lock_prefix = "🔒 " if task.locked else ""
            display = f"{lock_prefix}[{task.id}] [{engine_label}] {text_preview}"
            item.setText(display)

            # Col 2 状态
            status_cell = self._table.cellWidget(row, COL_STATUS)
            if isinstance(status_cell, _StatusCell):
                status_cell.set_status(task.status)

            # Col 3 试听
            audition_cell = self._table.cellWidget(row, COL_AUDITION)
            if isinstance(audition_cell, _AuditionCell):
                audition_cell.configure(task)
                _safe_disconnect(audition_cell._ref_btn.clicked)
                _safe_disconnect(audition_cell._out_btn.clicked)
                audition_cell.set_ref_callback(
                    self._make_play_ref_callback(task.id)
                )
                audition_cell.set_out_callback(
                    self._make_play_output_callback(task.id)
                )

            # Col 4 锁定按钮
            lock_btn = self._table.cellWidget(row, COL_LOCK)
            if isinstance(lock_btn, QPushButton):
                lock_btn.setChecked(task.locked)
                lock_btn.setText("🔒" if task.locked else "🔓")
                lock_btn.setVisible(task.status == TaskStatus.COMPLETED)
                lock_btn.setEnabled(task.status == TaskStatus.COMPLETED)

            # Col 5 生成按钮可见性
            self._set_gen_button_visible(row, task.status)
            break

    # ------------------------------------------------------------------
    # 选中 / 勾选
    # ------------------------------------------------------------------
    def selected_task_id(self) -> str | None:
        selected = self._table.selectedItems()
        if not selected:
            return None
        row = selected[0].row()
        item = self._table.item(row, COL_SUMMARY)
        if item:
            return item.data(Qt.ItemDataRole.UserRole)
        return None

    def checked_task_ids(self) -> list[str]:
        result: list[str] = []
        for row in range(self._table.rowCount()):
            cb_widget = self._table.cellWidget(row, COL_CHECK)
            if cb_widget:
                btn = cb_widget.findChild(QPushButton)
                if btn and btn.isChecked():
                    result.append(btn.property("task_id"))
        return result

    def select_all(self) -> None:
        self._block_check_signal = True
        try:
            for row in range(self._table.rowCount()):
                cb_widget = self._table.cellWidget(row, COL_CHECK)
                if cb_widget:
                    btn = cb_widget.findChild(QPushButton)
                    if btn:
                        btn.setChecked(True)
        finally:
            self._block_check_signal = False
        self.tasks_checked.emit(self.checked_task_ids())

    def deselect_all(self) -> None:
        self._block_check_signal = True
        try:
            for row in range(self._table.rowCount()):
                cb_widget = self._table.cellWidget(row, COL_CHECK)
                if cb_widget:
                    btn = cb_widget.findChild(QPushButton)
                    if btn:
                        btn.setChecked(False)
        finally:
            self._block_check_signal = False
        self.tasks_checked.emit([])

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------
    def _on_selection_changed(self) -> None:
        tid = self.selected_task_id()
        self.task_selected.emit(tid or "")

    def _on_double_clicked(self, item: QTableWidgetItem) -> None:
        """双击播放输出音频（仅在 col 1 有效，避免误触）"""
        if item.column() == COL_SUMMARY:
            tid = item.data(Qt.ItemDataRole.UserRole)
            if tid:
                self.play_audio_requested.emit(tid)

    def _on_cell_clicked(self, row: int, col: int) -> None:
        pass  # 勾选/按钮由自身信号处理

    def _on_check_changed(self) -> None:
        if not self._block_check_signal:
            self.tasks_checked.emit(self.checked_task_ids())

    def _on_generate_clicked(self) -> None:
        btn = self.sender()
        if isinstance(btn, QPushButton):
            task_id = btn.property("task_id")
            if task_id:
                self.task_generate_requested.emit(str(task_id))

    def _on_lock_clicked(self) -> None:
        """表格行中锁定按钮点击"""
        btn = self.sender()
        if isinstance(btn, QPushButton):
            task_id = btn.property("task_id")
            if task_id:
                self.lock_toggled.emit(str(task_id), btn.isChecked())

    # ------------------------------------------------------------------
    # 试听回调工厂
    # ------------------------------------------------------------------
    def _make_play_ref_callback(self, task_id: str):
        """创建播放参考音频的回调闭包"""
        def _play_ref():
            self.play_ref_requested.emit(task_id)
        return _play_ref

    def _make_play_output_callback(self, task_id: str):
        """创建播放输出音频的回调闭包"""
        def _play_out():
            self.play_output_requested.emit(task_id)
        return _play_out

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    def _find_task(self, task_id: str) -> Task | None:
        for t in self._tasks:
            if t.id == task_id:
                return t
        return None

    def _set_gen_button_visible(self, row: int, status: TaskStatus) -> None:
        btn_widget = self._table.cellWidget(row, COL_ACTION)
        if btn_widget:
            btn = btn_widget.findChild(QPushButton)
            if btn:
                visible = status in (
                    TaskStatus.PENDING,
                    TaskStatus.FAILED,
                    TaskStatus.COMPLETED,
                )
                btn.setVisible(visible)

    # ------------------------------------------------------------------
    # 拖拽排序
    # ------------------------------------------------------------------
    def eventFilter(self, obj, event: QEvent) -> bool:
        if obj == self._table.viewport() and event.type() == QEvent.Type.Drop:
            QTimer.singleShot(0, self._sync_order_after_drop)
        return super().eventFilter(obj, event)

    def _sync_order_after_drop(self) -> None:
        """拖拽完成后，按表格行序重建内部 task 列表并彻底重建 UI"""
        try:
            ordered_ids: list[str] = []
            for row in range(self._table.rowCount()):
                item = self._table.item(row, COL_SUMMARY)
                if item:
                    tid = item.data(Qt.ItemDataRole.UserRole)
                    if tid:
                        ordered_ids.append(tid)

            id_to_task: dict[str, Task] = {t.id: t for t in self._tasks}
            new_tasks: list[Task] = []
            for tid in ordered_ids:
                if tid in id_to_task:
                    new_tasks.append(id_to_task[tid])
            seen = set(ordered_ids)
            for t in self._tasks:
                if t.id not in seen:
                    new_tasks.append(t)
            self._tasks = new_tasks

            self._table.blockSignals(True)
            self._table.setRowCount(0)
            self.load_tasks(new_tasks)
            self._table.blockSignals(False)

            if ordered_ids:
                self.tasks_reordered.emit(ordered_ids)
        except Exception:
            pass
