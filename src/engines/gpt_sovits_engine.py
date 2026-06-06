"""GPT-SoVITS 引擎适配器 — 预留框架"""

from __future__ import annotations

from typing import Any

from .base_engine import (
    BaseEngine,
    EngineException,
    EngineMeta,
    ParamField,
)


class GPTSovitsEngine(BaseEngine):
    """GPT-SoVITS 引擎适配器（预留，待后续对接细化）"""

    meta = EngineMeta(
        engine_id="gpt_sovits",
        engine_name="GPT-SoVITS",
        version="0.1",
        description="GPT-SoVITS 语音合成引擎（待对接）",
    )

    def get_param_schema(self) -> list[ParamField]:
        return [
            ParamField(
                name="reference_audio",
                label="参考音频",
                field_type="file",
                required=True,
            ),
            ParamField(
                name="text",
                label="目标文案",
                field_type="text",
                required=True,
            ),
            ParamField(
                name="language",
                label="语言",
                field_type="select",
                required=False,
                default="zh",
                options=["zh", "en", "ja"],
            ),
        ]

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not params.get("text", "").strip():
            errors.append("目标文案不能为空")
        if not params.get("reference_audio", ""):
            errors.append("参考音频不能为空")
        return errors

    async def test_connection(self, url: str) -> tuple[bool, str]:
        import httpx

        base_url = url.rstrip("/")
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            # Gradio 5.x: /config
            try:
                resp = await client.get(f"{base_url}/config")
                if resp.status_code == 200:
                    data = resp.json()
                    ver = data.get("version", "unknown")
                    return True, f"连接成功 (Gradio {ver})"
            except Exception:
                pass

            # Gradio 3.x/4.x: /info
            try:
                resp = await client.get(f"{base_url}/info")
                if resp.status_code == 200:
                    data = resp.json()
                    ver = data.get("version", "unknown")
                    return True, f"连接成功 (Gradio {ver})"
            except Exception:
                pass

            # 基础可达性
            try:
                resp = await client.get(base_url)
                if resp.status_code < 500:
                    return True, "服务器可达"
            except httpx.ConnectError:
                return False, "无法连接到服务器"
            except httpx.TimeoutException:
                return False, "连接超时"
            except Exception as e:
                return False, f"连接失败: {e}"

        return False, "未知错误"

    async def generate(self, url: str, params: dict[str, Any]) -> bytes:
        raise EngineException("GPT-SoVITS 引擎尚未实现，等待 API 对接")
