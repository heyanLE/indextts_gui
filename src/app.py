"""主窗口 — QMainWindow 集成全部模块"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QStackedWidget, QMessageBox, QApplication,
)
from PySide6.QtGui import QCloseEvent

from src.core.config_manager import ConfigManager
from src.core.taskset import TaskSet
from src.core.task_queue import TaskQueue
from src.core.recipe import RecipeManager
from src.core.task import TaskStatus
from src.core.popup_suppressor import suppress_popups, restore_popups
from src.ui.audio_player import AudioPlayer
from src.ui.config_tab import ConfigTab
from src.ui.task_list_tab import TaskListTab
from src.ui.recipe_tab import RecipeTab
from src.ui.queue_visualizer import QueueVisualizer


TITLE = "IndexTTS-GUI2 — AI 语音合成任务管理"
WIDTH, HEIGHT = 1200, 800


class MainWindow(QMainWindow):
    """应用主窗口"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(TITLE)
        self.resize(WIDTH, HEIGHT)

        # ── 核心实例 ──
        self._config = ConfigManager()
        self._recipe_manager = RecipeManager(self._config._config_dir)
        self._taskset: TaskSet | None = None
        self._task_queue: TaskQueue | None = None
        self._paused = False

        # ── 构建 UI ──
        self._setup_ui()
        self._connect_signals()
        self._restore_state()
        self._apply_theme()

        # 🔇 延迟恢复任务集：等事件循环就绪后再加载，避免 widget 构建期间
        # 信号级联（combo box / 表格重建 / engine schema 切换等）触发弹窗
        QTimer.singleShot(0, self._restore_task_set)

    # ==================================================================
    # UI 构建
    # ==================================================================
    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 全局播放器（顶部固定） ──
        self._audio_player = AudioPlayer()
        root.addWidget(self._audio_player)

        # ── 队列功能区（播放器下方，有任务时显示） ──
        self._queue_row = QWidget()
        self._queue_row.setObjectName("queueRow")
        self._queue_row.setVisible(False)
        qr_layout = QHBoxLayout(self._queue_row)
        qr_layout.setContentsMargins(8, 4, 8, 4)
        qr_layout.setSpacing(8)

        self._pause_btn = QPushButton("⏸ 暂停")
        self._pause_btn.setObjectName("warningBtn")
        self._pause_btn.clicked.connect(self._on_queue_pause)

        self._clear_btn = QPushButton("🗑 清空")
        self._clear_btn.setObjectName("dangerBtn")
        self._clear_btn.clicked.connect(self._on_queue_clear)

        qr_layout.addWidget(self._pause_btn)
        qr_layout.addWidget(self._clear_btn)

        self._queue_viz = QueueVisualizer()
        self._queue_viz.visibility_changed.connect(self._on_viz_visibility_changed)
        qr_layout.addWidget(self._queue_viz, 1)

        root.addWidget(self._queue_row)

        # ── Tab 栏（横版） ──
        tab_bar = QWidget()
        tab_bar.setObjectName("tabBar")
        tab_bar.setFixedHeight(42)
        tb_layout = QHBoxLayout(tab_bar)
        tb_layout.setContentsMargins(4, 4, 4, 4)
        tb_layout.setSpacing(2)

        self._tab_config_btn = QPushButton("⚙️  软件配置")
        self._tab_config_btn.setCheckable(True)
        self._tab_config_btn.setChecked(True)
        self._tab_config_btn.setObjectName("tabButton")

        self._tab_recipe_btn = QPushButton("📦  配方管理")
        self._tab_recipe_btn.setCheckable(True)
        self._tab_recipe_btn.setObjectName("tabButton")

        self._tab_task_btn = QPushButton("📋  任务列表")
        self._tab_task_btn.setCheckable(True)
        self._tab_task_btn.setObjectName("tabButton")

        self._tab_config_btn.clicked.connect(lambda: self._switch_tab(0))
        self._tab_recipe_btn.clicked.connect(lambda: self._switch_tab(1))
        self._tab_task_btn.clicked.connect(lambda: self._switch_tab(2))

        tb_layout.addWidget(self._tab_config_btn)
        tb_layout.addWidget(self._tab_recipe_btn)
        tb_layout.addWidget(self._tab_task_btn)
        tb_layout.addStretch()

        root.addWidget(tab_bar)

        # ── 内容区域（QStackedWidget） ──
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        # Tab 0: 软件配置
        self._config_tab = ConfigTab(self._config)
        self._stack.addWidget(self._config_tab)

        # Tab 1: 配方管理
        self._recipe_tab = RecipeTab(self._recipe_manager)
        self._stack.addWidget(self._recipe_tab)

        # Tab 2: 任务列表
        self._task_tab = TaskListTab()
        self._stack.addWidget(self._task_tab)

        self._switch_tab(0)

    # ==================================================================
    # 信号连接
    # ==================================================================
    def _connect_signals(self) -> None:
        # 配置 Tab → 切换任务集
        self._config_tab.task_set_changed.connect(self._on_task_set_changed)

        # 任务 Tab → 批量生成
        self._task_tab.batch_generate.connect(self._on_batch_generate)

        # 任务 Tab → 播放音频
        self._task_tab.play_audio.connect(self._audio_player.play)

        # 任务 detail panel 的播放按钮
        self._task_tab._detail_panel._play_ref_btn.clicked.connect(
            self._on_play_reference
        )
        self._task_tab._detail_panel._play_output_btn.clicked.connect(
            self._on_play_output
        )

        # 配方 Tab → 通知详情面板刷新
        self._recipe_tab.recipe_added.connect(self._on_recipes_changed)
        self._recipe_tab.recipe_updated.connect(self._on_recipes_changed)
        self._recipe_tab.recipe_deleted.connect(self._on_recipes_changed)

        # 详情面板保存配方 → 通知配方页刷新
        self._task_tab._detail_panel.recipe_saved.connect(
            lambda r: self._recipe_tab.refresh()
        )

        # 注入 RecipeManager 到详情面板 + 任务列表Tab（批量导入对话框用）
        self._task_tab._detail_panel.set_recipe_manager(self._recipe_manager)
        self._task_tab.set_recipe_manager(self._recipe_manager)

    # ==================================================================
    # Tab 切换
    # ==================================================================
    def _switch_tab(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        self._tab_config_btn.setChecked(index == 0)
        self._tab_recipe_btn.setChecked(index == 1)
        self._tab_task_btn.setChecked(index == 2)

    # ==================================================================
    # 任务集切换
    # ==================================================================
    def _on_task_set_changed(self, path_str: str) -> None:
        """切换到新的任务集"""
        path = Path(path_str)
        if not path.exists():
            QMessageBox.warning(self, "错误", f"目录不存在: {path_str}")
            return

        # 🔇 整个切换期间全局抑制弹窗（包括旧队列停止 + 新任务集加载）
        suppress_popups()
        try:
            try:
                self._taskset = TaskSet.load(path)
            except FileNotFoundError:
                self._taskset = TaskSet.create(path.name, path)
            except Exception as e:
                QMessageBox.critical(self, "错误", f"加载任务集失败: {e}")
                return

            self._task_tab.set_task_set(self._taskset)
            self.setWindowTitle(f"{TITLE} — {self._taskset.name}")

            # 停止旧队列 + 重置暂停按钮
            if self._task_queue and self._task_queue.isRunning():
                self._task_queue.stop()
                self._task_queue.wait(3000)
            self._task_queue = None
            self._paused = False
            self._pause_btn.setText("⏸ 暂停")
        finally:
            restore_popups()

    def _on_recipes_changed(self, *args) -> None:
        """配方变更时通知任务详情面板刷新配方下拉框"""
        self._task_tab._detail_panel.refresh_recipes()

    # ==================================================================
    # 批量生成
    # ==================================================================
    def _on_batch_generate(self, tasks: list) -> None:
        """将选中任务加入已有队列（不销毁正在运行的队列）"""
        if not self._taskset:
            QMessageBox.warning(self, "提示", "请先选择任务集")
            return

        # 确保引擎已配置
        for task in tasks:
            url = self._config.get_engine_url(task.engine)
            if not url:
                QMessageBox.warning(
                    self, "引擎未配置",
                    f"任务 {task.id} 使用的引擎 {task.engine} 未配置 API 地址，请在配置页面设置。"
                )
                return

        # ── 复用现有队列：直接追加，不销毁正在运行的任务 ──
        if self._task_queue and self._task_queue.isRunning():
            self._task_queue.add_tasks(tasks)
            # 🔵 队列可视化：追加新任务胶囊
            for task in tasks:
                self._queue_viz.add_task(task.id, task.text, TaskStatus.QUEUED)
            QMessageBox.information(
                self, "已加入队列",
                f"{len(tasks)} 个任务已加入生成队列，将按顺序处理。"
            )
            return

        # 创建新队列（首次或无队列时）
        self._task_queue = TaskQueue(self._taskset, self._config)

        self._task_queue.task_status_changed.connect(self._on_queue_status_changed)
        self._task_queue.task_completed.connect(self._on_task_completed)
        self._task_queue.task_failed.connect(self._on_task_failed)
        self._task_queue.queue_progress.connect(self._on_queue_progress)
        self._task_queue.all_done.connect(self._on_queue_all_done)
        self._task_queue.paused_changed.connect(self._on_queue_paused_changed)

        self._task_queue.add_tasks(tasks)

        # 🔵 队列可视化：添加所有任务到胶囊列表
        for task in tasks:
            self._queue_viz.add_task(task.id, task.text, TaskStatus.QUEUED)

        self._task_queue.start()

        QMessageBox.information(
            self, "已加入队列",
            f"{len(tasks)} 个任务已加入生成队列，将按顺序处理。"
        )
        self._switch_tab(2)

    # ------------------------------------------------------------------
    # 队列信号处理
    # ------------------------------------------------------------------
    def _on_queue_status_changed(self, task_id: str, status_name: str) -> None:
        self._task_tab.refresh_task(task_id)
        # 更新队列可视化
        try:
            s = TaskStatus(status_name)
            self._queue_viz.update_status(task_id, s)
        except ValueError:
            pass

    def _on_task_completed(self, task_id: str, audio_path: str) -> None:
        self._task_tab.refresh_task(task_id)
        # 从队列可视化移除
        self._queue_viz.remove_task(task_id)

    def _on_task_failed(self, task_id: str, error_msg: str) -> None:
        self._task_tab.refresh_task(task_id)
        # 从队列可视化移除
        self._queue_viz.remove_task(task_id)

    def _on_queue_progress(self, completed: int, total: int) -> None:
        pass  # 可扩展进度显示

    def _on_queue_pause(self) -> None:
        """暂停/继续按钮点击"""
        if not self._task_queue:
            return
        self._paused = not self._paused
        self._pause_btn.setText("▶ 继续" if self._paused else "⏸ 暂停")
        if self._paused:
            self._task_queue.pause()
        else:
            self._task_queue.resume()

    def _on_viz_visibility_changed(self, visible: bool) -> None:
        """队列可视化器空/非空 → 同步队列行显隐"""
        self._queue_row.setVisible(visible)

    def _on_queue_clear(self) -> None:
        """清空队列：停止处理 + 回退任务状态 + 清空可视化"""
        if not self._taskset:
            return

        if self._task_queue and self._task_queue.isRunning():
            self._task_queue.stop()
            self._task_queue.wait(5000)
            drained = self._task_queue.drain_queue(self._taskset)
        elif self._task_queue:
            drained = self._task_queue.drain_queue(self._taskset)
        else:
            # 没有队列在运行，直接处理 QUEUED 状态的任务
            drained = 0
            for task in self._taskset.tasks:
                if task.status == TaskStatus.QUEUED:
                    if task.output_audio_path:
                        task.transition_to(TaskStatus.COMPLETED)
                    else:
                        task.transition_to(TaskStatus.PENDING)
                    drained += 1
            self._taskset.save()

        # 清空队列实例引用
        self._task_queue = None

        # 清空可视化器 + 刷新表格
        self._queue_viz.clear()
        self._task_tab.refresh_all()

        # 恢复暂停按钮为「暂停」
        self._paused = False
        self._pause_btn.setText("⏸ 暂停")

        if drained > 0:
            QMessageBox.information(
                self, "队列已清空",
                f"已将 {drained} 个队列中的任务恢复为合适的初始状态。\n\n"
                "未生成的任务 → 未开始\n"
                "已生成的任务 → 生成完成",
            )

    def _on_queue_all_done(self) -> None:
        self._task_queue = None
        # 重置暂停按钮
        self._paused = False
        self._pause_btn.setText("⏸ 暂停")
        # 队列完成后延迟清空可视化器
        QTimer.singleShot(1000, self._queue_viz.clear)

    def _on_queue_paused_changed(self, paused: bool) -> None:
        """队列内部暂停状态变更 → 同步按钮文字"""
        self._paused = paused
        self._pause_btn.setText("▶ 继续" if paused else "⏸ 暂停")

    # ==================================================================
    # 播放参考音频
    # ==================================================================
    def _on_play_reference(self) -> None:
        """播放当前任务引擎配置中的参考音频"""
        panel = self._task_tab._detail_panel
        if not panel._current_task:
            return

        params = panel._engine_config.get_params()
        # 优先播放参考音频，其次情感参考音频
        ref = params.get("reference_audio") or params.get("emotion_audio")
        if ref and Path(ref).exists():
            self._audio_player.play(ref)

    def _on_play_output(self) -> None:
        """播放当前任务的生成音频"""
        panel = self._task_tab._detail_panel
        if not panel._current_task:
            return

        task = panel._current_task
        if task.output_audio_path and Path(task.output_audio_path).exists():
            self._audio_player.play(task.output_audio_path)

    # ==================================================================
    # 窗口状态恢复与保存
    # ==================================================================
    def _restore_state(self) -> None:
        geo = self._config.window_geometry
        if geo:
            self.restoreGeometry(geo)
        state = self._config.window_state
        if state:
            self.restoreState(state)

    def _restore_task_set(self) -> None:
        """启动时自动加载上次打开的任务集"""
        current = self._config.current_task_set_path
        if not current:
            return
        t_path = Path(current)
        if not t_path.exists():
            return
        self._on_task_set_changed(current)

    def closeEvent(self, event: QCloseEvent) -> None:
        # 停止队列
        if self._task_queue and self._task_queue.isRunning():
            ret = QMessageBox.question(
                self, "确认退出",
                "任务队列正在运行中，确定要退出吗？\n未完成的任务将在下次启动时保留。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._task_queue.stop()
            self._task_queue.wait(5000)

        # 保存窗口状态
        self._config.window_geometry = self.saveGeometry()
        self._config.window_state = self.saveState()
        self._config.save()

        event.accept()

    # ==================================================================
    # 主题
    # ==================================================================
    def _apply_theme(self) -> None:
        """加载 Red Hat UX QSS 主题"""
        import os
        import sys

        # 查找 style.qss
        possible_paths = [
            Path(__file__).parent / "resources" / "style.qss",
            Path(sys._MEIPASS) / "src" / "resources" / "style.qss"
            if hasattr(sys, "_MEIPASS") else None,
        ]
        qss_path = None
        for p in possible_paths:
            if p is not None and p.exists():
                qss_path = p
                break

        if qss_path:
            suppress_popups()
            try:
                self.setStyleSheet(qss_path.read_text(encoding="utf-8"))
            except Exception:
                pass
            finally:
                restore_popups()
