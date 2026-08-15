"""配置管理器 — 应用全局配置（QSettings + JSON 混合方案）"""

from __future__ import annotations

import shutil
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Optional

from PySide6.QtCore import QSettings

from ._persistence import atomic_write_json


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
        self._lock = RLock()
        self._load_error: str | None = None
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
        if not engine_id:
            raise ValueError("engine_id 不能为空")
        with self._lock:
            previous = deepcopy(self.engines)
            if engine_id not in self.engines:
                self.engines[engine_id] = EngineConfig(engine_id=engine_id)
            self.engines[engine_id].url = str(url).strip()
            try:
                self.save()
            except Exception:
                self.engines = previous
                raise

    def get_engine_url(self, engine_id: str) -> str:
        with self._lock:
            config = self.engines.get(engine_id)
            return config.url if config is not None else ""

    def get_engine_config(self, engine_id: str) -> EngineConfig:
        with self._lock:
            return self.engines.get(engine_id, EngineConfig(engine_id=engine_id))

    def set_engine_connected(self, engine_id: str, connected: bool) -> None:
        with self._lock:
            previous = deepcopy(self.engines)
            cfg = self.engines.setdefault(engine_id, EngineConfig(engine_id=engine_id))
            cfg.last_connected = bool(connected)
            try:
                self.save()
            except Exception:
                self.engines = previous
                raise

    def remove_engine(self, engine_id: str) -> None:
        with self._lock:
            previous = deepcopy(self.engines)
            self.engines.pop(engine_id, None)
            try:
                self.save()
            except Exception:
                self.engines = previous
                raise

    # ------------------------------------------------------------------
    # 全局设置
    # ------------------------------------------------------------------
    def update_settings(self, **kwargs: Any) -> None:
        unknown = set(kwargs) - set(GlobalSettings.__dataclass_fields__)
        if unknown:
            raise ValueError(f"未知设置项: {', '.join(sorted(unknown))}")
        with self._lock:
            candidate = asdict(self.settings)
            candidate.update(kwargs)
            previous = self.settings
            self.settings = self._parse_settings(candidate)
            try:
                self.save()
            except Exception:
                self.settings = previous
                raise

    # ------------------------------------------------------------------
    # 任务集历史
    # ------------------------------------------------------------------
    def add_recent_task_set(self, path: str) -> None:
        normalized = str(Path(path).expanduser().resolve(strict=False))
        with self._lock:
            previous = list(self.recent_task_sets)
            if normalized in self.recent_task_sets:
                self.recent_task_sets.remove(normalized)
            self.recent_task_sets.insert(0, normalized)
            # 最多保留 20 个
            self.recent_task_sets = self.recent_task_sets[:20]
            try:
                self.save()
            except Exception:
                self.recent_task_sets = previous
                raise

    def activate_task_set(self, path: str) -> None:
        """Atomically persist the current path together with recent history."""
        normalized = str(Path(path).expanduser().resolve(strict=False))
        with self._lock:
            previous_recent = list(self.recent_task_sets)
            previous_current = self.current_task_set_path
            if normalized in self.recent_task_sets:
                self.recent_task_sets.remove(normalized)
            self.recent_task_sets.insert(0, normalized)
            self.recent_task_sets = self.recent_task_sets[:20]
            self.current_task_set_path = normalized
            try:
                self.save()
            except Exception:
                self.recent_task_sets = previous_recent
                self.current_task_set_path = previous_current
                raise

    def remove_recent_task_set(self, path: str) -> None:
        normalized = str(Path(path).expanduser().resolve(strict=False))
        with self._lock:
            previous = list(self.recent_task_sets)
            if normalized in self.recent_task_sets:
                self.recent_task_sets.remove(normalized)
            try:
                self.save()
            except Exception:
                self.recent_task_sets = previous
                raise

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def load(self) -> None:
        """从磁盘加载配置"""
        import json

        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("配置根节点必须是对象")
                # 加载引擎配置（重置连接状态，因为重启后之前的连接已无效）
                engines_raw = data.get("engines", {})
                if not isinstance(engines_raw, dict):
                    raise ValueError("engines 必须是对象")
                engines: dict[str, EngineConfig] = {}
                for engine_id, raw_config in engines_raw.items():
                    if not isinstance(engine_id, str) or not isinstance(raw_config, dict):
                        raise ValueError("引擎配置无效")
                    url = raw_config.get("url", "")
                    if not isinstance(url, str):
                        raise ValueError(f"引擎 {engine_id} URL 必须是字符串")
                    engines[engine_id] = EngineConfig(engine_id=engine_id, url=url.strip())
                # 加载全局设置
                settings = self._parse_settings(data.get("settings", {}))
                # 加载历史
                recent_raw = data.get("recent_task_sets", [])
                if not isinstance(recent_raw, list) or not all(isinstance(item, str) for item in recent_raw):
                    raise ValueError("recent_task_sets 必须是字符串数组")
                recent = list(dict.fromkeys(recent_raw))[:20]
                current = data.get("current_task_set_path")
                if current is not None and not isinstance(current, str):
                    raise ValueError("current_task_set_path 必须是字符串或 null")

                # Publish only after validating the complete document.
                with self._lock:
                    self.engines = engines
                    self.settings = settings
                    self.recent_task_sets = recent
                    self.current_task_set_path = current
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                self._load_error = str(exc)
                return  # 损坏的配置文件，使用默认值

    @property
    def load_error(self) -> str | None:
        return self._load_error

    @staticmethod
    def _parse_settings(raw: Any) -> GlobalSettings:
        if not isinstance(raw, dict):
            raise ValueError("settings 必须是对象")
        defaults = GlobalSettings()
        output_format = raw.get("output_format", defaults.output_format)
        queue_interval = raw.get("queue_interval", defaults.queue_interval)
        download_timeout = raw.get("download_timeout", defaults.download_timeout)
        language = raw.get("language", defaults.language)
        if not isinstance(output_format, str) or not output_format:
            raise ValueError("output_format 必须是非空字符串")
        if isinstance(queue_interval, bool) or not isinstance(queue_interval, int) or queue_interval < 0:
            raise ValueError("queue_interval 必须是非负整数")
        if isinstance(download_timeout, bool) or not isinstance(download_timeout, int) or download_timeout <= 0:
            raise ValueError("download_timeout 必须是正整数")
        if not isinstance(language, str) or not language:
            raise ValueError("language 必须是非空字符串")
        return GlobalSettings(
            output_format=output_format,
            queue_interval=queue_interval,
            download_timeout=download_timeout,
            language=language,
        )

    def save(self) -> None:
        """保存配置到磁盘"""
        with self._lock:
            data: dict[str, Any] = {
                "engines": {eid: asdict(cfg) for eid, cfg in self.engines.items()},
                "settings": asdict(self.settings),
                "recent_task_sets": list(self.recent_task_sets),
                "current_task_set_path": self.current_task_set_path,
            }
            if self._load_error is not None and self.config_file.exists():
                backup = self.config_file.with_name(
                    f"{self.config_file.stem}.corrupt-{uuid.uuid4().hex[:8]}"
                    f"{self.config_file.suffix}"
                )
                shutil.copy2(self.config_file, backup)
            atomic_write_json(self.config_file, data)
            self._load_error = None

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
