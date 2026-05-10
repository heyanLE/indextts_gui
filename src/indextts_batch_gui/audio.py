from __future__ import annotations

from pathlib import Path

try:  # pragma: no cover - platform backend availability varies.
    from PySide6.QtCore import QUrl
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
except Exception:  # pragma: no cover
    QAudioOutput = None
    QMediaPlayer = None
    QUrl = None


class AudioPlaybackError(RuntimeError):
    pass


class AudioPlaybackService:
    def __init__(self) -> None:
        if QMediaPlayer is None or QAudioOutput is None or QUrl is None:
            self._player = None
            self._audio_output = None
            self._current_source_path: Path | None = None
            return

        self._player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._audio_output.setVolume(0.8)
        self._player.setAudioOutput(self._audio_output)
        self._current_source_path: Path | None = None

    def play(self, audio_path: Path) -> None:
        if not audio_path.exists():
            raise AudioPlaybackError(f"未找到音频文件: {audio_path}")
        if self._player is None or QUrl is None:
            raise AudioPlaybackError("当前环境不支持 Qt 音频播放")
        try:
            resolved = audio_path.resolve()
            self._player.setSource(QUrl.fromLocalFile(str(resolved)))
            self._current_source_path = resolved
            self._player.play()
        except Exception as exc:  # pragma: no cover
            raise AudioPlaybackError(str(exc)) from exc

    def pause(self) -> None:
        if self._player is None:
            raise AudioPlaybackError("当前环境不支持 Qt 音频播放")
        self._player.pause()

    def resume(self) -> None:
        if self._player is None:
            raise AudioPlaybackError("当前环境不支持 Qt 音频播放")
        self._player.play()

    def toggle_pause(self) -> bool:
        if self._player is None or QMediaPlayer is None:
            raise AudioPlaybackError("当前环境不支持 Qt 音频播放")
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            return True
        self._player.play()
        return False

    def stop(self) -> None:
        if self._player is not None:
            self._player.stop()
            if QUrl is not None:
                self._player.setSource(QUrl())
            self._current_source_path = None

    def release_file(self, audio_path: Path) -> None:
        if self._player is None:
            return
        try:
            target = audio_path.resolve()
        except OSError:
            target = audio_path
        if self._current_source_path is None:
            return
        if self._current_source_path != target:
            return
        self.stop()

    def set_volume(self, volume: float) -> None:
        if self._audio_output is None:
            raise AudioPlaybackError("当前环境不支持 Qt 音频播放")
        self._audio_output.setVolume(max(0.0, min(1.0, float(volume))))
