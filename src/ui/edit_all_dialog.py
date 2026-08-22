"""编辑全部对话框 — 批量修改选中任务的共用配置"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QComboBox, QPushButton, QGroupBox, QMessageBox, QSizePolicy,
    QCheckBox, QWidget,
)

from src.core.recipe import Recipe, RecipeManager, _params_equal
from src.engines import engine_registry
from src.engines.base_engine import BaseEngine
from src.ui.engine_config_widget import EngineConfigWidget


class EditAllDialog(QDialog):
    """批量编辑选中任务配置的对话框

    使用方式：
        dialog = EditAllDialog(parent)
        dialog.set_recipe_manager(recipe_manager)
        dialog.set_initial_params(first_task.engine, first_task.engine_params, first_task.text)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            engine_id, engine_params, update_text, new_text = dialog.get_result()
            for task in selected_tasks:
                task.engine = engine_id
                task.engine_params = dict(engine_params)
                task.engine_params["text"] = task.text
                if update_text:
                    task.text = new_text
                    task.engine_params["text"] = new_text
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_engine: BaseEngine | None = None
        self._recipe_manager: RecipeManager | None = None
        self._recipe_load_suppress = False
        self._initial_engine_id = "indextts"
        self._initial_params: dict = {}

        self.setWindowTitle("编辑全部选中任务")
        self.setMinimumSize(640, 620)
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

    def set_initial_params(self, engine_id: str, engine_params: dict, text: str) -> None:
        """预填充参数（通常来自第一个选中任务）"""
        self._initial_engine_id = engine_id
        self._initial_params = dict(engine_params)

        # 引擎选择
        idx = self._engine_combo.findData(engine_id)
        if idx >= 0:
            self._engine_combo.blockSignals(True)
            self._engine_combo.setCurrentIndex(idx)
            self._engine_combo.blockSignals(False)

        # 加载引擎 schema 并设置参数
        engine = engine_registry.get(engine_id)
        if engine:
            self._current_engine = engine
            self._engine_config.set_schema(engine.get_param_schema())
            self._engine_config.set_params(engine_params)

        # 文案
        self._text_edit.setPlainText(text)

        # 配方匹配
        self._check_recipe_match()

    def get_result(self) -> tuple[str, dict, bool, str]:
        """获取编辑结果

        Returns:
            (engine_id, engine_params, update_text, new_text)
        """
        engine_id = self._engine_combo.currentData() or ""
        engine_params = self._engine_config.get_params()
        update_text = self._text_checkbox.isChecked()
        new_text = self._text_edit.toPlainText().strip()
        return engine_id, engine_params, update_text, new_text

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # ── 标题说明 ──
        hint = QLabel("修改后将应用到所有已勾选的任务。左栏文本默认不变，可勾选「同时更新文案」统一替换。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6B7280; font-size: 12px; margin-bottom: 4px;")
        layout.addWidget(hint)

        # ── 文案编辑（可选） ──
        text_group = QGroupBox()
        tv = QVBoxLayout(text_group)
        tv.setContentsMargins(10, 12, 10, 10)

        text_header = QHBoxLayout()
        self._text_checkbox = QCheckBox("同时更新文案内容")
        self._text_checkbox.setStyleSheet("font-weight: bold;")
        self._text_checkbox.toggled.connect(self._on_text_checkbox_toggled)
        text_header.addWidget(self._text_checkbox)
        text_header.addStretch()
        tv.addLayout(text_header)

        self._text_edit = QTextEdit()
        self._text_edit.setPlaceholderText("输入新的文案内容（将覆盖所有选中任务的文案）…")
        self._text_edit.setMaximumHeight(80)
        self._text_edit.setEnabled(False)
        tv.addWidget(self._text_edit)
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
        self._engine_config.setTitle("引擎参数配置（所有选中任务共用）")
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

        self._save_btn = QPushButton("保存到全部选中任务")
        self._save_btn.setObjectName("primaryBtn")
        self._save_btn.clicked.connect(self._on_save)

        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self._save_btn)
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

    def _on_engine_changed(self, index: int) -> None:
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
        self._check_recipe_match()

    # ------------------------------------------------------------------
    # 配方操作
    # ------------------------------------------------------------------
    def _refresh_recipe_combo(self) -> None:
        self._recipe_combo.blockSignals(True)
        self._recipe_combo.clear()

        self._recipe_combo.addItem("（自定义）", None)
        self._recipe_combo.setItemData(0, Qt.GlobalColor.gray, Qt.ItemDataRole.ForegroundRole)

        if self._recipe_manager:
            for recipe in self._recipe_manager.list_all():
                label = f"{recipe.name} [{recipe.engine}]"
                self._recipe_combo.addItem(label, recipe.id)

        self._recipe_combo.blockSignals(False)

    def _on_recipe_selected(self, index: int) -> None:
        if index <= 0:
            return

        recipe_id = self._recipe_combo.itemData(index)
        if not recipe_id or not self._recipe_manager:
            return

        recipe = self._recipe_manager.get(recipe_id)
        if not recipe:
            return

        self._recipe_load_suppress = True

        eng_idx = self._engine_combo.findData(recipe.engine)
        if eng_idx >= 0:
            self._engine_combo.setCurrentIndex(eng_idx)

        engine = engine_registry.get(recipe.engine)
        if engine:
            self._current_engine = engine
            self._engine_config.set_schema(engine.get_param_schema())
            self._engine_config.set_params(recipe.engine_params)

        self._recipe_load_suppress = False

    def _check_recipe_match(self) -> None:
        if not self._recipe_manager:
            return
        current_engine_id = self._engine_combo.currentData() or ""
        current_params = self._engine_config.get_params()

        matched_id = None
        for recipe in self._recipe_manager.list_all():
            if recipe.engine == current_engine_id and _params_equal(recipe.engine_params, current_params):
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
    # 文案复选框
    # ------------------------------------------------------------------
    def _on_text_checkbox_toggled(self, checked: bool) -> None:
        self._text_edit.setEnabled(checked)
        if not checked:
            self._text_edit.setStyleSheet("")
        else:
            self._text_edit.setStyleSheet("")

    # ------------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------------
    def _on_save(self) -> None:
        # 基础校验
        engine_params = self._engine_config.get_params()
        engine_params["text"] = "placeholder"
        if self._current_engine:
            errors = self._current_engine.validate_params(engine_params)
            errors = [e for e in errors if "文案" not in e]
            if errors:
                QMessageBox.warning(self, "参数校验失败", "\n".join(errors))
                return

        if self._text_checkbox.isChecked() and not self._text_edit.toPlainText().strip():
            QMessageBox.warning(self, "提示", '勾选了「同时更新文案」，但文案内容为空。')
            return

        self.accept()
