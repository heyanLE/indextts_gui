"""配置 Tab 页 — 引擎 URL 配置 + 任务集管理 + 全局设置"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QGroupBox, QComboBox, QSpinBox, QFileDialog,
    QListWidget, QListWidgetItem, QMessageBox, QScrollArea,
)

from src.core.config_manager import ConfigManager
from src.engines import engine_registry
from src.engines.base_engine import BaseEngine


class _ConnectionTestThread(QThread):
    """Run a connection probe outside the GUI thread.

    A hard overall timeout is applied here because an engine probe may try
    several HTTP endpoints, each with its own timeout.
    """

    result_ready = Signal(bool, str)

    def __init__(
        self,
        engine: BaseEngine,
        url: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        self._url = url

    def run(self) -> None:
        import asyncio

        async def probe() -> tuple[bool, str]:
            return await asyncio.wait_for(
                self._engine.test_connection(self._url), timeout=15.0
            )

        try:
            success, message = asyncio.run(probe())
        except TimeoutError:
            success, message = False, "连接测试超时"
        except Exception as exc:
            success, message = False, f"连接失败: {exc}"
        self.result_ready.emit(success, message)


class ConfigTab(QWidget):
    """软件配置 Tab"""

    engine_url_changed = Signal(str, str)  # (engine_id, url)
    task_set_changed = Signal(str)         # new_path

    def __init__(self, config: ConfigManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._connection_tests: dict[str, _ConnectionTestThread] = {}
        self._taskset_commit_serial = 0

        self._setup_ui()
        self._load_config()

    def _setup_ui(self) -> None:
        """构建配置页面 UI"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        self._main_layout = QVBoxLayout(container)
        self._main_layout.setSpacing(16)
        self._main_layout.setContentsMargins(0, 0, 0, 0)

        # ─── 引擎配置 ───
        self._build_engine_section()
        # ─── 任务集管理 ───
        self._build_taskset_section()
        # ─── 全局设置 ───
        self._build_settings_section()

        self._main_layout.addStretch()
        scroll.setWidget(container)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # ==================================================================
    # 引擎配置区域
    # ==================================================================
    def _build_engine_section(self) -> None:
        group = QGroupBox("引擎配置")
        group.setObjectName("configCard")
        self._engine_layout = QVBoxLayout(group)
        self._engine_layout.setSpacing(12)

        self._engine_rows: dict[str, dict[str, QWidget]] = {}

        for engine in engine_registry.list_engines():
            self._add_engine_row(engine.meta.engine_id, engine.meta.engine_name)

        self._main_layout.addWidget(group)

    def _add_engine_row(self, engine_id: str, engine_name: str) -> None:
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)

        label = QLabel(engine_name)
        label.setMinimumWidth(100)
        label.setObjectName("engineLabel")
        row.addWidget(label)

        url_input = QLineEdit()
        url_input.setPlaceholderText(f"请输入 {engine_name} API 地址...")
        url_input.setObjectName("urlInput")
        row.addWidget(url_input, 1)

        test_btn = QPushButton("测试连接")
        test_btn.setObjectName("actionBtn")
        row.addWidget(test_btn)

        status_indicator = QLabel("○ 未连接")
        status_indicator.setObjectName("statusIndicator")
        row.addWidget(status_indicator)

        self._engine_layout.addWidget(row_widget)

        self._engine_rows[engine_id] = {
            "url": url_input,
            "test_btn": test_btn,
            "status": status_indicator,
        }

        # 绑定事件
        test_btn.clicked.connect(
            lambda checked, eid=engine_id: self._test_engine(eid)
        )
        url_input.editingFinished.connect(
            lambda eid=engine_id: self._save_engine_url(eid)
        )

    def _save_engine_url(self, engine_id: str) -> str:
        """Persist the URL shown in the editor and invalidate stale status."""
        row = self._engine_rows.get(engine_id)
        if not row:
            return ""
        url = row["url"].text().strip()
        previous = self._config.get_engine_url(engine_id)
        if url != previous:
            self._config.set_engine_url(engine_id, url)
            self._config.set_engine_connected(engine_id, False)
            row["status"].setText("○ 未连接")
            row["status"].setStyleSheet("")
            self.engine_url_changed.emit(engine_id, url)
        return url

    def _test_engine(self, engine_id: str) -> None:
        """测试引擎 API 连接"""
        row = self._engine_rows.get(engine_id)
        if not row:
            return

        if engine_id in self._connection_tests:
            return

        url = self._save_engine_url(engine_id)
        if not url:
            QMessageBox.warning(self, "提示", "请先输入 API 地址")
            return

        engine = engine_registry.get(engine_id)
        if engine is None:
            row["status"].setText("✕ 引擎不可用")
            row["status"].setStyleSheet("color: #C9190B;")
            return

        row["test_btn"].setText("检测中…")
        row["test_btn"].setEnabled(False)
        row["url"].setEnabled(False)
        row["status"].setText("⏳ 检测中…")
        row["status"].setStyleSheet("color: #D97706;")

        worker = _ConnectionTestThread(engine, url, self)
        self._connection_tests[engine_id] = worker
        worker.result_ready.connect(
            lambda success, msg, eid=engine_id, tested_url=url:
                self._on_connection_test_result(eid, tested_url, success, msg)
        )
        worker.finished.connect(
            lambda eid=engine_id, thread=worker:
                self._on_connection_test_finished(eid, thread)
        )
        worker.start()

    def _on_connection_test_result(
        self,
        engine_id: str,
        tested_url: str,
        success: bool,
        message: str,
    ) -> None:
        row = self._engine_rows.get(engine_id)
        if not row:
            return
        # Do not let an obsolete result mark a newly edited URL as connected.
        if row["url"].text().strip() != tested_url:
            return
        if success:
            row["status"].setText("● 已连接")
            row["status"].setStyleSheet("color: #3E8635; font-weight: bold;")
        else:
            row["status"].setText(f"✕ {message}")
            row["status"].setStyleSheet("color: #C9190B;")
        self._config.set_engine_connected(engine_id, success)

    def _on_connection_test_finished(
        self, engine_id: str, thread: _ConnectionTestThread
    ) -> None:
        if self._connection_tests.get(engine_id) is thread:
            self._connection_tests.pop(engine_id, None)
        row = self._engine_rows.get(engine_id)
        if row:
            row["test_btn"].setText("测试连接")
            row["test_btn"].setEnabled(True)
            row["url"].setEnabled(True)
        thread.deleteLater()

    def shutdown_connection_tests(self) -> None:
        """Wait for active probes so QThreads are never destroyed while running."""
        threads = list(self._connection_tests.values())
        for thread in threads:
            thread.requestInterruption()
        for thread in threads:
            thread.wait()

    # ==================================================================
    # 任务集管理区域
    # ==================================================================
    def _build_taskset_section(self) -> None:
        group = QGroupBox("任务集管理")
        group.setObjectName("configCard")
        layout = QVBoxLayout(group)
        layout.setSpacing(10)

        # 当前任务集路径
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("当前任务集:"))
        self._taskset_path = QLineEdit()
        self._taskset_path.setReadOnly(True)
        self._taskset_path.setPlaceholderText("未选择任务集...")
        path_row.addWidget(self._taskset_path, 1)

        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(self._browse_taskset)
        path_row.addWidget(browse_btn)
        layout.addLayout(path_row)

        # 历史任务集列表
        layout.addWidget(QLabel("历史任务集:"))
        self._taskset_list = QListWidget()
        self._taskset_list.setMaximumHeight(150)
        self._taskset_list.setObjectName("tasksetHistory")
        self._taskset_list.itemClicked.connect(self._open_taskset)
        layout.addWidget(self._taskset_list)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        new_btn = QPushButton("新建任务集")
        new_btn.setObjectName("primaryBtn")
        new_btn.clicked.connect(self._new_taskset)

        open_btn = QPushButton("打开任务集")
        open_btn.setObjectName("actionBtn")
        open_btn.clicked.connect(self._browse_taskset)

        del_btn = QPushButton("删除选中任务集")
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self._delete_taskset)

        btn_row.addWidget(new_btn)
        btn_row.addWidget(open_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._main_layout.addWidget(group)

    def _browse_taskset(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择任务集目录")
        if path:
            self._open_taskset_by_path(path)

    def _new_taskset(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择目录以创建任务集")
        if not path:
            return
        # MainWindow owns the transactional load/create operation. The direct
        # signal connection returns only after it either commits or rejects.
        commit_serial = self._taskset_commit_serial
        self.task_set_changed.emit(path)
        if self._taskset_commit_serial != commit_serial:
            QMessageBox.information(self, "成功", f"任务集已创建或打开: {path}")

    def _open_taskset(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self._open_taskset_by_path(path)

    def _delete_taskset(self) -> None:
        item = self._taskset_list.currentItem()
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        ret = QMessageBox.question(
            self, "确认", f"确定从历史列表中删除此任务集？\n{path}\n(不会删除磁盘文件)")
        if ret == QMessageBox.StandardButton.Yes:
            self._config.remove_recent_task_set(path)
            self._load_recent_tasksets()

    def _open_taskset_by_path(self, path: str) -> None:
        """Request a switch; do not mutate persisted state before load succeeds."""
        self.task_set_changed.emit(path)

    def commit_task_set(self, path: str) -> None:
        """Commit UI/config state after MainWindow loaded the task set."""
        normalized = str(Path(path).expanduser().resolve(strict=False))
        # The path display reflects the active in-memory session even if the
        # preference file cannot be updated (the caller reports that failure).
        self._taskset_path.setText(normalized)
        self._config.activate_task_set(normalized)
        self._load_recent_tasksets()
        self._taskset_commit_serial += 1

    # ==================================================================
    # 全局设置区域
    # ==================================================================
    def _build_settings_section(self) -> None:
        group = QGroupBox("全局设置")
        group.setObjectName("configCard")
        layout = QVBoxLayout(group)
        layout.setSpacing(10)

        # 队列间隔
        interval_row = QHBoxLayout()
        interval_row.addWidget(QLabel("队列间隔:"))
        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(1, 30)
        self._interval_spin.setSuffix(" 秒")
        interval_row.addWidget(self._interval_spin)
        interval_row.addStretch()
        layout.addLayout(interval_row)

        # 下载超时
        timeout_row = QHBoxLayout()
        timeout_row.addWidget(QLabel("生成/下载超时:"))
        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(30, 600)
        self._timeout_spin.setSuffix(" 秒")
        timeout_row.addWidget(self._timeout_spin)
        timeout_row.addStretch()
        layout.addLayout(timeout_row)

        # 保存按钮
        save_btn = QPushButton("保存设置")
        save_btn.setObjectName("primaryBtn")
        save_btn.clicked.connect(self._save_settings)
        layout.addWidget(save_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        self._main_layout.addWidget(group)

    def _save_settings(self) -> None:
        self._config.update_settings(
            queue_interval=self._interval_spin.value(),
            download_timeout=self._timeout_spin.value(),
        )
        QMessageBox.information(self, "成功", "设置已保存")

    # ==================================================================
    # 配置加载
    # ==================================================================
    def _load_config(self) -> None:
        """从 ConfigManager 加载所有配置到 UI"""
        # 引擎 URL
        for engine_id, row in self._engine_rows.items():
            url = self._config.get_engine_url(engine_id)
            row["url"].setText(url)
            cfg = self._config.get_engine_config(engine_id)
            if cfg.last_connected:
                row["status"].setText("● 已连接")
                row["status"].setStyleSheet("color: #3E8635; font-weight: bold;")

        # 任务集
        self._load_recent_tasksets()
        current = self._config.current_task_set_path
        if current:
            self._taskset_path.setText(current)

        # 全局设置
        s = self._config.settings
        self._interval_spin.setValue(s.queue_interval)
        self._timeout_spin.setValue(s.download_timeout)

    def _load_recent_tasksets(self) -> None:
        self._taskset_list.clear()
        for path in self._config.recent_task_sets:
            item = QListWidgetItem(Path(path).name)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self._taskset_list.addItem(item)

    def get_current_taskset_path(self) -> str:
        return self._taskset_path.text().strip()
