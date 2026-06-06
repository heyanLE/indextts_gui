"""配置 Tab 页 — 引擎 URL 配置 + 任务集管理 + 全局设置"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QGroupBox, QComboBox, QSpinBox, QFileDialog,
    QListWidget, QListWidgetItem, QMessageBox, QScrollArea,
)

from src.core.config_manager import ConfigManager
from src.engines import engine_registry


class ConfigTab(QWidget):
    """软件配置 Tab"""

    engine_url_changed = Signal(str, str)  # (engine_id, url)
    task_set_changed = Signal(str)         # new_path

    def __init__(self, config: ConfigManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config

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

    def _test_engine(self, engine_id: str) -> None:
        """测试引擎 API 连接"""
        row = self._engine_rows.get(engine_id)
        if not row:
            return

        url = row["url"].text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请先输入 API 地址")
            return

        row["test_btn"].setText("检测中…")
        row["test_btn"].setEnabled(False)
        row["status"].setText("⏳ 检测中…")
        row["status"].setStyleSheet("color: #D97706;")

        self._config.set_engine_url(engine_id, url)

        engine = engine_registry.get(engine_id)
        if engine is None:
            return

        import asyncio

        async def do_test():
            return await engine.test_connection(url)

        try:
            loop = asyncio.new_event_loop()
            success, msg = loop.run_until_complete(do_test())
            loop.close()

            if success:
                row["status"].setText("● 已连接")
                row["status"].setStyleSheet("color: #3E8635; font-weight: bold;")
                self._config.set_engine_connected(engine_id, True)
            else:
                row["status"].setText(f"✕ {msg}")
                row["status"].setStyleSheet("color: #C9190B;")
                self._config.set_engine_connected(engine_id, False)

        except Exception as e:
            row["status"].setText(f"✕ 连接失败")
            row["status"].setStyleSheet("color: #C9190B;")

        finally:
            row["test_btn"].setText("测试连接")
            row["test_btn"].setEnabled(True)

        self.engine_url_changed.emit(engine_id, url)

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

        from src.core.taskset import TaskSet

        dir_path = Path(path)
        name = dir_path.name or "voice_project"
        ts = TaskSet.create(name, dir_path)
        self._add_recent_and_switch(str(dir_path))
        QMessageBox.information(self, "成功", f"任务集已创建: {dir_path}")

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
        self._taskset_path.setText(path)
        self._add_recent_and_switch(path)
        self.task_set_changed.emit(path)

    def _add_recent_and_switch(self, path: str) -> None:
        self._config.add_recent_task_set(path)
        self._config.current_task_set_path = path
        self._config.save()
        self._load_recent_tasksets()

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
        timeout_row.addWidget(QLabel("下载超时:"))
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
