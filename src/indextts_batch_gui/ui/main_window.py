from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests
from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QPlainTextEdit,
    QSlider,
    QSplitter,
    QSpinBox,
    QScrollArea,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..api_client import IndexTTSClient
from ..audio import AudioPlaybackError, AudioPlaybackService
from ..config import load_app_config, save_app_config
from ..models import AppConfig, TaskRecord, TaskSetDefaults
from ..scheduler import BatchRunner, mark_regen_if_changed
from ..storage import TaskSetStorage


class BatchWorker(QObject):
    progress = Signal(object)
    finished = Signal()
    failed = Signal(str)

    def __init__(self, runner: BatchRunner, tasks: list[TaskRecord]) -> None:
        super().__init__()
        self._runner = runner
        self._tasks = tasks

    def run(self) -> None:
        try:
            self._runner.run(self._tasks, self._emit_progress)
            self.finished.emit()
        except Exception as exc:
            self.failed.emit(str(exc))

    def _emit_progress(self, task: TaskRecord) -> None:
        self.progress.emit(task)


class ReorderableTaskTable(QTableWidget):
    rows_reordered = Signal(int, int)

    def __init__(self, rows: int, columns: int, parent: QWidget | None = None) -> None:
        super().__init__(rows, columns, parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QTableWidget.DragDrop)
        self.setDragDropOverwriteMode(False)
        self.setDefaultDropAction(Qt.MoveAction)
        self._drag_row = -1

    def startDrag(self, supportedActions) -> None:  # type: ignore[override]
        selected = self.selectionModel().selectedRows()
        if selected:
            self._drag_row = selected[0].row()
        else:
            self._drag_row = -1
        super().startDrag(supportedActions)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        source_row = self._drag_row
        target_row = self.indexAt(event.position().toPoint()).row()
        if source_row < 0:
            event.ignore()
            return
        if target_row < 0:
            target_row = self.rowCount() - 1
        if target_row < 0:
            target_row = 0
        if target_row >= self.rowCount():
            target_row = self.rowCount() - 1
        # Ignore Qt's internal item move; we reorder the backing task list ourselves
        # and then fully refresh table contents to avoid row/widget loss.
        event.ignore()
        self._drag_row = -1
        if source_row != target_row:
            self.rows_reordered.emit(source_row, target_row)


