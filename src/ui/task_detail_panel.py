"""任务详情面板 — 右侧详情区域"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTextEdit, QComboBox, QPushButton, QGroupBox,
    QMessageBox, QFileDialog, QSizePolicy, QInputDialog,
)

from src.core.task import Task, TaskStatus
from src.core.recipe import Recipe, RecipeManager, _params_equal
from src.engines import engine_registry
from src.engines.base_engine import BaseEngine
from src.ui.engine_config_widget import EngineConfigWidget


class TaskDetailPanel(QWidget):
    """任务详情面板"""

    task_saved = Signal(Task)         # 自动保存通知
    task_deleted = Signal(str)        # 删除任务 (task_id)
    play_ref_audio = Signal(str)      # 播放参考音频
    play_output = Signal(str)         # 播放输出音频
    recipe_saved = Signal(Recipe)     # 保存配方通知（通知配方页刷新）

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_task: Task | None = None
        self._current_engine: BaseEngine | None = None
        self._recipe_manager: RecipeManager | None = None
        self._last_engine_id: str = ""  # 缓存当前引擎 ID，避免重复重建
        self._has_pending_changes = False

        # ⏱ 防抖定时器：参数变更后 400ms 才真正保存，避免每次按键都写磁盘
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._do_deferred_save)

        self._setup_ui()

    def set_recipe_manager(self, manager: RecipeManager) -> None:
        """设置配方管理器引用"""
        self._recipe_manager = manager
        self._refresh_recipe_combo()

    def refresh_recipes(self) -> None:
        """外部通知配方列表变更"""
        self._refresh_recipe_combo()
        self._check_recipe_match()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── 标题 ──
        self._title_label = QLabel("📝 任务详情 — 未选中")
        self._title_label.setObjectName("detailTitle")
        layout.addWidget(self._title_label)

        # ── 文案编辑 ──
        text_group = QGroupBox("文案内容")
        tl = QVBoxLayout(text_group)
        self._text_edit = QTextEdit()
        self._text_edit.setPlaceholderText("请输入目标文案…")
        self._text_edit.setMaximumHeight(100)
        self._text_edit.textChanged.connect(self._on_params_dirty)
        tl.addWidget(self._text_edit)
        layout.addWidget(text_group)

        # ── 配方选择 ──
        recipe_row = QHBoxLayout()
        recipe_row.setSpacing(6)
        recipe_row.addWidget(QLabel("配方:"))
        self._recipe_combo = QComboBox()
        self._recipe_combo.setMinimumWidth(160)
        self._recipe_combo.currentIndexChanged.connect(self._on_recipe_selected)
        recipe_row.addWidget(self._recipe_combo, 1)

        self._save_recipe_btn = QPushButton("保存为配方")
        self._save_recipe_btn.setObjectName("actionBtn")
        self._save_recipe_btn.clicked.connect(self._on_save_recipe)
        self._save_recipe_btn.setMinimumWidth(90)
        recipe_row.addWidget(self._save_recipe_btn)
        layout.addLayout(recipe_row)

        # ── 引擎选择 ──
        engine_row = QHBoxLayout()
        engine_row.addWidget(QLabel("生成引擎:"))
        self._engine_combo = QComboBox()
        self._engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        self._engine_combo.currentTextChanged.connect(self._on_params_dirty)
        engine_row.addWidget(self._engine_combo, 1)
        layout.addLayout(engine_row)

        # ── 引擎参数配置（动态表单） ──
        self._engine_config = EngineConfigWidget()
        self._engine_config.params_changed.connect(self._on_params_dirty)
        self._engine_config.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        layout.addWidget(self._engine_config, 1)

        # ── 状态信息 ──
        self._status_label = QLabel("状态: —")
        self._status_label.setObjectName("statusInfo")
        layout.addWidget(self._status_label)

        self._output_label = QLabel("输出: —")
        self._output_label.setObjectName("outputInfo")
        self._output_label.setWordWrap(True)
        layout.addWidget(self._output_label)

        self._error_label = QLabel("")
        self._error_label.setObjectName("errorInfo")
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet("color: #C9190B;")
        self._error_label.setVisible(False)
        layout.addWidget(self._error_label)

        # ── 操作按钮 ──
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self._play_ref_btn = QPushButton("▶ 试听参考音频")
        self._play_ref_btn.setObjectName("actionBtn")

        self._play_output_btn = QPushButton("▶ 试听生成音频")
        self._play_output_btn.setObjectName("successBtn")

        self._restore_btn = QPushButton("↩ 回溯结果入参")
        self._restore_btn.setObjectName("actionBtn")
        self._restore_btn.setToolTip("将当前入参恢复为生成该音频时使用的参数")
        self._restore_btn.clicked.connect(self._on_restore_config)
        self._restore_btn.setVisible(False)

        self._del_btn = QPushButton("🗑 删除")
        self._del_btn.setObjectName("dangerBtn")
        self._del_btn.clicked.connect(self._on_delete)

        self._lock_btn = QPushButton("🔒 锁定")
        self._lock_btn.setObjectName("actionBtn")
        self._lock_btn.setCheckable(True)
        self._lock_btn.clicked.connect(self._on_toggle_lock)
        self._lock_btn.setVisible(False)

        btn_layout.addWidget(self._play_ref_btn)
        btn_layout.addWidget(self._play_output_btn)
        btn_layout.addWidget(self._restore_btn)
        btn_layout.addWidget(self._lock_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self._del_btn)
        layout.addLayout(btn_layout)

        layout.addStretch()
        self.setEnabled(False)
        self._auto_save_suppress = False  # 加载参数时抑制自动保存

    # ------------------------------------------------------------------
    # 引擎列表初始化
    # ------------------------------------------------------------------
    def init_engines(self) -> None:
        """初始化引擎下拉列表

        注意：调用方（set_task_set）已对 _engine_combo 做了
        blockSignals(True)，此处不再重复阻塞/解除，避免意外解除
        外层保护导致后续 clear() 触发引擎 schema 重建成顶级窗口。
        """
        self._engine_combo.clear()
        for engine in engine_registry.list_engines():
            self._engine_combo.addItem(
                engine.meta.engine_name, engine.meta.engine_id
            )

    # ------------------------------------------------------------------
    # 加载任务
    # ------------------------------------------------------------------
    def load_task(self, task: Task | None) -> None:
        """加载任务到详情面板"""
        self._flush_save()  # 先持久化当前编辑，避免切换时丢失防抖内容

        self._current_task = task

        if task is None:
            self.clear()
            return

        # Task files created before the language selector existed have no
        # language key.  Populate Japanese text containing kana as JA before
        # the form is shown, so the visible value is also the saved value.
        migrated_language = False
        if task.engine == "indextts" and "language" not in task.engine_params:
            has_kana = any(
                "\u3040" <= char <= "\u30ff" for char in task.text
            )
            task.engine_params["language"] = "JA" if has_kana else "ZH"
            migrated_language = True

        self._auto_save_suppress = True  # 加载时抑制自动保存
        self.setEnabled(True)
        self._title_label.setText(f"📝 任务详情 — {task.id}")

        # 文案
        self._text_edit.blockSignals(True)
        self._text_edit.setPlainText(task.text)
        self._text_edit.blockSignals(False)

        # 引擎选择（阻断信号避免重复触发 _on_engine_changed）
        self._engine_combo.blockSignals(True)
        idx = self._engine_combo.findData(task.engine)
        if idx >= 0:
            self._engine_combo.setCurrentIndex(idx)
        else:
            self._engine_combo.setCurrentIndex(0)
        self._engine_combo.blockSignals(False)

        # 引擎参数（仅引擎变更时才重建表单）
        self._load_engine_schema(force=task.engine != self._last_engine_id)

        # 参数回填
        self._engine_config.set_params(task.engine_params)

        # 状态显示
        self._update_status_display()

        # 配方匹配检测
        self._check_recipe_match()

        self._auto_save_suppress = False
        if migrated_language:
            self.task_saved.emit(task)

    def clear(self) -> None:
        """清空详情面板"""
        self._save_timer.stop()  # 取消任何待处理的防抖保存
        self._has_pending_changes = False
        self._last_engine_id = ""
        self._current_task = None
        self.setEnabled(False)
        self._title_label.setText("📝 任务详情 — 未选中")
        self._text_edit.clear()
        self._engine_combo.setCurrentIndex(0)
        self._engine_config.clear()
        self._status_label.setText("状态: —")
        self._output_label.setText("输出: —")
        self._error_label.setVisible(False)

    # ------------------------------------------------------------------
    # 引擎切换
    # ------------------------------------------------------------------
    def _on_engine_changed(self, index: int) -> None:
        self._load_engine_schema(force=True)

    def _load_engine_schema(self, force: bool = False) -> None:
        """加载引擎参数 schema。仅当引擎 ID 变更或 force=True 时才重建表单。"""
        engine_id = self._engine_combo.currentData()
        if not isinstance(engine_id, str):
            engine_id = str(engine_id) if engine_id else ""

        # 引擎未变 → 跳过重建，只刷新可见性
        if not force and engine_id == self._last_engine_id:
            # 但用户手动切换引擎时应还是走 force=True 路径
            engine = engine_registry.get(engine_id)
            if engine:
                self._current_engine = engine
                if self._current_task:
                    self._engine_config.set_params(self._current_task.engine_params)
                self._update_play_ref_btn()
            return

        self._last_engine_id = engine_id
        engine = engine_registry.get(engine_id)
        if engine:
            self._current_engine = engine
            self._engine_config.set_schema(engine.get_param_schema())
            # 加载当前 task 的参数
            if self._current_task:
                self._engine_config.set_params(self._current_task.engine_params)
            self._update_play_ref_btn()
        else:
            self._engine_config.set_schema([])

    def _update_play_ref_btn(self) -> None:
        """根据引擎决定是否显示试听参考音频按钮"""
        has_ref_audio = False
        if self._current_engine:
            schema = self._current_engine.get_param_schema()
            has_ref_audio = any(
                f.name in ("reference_audio", "emotion_audio") and f.field_type == "file"
                for f in schema
            )
        self._play_ref_btn.setVisible(has_ref_audio)

    # ------------------------------------------------------------------
    # 状态显示
    # ------------------------------------------------------------------
    def _update_status_display(self) -> None:
        if not self._current_task:
            return

        task = self._current_task
        status = task.status

        status_colors: dict[TaskStatus, str] = {
            TaskStatus.PENDING: "#6A6E73",
            TaskStatus.QUEUED: "#0066CC",
            TaskStatus.GENERATING: "#EE0000",
            TaskStatus.COMPLETED: "#3E8635",
            TaskStatus.FAILED: "#C9190B",
        }
        color = status_colors.get(status, "#9E9E9E")

        self._status_label.setText(f"状态: <span style='color:{color};font-weight:bold;'>{status.value}</span>")
        self._status_label.setTextFormat(Qt.TextFormat.RichText)

        # 输出文件
        if task.output_audio_path:
            self._output_label.setText(f"输出: {task.output_audio_path}")
        else:
            self._output_label.setText("输出: —")

        # 错误信息
        if task.error_message and status == TaskStatus.FAILED:
            self._error_label.setText(f"错误: {task.error_message}")
            self._error_label.setVisible(True)
        else:
            self._error_label.setVisible(False)

        # 锁定按钮（仅在完成态显示）
        is_completed = status == TaskStatus.COMPLETED
        self._lock_btn.setVisible(is_completed)
        if is_completed:
            self._lock_btn.setChecked(task.locked)
            self._lock_btn.setText("🔓 解锁" if task.locked else "🔒 锁定")

        # 按钮状态控制（locked 仅对完成态生效）
        locked = task.locked and is_completed
        read_only = locked or not task.can_edit()
        self._text_edit.setReadOnly(read_only)
        self._engine_combo.setEnabled(not read_only)
        self._recipe_combo.setEnabled(not read_only)
        self._save_recipe_btn.setEnabled(not read_only)
        self._engine_config.setEnabled(not read_only)
        # 锁定后禁用加锁按钮（通过它自身可切换）
        self._lock_btn.setEnabled(True)  # 始终可点击来解锁
        # 但如任务非可编辑状态（队列中/生成中），锁定按钮也应禁用
        if not task.can_edit():
            self._lock_btn.setEnabled(False)

        has_output = task.output_audio_path is not None
        self._play_output_btn.setEnabled(has_output)

        # 回溯按钮：仅在任务完成且有 generation_config 时显示
        self._restore_btn.setVisible(
            status == TaskStatus.COMPLETED and task.generation_config is not None
        )

    # ------------------------------------------------------------------
    # 自动保存
    # ------------------------------------------------------------------
    def _on_params_dirty(self) -> None:
        """参数变更时启动防抖保存（抑制加载期间的触发）"""
        if getattr(self, '_auto_save_suppress', False):
            return
        if not self._current_task or not self._current_task.can_edit():
            return
        self._has_pending_changes = True
        # ⏱ 启动 400ms 防抖定时器，连续输入时只在停止输入后才真正保存
        self._save_timer.start()

    def _do_deferred_save(self) -> None:
        """防抖定时器触发 → 真正的保存 + 通知"""
        if not self._current_task or not self._current_task.can_edit():
            return
        self._save_to_task()
        self._has_pending_changes = False
        self.task_saved.emit(self._current_task)
        self._check_recipe_match()

    def _flush_save(self) -> None:
        """立即保存（切换任务前调用，不等待防抖）"""
        self._save_timer.stop()
        if not self._has_pending_changes:
            return
        if not self._current_task or not self._current_task.can_edit():
            # The model changed state externally; the following load will make
            # the model authoritative, so do not retain a stale dirty flag.
            self._has_pending_changes = False
            return
        self._save_to_task()
        self._has_pending_changes = False
        self.task_saved.emit(self._current_task)

    def flush_pending_save(self) -> None:
        """Public lifecycle hook used before task-set switches and app exit."""
        self._flush_save()

    def _check_recipe_match(self) -> None:
        """检查当前参数是否匹配某个配方，不匹配则切换为自定义"""
        if not self._recipe_manager or not self._current_task:
            return
        task = self._current_task
        current_params = self._engine_config.get_params()

        # 遍历配方查找匹配
        matched_id = None
        for recipe in self._recipe_manager.list_all():
            if recipe.engine == task.engine and _params_equal(recipe.engine_params, current_params):
                matched_id = recipe.id
                break

        # 更新下拉框选择
        self._recipe_combo.blockSignals(True)
        if matched_id:
            idx = self._recipe_combo.findData(matched_id)
            if idx >= 0:
                self._recipe_combo.setCurrentIndex(idx)
        else:
            # 设置为自定义
            self._recipe_combo.setCurrentIndex(0)  # 第一项是自定义
        self._recipe_combo.blockSignals(False)

    def _save_to_task(self) -> None:
        """将 UI 控件值写回内存中的 Task 对象"""
        if not self._current_task:
            return
        task = self._current_task
        task.text = self._text_edit.toPlainText().strip()
        task.engine = self._engine_combo.currentData() or ""
        task.engine_params = self._engine_config.get_params()
        task.engine_params["text"] = task.text

    # ------------------------------------------------------------------
    # 配方操作
    # ------------------------------------------------------------------
    def _refresh_recipe_combo(self) -> None:
        """刷新配方下拉框"""
        self._recipe_combo.blockSignals(True)
        self._recipe_combo.clear()

        # 第一项：(自定义)
        self._recipe_combo.addItem("（自定义）", None)
        self._recipe_combo.setItemData(0, Qt.GlobalColor.gray, Qt.ItemDataRole.ForegroundRole)

        if self._recipe_manager:
            for recipe in self._recipe_manager.list_all():
                label = f"{recipe.name} [{recipe.engine}]"
                self._recipe_combo.addItem(label, recipe.id)

        self._recipe_combo.blockSignals(False)

    def _on_recipe_selected(self, index: int) -> None:
        """配方选择变更"""
        if index <= 0:  # 自定义
            return

        recipe_id = self._recipe_combo.itemData(index)
        if not recipe_id or not self._recipe_manager or not self._current_task:
            return

        recipe = self._recipe_manager.get(recipe_id)
        if not recipe:
            return

        self._auto_save_suppress = True

        # 切换引擎（阻断信号避免级联，手动 force 重建一次）
        self._engine_combo.blockSignals(True)
        eng_idx = self._engine_combo.findData(recipe.engine)
        if eng_idx >= 0:
            self._engine_combo.setCurrentIndex(eng_idx)
        self._engine_combo.blockSignals(False)

        # 替换全部参数
        self._load_engine_schema(force=True)
        self._engine_config.set_params(recipe.engine_params)

        self._auto_save_suppress = False
        self._on_params_dirty()

    def _on_save_recipe(self) -> None:
        """保存当前参数为配方"""
        if not self._current_task or not self._recipe_manager:
            return

        name, ok = QInputDialog.getText(
            self, "保存为配方", "请输入配方名称:",
            QLineEdit.EchoMode.Normal, ""
        )
        if not ok or not name.strip():
            return

        recipe = Recipe(
            id="",
            name=name.strip(),
            # The combo box is the source of truth while a debounced save may
            # still be pending after the user changed engines.
            engine=self._engine_combo.currentData() or self._current_task.engine,
            engine_params=self._engine_config.get_params(),
        )
        self._recipe_manager.add(recipe)
        self._refresh_recipe_combo()
        self.recipe_saved.emit(recipe)

        # 选中刚保存的配方
        idx = self._recipe_combo.findData(recipe.id)
        if idx >= 0:
            self._recipe_combo.setCurrentIndex(idx)

    # ------------------------------------------------------------------
    # 按钮事件
    # ------------------------------------------------------------------
    def _on_toggle_lock(self) -> None:
        """切换任务锁定状态"""
        if not self._current_task:
            return
        if self._current_task.status != TaskStatus.COMPLETED:
            self._update_status_display()
            return
        self._current_task.locked = self._lock_btn.isChecked()
        self._update_status_display()
        self.task_saved.emit(self._current_task)

    def _on_restore_config(self) -> None:
        """回溯到生成该音频时的入参"""
        if not self._current_task or not self._current_task.generation_config:
            return
        gc = self._current_task.generation_config
        self._auto_save_suppress = True

        # 恢复文案
        self._text_edit.setPlainText(gc.get("text", ""))
        # 恢复引擎（阻断信号，手动 force 重建）
        engine_id = gc.get("engine", "indextts")
        self._engine_combo.blockSignals(True)
        idx = self._engine_combo.findData(engine_id)
        if idx >= 0:
            self._engine_combo.setCurrentIndex(idx)
        self._engine_combo.blockSignals(False)
        # 恢复引擎参数
        params = gc.get("engine_params", {})
        self._load_engine_schema(force=True)
        self._engine_config.set_params(params)

        self._auto_save_suppress = False
        # 触发一次自动保存
        self._on_params_dirty()

    def _on_delete(self) -> None:
        if not self._current_task:
            return
        ret = QMessageBox.question(
            self, "确认删除",
            f"确定删除任务 {self._current_task.id} 吗？\n此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret == QMessageBox.StandardButton.Yes:
            tid = self._current_task.id
            self.task_deleted.emit(tid)
