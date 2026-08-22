"""引擎参数配置组件 — 根据引擎类型和参数 schema 动态生成表单"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal, QEvent, QObject
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QSlider, QPushButton, QFileDialog, QGroupBox,
    QDoubleSpinBox, QScrollArea, QSizePolicy, QSpinBox, QCheckBox,
)

from src.engines.base_engine import ParamField


class _WheelBlocker(QObject):
    """事件过滤器：阻止滑块和输入框响应鼠标滚轮，避免误操作"""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel:
            return True  # 吞掉滚轮事件
        return super().eventFilter(obj, event)


class EngineConfigWidget(QGroupBox):
    """引擎参数配置组件

    根据传入的 ParamField 列表动态生成表单。
    支持 visible_when 条件来控制字段可见性。
    """

    params_changed = Signal()  # 参数变化时发出

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("引擎参数配置", parent)
        self._fields: list[ParamField] = []
        self._widgets: dict[str, QWidget] = {}
        self._form_widget: QWidget | None = None
        self._form_layout: QVBoxLayout | None = None

        # 外层布局（预留间距避免内容紧贴边框）
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 14, 6, 6)

        # 可滚动区域
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll_area.setMinimumHeight(180)
        self._scroll_area.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        outer.addWidget(self._scroll_area)

    def set_schema(self, fields: list[ParamField]) -> None:
        """设置参数 schema 并重新构建表单"""
        self._fields = fields
        self._rebuild()

    def _rebuild(self) -> None:
        """根据 schema 重新构建所有表单项"""
        # 阻断自身信号，防止 params_changed 级联触发
        self.blockSignals(True)
        try:
            self._do_rebuild()
        finally:
            self.blockSignals(False)

    def _do_rebuild(self) -> None:
        """实际重建表单（信号已被阻断）"""
        # 清除旧组件
        if self._form_widget:
            self._form_widget.deleteLater()
            self._form_widget = None

        self._widgets.clear()

        # 创建表单容器 widget（传入 self 防止无 parent 时成为顶级窗口）
        self._form_widget = QWidget(self)
        self._form_layout = QVBoxLayout(self._form_widget)
        self._form_layout.setSpacing(8)
        self._form_layout.setContentsMargins(10, 12, 10, 10)

        for field in self._fields:
            row = self._create_field_row(field)
            self._form_layout.addLayout(row)

        self._form_layout.addStretch()

        # 挂载到滚动区域
        self._scroll_area.setWidget(self._form_widget)

        # 立即应用可见性条件
        self._on_visibility_check()

    def _create_field_row(self, field: ParamField) -> QHBoxLayout:
        """创建单个参数行"""
        row = QHBoxLayout()
        row.setSpacing(6)

        # 标签（加宽以避免情绪向量等长标签挤压控件）
        label = QLabel(field.label)
        label.setMinimumWidth(120)
        label.setMaximumWidth(160)
        label.setObjectName(f"param_{field.name}_label")
        row.addWidget(label)

        widget: QWidget
        _blocker = _WheelBlocker(self)

        if field.field_type == "text":
            w = QLineEdit()
            if field.default:
                w.setText(str(field.default))
            w.textChanged.connect(lambda: self.params_changed.emit())
            w.installEventFilter(_blocker)
            widget = w

        elif field.field_type == "file":
            container = QWidget()
            hl = QHBoxLayout(container)
            hl.setContentsMargins(0, 0, 0, 0)
            w = QLineEdit()
            w.setReadOnly(True)
            if field.default:
                w.setText(str(field.default))
            w.installEventFilter(_blocker)
            btn = QPushButton("\u2026")
            btn.setMinimumWidth(36)
            btn.setMaximumWidth(36)
            btn.setMaximumHeight(28)
            btn.setStyleSheet(
                "QPushButton {"
                " padding: 4px 4px;"
                " font-size: 16px; font-weight: 700;"
                " background-color: #E8EBF0; color: #1A1C20;"
                " border: 1px solid #CBD1D9; border-radius: 4px;"
                "}"
                "QPushButton:hover { background-color: #DADFE6; }"
            )
            btn.clicked.connect(
                lambda checked, fw=w: self._pick_file(fw)
            )
            hl.addWidget(w, 1)
            hl.addWidget(btn)
            widget = container

        elif field.field_type == "select":
            w = QComboBox()
            w.addItems(field.options)
            if field.default and field.default in field.options:
                w.setCurrentText(str(field.default))
            w.currentIndexChanged.connect(self._on_visibility_check)
            w.currentIndexChanged.connect(lambda: self.params_changed.emit())
            w.installEventFilter(_blocker)
            widget = w

        elif field.field_type == "slider":
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

            # 双向同步
            sp.valueChanged.connect(
                lambda v: sl.setValue(int(v * 100))
            )
            sl.valueChanged.connect(
                lambda v: sp.setValue(v / 100.0)
            )
            sp.valueChanged.connect(
                lambda: self.params_changed.emit()
            )

            # 禁用滚轮避免误操作
            sl.installEventFilter(_blocker)
            sp.installEventFilter(_blocker)

            hl.addWidget(sl, 1)
            hl.addWidget(sp)
            widget = container

        elif field.field_type == "checkbox":
            w = QCheckBox()
            w.setChecked(bool(field.default))
            w.toggled.connect(lambda: self.params_changed.emit())
            widget = w

        else:
            widget = QLabel("—")

        widget.setObjectName(f"param_{field.name}_widget")
        row.addWidget(widget, 1)
        self._widgets[field.name] = widget

        return row

    # ------------------------------------------------------------------
    # 文件选择
    # ------------------------------------------------------------------
    def _pick_file(self, line_edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择音频文件", "",
            "Audio Files (*.wav *.mp3 *.ogg *.flac);;All Files (*)"
        )
        if path:
            line_edit.setText(path)
            self.params_changed.emit()

    # ------------------------------------------------------------------
    # 可见性控制
    # ------------------------------------------------------------------
    def _on_visibility_check(self) -> None:
        """根据 visible_when 条件控制字段可见性

        visible_when 支持三种格式：
        1. {"emotion_mode": "emotion_vector"}  — key=参考字段，value=期待值
        2. {"field": "emotion_mode", "value": "emotion_vector"}  — 显式格式
        3. {"emotion_mode": ["ref_audio", "vector"]}  — value 为列表，命中其一即可
        """
        params = self.get_params()

        for field in self._fields:
            if not field.visible_when:
                continue
            cond = field.visible_when

            # 兼容两种格式
            if "field" in cond and "value" in cond:
                ref_key = cond["field"]
                ref_val = cond["value"]
            else:
                # 格式 1/3：唯一的 key-value 就是 field→value 映射
                ref_key, ref_val = next(iter(cond.items()))

            actual = str(params.get(ref_key, ""))

            if isinstance(ref_val, (list, tuple)):
                visible = actual in [str(v) for v in ref_val]
            else:
                visible = actual == str(ref_val)

            label_w = self.findChild(QLabel, f"param_{field.name}_label")
            input_w = self.findChild(QWidget, f"param_{field.name}_widget")

            if isinstance(label_w, QLabel):
                label_w.setVisible(visible)
            if isinstance(input_w, QWidget):
                input_w.setVisible(visible)

    # ------------------------------------------------------------------
    # 数据读写
    # ------------------------------------------------------------------
    def get_params(self) -> dict[str, Any]:
        """获取当前表单中的所有参数值"""
        result: dict[str, Any] = {}

        for field in self._fields:
            widget = self._widgets.get(field.name)
            if widget is None:
                continue

            if field.field_type == "text":
                w = widget.findChild(QLineEdit)
                if not w:
                    w = widget  # type: ignore[assignment]
                if isinstance(w, QLineEdit):
                    result[field.name] = w.text()

            elif field.field_type == "file":
                w = widget.findChild(QLineEdit)
                if isinstance(w, QLineEdit):
                    result[field.name] = w.text()

            elif field.field_type == "select":
                if isinstance(widget, QComboBox):
                    result[field.name] = widget.currentText()

            elif field.field_type == "slider":
                sp = widget.findChild(QDoubleSpinBox)
                if isinstance(sp, QDoubleSpinBox):
                    result[field.name] = sp.value()

            elif field.field_type == "checkbox":
                if isinstance(widget, QCheckBox):
                    result[field.name] = widget.isChecked()

        return result

    def set_params(self, params: dict[str, Any]) -> None:
        """设置表单参数值，不把程序化回填误报为用户编辑。

        First restore every field to its schema default.  Without this reset,
        an omitted value in a legacy task can incorrectly retain the value
        shown for the previously selected task.
        """
        self.clear()
        for field in self._fields:
            widget = self._widgets.get(field.name)
            if widget is None or field.name not in params:
                continue
            value = params[field.name]

            if field.field_type == "text":
                w = widget.findChild(QLineEdit)
                if not w:
                    w = widget  # type: ignore[assignment]
                if isinstance(w, QLineEdit):
                    blocked = w.blockSignals(True)
                    try:
                        w.setText("" if value is None else str(value))
                    finally:
                        w.blockSignals(blocked)

            elif field.field_type == "file":
                w = widget.findChild(QLineEdit)
                if isinstance(w, QLineEdit):
                    blocked = w.blockSignals(True)
                    try:
                        w.setText("" if value is None else str(value))
                    finally:
                        w.blockSignals(blocked)

            elif field.field_type == "select":
                if isinstance(widget, QComboBox):
                    idx = widget.findText(str(value))
                    if idx >= 0:
                        blocked = widget.blockSignals(True)
                        try:
                            widget.setCurrentIndex(idx)
                        finally:
                            widget.blockSignals(blocked)

            elif field.field_type == "slider":
                sp = widget.findChild(QDoubleSpinBox)
                if isinstance(sp, QDoubleSpinBox):
                    try:
                        numeric = float(value)
                    except (TypeError, ValueError):
                        numeric = float(field.default or 0.0)
                    slider = widget.findChild(QSlider)
                    spin_blocked = sp.blockSignals(True)
                    slider_blocked = slider.blockSignals(True) if slider else False
                    try:
                        sp.setValue(numeric)
                        if slider:
                            slider.setValue(int(numeric * 100))
                    finally:
                        sp.blockSignals(spin_blocked)
                        if slider:
                            slider.blockSignals(slider_blocked)

            elif field.field_type == "checkbox":
                if isinstance(widget, QCheckBox):
                    blocked = widget.blockSignals(True)
                    try:
                        widget.setChecked(bool(value))
                    finally:
                        widget.blockSignals(blocked)

        self._on_visibility_check()

    def clear(self) -> None:
        """清空所有参数"""
        self.blockSignals(True)
        try:
            for field in self._fields:
                widget = self._widgets.get(field.name)
                if widget is None:
                    continue

                if field.field_type == "text":
                    w = widget.findChild(QLineEdit)
                    if not w:
                        w = widget  # type: ignore[assignment]
                    if isinstance(w, QLineEdit):
                        w.clear()

                elif field.field_type == "file":
                    w = widget.findChild(QLineEdit)
                    if isinstance(w, QLineEdit):
                        w.clear()

                elif field.field_type == "select":
                    if isinstance(widget, QComboBox):
                        widget.setCurrentIndex(0)

                elif field.field_type == "slider":
                    sp = widget.findChild(QDoubleSpinBox)
                    if isinstance(sp, QDoubleSpinBox):
                        sp.setValue(float(field.default or 0.0))

                elif field.field_type == "checkbox":
                    if isinstance(widget, QCheckBox):
                        widget.setChecked(bool(field.default))
        finally:
            self.blockSignals(False)
