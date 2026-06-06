"""配置管理器 — 应用全局配置（QSettings + JSON 混合方案）"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QSettings


@dataclass
class EngineConfig:
    """单个引擎的 URL 配置"""

    engine_id: str
    url: str = ""
    last_connected: bool = False


@dataclass
class GlobalSettings:
    """全局应用设置"""

    output_format: str = "wav"  # 音频输出格式
    queue_interval: int = 2  # 队列间隔（秒）
    download_timeout: int = 120  # 下载超时（秒）
    language: str = "zh-CN"


class ConfigManager:
    """应用全局配置管理器

    使用 QSettings 存储轻量偏好（窗口位置等），
    使用 JSON 存储复杂配置（引擎列表、历史任务集）。
    """

    SETTINGS_ORG = "IndexTTS-GUI2"
    SETTINGS_APP = "IndexTTS-GUI2"

    def __init__(self) -> None:
        self._qsettings = QSettings(self.SETTINGS_ORG, self.SETTINGS_APP)
        self._config_dir = self._resolve_config_dir()
        self._config_dir.mkdir(parents=True, exist_ok=True)

        self.engines: dict[str, EngineConfig] = {}
        self.settings = GlobalSettings()
        self.recent_task_sets: list[str] = []  # 最近的任务集路径列表
        self.current_task_set_path: Optional[str] = None

        self.load()

    # ------------------------------------------------------------------
    # 配置路径
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_config_dir() -> Path:
        return Path.home() / ".indextts-gui2"

    @property
    def config_file(self) -> Path:
        return self._config_dir / "config.json"

    # ------------------------------------------------------------------
    # 引擎配置
    # ------------------------------------------------------------------
    def set_engine_url(self, engine_id: str, url: str) -> None:
        if engine_id not in self.engines:
            self.engines[engine_id] = EngineConfig(engine_id=engine_id)
        self.engines[engine_id].url = url
        self.save()

    def get_engine_url(self, engine_id: str) -> str:
        return self.engines.get(engine_id, EngineConfig(engine_id=engine_id)).url

    def get_engine_config(self, engine_id: str) -> EngineConfig:
        if engine_id not in self.engines:
            self.engines[engine_id] = EngineConfig(engine_id=engine_id)
        return self.engines[engine_id]

    def set_engine_connected(self, engine_id: str, connected: bool) -> None:
        cfg = self.get_engine_config(engine_id)
        cfg.last_connected = connected
        self.save()

    def remove_engine(self, engine_id: str) -> None:
        self.engines.pop(engine_id, None)
        self.save()

    # ------------------------------------------------------------------
    # 全局设置
    # ------------------------------------------------------------------
    def update_settings(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            if hasattr(self.settings, k):
                setattr(self.settings, k, v)
        self.save()

    # ------------------------------------------------------------------
    # 任务集历史
    # ------------------------------------------------------------------
    def add_recent_task_set(self, path: str) -> None:
        if path in self.recent_task_sets:
            self.recent_task_sets.remove(path)
        self.recent_task_sets.insert(0, path)
        # 最多保留 20 个
        self.recent_task_sets = self.recent_task_sets[:20]
        self.save()

    def remove_recent_task_set(self, path: str) -> None:
        if path in self.recent_task_sets:
            self.recent_task_sets.remove(path)
        self.save()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def load(self) -> None:
        """从磁盘加载配置"""
        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text(encoding="utf-8"))
                # 加载引擎配置（重置连接状态，因为重启后之前的连接已无效）
                engines_raw = data.get("engines", {})
                self.engines = {
                    eid: EngineConfig(**cfg) for eid, cfg in engines_raw.items()
                }
                for cfg in self.engines.values():
                    cfg.last_connected = False
                # 加载全局设置
                settings_raw = data.get("settings", {})
                for k, v in settings_raw.items():
                    if hasattr(self.settings, k):
                        setattr(self.settings, k, v)
                # 加载历史
                self.recent_task_sets = data.get("recent_task_sets", [])
                self.current_task_set_path = data.get("current_task_set_path")
            except Exception:
                pass  # 损坏的配置文件，使用默认值

    def save(self) -> None:
        """保存配置到磁盘"""
        data: dict[str, Any] = {
            "engines": {eid: asdict(cfg) for eid, cfg in self.engines.items()},
            "settings": asdict(self.settings),
            "recent_task_sets": self.recent_task_sets,
            "current_task_set_path": self.current_task_set_path,
        }
        tmp = self.config_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.config_file)

    # ------------------------------------------------------------------
    # QSettings 代理 — 窗口几何等 UI 偏好
    # ------------------------------------------------------------------
    @property
    def window_geometry(self) -> Optional[bytes]:
        return self._qsettings.value("window/geometry")

    @window_geometry.setter
    def window_geometry(self, value: bytes) -> None:
        self._qsettings.setValue("window/geometry", value)

    @property
    def window_state(self) -> Optional[bytes]:
        return self._qsettings.value("window/state")

    @window_state.setter
    def window_state(self, value: bytes) -> None:
        self._qsettings.setValue("window/state", value)

    @property
    def splitter_state(self) -> Optional[bytes]:
        return self._qsettings.value("window/splitter")

    @splitter_state.setter
    def splitter_state(self, value: bytes) -> None:
        self._qsettings.setValue("window/splitter", value)
