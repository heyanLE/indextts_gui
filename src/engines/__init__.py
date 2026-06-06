"""引擎注册表 — 管理所有可用的 TTS 引擎"""

from __future__ import annotations

from .base_engine import BaseEngine
from .indextts_engine import IndexTTSEngine
# GPT-SoVITS 引擎接口已预留（gpt_sovits_engine.py），暂不注册
# from .gpt_sovits_engine import GPTSovitsEngine


class EngineRegistry:
    """引擎注册中心（单例模式）"""

    _instance: EngineRegistry | None = None
    _engines: dict[str, BaseEngine] = {}
    _discovered: bool = False

    def __new__(cls) -> EngineRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def discover(self) -> dict[str, BaseEngine]:
        """发现并加载所有内置引擎（首次调用时自动扫描）"""
        if not self._discovered:
            self._register(IndexTTSEngine())
            # GPT-SoVITS 暂不注册，引擎接口已预留
            # self._register(GPTSovitsEngine())
            self._discovered = True
        return dict(self._engines)

    def _register(self, engine: BaseEngine) -> None:
        self._engines[engine.meta.engine_id] = engine

    def register(self, engine: BaseEngine) -> None:
        """手动注册引擎（用于用户自定义扩展）"""
        self._engines[engine.meta.engine_id] = engine

    def get(self, engine_id: str) -> BaseEngine | None:
        return self._engines.get(engine_id)

    def list_engines(self) -> list[BaseEngine]:
        self.discover()
        return list(self._engines.values())

    def engine_ids(self) -> list[str]:
        self.discover()
        return list(self._engines.keys())


# 全局单例
engine_registry = EngineRegistry()
