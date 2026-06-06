"""引擎抽象基类 — 定义统一的引擎接口"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParamField:
    """参数字段定义"""

    name: str
    label: str
    field_type: str  # "text" | "file" | "select" | "slider" | "group"
    required: bool = False
    default: Any = None
    # select 选项
    options: list[str] = field(default_factory=list)
    # slider 范围
    min_val: float = 0.0
    max_val: float = 1.0
    step: float = 0.01
    # 子字段（用于 group 类型，如情感向量模式）
    children: list[ParamField] = field(default_factory=list)
    # 可见性条件：{"emotion_mode": "emotion_vector"} 或 {"field": "emotion_mode", "value": "emotion_vector"}
    visible_when: dict[str, Any] = field(default_factory=dict)


@dataclass
class EngineMeta:
    """引擎元信息"""

    engine_id: str
    engine_name: str
    version: str = "1.0"
    description: str = ""


class BaseEngine(ABC):
    """TTS 引擎抽象基类

    所有引擎适配器需继承此类并实现全部抽象方法。
    """

    # 子类必须覆盖这两个类属性
    meta: EngineMeta

    @abstractmethod
    def get_param_schema(self) -> list[ParamField]:
        """返回引擎参数的定义 schema

        用于 UI 动态生成参数表单。
        """
        ...

    @abstractmethod
    def validate_params(self, params: dict[str, Any]) -> list[str]:
        """校验参数合法性，返回错误消息列表（空列表表示通过）"""
        ...

    @abstractmethod
    async def test_connection(self, url: str) -> tuple[bool, str]:
        """测试引擎 API 连接

        Returns:
            (是否成功, 状态消息)
        """
        ...

    @abstractmethod
    async def generate(self, url: str, params: dict[str, Any]) -> bytes:
        """调用引擎 API 生成音频

        Args:
            url: 引擎 API 地址
            params: 已校验的参数字典

        Returns:
            生成的音频二进制数据

        Raises:
            EngineException: 调用失败时抛出
        """
        ...


class EngineException(Exception):
    """引擎异常"""
    pass
