"""配方编辑对话框 — 新建/编辑配方名称、引擎和动态参数"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QPushButton, QGroupBox, QScrollArea, QWidget,
    QSizePolicy, QMessageBox, QCheckBox,
)

from src.core.recipe import Recipe
from src.engines import engine_registry
from src.engines.base_engine import ParamField


class RecipeEditDialog(QDialog):
    """配方编辑对话框"""

    recipe_saved = Signal(Recipe)

    def __init__(
        self,
        parent: QWidget | None = None,
        recipe: Recipe | None = None,
        existing_names: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._recipe = recipe
        self._existing_names = existing_names or []
        self._param_widgets: dict[str, QWidget] = {}
        self._current_fields: list[ParamField] = []

        self.setWindowTitle("编辑配方" if recipe else "新建配方")
        self.setMinimumWidth(520)
        self.setMinimumHeight(460)
        self.setModal(True)

        self._build_ui()

        if recipe:
            self._load_recipe(recipe)

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        # --- 名称 ---
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("配方名称"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("请输入配方名称")
        name_layout.addWidget(self._name_edit, 1)
        layout.addLayout(name_layout)

        # --- 引擎选择 ---
        engine_layout = QHBoxLayout()
        engine_layout.addWidget(QLabel("引擎类型"))
        self._engine_combo = QComboBox()
        for eng in engine_registry.list_engines():
            self._engine_combo.addItem(eng.meta.engine_name, eng.meta.engine_id)
        self._engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        engine_layout.addWidget(self._engine_combo, 1)
        layout.addLayout(engine_layout)

        # --- 参数表单区域 ---
        self._params_group = QGroupBox("引擎参数")
        self._params_group.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        params_outer = QVBoxLayout(self._params_group)
        params_outer.setContentsMargins(6, 10, 6, 6)

        self._params_scroll = QScrollArea()
        self._params_scroll.setWidgetResizable(True)
        self._params_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        params_outer.addWidget(self._params_scroll)

        self._params_container = QWidget()
        self._params_layout = QVBoxLayout(self._params_container)
        self._params_layout.setSpacing(8)
        self._params_layout.setContentsMargins(8, 8, 8, 8)
        self._params_scroll.setWidget(self._params_container)

        layout.addWidget(self._params_group, 1)

        # --- 按钮 ---
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        self._save_btn = QPushButton("保存")
        self._save_btn.setObjectName("primaryBtn")
        self._save_btn.clicked.connect(self._on_save)
        self._save_btn.setMinimumWidth(80)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(self._save_btn)
        layout.addLayout(btn_layout)

        # 初始构建参数表单
        self._on_engine_changed()

    # ------------------------------------------------------------------
    # 引擎切换 → 重建参数表单
    # ------------------------------------------------------------------
    def _on_engine_changed(self) -> None:
        engine_id = self._engine_combo.currentData()
        engine = engine_registry.get(engine_id)
        if engine is None:
            return

        fields = engine.get_param_schema()
        self._current_fields = fields
        self._rebuild_params()

    def _rebuild_params(self) -> None:
        # 清除旧组件
        while self._params_layout.count():
            item = self._params_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._param_widgets.clear()

        for field in self._current_fields:
            row = QHBoxLayout()
            row.setSpacing(6)

            label = QLabel(field.label)
            label.setMinimumWidth(120)
            label.setMaximumWidth(160)
            row.addWidget(label)

            widget = self._create_widget(field)
            row.addWidget(widget, 1)
            self._params_layout.addLayout(row)

        self._params_layout.addStretch()

    def _create_widget(self, field: ParamField) -> QWidget:
        if field.field_type == "text":
            w = QLineEdit()
            if field.default is not None:
                w.setText(str(field.default))
            self._param_widgets[field.name] = w
            return w

        elif field.field_type == "file":
            container = QWidget()
            hl = QHBoxLayout(container)
            hl.setContentsMargins(0, 0, 0, 0)
            w = QLineEdit()
            w.setReadOnly(True)
            if field.default is not None:
                w.setText(str(field.default))
            btn = QPushButton("\u2026")
            btn.setFixedWidth(32)
            btn.clicked.connect(lambda checked, fw=w: self._pick_file(fw))
            hl.addWidget(w, 1)
            hl.addWidget(btn)
            self._param_widgets[field.name] = w
            return container

        elif field.field_type == "select":
            w = QComboBox()
            w.addItems(field.options)
            if field.default is not None and field.default in field.options:
                w.setCurrentText(str(field.default))
            self._param_widgets[field.name] = w
            return w

        elif field.field_type == "slider":
            from PySide6.QtWidgets import QDoubleSpinBox, QSlider

            container = QWidget()
            hl = QHBoxLayout(container)
            hl.setContentsMargins(0, 0, 0, 0)
            sp = QDoubleSpinBox()
            sp.setRange(field.min_val, field.max_val)
            sp.setSingleStep(field.step)
            sp.setDecimals(2)
            sp.setFixedWidth(72)
            if field.default is not None:
                sp.setValue(float(field.default))

            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setRange(int(field.min_val * 100), int(field.max_val * 100))
            sl.setSingleStep(int(field.step * 100))
            if field.default is not None:
                sl.setValue(int(float(field.default) * 100))

            sp.valueChanged.connect(lambda v, s=sl: s.setValue(int(v * 100)))
            sl.valueChanged.connect(lambda v, s=sp: s.setValue(v / 100.0))

            hl.addWidget(sl, 1)
            hl.addWidget(sp)
            self._param_widgets[field.name] = sp
            return container

        elif field.field_type == "checkbox":
            w = QCheckBox()
            w.setChecked(bool(field.default))
            self._param_widgets[field.name] = w
            return w

        return QLabel("—")

    def _pick_file(self, line_edit: QLineEdit) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "选择音频文件", "",
            "Audio Files (*.wav *.mp3 *.ogg *.flac);;All Files (*)"
        )
        if path:
            line_edit.setText(path)

    # ------------------------------------------------------------------
    # 加载已有配方
    # ------------------------------------------------------------------
    def _load_recipe(self, recipe: Recipe) -> None:
        self._name_edit.setText(recipe.name)

        # 设置引擎
        idx = self._engine_combo.findData(recipe.engine)
        if idx >= 0:
            self._engine_combo.setCurrentIndex(idx)

        # 填充参数值
        for field in self._current_fields:
            widget = self._param_widgets.get(field.name)
            if widget is None or field.name not in recipe.engine_params:
                continue
            val = recipe.engine_params[field.name]

            if field.field_type == "text":
                if isinstance(widget, QLineEdit):
                    widget.setText(str(val))
            elif field.field_type == "file":
                if isinstance(widget, QLineEdit):
                    widget.setText(str(val))
            elif field.field_type == "select":
                if isinstance(widget, QComboBox):
                    idx2 = widget.findText(str(val))
                    if idx2 >= 0:
                        widget.setCurrentIndex(idx2)
            elif field.field_type == "slider":
                from PySide6.QtWidgets import QDoubleSpinBox
                if isinstance(widget, QDoubleSpinBox):
                    try:
                        widget.setValue(float(val))
                    except (TypeError, ValueError):
                        widget.setValue(float(field.default or 0.0))
            elif field.field_type == "checkbox":
                if isinstance(widget, QCheckBox):
                    widget.setChecked(bool(val))

    # ------------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------------
    def _on_save(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "配方名称不能为空")
            return

        # 检查同名冲突（排除自身）
        if self._recipe and name == self._recipe.name:
            pass  # 未改名，允许
        elif name in self._existing_names:
            reply = QMessageBox.question(
                self, "名称冲突",
                f"配方 \u201c{name}\u201d 已存在，是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        engine_id = self._engine_combo.currentData()

        # 收集参数
        params: dict = {}
        for field in self._current_fields:
            widget = self._param_widgets.get(field.name)
            if widget is None:
                continue
            if field.field_type in ("text", "file"):
                if isinstance(widget, QLineEdit):
                    params[field.name] = widget.text()
            elif field.field_type == "select":
                if isinstance(widget, QComboBox):
                    params[field.name] = widget.currentText()
            elif field.field_type == "slider":
                from PySide6.QtWidgets import QDoubleSpinBox
                if isinstance(widget, QDoubleSpinBox):
                    params[field.name] = widget.value()
            elif field.field_type == "checkbox":
                if isinstance(widget, QCheckBox):
                    params[field.name] = widget.isChecked()

        if self._recipe:
            # 更新已有配方
            self._recipe.name = name
            self._recipe.engine = engine_id
            self._recipe.engine_params = params
            self._recipe.touch()
            self.recipe_saved.emit(self._recipe)
        else:
            # 新建配方
            from src.core.recipe import Recipe as R
            new_recipe = R(id="", name=name, engine=engine_id, engine_params=params)
            self.recipe_saved.emit(new_recipe)

        self.accept()
