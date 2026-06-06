"""任务队列调度器 — QThread + queue.Queue 串行调度"""

from __future__ import annotations

import queue
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal, QMutex, QMutexLocker

from src.core.task import Task, TaskStatus
from src.core.taskset import TaskSet
from src.core.config_manager import ConfigManager
from src.engines import engine_registry


class TaskQueue(QThread):
    """任务队列调度线程

    在独立 QThread 中运行，串行取出队列中的任务并调用引擎 API 生成。
    通过 Signal 通知 UI 层状态变更。
    """

    # 信号
    task_status_changed = Signal(str, str)  # (task_id, status_name)
    task_completed = Signal(str, str)        # (task_id, audio_path)
    task_failed = Signal(str, str)           # (task_id, error_msg)
    queue_progress = Signal(int, int)        # (completed, total)
    all_done = Signal()
    paused_changed = Signal(bool)            # 暂停/继续状态变更

    def __init__(
        self,
        taskset: TaskSet,
        config: ConfigManager,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)
        self._taskset = taskset
        self._config = config
        self._queue: queue.Queue[Task] = queue.Queue()
        self._running = True
        self._paused = False
        self._mutex = QMutex()
        self._total_count = 0

    # ------------------------------------------------------------------
    # 队列操作（从主线程调用）
    # ------------------------------------------------------------------
    def add_tasks(self, tasks: list[Task]) -> None:
        """将任务加入队列"""
        for task in tasks:
            with QMutexLocker(self._mutex):
                if task.status in (TaskStatus.PENDING, TaskStatus.FAILED, TaskStatus.COMPLETED):
                    task.transition_to(TaskStatus.QUEUED)
                    # QUEUED 是纯运行时状态，不持久化
                    self._queue.put(task)
                    self._total_count += 1
                    self.task_status_changed.emit(task.id, TaskStatus.QUEUED.value)

    def stop(self) -> None:
        """停止队列处理（等待当前任务完成后退出）"""
        self._running = False

    def pause(self) -> None:
        self._paused = True
        self.paused_changed.emit(True)

    def resume(self) -> None:
        self._paused = False
        self.paused_changed.emit(False)

    def is_paused(self) -> bool:
        return self._paused

    def drain_queue(self, taskset: TaskSet) -> int:
        """清空内部队列并将 QUEUED 任务回退到合适状态

        规则：
        - 未生成（无 output_audio_path）→ PENDING
        - 已生成（有 output_audio_path）→ COMPLETED

        Returns:
            被回退的任务数量
        """
        count = 0
        # 排空内部 queue.Queue
        while not self._queue.empty():
            try:
                task = self._queue.get_nowait()
                self._revert_task(task)
                count += 1
            except queue.Empty:
                break

        # 遍历 taskset 中所有 QUEUED 任务（处理可能未在内部队列里的情况）
        for task in taskset.tasks:
            if task.status == TaskStatus.QUEUED:
                self._revert_task(task)
                count += 1

        taskset.save()
        return count

    @staticmethod
    def _revert_task(task: Task) -> None:
        """将队列中的任务回退到未生成/已生成状态"""
        if task.output_audio_path:
            task.transition_to(TaskStatus.COMPLETED)
        else:
            task.transition_to(TaskStatus.PENDING)

    def queue_size(self) -> int:
        return self._queue.qsize()

    # ------------------------------------------------------------------
    # 线程执行体
    # ------------------------------------------------------------------
    def run(self) -> None:
        """QThread 主循环"""
        completed_count = 0

        while self._running:
            if self._paused:
                time.sleep(0.5)
                continue

            try:
                task = self._queue.get(timeout=1)
            except queue.Empty:
                # 等待新任务
                continue

            if not self._running:
                break

            # 开始生成
            self._generate_one(task)
            completed_count += 1

            self.queue_progress.emit(completed_count, self._total_count)

            # 队列间隔
            interval = self._config.settings.queue_interval
            for _ in range(interval * 10):
                if not self._running:
                    break
                time.sleep(0.1)

        self.all_done.emit()

    def _generate_one(self, task: Task) -> None:
        """生成单个任务"""
        # 状态: QUEUED → GENERATING
        task.transition_to(TaskStatus.GENERATING)
        self._taskset.save()
        self.task_status_changed.emit(task.id, TaskStatus.GENERATING.value)

        try:
            engine = engine_registry.get(task.engine)
            if engine is None:
                raise Exception(f"未知引擎: {task.engine}")

            url = self._config.get_engine_url(task.engine)
            if not url:
                raise Exception(f"未配置 {task.engine} 的 API 地址")

            # 合并 text 到 params（引擎 schema 不再单独包含 text 字段）
            params = dict(task.engine_params)
            params["text"] = task.text

            # 校验参数
            errors = engine.validate_params(params)
            if errors:
                raise Exception("\n".join(errors))

            # 异步调用 → 同步等待
            import asyncio
            loop = asyncio.new_event_loop()
            audio_bytes = loop.run_until_complete(engine.generate(url, params))
            loop.close()

            # 保存音频文件（indextts 固定输出 wav）
            filename = task.audio_filename("wav")
            output_path = self._taskset.outputs_dir / filename
            output_path.write_bytes(audio_bytes)

            # 保存快照配置（文案、引擎、参数）
            task.generation_config = {
                "text": task.text,
                "engine": task.engine,
                "engine_params": dict(task.engine_params),
            }
            task.output_audio_path = str(output_path)
            task.error_message = None
            task.transition_to(TaskStatus.COMPLETED)
            self._taskset.save()

            self.task_completed.emit(task.id, str(output_path))
            self.task_status_changed.emit(task.id, TaskStatus.COMPLETED.value)

        except Exception as e:
            task.error_message = str(e)
            task.transition_to(TaskStatus.FAILED)
            self._taskset.save()

            self.task_failed.emit(task.id, str(e))
            self.task_status_changed.emit(task.id, TaskStatus.FAILED.value)
