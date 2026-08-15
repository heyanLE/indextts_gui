"""批量导入对话框 — 输入多行文本，一行一个任务"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QComboBox, QPushButton, QGroupBox, QMessageBox, QSizePolicy,
)

from src.core.recipe import RecipeManager
from src.engines import engine_registry
from src.engines.base_engine import BaseEngine
from src.ui.engine_config_widget import EngineConfigWidget


class BatchImportDialog(QDialog):
    """批量导入任务对话框

    使用方式：
        dialog = BatchImportDialog(parent)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            tasks = dialog.get_tasks(taskset.next_task_id)
            for task in tasks:
                taskset.add_task(task)
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_engine: BaseEngine | None = None
        self._recipe_manager: RecipeManager | None = None
        self._recipe_load_suppress = False

        self.setWindowTitle("批量导入任务")
        self.setMinimumSize(680, 680)
        self.setModal(True)
        self._setup_ui()
        self._init_engines()

    def set_recipe_manager(self, manager: RecipeManager) -> None:
        """设置配方管理器引用"""
        self._recipe_manager = manager
        self._refresh_recipe_combo()

    def refresh_recipes(self) -> None:
        """外部通知配方列表变更"""
        self._refresh_recipe_combo()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # ── 文案输入 ──
        text_group = QGroupBox("批量文案（一行 = 一个任务）")
        tv = QVBoxLayout(text_group)
        tv.setContentsMargins(10, 14, 10, 10)

        hint = QLabel("每行一条文案，空行将被自动过滤。")
        hint.setStyleSheet("color: #6B7280; font-size: 12px;")
        tv.addWidget(hint)

        self._text_edit = QTextEdit()
        self._text_edit.setPlaceholderText(
            "请输入目标文案，每行一个任务...\n\n例如：\n今天天气真不错\n明天会更好\n\n"
        )
        self._text_edit.setMinimumHeight(160)
        self._text_edit.textChanged.connect(self._on_text_changed)
        tv.addWidget(self._text_edit)

        # 行数预览
        self._preview_label = QLabel("已识别: 0 条文案")
        self._preview_label.setStyleSheet("color: #2196F3; font-weight: bold;")
        tv.addWidget(self._preview_label)

        layout.addWidget(text_group)

        # ── 配方选择 ──
        recipe_row = QHBoxLayout()
        recipe_row.setSpacing(6)
        recipe_row.addWidget(QLabel("配方:"))
        self._recipe_combo = QComboBox()
        self._recipe_combo.currentIndexChanged.connect(self._on_recipe_selected)
        recipe_row.addWidget(self._recipe_combo, 1)
        layout.addLayout(recipe_row)

        # ── 引擎选择 ──
        engine_row = QHBoxLayout()
        engine_row.addWidget(QLabel("生成引擎:"))
        self._engine_combo = QComboBox()
        self._engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        engine_row.addWidget(self._engine_combo, 1)
        layout.addLayout(engine_row)

        # ── 引擎参数配置（动态表单） ──
        self._engine_config = EngineConfigWidget()
        self._engine_config.setTitle("引擎参数配置（所有任务共用）")
        self._engine_config.params_changed.connect(self._check_recipe_match)
        self._engine_config.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        layout.addWidget(self._engine_config, 1)

        # ── 按钮 ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("actionBtn")
        cancel_btn.clicked.connect(self.reject)

        self._import_btn = QPushButton("批量导入")
        self._import_btn.setObjectName("primaryBtn")
        self._import_btn.clicked.connect(self._on_import)
        self._import_btn.setEnabled(False)

        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self._import_btn)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    # 引擎列表
    # ------------------------------------------------------------------
    def _init_engines(self) -> None:
        self._engine_combo.blockSignals(True)
        self._engine_combo.clear()
        for engine in engine_registry.list_engines():
            self._engine_combo.addItem(
                engine.meta.engine_name, engine.meta.engine_id
            )
        self._engine_combo.blockSignals(False)
        if self._engine_combo.count() > 0:
            self._engine_combo.setCurrentIndex(0)
            self._on_engine_changed(0)

    def _on_engine_changed(self, index: int) -> None:
        """引擎下拉切换 → 重建参数表单"""
        if self._recipe_load_suppress:
            return
        engine_id = self._engine_combo.currentData()
        engine = engine_registry.get(engine_id)
        if engine:
            self._current_engine = engine
            self._engine_config.set_schema(engine.get_param_schema())
        else:
            self._current_engine = None
            self._engine_config.set_schema([])
        # 引擎变更时检测配方匹配
        self._check_recipe_match()

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
        """选择配方 → 切换引擎 + 加载参数"""
        if index <= 0:  # 自定义
            return

        recipe_id = self._recipe_combo.itemData(index)
        if not recipe_id or not self._recipe_manager:
            return

        recipe = self._recipe_manager.get(recipe_id)
        if not recipe:
            return

        self._recipe_load_suppress = True
        try:
            # 切换引擎
            eng_idx = self._engine_combo.findData(recipe.engine)
            if eng_idx >= 0:
                self._engine_combo.setCurrentIndex(eng_idx)

            # 加载引擎 schema 并设置参数
            engine = engine_registry.get(recipe.engine)
            if engine:
                self._current_engine = engine
                self._engine_config.set_schema(engine.get_param_schema())
                self._engine_config.set_params(recipe.engine_params)
        finally:
            self._recipe_load_suppress = False

    def _check_recipe_match(self) -> None:
        """检查当前引擎+参数是否匹配某个配方，不匹配则切回自定义"""
        if not self._recipe_manager:
            return
        current_engine_id = self._engine_combo.currentData() or ""
        current_params = self._engine_config.get_params()

        matched_id = None
        for recipe in self._recipe_manager.list_all():
            if recipe.engine == current_engine_id:
                from src.core.recipe import _params_equal
                if _params_equal(recipe.engine_params, current_params):
                    matched_id = recipe.id
                    break

        self._recipe_combo.blockSignals(True)
        if matched_id:
            idx = self._recipe_combo.findData(matched_id)
            if idx >= 0:
                self._recipe_combo.setCurrentIndex(idx)
        else:
            self._recipe_combo.setCurrentIndex(0)
        self._recipe_combo.blockSignals(False)

    # ------------------------------------------------------------------
    # 文案解析
    # ------------------------------------------------------------------
    def _on_text_changed(self) -> None:
        lines = self._parse_lines()
        self._preview_label.setText(f"已识别: {len(lines)} 条文案")
        self._import_btn.setEnabled(len(lines) > 0)

    def _parse_lines(self) -> list[str]:
        """解析文本区域中的有效文案行"""
        raw = self._text_edit.toPlainText()
        return [
            line.strip()
            for line in raw.splitlines()
            if line.strip()
        ]

    # ------------------------------------------------------------------
    # 导入
    # ------------------------------------------------------------------
    def _on_import(self) -> None:
        lines = self._parse_lines()
        if not lines:
            QMessageBox.warning(self, "提示", "文案不能为空，请至少输入一行文案。")
            return

        # 校验引擎参数（批量模式下 text 来自文案行，不在此处校验）
        params = self.get_engine_params()
        params["text"] = "placeholder"  # 仅供校验通过，实际文案由各行指定
        if self._current_engine:
            errors = self._current_engine.validate_params(params)
            errors = [e for e in errors if "文案" not in e]  # 批量导入不校验文案为空
            if errors:
                QMessageBox.warning(self, "参数校验失败", "\n".join(errors))
                return

        self.accept()

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------
    def get_lines(self) -> list[str]:
        """获取解析后的文案列表"""
        return self._parse_lines()

    def get_engine_id(self) -> str:
        """获取当前选择的引擎 ID"""
        return self._engine_combo.currentData() or ""

    def get_engine_params(self) -> dict:
        """获取当前引擎参数"""
        return self._engine_config.get_params()

    def get_tasks(self, next_id_func) -> list:
        """生成 Task 对象列表

        Args:
            next_id_func: 每次调用返回下一个 task_id 的可调用对象
        """
        from src.core.task import Task

        lines = self._parse_lines()
        engine_id = self.get_engine_id()
        base_params = self.get_engine_params()

        tasks = []
        for line in lines:
            task_params = dict(base_params)
            task_params["text"] = line
            task = Task(
                id=next_id_func(),
                text=line,
                engine=engine_id,
                engine_params=task_params,
            )
            tasks.append(task)
        return tasks
