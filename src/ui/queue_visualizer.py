"""队列可视化组件 — 横向 QScrollArea 展示排队任务胶囊"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QScrollArea, QSizePolicy,
)

from src.core.task import TaskStatus
from src.ui.spinner_widget import SpinnerWidget


class _QueueCapsule(QWidget):
    """单个任务胶囊 — 支持 loading 动画"""

    def __init__(
        self,
        task_id: str,
        text: str,
        status: TaskStatus,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.task_id = task_id
        self._status = status
        self.setFixedSize(150, 32)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setToolTip(text)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(8, 2, 8, 2)
        self._layout.setSpacing(4)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Spinner（仅 GENERATING 时显示）
        self._spinner = SpinnerWidget(size=14, color="white", line_width=2)
        self._spinner.stop()
        self._spinner.setVisible(False)
        self._layout.addWidget(self._spinner)

        # 文案标签
        display = text[:18] + "\u2026" if len(text) > 18 else text
        self._label = QLabel(display)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(self._label)

        self._apply_style()

    def _apply_style(self) -> None:
        if self._status == TaskStatus.GENERATING:
            bg = "#EE0000"
            fg = "white"
            border = "#EE0000"
            weight = "bold"
        elif self._status == TaskStatus.QUEUED:
            bg = "#F5F5F5"
            fg = "#0066CC"
            border = "#0066CC"
            weight = "500"
        else:
            bg = "#E8E8E8"
            fg = "#888888"
            border = "#D2D2D2"
            weight = "normal"

        self.setStyleSheet(
            f"_QueueCapsule {{"
            f" background-color: {bg};"
            f" border: 1px solid {border}; border-radius: 16px;"
            f" }}"
        )
        self._label.setStyleSheet(
            f"QLabel {{"
            f" color: {fg}; font-size: 12px; font-weight: {weight};"
            f" background: transparent; border: none;"
            f" }}"
        )

        # 控制 Spinner 显隐
        if self._status == TaskStatus.GENERATING:
            self._spinner.setVisible(True)
            self._spinner.start()
        else:
            self._spinner.stop()
            self._spinner.setVisible(False)

    def update_status(self, status: TaskStatus) -> None:
        self._status = status
        self._apply_style()


class QueueVisualizer(QScrollArea):
    """横向队列可视化器

    展示排队中任务的胶囊列表，首个高亮为生成中（红色），
    其余为队列中（蓝色边框），已完成/失败后自动移除。
    """

    visibility_changed = Signal(bool)  # 通知外部容器是否为空

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("queueVisualizer")
        self.setFixedHeight(48)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # 内部容器
        self._container = QWidget()
        self._container.setObjectName("queueContainer")
        self._layout = QHBoxLayout(self._container)
        self._layout.setContentsMargins(8, 6, 8, 6)
        self._layout.setSpacing(8)
        self._layout.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._layout.addStretch()
        self.setWidget(self._container)

        self._capsules: dict[str, _QueueCapsule] = {}
        self._visible: bool = False
        # Map IDs to the exact capsule scheduled for removal. A task can be
        # re-queued with the same ID before the animation delay expires; a
        # stale timer must never remove that new capsule.
        self._pending_removes: dict[str, _QueueCapsule] = {}

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def set_tasks(self, tasks: list[dict]) -> None:
        """批量设置队列任务

        参数:
            tasks: list[dict], 每个 dict 包含 id, text, status
        """
        self.clear()
        if not tasks:
            self._set_visible(False)
            return
        for t in tasks:
            self.add_task(
                t["id"], t.get("text", ""), t.get("status", TaskStatus.QUEUED)
            )
        self._set_visible(True)

    def add_task(self, task_id: str, text: str, status: TaskStatus = TaskStatus.QUEUED) -> None:
        """添加一个任务胶囊"""
        if task_id in self._capsules:
            capsule = self._capsules[task_id]
            self._pending_removes.pop(task_id, None)
            capsule.update_status(status)
            self._set_visible(True)
            return
        # 在 stretch 前插入
        capsule = _QueueCapsule(task_id, text, status)
        self._layout.insertWidget(self._layout.count() - 1, capsule)
        self._capsules[task_id] = capsule
        self._set_visible(True)

    def update_status(self, task_id: str, status: TaskStatus) -> None:
        """更新任务状态显示"""
        capsule = self._capsules.get(task_id)
        if not capsule:
            return
        capsule.update_status(status)
        if status == TaskStatus.GENERATING:
            # 提升为第一个
            idx = self._layout.indexOf(capsule)
            if idx > 0:
                self._layout.removeWidget(capsule)
                self._layout.insertWidget(0, capsule)

    def remove_task(self, task_id: str) -> None:
        """移除任务胶囊（延迟动画后执行）"""
        if task_id not in self._capsules:
            return
        capsule = self._capsules[task_id]
        self._pending_removes[task_id] = capsule
        QTimer.singleShot(600, lambda: self._do_remove(task_id, capsule))

    def _do_remove(self, task_id: str, expected: _QueueCapsule) -> None:
        if self._pending_removes.get(task_id) is not expected:
            return
        self._pending_removes.pop(task_id, None)
        if self._capsules.get(task_id) is not expected:
            return
        capsule = self._capsules.pop(task_id, None)
        if capsule:
            self._layout.removeWidget(capsule)
            capsule.deleteLater()
        if not self._capsules:
            self._set_visible(False)

    def clear(self) -> None:
        """清空所有胶囊"""
        for capsule in list(self._capsules.values()):
            self._layout.removeWidget(capsule)
            capsule.deleteLater()
        self._capsules.clear()
        self._pending_removes.clear()
        self._set_visible(False)

    def _set_visible(self, visible: bool) -> None:
        if self._visible == visible:
            return
        self._visible = visible
        self.setVisible(visible)
        self.visibility_changed.emit(visible)
