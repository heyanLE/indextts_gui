"""全局音频播放器 — 基于 QMediaPlayer 的单例组件"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Signal, QObject
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QLabel, QSlider
from PySide6.QtCore import Qt


class AudioPlayer(QWidget):
    """全局音频播放器

    全局单例，顶部固定栏。支持播放/暂停，新播放自动停止旧播放。
    """

    # 信号
    playback_started = Signal(str)  # 文件路径
    playback_paused = Signal()
    playback_stopped = Signal()
    position_changed = Signal(int)  # 毫秒

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._player: QMediaPlayer | None = None
        self._audio_output: QAudioOutput | None = None
        self._current_file: str = ""
        self._is_playing: bool = False
        self._slider_pressed: bool = False
        self._stopped_notified: bool = True

        self._setup_ui()
        self._setup_player()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _setup_ui(self) -> None:
        self.setFixedHeight(48)
        self.setObjectName("audioPlayerBar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(8)

        # 播放/暂停按钮
        self._play_btn = QPushButton("▶")
        self._play_btn.setFixedSize(36, 36)
        self._play_btn.setObjectName("playBtn")
        self._play_btn.clicked.connect(self._toggle_play)
        self._play_btn.setEnabled(False)

        # 播放进度条
        self._progress_slider = QSlider(Qt.Orientation.Horizontal)
        self._progress_slider.setObjectName("progressSlider")
        self._progress_slider.setRange(0, 1000)
        self._progress_slider.setEnabled(False)
        self._progress_slider.sliderPressed.connect(self._on_slider_pressed)
        self._progress_slider.sliderReleased.connect(self._on_slider_released)

        # 时间显示
        self._time_label = QLabel("00:00 / 00:00")
        self._time_label.setObjectName("timeLabel")
        self._time_label.setFixedWidth(110)

        # 文件名显示
        self._file_label = QLabel("未播放")
        self._file_label.setObjectName("fileLabel")
        self._file_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(self._play_btn)
        layout.addWidget(self._progress_slider, 1)
        layout.addWidget(self._time_label)
        layout.addWidget(self._file_label, 2)

    def _setup_player(self) -> None:
        self._player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._audio_output.setVolume(0.8)
        self._player.setAudioOutput(self._audio_output)

        self._player.playbackStateChanged.connect(self._on_state_changed)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.errorOccurred.connect(self._on_error)

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------
    def play(self, file_path: str) -> None:
        """播放音频文件（自动停止当前播放）"""
        if not file_path or not Path(file_path).exists():
            return

        self._current_file = file_path

        if self._player:
            self._player.stop()
            self._player.setSource(QUrl.fromLocalFile(file_path))
            self._stopped_notified = False
            self._player.play()

        self._play_btn.setEnabled(True)
        self._progress_slider.setEnabled(True)
        self._play_btn.setText("⏸")
        self._file_label.setText(Path(file_path).name)
        self._is_playing = True
        self.playback_started.emit(file_path)

    def pause(self) -> None:
        if self._player:
            self._player.pause()

    def resume(self) -> None:
        if self._player:
            self._player.play()

    def stop(self) -> None:
        if self._player:
            self._player.stop()
        self._current_file = ""
        self._is_playing = False
        self._play_btn.setText("▶")
        self._play_btn.setEnabled(False)
        self._progress_slider.setEnabled(False)
        self._progress_slider.setValue(0)
        self._file_label.setText("未播放")
        self._time_label.setText("00:00 / 00:00")
        self._notify_stopped()

    def release_file(self, file_path: str) -> bool:
        """Release a matching media file so Windows can replace it.

        ``QMediaPlayer.stop()`` alone may keep the source file handle open on
        Windows.  Clearing the source after stopping releases that handle
        before a regenerated WAV is atomically published.
        """
        if not file_path or not self._current_file:
            return False

        try:
            matches = Path(self._current_file).resolve(strict=False) == Path(file_path).resolve(strict=False)
        except OSError:
            matches = self._current_file == file_path
        if not matches:
            return False

        self.stop()
        if self._player:
            self._player.setSource(QUrl())
        return True

    def _notify_stopped(self) -> None:
        """Emit one stopped event per playback session."""
        if self._stopped_notified:
            return
        self._stopped_notified = True
        self.playback_stopped.emit()

    # ------------------------------------------------------------------
    # 私有方法
    # ------------------------------------------------------------------
    def _toggle_play(self) -> None:
        if self._is_playing:
            self.pause()
        else:
            self.resume()

    def _on_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._is_playing = True
            self._play_btn.setText("⏸")
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self._is_playing = False
            self._play_btn.setText("▶")
            self.playback_paused.emit()
        elif state == QMediaPlayer.PlaybackState.StoppedState:
            self._is_playing = False
            self._play_btn.setText("▶")
            self._notify_stopped()

    def _on_position_changed(self, pos_ms: int) -> None:
        if not self._slider_pressed and self._player:
            dur = self._player.duration()
            if dur > 0:
                self._progress_slider.setValue(int(pos_ms / dur * 1000))
            self._update_time_label(pos_ms, dur)
        self.position_changed.emit(pos_ms)

    def _on_duration_changed(self, dur_ms: int) -> None:
        self._update_time_label(
            self._player.position() if self._player else 0, dur_ms
        )

    def _on_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._progress_slider.setValue(1000)
            self.stop()

    def _on_error(self, error: QMediaPlayer.Error, error_string: str) -> None:
        self.stop()
        self._file_label.setText(f"播放错误: {error_string}")

    def _on_slider_pressed(self) -> None:
        self._slider_pressed = True

    def _on_slider_released(self) -> None:
        self._slider_pressed = False
        if self._player:
            dur = self._player.duration()
            if dur > 0:
                pos = int(self._progress_slider.value() / 1000 * dur)
                self._player.setPosition(pos)

    def _update_time_label(self, pos_ms: int, dur_ms: int) -> None:
        self._time_label.setText(
            f"{self._format_time(pos_ms)} / {self._format_time(dur_ms)}"
        )

    @staticmethod
    def _format_time(ms: int) -> str:
        s = ms // 1000
        m = s // 60
        s = s % 60
        return f"{m:02d}:{s:02d}"
