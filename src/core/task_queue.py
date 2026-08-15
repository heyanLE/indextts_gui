"""任务队列调度器 — QThread + queue.Queue 串行调度"""

from __future__ import annotations

import queue
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal, QMutex, QMutexLocker

from src.core._persistence import atomic_write_bytes
from src.core.task import Task, TaskStatus, sanitize_filename
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
    def add_tasks(self, tasks: list[Task]) -> list[Task]:
        """将可排队任务加入队列并返回实际接收的对象。"""
        accepted: list[Task] = []
        with QMutexLocker(self._mutex):
            if not self._running:
                raise RuntimeError("任务队列已停止，不能继续添加任务")
            for task in tasks:
                if task.status in (TaskStatus.PENDING, TaskStatus.FAILED, TaskStatus.COMPLETED):
                    task.transition_to(TaskStatus.QUEUED)
                    self._queue.put(task)
                    self._total_count += 1
                    accepted.append(task)
        # Never invoke arbitrary slots while holding the queue mutex.
        for task in accepted:
            self.task_status_changed.emit(task.id, TaskStatus.QUEUED.value)
        return accepted

    def stop(self) -> None:
        """停止队列处理（等待当前任务完成后退出）"""
        with QMutexLocker(self._mutex):
            self._running = False

    def pause(self) -> None:
        changed = False
        with QMutexLocker(self._mutex):
            if self._running and not self._paused:
                self._paused = True
                changed = True
        if changed:
            self.paused_changed.emit(True)

    def resume(self) -> None:
        changed = False
        with QMutexLocker(self._mutex):
            if self._paused:
                self._paused = False
                changed = True
        if changed:
            self.paused_changed.emit(False)

    def is_paused(self) -> bool:
        with QMutexLocker(self._mutex):
            return self._paused

    def drain_queue(self, taskset: TaskSet) -> int:
        """清空内部队列并将 QUEUED 任务回退到合适状态

        规则：
        - 未生成（无 output_audio_path）→ PENDING
        - 已生成（有 output_audio_path）→ COMPLETED

        Returns:
            被回退的任务数量
        """
        reverted_ids: set[str] = set()
        # 排空内部 queue.Queue
        while True:
            try:
                task = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                if task.status == TaskStatus.QUEUED:
                    self._revert_task(task)
                    reverted_ids.add(task.id)
            finally:
                self._queue.task_done()

        # 遍历 taskset 中所有 QUEUED 任务（处理可能未在内部队列里的情况）
        for task in taskset.tasks:
            if task.status == TaskStatus.QUEUED:
                self._revert_task(task)
                reverted_ids.add(task.id)

        taskset.save()
        return len(reverted_ids)

    @staticmethod
    def _revert_task(task: Task) -> None:
        """将队列中的任务回退到未生成/已生成状态"""
        task.revert_queued()

    def queue_size(self) -> int:
        return self._queue.qsize()

    # ------------------------------------------------------------------
    # 线程执行体
    # ------------------------------------------------------------------
    def run(self) -> None:
        """QThread 主循环"""
        completed_count = 0
        try:
            while True:
                with QMutexLocker(self._mutex):
                    running = self._running
                    paused = self._paused
                if not running:
                    break
                if paused:
                    time.sleep(0.05)
                    continue

                try:
                    # The timeout is both a wake-up bound for stop() and a short
                    # grace window in which the UI may append more work.
                    task = self._queue.get(timeout=0.2)
                except queue.Empty:
                    with QMutexLocker(self._mutex):
                        if self._paused:
                            continue
                        if self._queue.empty():
                            self._running = False
                            break
                    continue

                with QMutexLocker(self._mutex):
                    should_run = self._running
                if not should_run:
                    try:
                        if task.status == TaskStatus.QUEUED:
                            self._revert_task(task)
                            self.task_status_changed.emit(task.id, task.status.value)
                    finally:
                        self._queue.task_done()
                    break

                try:
                    self._generate_one(task)
                finally:
                    self._queue.task_done()
                completed_count += 1

                with QMutexLocker(self._mutex):
                    total_count = self._total_count
                self.queue_progress.emit(completed_count, total_count)

                # 队列间隔；暂停期间不消耗倒计时间。
                remaining_ticks = self._config.settings.queue_interval * 10
                while remaining_ticks > 0:
                    with QMutexLocker(self._mutex):
                        running = self._running
                        paused = self._paused
                    if not running:
                        break
                    if not paused:
                        remaining_ticks -= 1
                    time.sleep(0.1)
        finally:
            with QMutexLocker(self._mutex):
                self._running = False
                self._paused = False
            self.all_done.emit()

    def _generate_one(self, task: Task) -> None:
        """生成单个任务"""
        # 状态: QUEUED → GENERATING
        task.transition_to(TaskStatus.GENERATING)
        text, engine_id, engine_params = task.generation_snapshot()
        previous_output_path = task.output_path_snapshot()

        try:
            self._taskset.save()
            self.task_status_changed.emit(task.id, TaskStatus.GENERATING.value)

            engine = engine_registry.get(engine_id)
            if engine is None:
                raise RuntimeError(f"未知引擎: {engine_id}")

            url = self._config.get_engine_url(engine_id)
            if not url:
                raise RuntimeError(f"未配置 {engine_id} 的 API 地址")

            # 合并 text 到 params（引擎 schema 不再单独包含 text 字段）
            params = dict(engine_params)
            params["text"] = text

            # 校验参数
            errors = engine.validate_params(params)
            if errors:
                raise ValueError("\n".join(errors))

            # 异步调用 → 同步等待
            import asyncio
            loop = asyncio.new_event_loop()
            try:
                audio_bytes = loop.run_until_complete(
                    engine.generate(
                        url,
                        params,
                        timeout=self._config.settings.download_timeout,
                    )
                )
            finally:
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                finally:
                    loop.close()
            if not isinstance(audio_bytes, bytes) or not audio_bytes:
                raise TypeError("引擎必须返回非空音频 bytes")

            # 保存音频文件（indextts 固定输出 wav）
            filename = f"{task.id}_{sanitize_filename(text)}.wav"
            output_path = self._taskset.outputs_dir / filename
            atomic_write_bytes(output_path, audio_bytes)

            # 保存快照配置（文案、引擎、参数）
            generation_config = {
                "text": text,
                "engine": engine_id,
                "engine_params": engine_params,
            }
            task.complete_generation(str(output_path), generation_config)
            self._taskset.save()

            # Only clean the superseded file after the new audio and task state
            # are durably committed.  Never delete paths outside outputs_dir.
            if previous_output_path:
                previous_path = Path(previous_output_path).resolve(strict=False)
                new_path = output_path.resolve(strict=False)
                try:
                    previous_path.relative_to(self._taskset.outputs_dir.resolve())
                except ValueError:
                    pass
                else:
                    if previous_path != new_path:
                        try:
                            previous_path.unlink(missing_ok=True)
                        except OSError:
                            # Cleanup failure must not turn a successful synthesis
                            # into FAILED; the stale file is safe to remove later.
                            pass

            self.task_completed.emit(task.id, str(output_path))
            self.task_status_changed.emit(task.id, TaskStatus.COMPLETED.value)

        except Exception as e:
            error_message = str(e)
            if task.status in (TaskStatus.GENERATING, TaskStatus.COMPLETED):
                task.fail_generation(error_message)
            try:
                self._taskset.save()
            except Exception as save_error:
                error_message = f"{error_message}\n任务状态保存失败: {save_error}"

            self.task_failed.emit(task.id, error_message)
            self.task_status_changed.emit(task.id, task.status.value)