class MainWindow(QMainWindow):
    _EMO_VECTOR_TITLES = ["喜", "怒", "哀", "惧", "厌恶", "低落", "惊喜", "平静"]
    _EMO_METHOD_SAME_REF = "与音色参考音频相同"
    _EMO_METHOD_REF = "使用情感参考音频"
    _EMO_METHOD_VECTOR = "使用情感向量控制"
    _EMO_METHOD_OPTIONS = [_EMO_METHOD_SAME_REF, _EMO_METHOD_REF, _EMO_METHOD_VECTOR]
    _WEBUI_ADV_DEFAULTS = {
        "max_text_tokens_per_segment": 120,
        "do_sample": True,
        "top_p": 0.8,
        "top_k": 30,
        "temperature": 0.8,
        "length_penalty": 0.0,
        "num_beams": 3,
        "repetition_penalty": 10.0,
        "max_mel_tokens": 1500,
    }

    _KNOWN_TASK_CONFIG_KEYS = {
        "emotion_vector",
        "emo_vector",
        "emo_ref_path",
        "emotion_ref_audio",
        "custom_prompt",
        "emo_control_method",
        "emo_weight",
        "max_text_tokens_per_segment",
        "do_sample",
        "top_p",
        "top_k",
        "temperature",
        "length_penalty",
        "num_beams",
        "repetition_penalty",
        "max_mel_tokens",
    }

    _STATUS_DISPLAY = {
        "pending": "待处理",
        "queued": "排队中",
        "generating": "生成中",
        "done": "已完成",
        "failed": "失败",
        "cancelled": "已取消",
    }

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("IndexTTS 批量生成")
        self.resize(1280, 780)
        self.setMinimumSize(980, 560)

        self.app_config: AppConfig = load_app_config()
        self.storage: TaskSetStorage | None = None
        self.defaults = TaskSetDefaults()
        self.tasks: list[TaskRecord] = []
        self.player = AudioPlaybackService()
        self._temp_audio_files: list[Path] = []
        self._batch_thread: QThread | None = None
        self._batch_worker: BatchWorker | None = None
        self._batch_runner: BatchRunner | None = None
        self._batch_done_message = "批量生成完成"
        self._active_detail_task_id = ""
        self._handling_table_selection_change = False

        self._build_ui()
        self._apply_md_style()
        self._apply_config_to_fields()
        self._restore_persisted_state()

    def _build_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)
        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(8)

        main_layout.addWidget(self._build_audio_controller())

        self.main_tabs = QTabWidget()
        self.main_tabs.setDocumentMode(True)
        self.main_tabs.setTabPosition(QTabWidget.North)

        self.settings_tab_index = self.main_tabs.addTab(self._wrap_in_scroll(self._build_settings_tab()), "设置")
        self.task_editor_tab_index = self.main_tabs.addTab(self._build_task_editor_tab(), "添加任务")
        self.task_table_tab_index = self.main_tabs.addTab(self._build_task_table_box(), "批量任务")
        self.main_tabs.currentChanged.connect(self._on_main_tab_changed)

        main_layout.addWidget(self.main_tabs)

    def _build_audio_controller(self) -> QWidget:
        box = QGroupBox("音乐配置器")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        self.audio_now_playing_label = QLabel("当前音频: 未播放")
        self.audio_now_playing_label.setWordWrap(True)

        self.audio_pause_btn = QPushButton("暂停")
        self.audio_pause_btn.clicked.connect(self._toggle_global_audio_pause)

        self.audio_volume_slider = QSlider(Qt.Horizontal)
        self.audio_volume_slider.setRange(0, 100)
        self.audio_volume_slider.setValue(80)
        self.audio_volume_slider.valueChanged.connect(self._on_global_volume_changed)
        try:
            self.player.set_volume(0.8)
        except AudioPlaybackError:
            pass

        layout.addWidget(self.audio_now_playing_label, 1)
        layout.addWidget(self.audio_pause_btn)
        layout.addWidget(QLabel("音量"))
        layout.addWidget(self.audio_volume_slider)
        return box

    def _on_global_volume_changed(self, value: int) -> None:
        try:
            self.player.set_volume(value / 100.0)
        except AudioPlaybackError as exc:
            self._warn(str(exc))

    def _toggle_global_audio_pause(self) -> None:
        try:
            paused = self.player.toggle_pause()
        except AudioPlaybackError as exc:
            self._warn(str(exc))
            return
        self.audio_pause_btn.setText("继续" if paused else "暂停")

    def _play_with_global_player(self, path: Path) -> None:
        try:
            self.player.play(path)
            self.audio_now_playing_label.setText(f"当前音频: {path.name}")
            self.audio_pause_btn.setText("暂停")
        except AudioPlaybackError as exc:
            self._warn(str(exc))

    def _reset_global_audio_state(self) -> None:
        try:
            self.player.stop()
        except AudioPlaybackError:
            pass
        self.audio_now_playing_label.setText("当前音频: 未播放")
        self.audio_pause_btn.setText("暂停")

    def _release_audio_file_lock(self, audio_path: Path) -> None:
        try:
            self.player.release_file(audio_path)
        except AudioPlaybackError:
            pass
        if self.audio_now_playing_label.text() != "当前音频: 未播放":
            self.audio_now_playing_label.setText("当前音频: 未播放")
            self.audio_pause_btn.setText("暂停")

    def _wrap_in_scroll(self, content: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        return scroll

    def _build_task_editor_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        form_box = self._build_task_editor_box()
        layout.addWidget(self.task_editor_action_bar)

        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setWidget(form_box)
        form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        form_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        layout.addWidget(form_scroll, 1)
        return page

    def _build_task_detail_panel(self) -> QWidget:
        panel = QGroupBox("详细面板")
        layout = QVBoxLayout(panel)

        target_box = QGroupBox("任务定位")
        target_layout = QGridLayout(target_box)
        self.detail_task_id_combo = QComboBox()
        self.detail_task_id_combo.currentIndexChanged.connect(self._on_detail_task_id_changed)

        self.detail_task_text_edit = QPlainTextEdit()
        self.detail_task_text_edit.setFixedHeight(80)
        self.detail_task_ref_edit = QLineEdit()
        self.detail_task_ref_edit.setPlaceholderText("https://example.com/ref.wav 或本地路径")
        self.detail_ref_browse_btn = QPushButton("浏览本地")
        self.detail_ref_play_btn = QPushButton("播放")
        self.detail_ref_browse_btn.clicked.connect(self.pick_detail_ref)
        self.detail_ref_play_btn.clicked.connect(self.play_detail_ref)

        detail_ref_row = QHBoxLayout()
        detail_ref_row.addWidget(self.detail_task_ref_edit)
        detail_ref_row.addWidget(self.detail_ref_browse_btn)
        detail_ref_row.addWidget(self.detail_ref_play_btn)
        detail_ref_widget = QWidget()
        detail_ref_widget.setLayout(detail_ref_row)
        self.detail_is_final_check = QCheckBox("是否定稿")
        self.detail_is_final_check.stateChanged.connect(self._on_detail_is_final_changed)

        # Visual config fields (same style as task add/editor).
        self.detail_emo_method_combo = QComboBox()
        self.detail_emo_method_combo.addItems(self._EMO_METHOD_OPTIONS)
        self.detail_emo_method_combo.currentIndexChanged.connect(self._on_detail_emo_method_changed)

        self.detail_emo_ref_edit = QLineEdit()
        self.detail_emo_ref_edit.setPlaceholderText("https://example.com/emo.wav 或本地路径")
        self.detail_emo_ref_browse = QPushButton("浏览本地")
        self.detail_emo_ref_play = QPushButton("播放")
        self.detail_emo_ref_browse.clicked.connect(self.pick_detail_emotion_ref)
        self.detail_emo_ref_play.clicked.connect(self.play_detail_emotion_ref)
        detail_emo_ref_row = QHBoxLayout()
        detail_emo_ref_row.addWidget(self.detail_emo_ref_edit)
        detail_emo_ref_row.addWidget(self.detail_emo_ref_browse)
        detail_emo_ref_row.addWidget(self.detail_emo_ref_play)
        detail_emo_ref_widget = QWidget()
        detail_emo_ref_widget.setLayout(detail_emo_ref_row)

        self.detail_emo_weight_spin = QDoubleSpinBox()
        self.detail_emo_weight_spin.setRange(0.0, 1.0)
        self.detail_emo_weight_spin.setDecimals(2)
        self.detail_emo_weight_spin.setSingleStep(0.01)
        self.detail_emo_weight_spin.setValue(0.65)
        self.detail_emo_weight_slider = QSlider(Qt.Horizontal)
        self.detail_emo_weight_slider.setRange(0, 100)
        self.detail_emo_weight_slider.setValue(65)
        self.detail_emo_weight_slider.valueChanged.connect(self._on_detail_emo_weight_slider_changed)
        self.detail_emo_weight_spin.valueChanged.connect(self._on_detail_emo_weight_spin_changed)
        detail_weight_row = QHBoxLayout()
        detail_weight_row.addWidget(self.detail_emo_weight_spin)
        detail_weight_row.addWidget(self.detail_emo_weight_slider)
        detail_weight_widget = QWidget()
        detail_weight_widget.setLayout(detail_weight_row)

        self.detail_emo_vector_panel = QWidget()
        detail_vec_grid = QGridLayout(self.detail_emo_vector_panel)
        self.detail_emo_vector_sliders: list[QSlider] = []
        self.detail_emo_vector_values: list[QLabel] = []
        for index in range(8):
            tag = QLabel(self._EMO_VECTOR_TITLES[index])
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 100)
            slider.setValue(0)
            value_label = QLabel("0.00")
            slider.valueChanged.connect(lambda value, i=index: self._on_detail_emo_slider_changed(i, value))
            detail_vec_grid.addWidget(tag, index, 0)
            detail_vec_grid.addWidget(slider, index, 1)
            detail_vec_grid.addWidget(value_label, index, 2)
            self.detail_emo_vector_sliders.append(slider)
            self.detail_emo_vector_values.append(value_label)

        self.detail_custom_prompt_edit = QPlainTextEdit()
        self.detail_custom_prompt_edit.setFixedHeight(56)

        self.detail_segment_tokens_spin = QSpinBox()
        self.detail_segment_tokens_spin.setRange(1, 100000)
        self.detail_segment_tokens_spin.setValue(int(self._WEBUI_ADV_DEFAULTS["max_text_tokens_per_segment"]))
        self.detail_do_sample_check = QCheckBox("启用采样")
        self.detail_do_sample_check.setChecked(bool(self._WEBUI_ADV_DEFAULTS["do_sample"]))
        self.detail_top_p_spin = QDoubleSpinBox()
        self.detail_top_p_spin.setRange(0.0, 1.0)
        self.detail_top_p_spin.setSingleStep(0.01)
        self.detail_top_p_spin.setValue(float(self._WEBUI_ADV_DEFAULTS["top_p"]))
        self.detail_top_k_spin = QSpinBox()
        self.detail_top_k_spin.setRange(0, 1000)
        self.detail_top_k_spin.setValue(int(self._WEBUI_ADV_DEFAULTS["top_k"]))
        self.detail_temperature_spin = QDoubleSpinBox()
        self.detail_temperature_spin.setRange(0.0, 5.0)
        self.detail_temperature_spin.setSingleStep(0.05)
        self.detail_temperature_spin.setValue(float(self._WEBUI_ADV_DEFAULTS["temperature"]))
        self.detail_length_penalty_spin = QDoubleSpinBox()
        self.detail_length_penalty_spin.setRange(0.0, 5.0)
        self.detail_length_penalty_spin.setSingleStep(0.05)
        self.detail_length_penalty_spin.setValue(float(self._WEBUI_ADV_DEFAULTS["length_penalty"]))
        self.detail_num_beams_spin = QSpinBox()
        self.detail_num_beams_spin.setRange(1, 32)
        self.detail_num_beams_spin.setValue(int(self._WEBUI_ADV_DEFAULTS["num_beams"]))
        self.detail_repetition_penalty_spin = QDoubleSpinBox()
        self.detail_repetition_penalty_spin.setRange(0.0, 20.0)
        self.detail_repetition_penalty_spin.setSingleStep(0.05)
        self.detail_repetition_penalty_spin.setValue(float(self._WEBUI_ADV_DEFAULTS["repetition_penalty"]))
        self.detail_max_mel_tokens_spin = QSpinBox()
        self.detail_max_mel_tokens_spin.setRange(1, 100000)
        self.detail_max_mel_tokens_spin.setValue(int(self._WEBUI_ADV_DEFAULTS["max_mel_tokens"]))
        self.detail_emo_ref_edit.textChanged.connect(self._refresh_detail_config_preview)
        self.detail_custom_prompt_edit.textChanged.connect(self._refresh_detail_config_preview)
        self.detail_emo_weight_spin.valueChanged.connect(self._refresh_detail_config_preview)
        self.detail_segment_tokens_spin.valueChanged.connect(self._refresh_detail_config_preview)
        self.detail_do_sample_check.stateChanged.connect(self._refresh_detail_config_preview)
        self.detail_top_p_spin.valueChanged.connect(self._refresh_detail_config_preview)
        self.detail_top_k_spin.valueChanged.connect(self._refresh_detail_config_preview)
        self.detail_temperature_spin.valueChanged.connect(self._refresh_detail_config_preview)
        self.detail_length_penalty_spin.valueChanged.connect(self._refresh_detail_config_preview)
        self.detail_num_beams_spin.valueChanged.connect(self._refresh_detail_config_preview)
        self.detail_repetition_penalty_spin.valueChanged.connect(self._refresh_detail_config_preview)
        self.detail_max_mel_tokens_spin.valueChanged.connect(self._refresh_detail_config_preview)

        detail_adv_grid = QGridLayout()
        detail_adv_grid.addWidget(QLabel("分段最大文本 Token"), 0, 0)
        detail_adv_grid.addWidget(self.detail_segment_tokens_spin, 0, 1)
        detail_adv_grid.addWidget(QLabel("do_sample"), 1, 0)
        detail_adv_grid.addWidget(self.detail_do_sample_check, 1, 1)
        detail_adv_grid.addWidget(QLabel("Top P"), 2, 0)
        detail_adv_grid.addWidget(self.detail_top_p_spin, 2, 1)
        detail_adv_grid.addWidget(QLabel("Top K"), 3, 0)
        detail_adv_grid.addWidget(self.detail_top_k_spin, 3, 1)
        detail_adv_grid.addWidget(QLabel("温度"), 4, 0)
        detail_adv_grid.addWidget(self.detail_temperature_spin, 4, 1)
        detail_adv_grid.addWidget(QLabel("长度惩罚"), 5, 0)
        detail_adv_grid.addWidget(self.detail_length_penalty_spin, 5, 1)
        detail_adv_grid.addWidget(QLabel("束搜索数量"), 6, 0)
        detail_adv_grid.addWidget(self.detail_num_beams_spin, 6, 1)
        detail_adv_grid.addWidget(QLabel("重复惩罚"), 7, 0)
        detail_adv_grid.addWidget(self.detail_repetition_penalty_spin, 7, 1)
        detail_adv_grid.addWidget(QLabel("最大 Mel Token"), 8, 0)
        detail_adv_grid.addWidget(self.detail_max_mel_tokens_spin, 8, 1)

        self.detail_task_extra_json_edit = QPlainTextEdit()
        self.detail_task_extra_json_edit.setPlaceholderText("只读，显示上方 UI 对应的当前配置 JSON")
        self.detail_task_extra_json_edit.setFixedHeight(90)
        self.detail_task_extra_json_edit.setReadOnly(True)

        config_form = QWidget()
        self.detail_form_layout = QFormLayout(config_form)
        self.detail_form_layout.addRow("情感控制方式", self.detail_emo_method_combo)
        self.detail_emo_weight_row_widget = detail_weight_widget
        self.detail_form_layout.addRow("情感强度", self.detail_emo_weight_row_widget)
        self.detail_emo_vector_row_widget = self.detail_emo_vector_panel
        self.detail_emo_ref_row_widget = detail_emo_ref_widget
        self.detail_form_layout.addRow("情感向量", self.detail_emo_vector_row_widget)
        self.detail_form_layout.addRow("情感参考音频URL/路径", self.detail_emo_ref_row_widget)
        self.detail_form_layout.addRow("自定义述语", self.detail_custom_prompt_edit)

        adv_wrap = QWidget()
        adv_wrap.setLayout(detail_adv_grid)
        self.detail_form_layout.addRow("高级参数", adv_wrap)
        self.detail_form_layout.addRow("UI 配置 JSON（只读）", self.detail_task_extra_json_edit)

        config_scroll = QScrollArea()
        config_scroll.setWidgetResizable(True)
        config_scroll.setWidget(config_form)

        target_layout.addWidget(QLabel("任务ID"), 0, 0)
        target_layout.addWidget(self.detail_task_id_combo, 0, 1)
        target_layout.addWidget(QLabel("文本"), 1, 0)
        target_layout.addWidget(self.detail_task_text_edit, 1, 1)
        target_layout.addWidget(QLabel("参考音频"), 2, 0)
        target_layout.addWidget(detail_ref_widget, 2, 1)
        target_layout.addWidget(QLabel("定稿"), 3, 0)
        target_layout.addWidget(self.detail_is_final_check, 3, 1)

        layout.addWidget(target_box)
        layout.addWidget(config_scroll, 1)
        self.detail_fill_generated_btn = QPushButton("填入当前语音配置")
        self.detail_fill_generated_btn.clicked.connect(self.fill_from_generated_snapshot)
        layout.addWidget(self.detail_fill_generated_btn)
        tip_label = QLabel("右侧参数为当前任务唯一参数，切换任务或开始生成时自动保存。")
        layout.addWidget(tip_label)
        self._on_detail_emo_method_changed(self.detail_emo_method_combo.currentIndex())
        return panel

    def _apply_md_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #f5f7fb;
                color: #1f2937;
                font-size: 13px;
            }
            QTabWidget::pane {
                border: 1px solid #d9e2f1;
                background: #ffffff;
                border-radius: 10px;
                top: -1px;
            }
            QTabBar::tab {
                background: #e9eef8;
                color: #334155;
                border: 1px solid #d9e2f1;
                border-bottom: none;
                min-width: 110px;
                padding: 10px 14px;
                margin-right: 6px;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                color: #0f172a;
                font-weight: 600;
            }
            QGroupBox {
                border: 1px solid #e2e8f0;
                border-radius: 12px;
                margin-top: 10px;
                padding: 14px;
                font-weight: 600;
                background: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
                color: #2563eb;
            }
            QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                padding: 6px 8px;
                background: #ffffff;
                selection-background-color: #bfdbfe;
            }
            QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
                border: 1px solid #3b82f6;
            }
            QPushButton {
                background: #2563eb;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                padding: 8px 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #1d4ed8;
            }
            QPushButton:disabled {
                background: #94a3b8;
                color: #e2e8f0;
            }
            QTableWidget {
                border: 1px solid #d9e2f1;
                border-radius: 10px;
                background: #ffffff;
                gridline-color: #e5e7eb;
            }
            QHeaderView::section {
                background: #eff6ff;
                color: #1e3a8a;
                border: none;
                border-bottom: 1px solid #dbeafe;
                padding: 8px;
                font-weight: 600;
            }
            QProgressBar {
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                text-align: center;
                background: #f8fafc;
            }
            QProgressBar::chunk {
                border-radius: 7px;
                background: #22c55e;
            }
            """
        )

    def _build_global_box(self) -> QWidget:
        box = QGroupBox("全局设置")
        layout = QGridLayout(box)

        self.webui_url_edit = QLineEdit()
        self.webui_url_edit.setPlaceholderText("http://127.0.0.1:7860")
        self.host_edit = QLineEdit()
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.conc_spin = QSpinBox()
        self.conc_spin.setRange(1, 16)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 3600)

        self.save_global_btn = QPushButton("保存全局设置")
        self.save_global_btn.clicked.connect(self.save_global_settings)

        layout.addWidget(QLabel("WebUI URL"), 0, 0)
        layout.addWidget(self.webui_url_edit, 0, 1, 1, 3)
        layout.addWidget(QLabel("主机(回退)"), 1, 0)
        layout.addWidget(self.host_edit, 1, 1)
        layout.addWidget(QLabel("端口(回退)"), 1, 2)
        layout.addWidget(self.port_spin, 1, 3)
        layout.addWidget(QLabel("并发数"), 2, 0)
        layout.addWidget(self.conc_spin, 2, 1)
        layout.addWidget(QLabel("超时(秒)"), 2, 2)
        layout.addWidget(self.timeout_spin, 2, 3)
        layout.addWidget(self.save_global_btn, 0, 4, 3, 1)

        return box

    def _build_settings_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._build_global_box())
        layout.addWidget(self._build_taskset_box())
        layout.addStretch(1)
        return page

    def _build_taskset_box(self) -> QWidget:
        box = QGroupBox("任务集")
        layout = QGridLayout(box)

        self.task_set_path_edit = QLineEdit()
        self.task_set_path_edit.setPlaceholderText("选择任务集目录")

        self.open_set_btn = QPushButton("打开任务集")
        self.new_set_btn = QPushButton("新建任务集")
        self.save_defaults_btn = QPushButton("保存默认参数")

        self.default_ref_edit = QLineEdit()
        self.default_ref_edit.setPlaceholderText("https://example.com/ref.wav 或本地路径")
        self.default_ref_browse = QPushButton("浏览本地")
        self.default_ref_play = QPushButton("播放")
        self.default_config_edit = QPlainTextEdit()
        self.default_config_edit.setPlaceholderText('{"temperature": 0.7}')
        self.default_config_edit.setFixedHeight(80)

        self.open_set_btn.clicked.connect(self.open_task_set)
        self.new_set_btn.clicked.connect(self.create_task_set)
        self.save_defaults_btn.clicked.connect(self.save_defaults)
        self.default_ref_browse.clicked.connect(self.pick_default_ref)
        self.default_ref_play.clicked.connect(self.play_default_ref)

        layout.addWidget(self.task_set_path_edit, 0, 0, 1, 3)
        layout.addWidget(self.open_set_btn, 0, 3)
        layout.addWidget(self.new_set_btn, 0, 4)

        layout.addWidget(QLabel("默认参考音频URL/路径"), 1, 0)
        layout.addWidget(self.default_ref_edit, 1, 1, 1, 2)
        layout.addWidget(self.default_ref_browse, 1, 3)
        layout.addWidget(self.default_ref_play, 1, 4)

        layout.addWidget(QLabel("默认配置 JSON"), 2, 0)
        layout.addWidget(self.default_config_edit, 2, 1, 1, 3)
        layout.addWidget(self.save_defaults_btn, 2, 4)

        return box

    def _build_task_editor_box(self) -> QWidget:
        box = QGroupBox("任务编辑")
        layout = QFormLayout(box)
        self.task_form_layout = layout

        self.task_text_edit = QPlainTextEdit()
        self.task_text_edit.setFixedHeight(80)
        self.task_ref_edit = QLineEdit()
        self.task_ref_edit.setPlaceholderText("https://example.com/ref.wav 或本地路径")
        self.task_ref_browse = QPushButton("浏览本地")
        self.task_ref_play = QPushButton("播放")
        ref_row = QHBoxLayout()
        ref_row.addWidget(self.task_ref_edit)
        ref_row.addWidget(self.task_ref_browse)
        ref_row.addWidget(self.task_ref_play)
        ref_wrapper = QWidget()
        ref_wrapper.setLayout(ref_row)

        self.task_config_edit = QPlainTextEdit()
        self.task_config_edit.setFixedHeight(80)
        self.task_config_edit.setPlaceholderText("只读，显示上方 UI 对应的当前配置 JSON")
        self.task_config_edit.setReadOnly(True)

        self.emo_method_combo = QComboBox()
        self.emo_method_combo.addItems(self._EMO_METHOD_OPTIONS)
        self.emo_method_combo.setCurrentIndex(0)

        self.emo_ref_edit = QLineEdit()
        self.emo_ref_edit.setPlaceholderText("https://example.com/emo.wav 或本地路径")
        self.emo_ref_browse = QPushButton("浏览本地")
        self.emo_ref_play = QPushButton("播放")
        emo_ref_row = QHBoxLayout()
        emo_ref_row.addWidget(self.emo_ref_edit)
        emo_ref_row.addWidget(self.emo_ref_browse)
        emo_ref_row.addWidget(self.emo_ref_play)
        emo_ref_wrapper = QWidget()
        emo_ref_wrapper.setLayout(emo_ref_row)

        self.emo_vector_slider_panel = QWidget()
        slider_layout = QGridLayout(self.emo_vector_slider_panel)
        self.emo_vector_sliders: list[QSlider] = []
        self.emo_vector_slider_values: list[QLabel] = []
        for index in range(8):
            tag = QLabel(self._EMO_VECTOR_TITLES[index])
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 100)
            slider.setValue(0)
            val_label = QLabel("0.00")
            slider.valueChanged.connect(lambda value, i=index: self._on_emo_slider_changed(i, value))
            slider_layout.addWidget(tag, index, 0)
            slider_layout.addWidget(slider, index, 1)
            slider_layout.addWidget(val_label, index, 2)
            self.emo_vector_sliders.append(slider)
            self.emo_vector_slider_values.append(val_label)
        self.emo_vector_slider_panel.setVisible(True)

        self.custom_prompt_edit = QPlainTextEdit()
        self.custom_prompt_edit.setFixedHeight(56)

        self.emo_method_edit = QLineEdit()
        self.emo_method_edit.setPlaceholderText("例如: vector 或 reference")

        self.emo_weight_spin = QDoubleSpinBox()
        self.emo_weight_spin.setRange(0.0, 1.0)
        self.emo_weight_spin.setDecimals(2)
        self.emo_weight_spin.setSingleStep(0.01)
        self.emo_weight_spin.setValue(0.65)

        self.emo_weight_slider = QSlider(Qt.Horizontal)
        self.emo_weight_slider.setRange(0, 100)
        self.emo_weight_slider.setValue(65)
        self.emo_weight_slider.valueChanged.connect(self._on_emo_weight_slider_changed)
        self.emo_weight_spin.valueChanged.connect(self._on_emo_weight_spin_changed)

        emo_weight_row = QHBoxLayout()
        emo_weight_row.addWidget(self.emo_weight_spin)
        emo_weight_row.addWidget(self.emo_weight_slider)
        emo_weight_wrapper = QWidget()
        emo_weight_wrapper.setLayout(emo_weight_row)

        self.segment_tokens_spin = QSpinBox()
        self.segment_tokens_spin.setRange(1, 100000)
        self.segment_tokens_spin.setValue(120)

        self.do_sample_check = QCheckBox("启用采样")
        self.do_sample_check.setChecked(True)

        self.top_p_spin = QDoubleSpinBox()
        self.top_p_spin.setRange(0.0, 1.0)
        self.top_p_spin.setSingleStep(0.01)
        self.top_p_spin.setValue(0.8)

        self.top_k_spin = QSpinBox()
        self.top_k_spin.setRange(0, 1000)
        self.top_k_spin.setValue(30)

        self.temperature_spin = QDoubleSpinBox()
        self.temperature_spin.setRange(0.0, 5.0)
        self.temperature_spin.setSingleStep(0.05)
        self.temperature_spin.setValue(0.8)

        self.length_penalty_spin = QDoubleSpinBox()
        self.length_penalty_spin.setRange(0.0, 5.0)
        self.length_penalty_spin.setSingleStep(0.05)
        self.length_penalty_spin.setValue(0.0)

        self.num_beams_spin = QSpinBox()
        self.num_beams_spin.setRange(1, 32)
        self.num_beams_spin.setValue(3)

        self.repetition_penalty_spin = QDoubleSpinBox()
        self.repetition_penalty_spin.setRange(0.0, 20.0)
        self.repetition_penalty_spin.setSingleStep(0.05)
        self.repetition_penalty_spin.setValue(10.0)

        self.max_mel_tokens_spin = QSpinBox()
        self.max_mel_tokens_spin.setRange(1, 100000)
        self.max_mel_tokens_spin.setValue(1500)

        self.segment_tokens_default_btn = QPushButton("默认")
        self.segment_tokens_default_btn.clicked.connect(
            lambda: self._reset_advanced_param("max_text_tokens_per_segment")
        )
        self.do_sample_default_btn = QPushButton("默认")
        self.do_sample_default_btn.clicked.connect(lambda: self._reset_advanced_param("do_sample"))
        self.top_p_default_btn = QPushButton("默认")
        self.top_p_default_btn.clicked.connect(lambda: self._reset_advanced_param("top_p"))
        self.top_k_default_btn = QPushButton("默认")
        self.top_k_default_btn.clicked.connect(lambda: self._reset_advanced_param("top_k"))
        self.temperature_default_btn = QPushButton("默认")
        self.temperature_default_btn.clicked.connect(lambda: self._reset_advanced_param("temperature"))
        self.length_penalty_default_btn = QPushButton("默认")
        self.length_penalty_default_btn.clicked.connect(lambda: self._reset_advanced_param("length_penalty"))
        self.num_beams_default_btn = QPushButton("默认")
        self.num_beams_default_btn.clicked.connect(lambda: self._reset_advanced_param("num_beams"))
        self.repetition_penalty_default_btn = QPushButton("默认")
        self.repetition_penalty_default_btn.clicked.connect(lambda: self._reset_advanced_param("repetition_penalty"))
        self.max_mel_tokens_default_btn = QPushButton("默认")
        self.max_mel_tokens_default_btn.clicked.connect(lambda: self._reset_advanced_param("max_mel_tokens"))

        advanced_content = QWidget()
        advanced_grid = QGridLayout(advanced_content)
        advanced_grid.addWidget(QLabel("分段最大文本 Token"), 0, 0)
        advanced_grid.addWidget(self.segment_tokens_spin, 0, 1)
        advanced_grid.addWidget(self.segment_tokens_default_btn, 0, 2)

        advanced_grid.addWidget(QLabel("do_sample"), 1, 0)
        advanced_grid.addWidget(self.do_sample_check, 1, 1)
        advanced_grid.addWidget(self.do_sample_default_btn, 1, 2)

        advanced_grid.addWidget(QLabel("Top P"), 2, 0)
        advanced_grid.addWidget(self.top_p_spin, 2, 1)
        advanced_grid.addWidget(self.top_p_default_btn, 2, 2)

        advanced_grid.addWidget(QLabel("Top K"), 3, 0)
        advanced_grid.addWidget(self.top_k_spin, 3, 1)
        advanced_grid.addWidget(self.top_k_default_btn, 3, 2)

        advanced_grid.addWidget(QLabel("温度"), 4, 0)
        advanced_grid.addWidget(self.temperature_spin, 4, 1)
        advanced_grid.addWidget(self.temperature_default_btn, 4, 2)

        advanced_grid.addWidget(QLabel("长度惩罚"), 5, 0)
        advanced_grid.addWidget(self.length_penalty_spin, 5, 1)
        advanced_grid.addWidget(self.length_penalty_default_btn, 5, 2)

        advanced_grid.addWidget(QLabel("束搜索数量"), 6, 0)
        advanced_grid.addWidget(self.num_beams_spin, 6, 1)
        advanced_grid.addWidget(self.num_beams_default_btn, 6, 2)

        advanced_grid.addWidget(QLabel("重复惩罚"), 7, 0)
        advanced_grid.addWidget(self.repetition_penalty_spin, 7, 1)
        advanced_grid.addWidget(self.repetition_penalty_default_btn, 7, 2)

        advanced_grid.addWidget(QLabel("最大 Mel Token"), 8, 0)
        advanced_grid.addWidget(self.max_mel_tokens_spin, 8, 1)
        advanced_grid.addWidget(self.max_mel_tokens_default_btn, 8, 2)

        self.advanced_toggle_btn = QPushButton("高级参数 ▼")
        self.advanced_toggle_btn.setCheckable(True)
        self.advanced_toggle_btn.setChecked(True)
        self.advanced_toggle_btn.toggled.connect(self._toggle_advanced_params)
        self.advanced_content_widget = advanced_content

        btn_row = QHBoxLayout()
        self.add_task_btn = QPushButton("添加任务")
        self.batch_from_lines_btn = QPushButton("按行批量添加")
        btn_row.addWidget(self.add_task_btn)
        btn_row.addWidget(self.batch_from_lines_btn)
        btn_wrapper = QWidget()
        btn_wrapper.setLayout(btn_row)
        self.task_editor_action_bar = btn_wrapper

        self.task_ref_browse.clicked.connect(self.pick_task_ref)
        self.task_ref_play.clicked.connect(self.play_task_ref)
        self.emo_method_combo.currentIndexChanged.connect(self._on_emo_method_changed)
        self.emo_ref_browse.clicked.connect(self.pick_emotion_ref)
        self.emo_ref_play.clicked.connect(self.play_emotion_ref)
        self.add_task_btn.clicked.connect(self.add_task)
        self.batch_from_lines_btn.clicked.connect(self.add_tasks_from_lines)
        self.emo_ref_edit.textChanged.connect(self._refresh_task_config_preview)
        self.custom_prompt_edit.textChanged.connect(self._refresh_task_config_preview)
        self.emo_weight_spin.valueChanged.connect(self._refresh_task_config_preview)
        self.segment_tokens_spin.valueChanged.connect(self._refresh_task_config_preview)
        self.do_sample_check.stateChanged.connect(self._refresh_task_config_preview)
        self.top_p_spin.valueChanged.connect(self._refresh_task_config_preview)
        self.top_k_spin.valueChanged.connect(self._refresh_task_config_preview)
        self.temperature_spin.valueChanged.connect(self._refresh_task_config_preview)
        self.length_penalty_spin.valueChanged.connect(self._refresh_task_config_preview)
        self.num_beams_spin.valueChanged.connect(self._refresh_task_config_preview)
        self.repetition_penalty_spin.valueChanged.connect(self._refresh_task_config_preview)
        self.max_mel_tokens_spin.valueChanged.connect(self._refresh_task_config_preview)

        layout.addRow("文本", self.task_text_edit)
        layout.addRow("参考音频URL/路径", ref_wrapper)
        layout.addRow("情感控制方式", self.emo_method_combo)
        self.emo_weight_row_widget = emo_weight_wrapper
        layout.addRow("情感强度", self.emo_weight_row_widget)
        self.emo_vector_row_widget = self.emo_vector_slider_panel
        self.emo_ref_row_widget = emo_ref_wrapper
        layout.addRow("情感向量", self.emo_vector_row_widget)
        layout.addRow("情感参考音频URL/路径", self.emo_ref_row_widget)
        layout.addRow("自定义述语", self.custom_prompt_edit)
        layout.addRow(self.advanced_toggle_btn)
        layout.addRow(self.advanced_content_widget)
        layout.addRow("UI 配置 JSON（只读）", self.task_config_edit)

        self._on_emo_method_changed(self.emo_method_combo.currentIndex())

        return box

    def _build_task_table_box(self) -> QWidget:
        page = QWidget()
        page_layout = QHBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(10)

        left_box = QGroupBox("批量任务")
        left_layout = QVBoxLayout(left_box)

        self.task_table = ReorderableTaskTable(0, 14)
        self.task_table.setHorizontalHeaderLabels(
            ["拖动", "ID", "文本", "参考音频", "定稿", "状态", "进度", "需重生成", "音频", "错误", "播放", "生成", "删除", "详情"]
        )
        self.task_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.task_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.task_table.setSelectionMode(QTableWidget.SingleSelection)
        self.task_table.itemSelectionChanged.connect(self.on_task_selected)
        self.task_table.rows_reordered.connect(self._on_table_rows_reordered)

        controls = QHBoxLayout()
        self.start_batch_btn = QPushButton("开始批量生成")
        self.pause_batch_btn = QPushButton("批量暂停")
        self.retry_failed_btn = QPushButton("重试失败任务")
        self.generate_selected_btn = QPushButton("生成选中任务")
        self.delete_selected_btn = QPushButton("删除选中任务")
        self.play_btn = QPushButton("播放选中项")
        self.play_btn.setEnabled(False)
        self.start_batch_btn.clicked.connect(self.start_batch)
        self.pause_batch_btn.clicked.connect(self.pause_queued_tasks)
        self.retry_failed_btn.clicked.connect(self.retry_failed)
        self.generate_selected_btn.clicked.connect(self.generate_selected_task)
        self.delete_selected_btn.clicked.connect(self.delete_selected_task)
        self.play_btn.clicked.connect(self.play_selected)

        controls.addWidget(self.start_batch_btn)
        controls.addWidget(self.pause_batch_btn)
        controls.addWidget(self.retry_failed_btn)
        controls.addWidget(self.generate_selected_btn)
        controls.addWidget(self.delete_selected_btn)
        controls.addWidget(self.play_btn)

        left_layout.addWidget(self.task_table)
        left_layout.addLayout(controls)

        right_panel = self._build_task_detail_panel()
        right_panel.setMinimumWidth(420)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_box)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 2)

        page_layout.addWidget(splitter)
        return page

    def _apply_config_to_fields(self) -> None:
        self.webui_url_edit.setText(self.app_config.webui_url)
        self.host_edit.setText(self.app_config.webui_host)
        self.port_spin.setValue(self.app_config.webui_port)
        self.conc_spin.setValue(self.app_config.concurrency)
        self.timeout_spin.setValue(self.app_config.request_timeout_sec)

    def _restore_persisted_state(self) -> None:
        draft = self.app_config.task_editor_draft or {}
        self._apply_task_editor_draft(draft)

        last_path = (self.app_config.last_task_set_path or "").strip()
        if last_path:
            candidate = Path(last_path)
            if candidate.exists() and candidate.is_dir():
                try:
                    self._load_task_set(candidate)
                except Exception:
                    pass

        max_index = max(0, self.main_tabs.count() - 1)
        self.main_tabs.setCurrentIndex(min(max_index, max(0, self.app_config.last_active_tab)))
        self._select_task_by_id(self.app_config.last_selected_task_id)
        self._refresh_task_config_preview()

    def _collect_task_editor_draft(self) -> dict:
        return {
            "task_text": self.task_text_edit.toPlainText(),
            "task_ref": self.task_ref_edit.text().strip(),
            "emo_method": self.emo_method_combo.currentText(),
            "emo_ref": self.emo_ref_edit.text().strip(),
            "custom_prompt": self.custom_prompt_edit.toPlainText(),
            "emo_weight": float(self.emo_weight_spin.value()),
            "vector": self._vector_from_sliders(),
            "advanced_expanded": bool(self.advanced_toggle_btn.isChecked()),
            "max_text_tokens_per_segment": int(self.segment_tokens_spin.value()),
            "do_sample": bool(self.do_sample_check.isChecked()),
            "top_p": float(self.top_p_spin.value()),
            "top_k": int(self.top_k_spin.value()),
            "temperature": float(self.temperature_spin.value()),
            "length_penalty": float(self.length_penalty_spin.value()),
            "num_beams": int(self.num_beams_spin.value()),
            "repetition_penalty": float(self.repetition_penalty_spin.value()),
            "max_mel_tokens": int(self.max_mel_tokens_spin.value()),
        }

    def _apply_task_editor_draft(self, draft: dict) -> None:
        if not isinstance(draft, dict):
            return
        self.task_text_edit.setPlainText(str(draft.get("task_text", "")))
        self.task_ref_edit.setText(str(draft.get("task_ref", "")))
        self.emo_ref_edit.setText(str(draft.get("emo_ref", "")))
        self.custom_prompt_edit.setPlainText(str(draft.get("custom_prompt", "")))

        method = str(draft.get("emo_method", "")).strip()
        if method in self._EMO_METHOD_OPTIONS:
            self.emo_method_combo.setCurrentText(method)

        vector = draft.get("vector")
        if isinstance(vector, list):
            self._set_vector_to_sliders([self._safe_float(v, 0.0) for v in vector])

        self.emo_weight_spin.setValue(self._safe_float(draft.get("emo_weight"), self.emo_weight_spin.value()))
        self.segment_tokens_spin.setValue(self._safe_int(draft.get("max_text_tokens_per_segment"), self.segment_tokens_spin.value()))
        self.do_sample_check.setChecked(bool(draft.get("do_sample", self.do_sample_check.isChecked())))
        self.top_p_spin.setValue(self._safe_float(draft.get("top_p"), self.top_p_spin.value()))
        self.top_k_spin.setValue(self._safe_int(draft.get("top_k"), self.top_k_spin.value()))
        self.temperature_spin.setValue(self._safe_float(draft.get("temperature"), self.temperature_spin.value()))
        self.length_penalty_spin.setValue(self._safe_float(draft.get("length_penalty"), self.length_penalty_spin.value()))
        self.num_beams_spin.setValue(self._safe_int(draft.get("num_beams"), self.num_beams_spin.value()))
        self.repetition_penalty_spin.setValue(self._safe_float(draft.get("repetition_penalty"), self.repetition_penalty_spin.value()))
        self.max_mel_tokens_spin.setValue(self._safe_int(draft.get("max_mel_tokens"), self.max_mel_tokens_spin.value()))

        advanced_expanded = bool(draft.get("advanced_expanded", True))
        self.advanced_toggle_btn.setChecked(advanced_expanded)
        self._toggle_advanced_params(advanced_expanded)
        self._refresh_task_config_preview()

    def _persist_runtime_state(self) -> None:
        self.app_config.webui_url = self.webui_url_edit.text().strip()
        self.app_config.webui_host = self.host_edit.text().strip() or self.app_config.webui_host
        self.app_config.webui_port = int(self.port_spin.value())
        self.app_config.concurrency = int(self.conc_spin.value())
        self.app_config.request_timeout_sec = int(self.timeout_spin.value())
        self.app_config.last_task_set_path = self.task_set_path_edit.text().strip()
        self.app_config.last_active_tab = int(self.main_tabs.currentIndex())
        self.app_config.task_editor_draft = self._collect_task_editor_draft()
        self.app_config.last_selected_task_id = self._selected_task_id() or ""
        save_app_config(self.app_config)

    def save_global_settings(self) -> None:
        url_text = self.webui_url_edit.text().strip()
        if url_text:
            normalized = self._normalize_webui_url(url_text)
            if not normalized:
                self._warn("WebUI URL 无效，请输入 http:// 或 https:// 地址")
                return
            self.app_config.webui_url = normalized
            self.webui_url_edit.setText(normalized)
        else:
            self.app_config.webui_url = ""

        host = self.host_edit.text().strip()
        if not self.app_config.webui_url and not host:
            self._warn("WebUI URL 为空时，主机地址不能为空")
            return
        self.app_config.webui_host = host
        self.app_config.webui_port = self.port_spin.value()
        self.app_config.concurrency = self.conc_spin.value()
        self.app_config.request_timeout_sec = self.timeout_spin.value()
        save_app_config(self.app_config)
        self.statusBar().showMessage("已保存全局设置", 3000)

    def create_task_set(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择任务集父目录")
        if not path:
            return
        name, ok = self._prompt_text("任务集名称", "请输入任务集目录名")
        if not ok or not name.strip():
            return
        full = Path(path) / name.strip()
        self._load_task_set(full)

    def open_task_set(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "打开任务集")
        if not path:
            return
        self._load_task_set(Path(path))

    def _load_task_set(self, path: Path) -> None:
        self.storage = TaskSetStorage(path)
        self.storage.bootstrap()
        self.task_set_path_edit.setText(str(path))
        self.defaults = self.storage.load_defaults()
        self.default_ref_edit.setText(self.defaults.reference_audio)
        self.default_config_edit.setPlainText(json.dumps(self.defaults.config, ensure_ascii=False, indent=2))
        self.tasks = self.storage.list_tasks()
        self._reconcile_generating_tasks_with_webui()
        self._refresh_table()
        self._refresh_task_config_preview()
        self.app_config.last_task_set_path = str(path)
        save_app_config(self.app_config)
        self.statusBar().showMessage(f"已加载任务集: {path}", 3000)

    def _reconcile_generating_tasks_with_webui(self) -> None:
        if not self.storage:
            return

        generating = [task for task in self.tasks if task.status == "generating"]
        if not generating:
            return

        client = IndexTTSClient(self.app_config)
        is_busy = client.is_webui_generating()

        if is_busy is True:
            self.statusBar().showMessage("检测到 WebUI 仍在生成，保留进行中状态", 3000)
            return

        if is_busy is None:
            self.statusBar().showMessage("无法确认 WebUI 生成状态，保留进行中状态", 3000)
            return

        changed = 0
        for task in generating:
            task.status = "pending"
            task.progress = 0
            task.error = ""
            self.storage.save_task(task)
            changed += 1

        if changed:
            self.statusBar().showMessage(f"已重置 {changed} 个任务为待处理", 3000)

    def pick_default_ref(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(self, "选择本地默认参考音频", filter="音频文件 (*.wav *.mp3)")
        if file_path:
            self.default_ref_edit.setText(file_path)

    def play_default_ref(self) -> None:
        self._play_audio_by_path_text(self.default_ref_edit.text())

    def save_defaults(self) -> None:
        if not self.storage:
            self._warn("请先打开或新建任务集")
            return
        cfg = self._parse_json(self.default_config_edit.toPlainText())
        if cfg is None:
            return
        self.defaults = TaskSetDefaults(reference_audio=self.default_ref_edit.text().strip(), config=cfg)
        self.storage.save_defaults(self.defaults)
        self.statusBar().showMessage("已保存默认参数", 3000)

    def pick_task_ref(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(self, "选择本地任务参考音频", filter="音频文件 (*.wav *.mp3)")
        if file_path:
            self.task_ref_edit.setText(file_path)

    def play_task_ref(self) -> None:
        self._play_audio_by_path_text(self.task_ref_edit.text())

    def pick_detail_ref(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(self, "选择本地任务参考音频", filter="音频文件 (*.wav *.mp3)")
        if file_path:
            self.detail_task_ref_edit.setText(file_path)

    def play_detail_ref(self) -> None:
        self._play_audio_by_path_text(self.detail_task_ref_edit.text())

    def pick_detail_emotion_ref(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(self, "选择本地情感参考音频", filter="音频文件 (*.wav *.mp3)")
        if file_path:
            self.detail_emo_ref_edit.setText(file_path)

    def play_detail_emotion_ref(self) -> None:
        self._play_audio_by_path_text(self.detail_emo_ref_edit.text())

    def pick_emotion_ref(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择本地情感参考音频",
            filter="音频文件 (*.wav *.mp3)",
        )
        if file_path:
            self.emo_ref_edit.setText(file_path)

    def play_emotion_ref(self) -> None:
        self._play_audio_by_path_text(self.emo_ref_edit.text())

    def add_task(self) -> None:
        if not self.storage:
            self._warn("请先打开或新建任务集")
            return

        text = self.task_text_edit.toPlainText().strip()
        if not text:
            self._warn("任务文本不能为空")
            return
        cfg = self._build_task_config(default_config=self.defaults.config)
        if cfg is None:
            return

        ref = self.task_ref_edit.text().strip() or self.defaults.reference_audio
        task = TaskRecord(
            task_id=uuid.uuid4().hex,
            text=text,
            reference_audio=ref,
            order=self._next_task_order(),
            config=cfg,
        )
        self.storage.save_task(task)
        self.tasks.append(task)
        self._refresh_table()

    def add_tasks_from_lines(self) -> None:
        if not self.storage:
            self._warn("请先打开或新建任务集")
            return

        lines = [line.strip() for line in self.task_text_edit.toPlainText().splitlines() if line.strip()]
        if not lines:
            self._warn("未找到非空文本行")
            return

        cfg = self._build_task_config(default_config=self.defaults.config)
        if cfg is None:
            return

        ref = self.task_ref_edit.text().strip() or self.defaults.reference_audio
        next_order = self._next_task_order()
        for line in lines:
            task = TaskRecord(
                task_id=uuid.uuid4().hex,
                text=line,
                reference_audio=ref,
                order=next_order,
                config=dict(cfg),
            )
            self.storage.save_task(task)
            self.tasks.append(task)
            next_order += 1
        self._refresh_table()

    def update_selected_task(self) -> None:
        idx = self._selected_task_index()
        if idx is None or not self.storage:
            self._warn("请先选择一个任务")
            return

        task = self.tasks[idx]
        cfg = self._build_task_config(default_config=task.config)
        if cfg is None:
            return

        task.text = self.task_text_edit.toPlainText().strip()
        task.reference_audio = self.task_ref_edit.text().strip()
        task.config = cfg
        mark_regen_if_changed(task)
        self.storage.save_task(task)
        self._refresh_table()

    def delete_selected_task(self) -> None:
        idx = self._selected_task_index()
        if idx is None or not self.storage:
            self._warn("请先选择一个任务")
            return
        task = self.tasks[idx]
        self.storage.delete_task(task)
        self.storage.remove_audio_if_exists(task)
        self.tasks.pop(idx)
        self._refresh_table()

    def generate_selected_task(self) -> None:
        idx = self._selected_task_index()
        if idx is None or not self.storage:
            self._warn("请先选择一个任务")
            return
        task = self.tasks[idx]
        if not self._ensure_task_detail_synced(task.task_id):
            return
        task = self.tasks[idx]
        if not self._validate_task_for_generate(task):
            return

        if task.status == "done":
            task.needs_regen = True
            self.storage.save_task(task)
        self._start_runner([task], done_message=f"任务生成完成: {task.task_id}", apply_detail_task_id=task.task_id)

    def generate_row(self, row_index: int) -> None:
        if row_index < 0 or row_index >= len(self.tasks):
            return
        self.task_table.selectRow(row_index)
        self.generate_selected_task()

    def delete_row(self, row_index: int) -> None:
        if row_index < 0 or row_index >= len(self.tasks):
            return
        self.task_table.selectRow(row_index)
        self.delete_selected_task()

    def start_batch(self) -> None:
        if not self.storage:
            self._warn("请先打开或新建任务集")
            return
        if not self._validate_before_batch():
            return

        self._start_runner(self.tasks, done_message="批量生成完成")

    def _start_runner(self, tasks: list[TaskRecord], done_message: str, apply_detail_task_id: str | None = None) -> None:
        if self._batch_thread and self._batch_thread.isRunning():
            self._warn("批量生成正在进行中")
            return
        if not self.storage:
            self._warn("请先打开或新建任务集")
            return

        target_detail_id = (apply_detail_task_id or self._selected_detail_task_id()).strip()
        if target_detail_id and not self._ensure_task_detail_synced(target_detail_id):
            return

        # Release file handles before regeneration to avoid playback occupation issues.
        self._reset_global_audio_state()

        client = IndexTTSClient(self.app_config)
        runner = BatchRunner(
            self.storage,
            client,
            max_workers=self.app_config.concurrency,
            release_file_callback=self._release_audio_file_lock,
        )
        self._batch_runner = runner
        self._batch_done_message = done_message
        self._set_batch_controls_running(True)

        self._batch_thread = QThread(self)
        self._batch_worker = BatchWorker(runner, tasks)
        self._batch_worker.moveToThread(self._batch_thread)

        self._batch_thread.started.connect(self._batch_worker.run)
        self._batch_worker.progress.connect(self._on_progress)
        self._batch_worker.finished.connect(self._on_batch_finished)
        self._batch_worker.failed.connect(self._on_batch_failed)

        self._batch_worker.finished.connect(self._batch_thread.quit)
        self._batch_worker.failed.connect(lambda _msg: self._batch_thread.quit())
        self._batch_thread.finished.connect(self._cleanup_batch_worker)
        self._batch_thread.start()

    def _set_batch_controls_running(self, running: bool) -> None:
        self.start_batch_btn.setEnabled(not running)
        self.pause_batch_btn.setEnabled(True)
        self.retry_failed_btn.setEnabled(not running)
        self.generate_selected_btn.setEnabled(not running)
        self.delete_selected_btn.setEnabled(not running)
        self.task_table.setEnabled(True)

    def _on_batch_finished(self) -> None:
        self._set_batch_controls_running(False)
        if self.storage is not None:
            try:
                self.tasks = self.storage.list_tasks()
            except Exception:
                pass
        self.statusBar().showMessage(self._batch_done_message, 3000)
        self._refresh_table()

    def _on_batch_failed(self, message: str) -> None:
        self._set_batch_controls_running(False)
        if self.storage is not None:
            try:
                self.tasks = self.storage.list_tasks()
            except Exception:
                pass
        self._warn(f"批量生成失败: {message}")
        self._refresh_table()

    def _cleanup_batch_worker(self) -> None:
        if self._batch_worker is not None:
            self._batch_worker.deleteLater()
            self._batch_worker = None
        if self._batch_thread is not None:
            self._batch_thread.deleteLater()
            self._batch_thread = None
        self._batch_runner = None

    def retry_failed(self) -> None:
        for task in self.tasks:
            if task.status == "failed":
                task.status = "pending"
                task.progress = 0
                task.error = ""
                task.needs_regen = True
                if self.storage:
                    self.storage.save_task(task)
        self.start_batch()

    def pause_queued_tasks(self) -> None:
        if not self.storage:
            self._warn("请先打开或新建任务集")
            return

        changed = 0
        for task in self.tasks:
            if task.status != "queued":
                continue
            task.status = "pending"
            task.progress = 0
            task.error = ""
            self.storage.save_task(task)
            changed += 1

        if changed == 0:
            self.statusBar().showMessage("当前没有排队中的任务", 3000)
            return

        self.statusBar().showMessage(f"已暂停 {changed} 个排队任务", 3000)
        self._refresh_table()

    def play_selected(self) -> None:
        idx = self._selected_task_index()
        if idx is None:
            self._warn("请先选择一个任务")
            return
        task = self.tasks[idx]
        if not task.audio_file:
            self._warn("当前任务没有可播放音频")
            return

        path = Path(task.audio_file)
        if self.storage and not path.is_absolute():
            path = self.storage.task_set_dir / path

        self._play_with_global_player(path)

    def on_task_selected(self) -> None:
        if self._handling_table_selection_change:
            return

        idx = self._selected_task_index()
        if idx is None:
            self.play_btn.setEnabled(False)
            return

        task = self.tasks[idx]

        previous_task_id = self._active_detail_task_id
        if previous_task_id and previous_task_id != task.task_id:
            if not self._apply_detail_panel_to_task(previous_task_id):
                self._handling_table_selection_change = True
                try:
                    self._select_task_by_id(previous_task_id)
                finally:
                    self._handling_table_selection_change = False
                self._set_detail_task_id(previous_task_id)
                self._refresh_detail_task_preview(previous_task_id)
                self._active_detail_task_id = previous_task_id
                return

        self.play_btn.setEnabled(bool(task.audio_file))
        self.app_config.last_selected_task_id = task.task_id
        self._set_detail_task_id(task.task_id)
        self._refresh_detail_task_preview(task.task_id)
        self._active_detail_task_id = task.task_id
        save_app_config(self.app_config)

    def _refresh_table(self) -> None:
        self._sort_tasks_in_place()
        v_scroll = self.task_table.verticalScrollBar().value()
        h_scroll = self.task_table.horizontalScrollBar().value()
        selected_task_id = self._selected_task_id() or self.app_config.last_selected_task_id
        self.task_table.setRowCount(0)
        self.task_table.setColumnCount(14)
        for task in self.tasks:
            row = self.task_table.rowCount()
            self.task_table.insertRow(row)
            values_by_col = {
                1: task.task_id,
                2: task.text,
                3: task.reference_audio,
                5: self._STATUS_DISPLAY.get(task.status, task.status),
                7: "是" if task.needs_regen else "否",
                8: task.audio_file,
                9: task.error,
            }
            handle_item = QTableWidgetItem("≡")
            handle_item.setTextAlignment(Qt.AlignCenter)
            handle_item.setFlags(handle_item.flags() ^ Qt.ItemIsEditable)
            self.task_table.setItem(row, 0, handle_item)
            for col, val in values_by_col.items():
                item = QTableWidgetItem(val)
                item.setFlags(item.flags() ^ Qt.ItemIsEditable)
                self.task_table.setItem(row, col, item)

            task_id = task.task_id

            final_check = QCheckBox()
            final_check.setChecked(bool(task.is_final))
            final_check.stateChanged.connect(
                lambda state, tid=task_id: self._set_task_final_by_id(
                    tid,
                    state == Qt.CheckState.Checked.value,
                )
            )
            final_wrapper = QWidget()
            final_layout = QHBoxLayout(final_wrapper)
            final_layout.setContentsMargins(0, 0, 0, 0)
            final_layout.setAlignment(Qt.AlignCenter)
            final_layout.addWidget(final_check)
            self.task_table.setCellWidget(row, 4, final_wrapper)

            progress = QProgressBar()
            if task.status == "generating":
                # Show busy state during long blocking generation requests.
                progress.setRange(0, 0)
            else:
                progress.setRange(0, 100)
                progress.setValue(task.progress)
            self.task_table.setCellWidget(row, 6, progress)

            play_btn = QPushButton("播放")
            play_btn.setEnabled(bool(task.audio_file))
            play_btn.clicked.connect(lambda _checked=False, tid=task_id: self.play_task_by_id(tid))
            self.task_table.setCellWidget(row, 10, play_btn)

            generate_btn = QPushButton("取消生成" if task.status == "queued" else "生成")
            if task.status == "queued":
                generate_btn.clicked.connect(lambda _checked=False, tid=task_id: self.cancel_task_by_id(tid))
            elif task.status == "generating":
                generate_btn.setText("生成中")
                generate_btn.setEnabled(False)
            else:
                generate_btn.clicked.connect(lambda _checked=False, tid=task_id: self.generate_task_by_id(tid))
            self.task_table.setCellWidget(row, 11, generate_btn)

            delete_btn = QPushButton("删除")
            delete_btn.clicked.connect(lambda _checked=False, tid=task_id: self.delete_task_by_id(tid))
            self.task_table.setCellWidget(row, 12, delete_btn)

            detail_btn = QPushButton("详情配置")
            detail_btn.clicked.connect(lambda _checked=False, tid=task_id: self.open_task_detail_by_id(tid))
            self.task_table.setCellWidget(row, 13, detail_btn)

        if selected_task_id:
            self._select_task_by_id(selected_task_id)
        self._refresh_detail_task_ids(selected_task_id)
        self.task_table.setColumnWidth(0, 52)
        self.task_table.verticalScrollBar().setValue(v_scroll)
        self.task_table.horizontalScrollBar().setValue(h_scroll)

    def _validate_task_for_generate(self, task: TaskRecord) -> bool:
        if not task.text.strip():
            self._warn(f"任务 {task.task_id} 的文本为空")
            return False
        if not task.reference_audio.strip():
            self._warn(f"任务 {task.task_id} 缺少参考音频")
            return False
        url_text = self.webui_url_edit.text().strip()
        if url_text and not self._normalize_webui_url(url_text):
            self._warn("WebUI URL 无效，请输入 http:// 或 https:// 地址")
            return False
        if not url_text:
            host = self.host_edit.text().strip()
            if not host:
                self._warn("WebUI URL 为空时，主机地址不能为空")
                return False
            if self.port_spin.value() <= 0:
                self._warn("端口无效")
                return False
        self.save_global_settings()
        return True

    def _selected_task_id(self) -> str | None:
        idx = self._selected_row_index()
        if idx is None:
            return None
        id_item = self.task_table.item(idx, 1)
        if id_item is None:
            return None
        task_id = id_item.text().strip()
        return task_id or None

    def _selected_task_index(self) -> int | None:
        task_id = self._selected_task_id()
        if not task_id:
            return None
        return self._find_task_index_by_id(task_id)

    def _select_task_by_id(self, task_id: str) -> bool:
        target = task_id.strip()
        if not target:
            return False
        for row, task in enumerate(self.tasks):
            if task.task_id == target:
                self.task_table.selectRow(row)
                return True
        return False

    def _find_task_index_by_id(self, task_id: str) -> int | None:
        target = task_id.strip()
        if not target:
            return None
        for index, task in enumerate(self.tasks):
            if task.task_id == target:
                return index
        return None

    def _next_task_order(self) -> int:
        if not self.tasks:
            return 1
        return max(task.order for task in self.tasks) + 1

    def _sort_tasks_in_place(self) -> None:
        self.tasks.sort(key=lambda task: (task.order, task.updated_at, task.task_id))

    def _selected_detail_task_id(self) -> str:
        return self.detail_task_id_combo.currentText().strip()

    def _set_detail_task_id(self, task_id: str) -> None:
        target = task_id.strip()
        if not target:
            return
        idx = self.detail_task_id_combo.findText(target)
        if idx >= 0:
            self.detail_task_id_combo.blockSignals(True)
            self.detail_task_id_combo.setCurrentIndex(idx)
            self.detail_task_id_combo.blockSignals(False)

    def _refresh_detail_task_ids(self, selected_task_id: str | None = None) -> None:
        current = (selected_task_id or self._selected_detail_task_id() or self.app_config.last_selected_task_id).strip()
        self.detail_task_id_combo.blockSignals(True)
        self.detail_task_id_combo.clear()
        for task in self.tasks:
            self.detail_task_id_combo.addItem(task.task_id)
        self.detail_task_id_combo.blockSignals(False)

        if current:
            self._set_detail_task_id(current)
            self._refresh_detail_task_preview(current)
            self._active_detail_task_id = current
            return

        if self.detail_task_id_combo.count() > 0:
            first_id = self.detail_task_id_combo.itemText(0)
            self._refresh_detail_task_preview(first_id)
            self._active_detail_task_id = first_id
        else:
            self._refresh_detail_task_preview("")
            self._active_detail_task_id = ""

    def _refresh_detail_task_preview(self, task_id: str) -> None:
        idx = self._find_task_index_by_id(task_id)
        if idx is None:
            self.detail_task_text_edit.setPlainText("")
            self.detail_task_ref_edit.setText("")
            self.detail_is_final_check.blockSignals(True)
            self.detail_is_final_check.setChecked(False)
            self.detail_is_final_check.blockSignals(False)
            self._load_detail_task_config_into_panel({})
            return
        task = self.tasks[idx]
        self.detail_task_text_edit.setPlainText(task.text)
        self.detail_task_ref_edit.setText(task.reference_audio)
        self.detail_is_final_check.blockSignals(True)
        self.detail_is_final_check.setChecked(bool(task.is_final))
        self.detail_is_final_check.blockSignals(False)
        self._load_detail_task_config_into_panel(task.config or {})

    def _on_detail_task_id_changed(self, _index: int) -> None:
        task_id = self._selected_detail_task_id()
        if not task_id:
            self._refresh_detail_task_preview("")
            return

        previous_task_id = self._active_detail_task_id
        if previous_task_id and previous_task_id != task_id:
            if not self._apply_detail_panel_to_task(previous_task_id):
                self._set_detail_task_id(previous_task_id)
                self._refresh_detail_task_preview(previous_task_id)
                return

        self._refresh_detail_task_preview(task_id)
        self._active_detail_task_id = task_id
        self.app_config.last_selected_task_id = task_id
        save_app_config(self.app_config)

    def _apply_detail_panel_to_task(self, task_id: str) -> bool:
        target = task_id.strip()
        if not target:
            return True
        idx = self._find_task_index_by_id(target)
        if idx is None:
            return True

        self._commit_pending_numeric_inputs(detail_only=True)
        task = self.tasks[idx]
        cfg = self._build_detail_task_config(default_config=task.config)
        if cfg is None:
            return False

        task.text = self.detail_task_text_edit.toPlainText().strip()
        task.reference_audio = self.detail_task_ref_edit.text().strip()
        task.is_final = bool(self.detail_is_final_check.isChecked())
        task.config = cfg
        mark_regen_if_changed(task)
        if self.storage is not None:
            self.storage.save_task(task)
        self.tasks[idx] = task
        self._sort_tasks_in_place()
        return True

    def _on_table_rows_reordered(self, source_row: int, target_row: int) -> None:
        if not self.storage:
            return

        if source_row < 0 or source_row >= len(self.tasks):
            return
        if target_row < 0 or target_row >= len(self.tasks):
            return

        moved_task = self.tasks.pop(source_row)
        self.tasks.insert(target_row, moved_task)
        moved_task_id = moved_task.task_id

        changed = False
        for index, task in enumerate(self.tasks, start=1):
            if task.order != index:
                task.order = index
                changed = True

        if not changed:
            return

        for task in self.tasks:
            self.storage.save_task(task)
        self.statusBar().showMessage("已更新任务顺序", 3000)
        self._refresh_table()
        if moved_task_id:
            self._select_task_by_id(moved_task_id)
            self._set_detail_task_id(moved_task_id)
            self._refresh_detail_task_preview(moved_task_id)
            self._active_detail_task_id = moved_task_id
            self.app_config.last_selected_task_id = moved_task_id
            save_app_config(self.app_config)

    def _set_task_final_by_id(self, task_id: str, checked: bool) -> None:
        idx = self._find_task_index_by_id(task_id)
        if idx is None:
            return
        task = self.tasks[idx]
        if task.is_final == checked:
            return
        task.is_final = checked
        self.tasks[idx] = task
        if self.storage is not None:
            self.storage.save_task(task)
        if self._selected_detail_task_id() == task_id:
            self.detail_is_final_check.blockSignals(True)
            self.detail_is_final_check.setChecked(checked)
            self.detail_is_final_check.blockSignals(False)

    def _on_detail_is_final_changed(self, state: int) -> None:
        task_id = self._selected_detail_task_id()
        if not task_id:
            return
        self._set_task_final_by_id(task_id, state == Qt.CheckState.Checked.value)

    def fill_from_generated_snapshot(self) -> None:
        task_id = self._selected_detail_task_id()
        idx = self._find_task_index_by_id(task_id)
        if idx is None:
            self._warn("请选择有效的任务ID")
            return

        task = self.tasks[idx]
        generated_cfg = dict(task.generated_config or {})
        generated_text = (task.generated_text or "").strip()
        generated_ref = (task.generated_reference_audio or "").strip()
        if not generated_cfg and not generated_text and not generated_ref:
            self._warn("当前任务还没有可回填的生成配置")
            return

        if generated_text:
            self.detail_task_text_edit.setPlainText(generated_text)
        if generated_ref:
            self.detail_task_ref_edit.setText(generated_ref)
        self._load_detail_task_config_into_panel(generated_cfg)

        if self._apply_detail_panel_to_task(task.task_id):
            self.statusBar().showMessage(f"已回填当前语音配置: {task.task_id}", 3000)
            self._refresh_table()

    def _build_detail_task_config(self, default_config: dict) -> dict | None:
        cfg = dict(default_config or {})
        explicit_keys: set[str] = set()

        emo_method = self.detail_emo_method_combo.currentText().strip()
        if emo_method:
            cfg["emo_control_method"] = emo_method

        parsed_vector: list[float] | None = None
        if emo_method == self._EMO_METHOD_VECTOR:
            parsed_vector = self._detail_vector_from_sliders()
        if parsed_vector is not None:
            cfg["emotion_vector"] = parsed_vector
            cfg["emo_vector"] = parsed_vector
            for index, value in enumerate(parsed_vector, start=1):
                key = f"vec{index}"
                if key not in explicit_keys:
                    cfg[key] = float(value)
        else:
            cfg.pop("emotion_vector", None)
            cfg.pop("emo_vector", None)
            for index in range(1, 9):
                key = f"vec{index}"
                if key not in explicit_keys:
                    cfg.pop(key, None)

        emo_ref_path = self.detail_emo_ref_edit.text().strip()
        if emo_method == self._EMO_METHOD_REF and emo_ref_path:
            cfg["emo_ref_path"] = emo_ref_path
            cfg["emotion_ref_audio"] = emo_ref_path
        else:
            cfg.pop("emo_ref_path", None)
            cfg.pop("emotion_ref_audio", None)

        custom_prompt = self.detail_custom_prompt_edit.toPlainText().strip()
        if custom_prompt:
            cfg["custom_prompt"] = custom_prompt
        else:
            cfg.pop("custom_prompt", None)

        emo_weight_value = float(self.detail_emo_weight_spin.value())
        self._set_cfg_if_non_default_or_explicit(
            cfg,
            "emo_weight",
            emo_weight_value,
            0.65,
            explicit_keys,
        )
        self._set_cfg_if_non_default_or_explicit(
            cfg,
            "max_text_tokens_per_segment",
            int(self.detail_segment_tokens_spin.value()),
            int(self._WEBUI_ADV_DEFAULTS["max_text_tokens_per_segment"]),
            explicit_keys,
        )
        self._set_cfg_if_non_default_or_explicit(
            cfg,
            "do_sample",
            bool(self.detail_do_sample_check.isChecked()),
            bool(self._WEBUI_ADV_DEFAULTS["do_sample"]),
            explicit_keys,
        )
        self._set_cfg_if_non_default_or_explicit(
            cfg,
            "top_p",
            float(self.detail_top_p_spin.value()),
            float(self._WEBUI_ADV_DEFAULTS["top_p"]),
            explicit_keys,
        )
        self._set_cfg_if_non_default_or_explicit(
            cfg,
            "top_k",
            int(self.detail_top_k_spin.value()),
            int(self._WEBUI_ADV_DEFAULTS["top_k"]),
            explicit_keys,
        )
        self._set_cfg_if_non_default_or_explicit(
            cfg,
            "temperature",
            float(self.detail_temperature_spin.value()),
            float(self._WEBUI_ADV_DEFAULTS["temperature"]),
            explicit_keys,
        )
        self._set_cfg_if_non_default_or_explicit(
            cfg,
            "length_penalty",
            float(self.detail_length_penalty_spin.value()),
            float(self._WEBUI_ADV_DEFAULTS["length_penalty"]),
            explicit_keys,
        )
        self._set_cfg_if_non_default_or_explicit(
            cfg,
            "num_beams",
            int(self.detail_num_beams_spin.value()),
            int(self._WEBUI_ADV_DEFAULTS["num_beams"]),
            explicit_keys,
        )
        self._set_cfg_if_non_default_or_explicit(
            cfg,
            "repetition_penalty",
            float(self.detail_repetition_penalty_spin.value()),
            float(self._WEBUI_ADV_DEFAULTS["repetition_penalty"]),
            explicit_keys,
        )
        self._set_cfg_if_non_default_or_explicit(
            cfg,
            "max_mel_tokens",
            int(self.detail_max_mel_tokens_spin.value()),
            int(self._WEBUI_ADV_DEFAULTS["max_mel_tokens"]),
            explicit_keys,
        )
        return cfg

    def _load_detail_task_config_into_panel(self, cfg: dict) -> None:
        vector = cfg.get("emotion_vector", cfg.get("emo_vector"))
        if isinstance(vector, list):
            self._set_detail_vector_to_sliders(vector)
        elif isinstance(vector, str):
            parsed = self._parse_emotion_vector(vector)
            if parsed is not None:
                self._set_detail_vector_to_sliders(parsed)
        else:
            self._set_detail_vector_to_sliders([])

        self.detail_emo_ref_edit.setText(str(cfg.get("emo_ref_path", cfg.get("emotion_ref_audio", ""))))
        self.detail_custom_prompt_edit.setPlainText(str(cfg.get("custom_prompt", "")))

        method = str(cfg.get("emo_control_method", "")).strip()
        if method not in self._EMO_METHOD_OPTIONS:
            has_ref = bool(str(cfg.get("emo_ref_path", cfg.get("emotion_ref_audio", "")).strip()))
            has_vector = isinstance(cfg.get("emotion_vector", cfg.get("emo_vector")), list)
            if has_vector:
                method = self._EMO_METHOD_VECTOR
            elif has_ref:
                method = self._EMO_METHOD_REF
            else:
                method = self._EMO_METHOD_SAME_REF
        self.detail_emo_method_combo.setCurrentText(method)

        self.detail_emo_weight_spin.setValue(self._safe_float(cfg.get("emo_weight"), 0.65))
        self.detail_segment_tokens_spin.setValue(
            self._safe_int(cfg.get("max_text_tokens_per_segment"), int(self._WEBUI_ADV_DEFAULTS["max_text_tokens_per_segment"]))
        )
        self.detail_do_sample_check.setChecked(bool(cfg.get("do_sample", self._WEBUI_ADV_DEFAULTS["do_sample"])))
        self.detail_top_p_spin.setValue(self._safe_float(cfg.get("top_p"), float(self._WEBUI_ADV_DEFAULTS["top_p"])))
        self.detail_top_k_spin.setValue(self._safe_int(cfg.get("top_k"), int(self._WEBUI_ADV_DEFAULTS["top_k"])))
        self.detail_temperature_spin.setValue(
            self._safe_float(cfg.get("temperature"), float(self._WEBUI_ADV_DEFAULTS["temperature"]))
        )
        self.detail_length_penalty_spin.setValue(
            self._safe_float(cfg.get("length_penalty"), float(self._WEBUI_ADV_DEFAULTS["length_penalty"]))
        )
        self.detail_num_beams_spin.setValue(self._safe_int(cfg.get("num_beams"), int(self._WEBUI_ADV_DEFAULTS["num_beams"])))
        self.detail_repetition_penalty_spin.setValue(
            self._safe_float(cfg.get("repetition_penalty"), float(self._WEBUI_ADV_DEFAULTS["repetition_penalty"]))
        )
        self.detail_max_mel_tokens_spin.setValue(
            self._safe_int(cfg.get("max_mel_tokens"), int(self._WEBUI_ADV_DEFAULTS["max_mel_tokens"]))
        )

        self._refresh_detail_config_preview(cfg)
        self._on_detail_emo_method_changed(self.detail_emo_method_combo.currentIndex())

    def _on_detail_emo_method_changed(self, _index: int) -> None:
        method = self.detail_emo_method_combo.currentText()
        show_ref = method == self._EMO_METHOD_REF
        show_vector = method == self._EMO_METHOD_VECTOR
        self.detail_form_layout.setRowVisible(self.detail_emo_weight_row_widget, show_vector)
        self.detail_form_layout.setRowVisible(self.detail_emo_ref_row_widget, show_ref)
        self.detail_form_layout.setRowVisible(self.detail_emo_vector_row_widget, show_vector)
        self._refresh_detail_config_preview()

    def _on_detail_emo_slider_changed(self, index: int, value: int) -> None:
        if 0 <= index < len(self.detail_emo_vector_values):
            self.detail_emo_vector_values[index].setText(f"{value / 100.0:.2f}")
        self._refresh_detail_config_preview()

    def _on_detail_emo_weight_slider_changed(self, value: int) -> None:
        weight = value / 100.0
        self.detail_emo_weight_spin.blockSignals(True)
        self.detail_emo_weight_spin.setValue(weight)
        self.detail_emo_weight_spin.blockSignals(False)
        self._refresh_detail_config_preview()

    def _on_detail_emo_weight_spin_changed(self, value: float) -> None:
        slider_value = int(round(value * 100))
        self.detail_emo_weight_slider.blockSignals(True)
        self.detail_emo_weight_slider.setValue(slider_value)
        self.detail_emo_weight_slider.blockSignals(False)
        self._refresh_detail_config_preview()

    def _detail_vector_from_sliders(self) -> list[float]:
        return [slider.value() / 100.0 for slider in self.detail_emo_vector_sliders]

    def _set_detail_vector_to_sliders(self, vector: list[float]) -> None:
        for index, slider in enumerate(self.detail_emo_vector_sliders):
            value = 0.0
            if index < len(vector):
                try:
                    value = float(vector[index])
                except (TypeError, ValueError):
                    value = 0.0
            slider.setValue(int(max(0.0, min(1.0, value)) * 100))

    def _on_main_tab_changed(self, index: int) -> None:
        self.app_config.last_active_tab = max(0, int(index))
        save_app_config(self.app_config)

    def _on_progress(self, task: TaskRecord) -> None:
        idx = self._find_task_index_by_id(task.task_id)
        if idx is not None:
            self.tasks[idx] = task
        self.statusBar().showMessage(f"{task.task_id}: {task.status} ({task.progress}%)")
        self._refresh_table()

    def play_row(self, row_index: int) -> None:
        if row_index < 0 or row_index >= len(self.tasks):
            return
        self.task_table.selectRow(row_index)
        self.play_selected()

    def play_task_by_id(self, task_id: str) -> None:
        if not self._select_task_by_id(task_id):
            self._warn("任务ID不存在于当前列表")
            return
        self.play_selected()

    def generate_task_by_id(self, task_id: str) -> None:
        idx = self._find_task_index_by_id(task_id)
        if idx is None:
            self._warn("任务ID不存在于当前列表")
            return
        task = self.tasks[idx]
        if task.status == "queued":
            self.cancel_task_by_id(task_id)
            return
        if task.status == "generating":
            self._warn("该任务正在生成中")
            return
        if not self._select_task_by_id(task_id):
            self._warn("任务ID不存在于当前列表")
            return
        self.generate_selected_task()

    def cancel_task_by_id(self, task_id: str) -> None:
        idx = self._find_task_index_by_id(task_id)
        if idx is None:
            self._warn("任务ID不存在于当前列表")
            return
        task = self.tasks[idx]
        if task.status != "queued":
            self._warn("仅排队中的任务可取消")
            return

        if self._batch_runner is not None:
            self._batch_runner.request_cancel(task_id)

        task.status = "cancelled"
        task.progress = 0
        task.error = "已取消排队"
        if self.storage is not None:
            self.storage.save_task(task)
        self.tasks[idx] = task
        self.statusBar().showMessage(f"已取消排队任务: {task_id}", 3000)
        self._refresh_table()

    def delete_task_by_id(self, task_id: str) -> None:
        if not self._select_task_by_id(task_id):
            self._warn("任务ID不存在于当前列表")
            return
        self.delete_selected_task()

    def open_task_detail(self, row_index: int) -> None:
        if row_index < 0 or row_index >= len(self.tasks):
            return
        self.task_table.selectRow(row_index)
        self.main_tabs.setCurrentIndex(self.task_table_tab_index)

    def open_task_detail_by_id(self, task_id: str) -> None:
        if not self._select_task_by_id(task_id):
            self._warn("任务ID不存在于当前列表")
            return
        self.main_tabs.setCurrentIndex(self.task_table_tab_index)

    def _validate_before_batch(self) -> bool:
        url_text = self.webui_url_edit.text().strip()
        if url_text and not self._normalize_webui_url(url_text):
            self._warn("WebUI URL 无效，请输入 http:// 或 https:// 地址")
            return False
        if not url_text:
            host = self.host_edit.text().strip()
            if not host:
                self._warn("WebUI URL 为空时，主机地址不能为空")
                return False
            if self.port_spin.value() <= 0:
                self._warn("端口无效")
                return False
        for task in self.tasks:
            if not task.text.strip():
                self._warn(f"任务 {task.task_id} 的文本为空")
                return False
            if not task.reference_audio.strip():
                self._warn(f"任务 {task.task_id} 缺少参考音频")
                return False
        self.save_global_settings()
        return True

    @staticmethod
    def _normalize_webui_url(value: str) -> str:
        raw = (value or "").strip()
        if not raw:
            return ""
        parsed = urlparse(raw)
        candidate = raw
        if not parsed.scheme:
            candidate = f"http://{raw}"
            parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        return candidate.rstrip("/")

    def _selected_row_index(self) -> int | None:
        selected = self.task_table.selectionModel().selectedRows()
        if not selected:
            return None
        return selected[0].row()

    def _parse_json(self, text: str) -> dict | None:
        raw = text.strip()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._warn(f"JSON 格式无效: {exc}")
            return None
        if not isinstance(data, dict):
            self._warn("配置 JSON 必须是对象")
            return None
        return data

    def _build_task_config(self, default_config: dict) -> dict | None:
        self._commit_pending_numeric_inputs(detail_only=False)
        cfg = dict(default_config or {})
        explicit_keys: set[str] = set()

        emo_method = self.emo_method_combo.currentText().strip()
        if emo_method:
            cfg["emo_control_method"] = emo_method

        parsed_vector: list[float] | None = None
        if emo_method == self._EMO_METHOD_VECTOR:
            parsed_vector = self._vector_from_sliders()
        if parsed_vector is not None:
            cfg["emotion_vector"] = parsed_vector
            cfg["emo_vector"] = parsed_vector
            for index, value in enumerate(parsed_vector, start=1):
                key = f"vec{index}"
                if key not in explicit_keys:
                    cfg[key] = float(value)
        else:
            cfg.pop("emotion_vector", None)
            cfg.pop("emo_vector", None)
            for index in range(1, 9):
                key = f"vec{index}"
                if key not in explicit_keys:
                    cfg.pop(key, None)

        emo_ref_path = self.emo_ref_edit.text().strip()
        if emo_method == self._EMO_METHOD_REF and emo_ref_path:
            cfg["emo_ref_path"] = emo_ref_path
            cfg["emotion_ref_audio"] = emo_ref_path
        else:
            cfg.pop("emo_ref_path", None)
            cfg.pop("emotion_ref_audio", None)

        custom_prompt = self.custom_prompt_edit.toPlainText().strip()
        if custom_prompt:
            cfg["custom_prompt"] = custom_prompt
        else:
            cfg.pop("custom_prompt", None)

        emo_weight_value = float(self.emo_weight_spin.value())
        self._set_cfg_if_non_default_or_explicit(cfg, "emo_weight", emo_weight_value, 0.65, explicit_keys)
        self._set_cfg_if_non_default_or_explicit(
            cfg,
            "max_text_tokens_per_segment",
            int(self.segment_tokens_spin.value()),
            int(self._WEBUI_ADV_DEFAULTS["max_text_tokens_per_segment"]),
            explicit_keys,
        )
        self._set_cfg_if_non_default_or_explicit(
            cfg,
            "do_sample",
            bool(self.do_sample_check.isChecked()),
            bool(self._WEBUI_ADV_DEFAULTS["do_sample"]),
            explicit_keys,
        )
        self._set_cfg_if_non_default_or_explicit(
            cfg,
            "top_p",
            float(self.top_p_spin.value()),
            float(self._WEBUI_ADV_DEFAULTS["top_p"]),
            explicit_keys,
        )
        self._set_cfg_if_non_default_or_explicit(
            cfg,
            "top_k",
            int(self.top_k_spin.value()),
            int(self._WEBUI_ADV_DEFAULTS["top_k"]),
            explicit_keys,
        )
        self._set_cfg_if_non_default_or_explicit(
            cfg,
            "temperature",
            float(self.temperature_spin.value()),
            float(self._WEBUI_ADV_DEFAULTS["temperature"]),
            explicit_keys,
        )
        self._set_cfg_if_non_default_or_explicit(
            cfg,
            "length_penalty",
            float(self.length_penalty_spin.value()),
            float(self._WEBUI_ADV_DEFAULTS["length_penalty"]),
            explicit_keys,
        )
        self._set_cfg_if_non_default_or_explicit(
            cfg,
            "num_beams",
            int(self.num_beams_spin.value()),
            int(self._WEBUI_ADV_DEFAULTS["num_beams"]),
            explicit_keys,
        )
        self._set_cfg_if_non_default_or_explicit(
            cfg,
            "repetition_penalty",
            float(self.repetition_penalty_spin.value()),
            float(self._WEBUI_ADV_DEFAULTS["repetition_penalty"]),
            explicit_keys,
        )
        self._set_cfg_if_non_default_or_explicit(
            cfg,
            "max_mel_tokens",
            int(self.max_mel_tokens_spin.value()),
            int(self._WEBUI_ADV_DEFAULTS["max_mel_tokens"]),
            explicit_keys,
        )
        return cfg

    def _load_task_config_into_form(self, cfg: dict) -> None:
        vector = cfg.get("emotion_vector", cfg.get("emo_vector"))
        if isinstance(vector, list):
            self._set_vector_to_sliders(vector)
        elif isinstance(vector, str):
            parsed = self._parse_emotion_vector(vector)
            if parsed is not None:
                self._set_vector_to_sliders(parsed)
        else:
            self._set_vector_to_sliders([])

        self.emo_ref_edit.setText(str(cfg.get("emo_ref_path", cfg.get("emotion_ref_audio", ""))))
        self.custom_prompt_edit.setPlainText(str(cfg.get("custom_prompt", "")))
        self._set_emo_method_from_config(cfg)

        self.emo_weight_spin.setValue(self._safe_float(cfg.get("emo_weight"), 0.65))
        self.segment_tokens_spin.setValue(
            self._safe_int(cfg.get("max_text_tokens_per_segment"), int(self._WEBUI_ADV_DEFAULTS["max_text_tokens_per_segment"]))
        )
        self.do_sample_check.setChecked(bool(cfg.get("do_sample", self._WEBUI_ADV_DEFAULTS["do_sample"])))
        self.top_p_spin.setValue(self._safe_float(cfg.get("top_p"), float(self._WEBUI_ADV_DEFAULTS["top_p"])))
        self.top_k_spin.setValue(self._safe_int(cfg.get("top_k"), int(self._WEBUI_ADV_DEFAULTS["top_k"])))
        self.temperature_spin.setValue(self._safe_float(cfg.get("temperature"), float(self._WEBUI_ADV_DEFAULTS["temperature"])))
        self.length_penalty_spin.setValue(
            self._safe_float(cfg.get("length_penalty"), float(self._WEBUI_ADV_DEFAULTS["length_penalty"]))
        )
        self.num_beams_spin.setValue(self._safe_int(cfg.get("num_beams"), int(self._WEBUI_ADV_DEFAULTS["num_beams"])))
        self.repetition_penalty_spin.setValue(
            self._safe_float(cfg.get("repetition_penalty"), float(self._WEBUI_ADV_DEFAULTS["repetition_penalty"]))
        )
        self.max_mel_tokens_spin.setValue(
            self._safe_int(cfg.get("max_mel_tokens"), int(self._WEBUI_ADV_DEFAULTS["max_mel_tokens"]))
        )

        self._refresh_task_config_preview(cfg)

    def _parse_emotion_vector(self, text: str) -> list[float] | None:
        raw = text.strip()
        if not raw:
            return []
        try:
            if raw.startswith("["):
                data = json.loads(raw)
                if not isinstance(data, list):
                    self._warn("情感向量 JSON 必须是数组")
                    return None
                return [float(v) for v in data]
            parts = [part.strip() for part in raw.split(",") if part.strip()]
            return [float(v) for v in parts]
        except (ValueError, TypeError, json.JSONDecodeError):
            self._warn("情感向量必须是逗号分隔数字或 JSON 数组")
            return None

    def _on_emo_method_changed(self, _index: int) -> None:
        method = self.emo_method_combo.currentText()
        show_ref = method == self._EMO_METHOD_REF
        show_vector = method == self._EMO_METHOD_VECTOR
        self.task_form_layout.setRowVisible(self.emo_weight_row_widget, show_vector)
        self.task_form_layout.setRowVisible(self.emo_ref_row_widget, show_ref)
        self.task_form_layout.setRowVisible(self.emo_vector_row_widget, show_vector)
        self._refresh_task_config_preview()

    def _set_emo_method_from_config(self, cfg: dict) -> None:
        method = str(cfg.get("emo_control_method", "")).strip()
        if method not in self._EMO_METHOD_OPTIONS:
            has_ref = bool(str(cfg.get("emo_ref_path", cfg.get("emotion_ref_audio", "")).strip()))
            has_vector = isinstance(cfg.get("emotion_vector", cfg.get("emo_vector")), list)
            if has_vector:
                method = self._EMO_METHOD_VECTOR
            elif has_ref:
                method = self._EMO_METHOD_REF
            else:
                method = self._EMO_METHOD_SAME_REF
        self.emo_method_combo.setCurrentText(method)

    def _toggle_advanced_params(self, checked: bool) -> None:
        self.advanced_content_widget.setVisible(checked)
        self.advanced_toggle_btn.setText("高级参数 ▼" if checked else "高级参数 ▶")

    def _reset_advanced_param(self, key: str) -> None:
        default = self._WEBUI_ADV_DEFAULTS.get(key)
        if default is None:
            return
        if key == "max_text_tokens_per_segment":
            self.segment_tokens_spin.setValue(int(default))
        elif key == "do_sample":
            self.do_sample_check.setChecked(bool(default))
        elif key == "top_p":
            self.top_p_spin.setValue(float(default))
        elif key == "top_k":
            self.top_k_spin.setValue(int(default))
        elif key == "temperature":
            self.temperature_spin.setValue(float(default))
        elif key == "length_penalty":
            self.length_penalty_spin.setValue(float(default))
        elif key == "num_beams":
            self.num_beams_spin.setValue(int(default))
        elif key == "repetition_penalty":
            self.repetition_penalty_spin.setValue(float(default))
        elif key == "max_mel_tokens":
            self.max_mel_tokens_spin.setValue(int(default))

    def _on_emo_slider_changed(self, index: int, value: int) -> None:
        if 0 <= index < len(self.emo_vector_slider_values):
            self.emo_vector_slider_values[index].setText(f"{value / 100.0:.2f}")
        self._refresh_task_config_preview()

    def _on_emo_weight_slider_changed(self, value: int) -> None:
        weight = value / 100.0
        self.emo_weight_spin.blockSignals(True)
        self.emo_weight_spin.setValue(weight)
        self.emo_weight_spin.blockSignals(False)
        self._refresh_task_config_preview()

    def _on_emo_weight_spin_changed(self, value: float) -> None:
        slider_value = int(round(value * 100))
        self.emo_weight_slider.blockSignals(True)
        self.emo_weight_slider.setValue(slider_value)
        self.emo_weight_slider.blockSignals(False)
        self._refresh_task_config_preview()

    def _vector_from_sliders(self) -> list[float]:
        return [slider.value() / 100.0 for slider in self.emo_vector_sliders]

    def _set_vector_to_sliders(self, vector: list[float]) -> None:
        for index, slider in enumerate(self.emo_vector_sliders):
            value = 0.0
            if index < len(vector):
                try:
                    value = float(vector[index])
                except (TypeError, ValueError):
                    value = 0.0
            slider.setValue(int(max(0.0, min(1.0, value)) * 100))

    def _play_audio_by_path_text(self, path_text: str) -> None:
        raw = path_text.strip()
        if not raw:
            self._warn("请先选择音频文件")
            return

        if raw.startswith("http://") or raw.startswith("https://"):
            try:
                response = requests.get(raw, timeout=self.app_config.request_timeout_sec)
                response.raise_for_status()
            except requests.RequestException as exc:
                self._warn(f"远端音频下载失败: {exc}")
                return

            suffix = ".wav"
            lowered = raw.lower()
            if lowered.endswith(".mp3"):
                suffix = ".mp3"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(response.content)
                tmp_path = Path(tmp.name)
            self._temp_audio_files.append(tmp_path)
            # Keep a small temp cache to avoid unbounded growth.
            while len(self._temp_audio_files) > 10:
                old = self._temp_audio_files.pop(0)
                try:
                    old.unlink(missing_ok=True)
                except OSError:
                    pass

            self._play_with_global_player(tmp_path)
            return

        path = Path(raw)
        if self.storage and not path.is_absolute():
            candidate = self.storage.task_set_dir / path
            if candidate.exists():
                path = candidate

        self._play_with_global_player(path)

    @staticmethod
    def _safe_float(value: object, fallback: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _safe_int(value: object, fallback: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _set_cfg_if_non_default_or_explicit(
        cfg: dict,
        key: str,
        value: object,
        default_value: object,
        explicit_keys: set[str],
    ) -> None:
        if key in explicit_keys or value != default_value:
            cfg[key] = value
        else:
            cfg.pop(key, None)

    def _refresh_task_config_preview(self, preview_config: object | None = None) -> None:
        if not isinstance(preview_config, dict):
            preview_config = self._build_task_config(self.defaults.config) or {}
        self.task_config_edit.setPlainText(json.dumps(preview_config, ensure_ascii=False, indent=2) if preview_config else "")

    def _refresh_detail_config_preview(self, preview_config: object | None = None) -> None:
        if not isinstance(preview_config, dict):
            idx = self._find_task_index_by_id(self._selected_detail_task_id())
            default_config = self.tasks[idx].config if idx is not None else {}
            preview_config = self._build_detail_task_config(default_config) or {}
        self.detail_task_extra_json_edit.setPlainText(
            json.dumps(preview_config, ensure_ascii=False, indent=2) if preview_config else ""
        )

    def _ensure_task_detail_synced(self, task_id: str) -> bool:
        """Persist currently edited detail panel and ensure target task detail is applied."""
        target = task_id.strip()
        if not target:
            return True

        active = (self._active_detail_task_id or self._selected_detail_task_id()).strip()
        if active and active != target:
            if not self._apply_detail_panel_to_task(active):
                self._set_detail_task_id(active)
                self._refresh_detail_task_preview(active)
                self._active_detail_task_id = active
                return False

        if active != target:
            self._set_detail_task_id(target)
            self._refresh_detail_task_preview(target)
            self._active_detail_task_id = target

        return self._apply_detail_panel_to_task(target)

    def _commit_pending_numeric_inputs(self, detail_only: bool) -> None:
        """Force-commit in-progress text edits in spin boxes before reading values."""
        widgets: list[object] = []
        detail_widget_names = [
            "detail_emo_weight_spin",
            "detail_segment_tokens_spin",
            "detail_top_p_spin",
            "detail_top_k_spin",
            "detail_temperature_spin",
            "detail_length_penalty_spin",
            "detail_num_beams_spin",
            "detail_repetition_penalty_spin",
            "detail_max_mel_tokens_spin",
        ]
        for name in detail_widget_names:
            widget = getattr(self, name, None)
            if widget is not None:
                widgets.append(widget)

        if not detail_only:
            task_widget_names = [
                "emo_weight_spin",
                "segment_tokens_spin",
                "top_p_spin",
                "top_k_spin",
                "temperature_spin",
                "length_penalty_spin",
                "num_beams_spin",
                "repetition_penalty_spin",
                "max_mel_tokens_spin",
            ]
            for name in task_widget_names:
                widget = getattr(self, name, None)
                if widget is not None:
                    widgets.append(widget)

        for widget in widgets:
            widget.interpretText()

        focused = QApplication.focusWidget()
        if focused is not None:
            focused.clearFocus()

    def _warn(self, msg: str) -> None:
        QMessageBox.warning(self, "提示", msg)

    def _prompt_text(self, title: str, label: str) -> tuple[str, bool]:
        from PySide6.QtWidgets import QInputDialog

        value, ok = QInputDialog.getText(self, title, label)
        return value, ok

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._batch_thread and self._batch_thread.isRunning():
            self._batch_thread.quit()
            self._batch_thread.wait(3000)
        try:
            self.player.stop()
        except AudioPlaybackError:
            pass
        self._persist_runtime_state()
        super().closeEvent(event)
