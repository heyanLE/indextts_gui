"""API 检测工具 — 探测引擎 API 类型与端点（兼容 Gradio 3.x ~ 5.x）"""

from __future__ import annotations

import httpx


async def detect_api_type(url: str) -> str:
    """探测引擎 API 类型

    Returns:
        "gradio" | "gradio5" | "openapi" | "unknown"
    """
    base_url = url.rstrip("/")

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        # 1. Gradio 5.x: /config 端点
        try:
            resp = await client.get(f"{base_url}/config")
            if resp.status_code == 200:
                data = resp.json()
                if "version" in data and "components" in data:
                    return "gradio5"
        except Exception:
            pass

        # 2. Gradio 3.x/4.x: /info 端点
        try:
            resp = await client.get(f"{base_url}/info")
            if resp.status_code == 200:
                data = resp.json()
                if "version" in data or "named_endpoints" in data:
                    return "gradio"
        except Exception:
            pass

        # 3. OpenAPI
        try:
            resp = await client.get(f"{base_url}/openapi.json")
            if resp.status_code == 200:
                return "openapi"
        except Exception:
            pass

        # 4. FastAPI docs
        try:
            resp = await client.get(f"{base_url}/docs")
            if resp.status_code == 200:
                return "openapi"
        except Exception:
            pass

    return "unknown"


async def get_gradio_endpoints(url: str) -> dict[str, any]:
    """获取 Gradio API 的命名端点信息（兼容 Gradio 3.x ~ 5.x）

    Returns:
        { "endpoint_name": {"parameters": [...], "returns": [...]} }
    """
    base_url = url.rstrip("/")
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        # 先尝试 Gradio 5.x /config
        resp = await client.get(f"{base_url}/config")
        if resp.status_code == 200:
            config = resp.json()
            # 从 config 中提取端点信息
            endpoints = _parse_gradio5_endpoints(config)
            if endpoints:
                return endpoints

        # 回退到 Gradio 3.x/4.x /info
        resp = await client.get(f"{base_url}/info")
        resp.raise_for_status()
        info = resp.json()
        return info.get("named_endpoints", {})


def _parse_gradio5_endpoints(config: dict[str, any]) -> dict[str, any]:
    """从 Gradio 5.x config 中提取端点信息"""
    named_endpoints: dict[str, any] = {}
    dependencies = config.get("dependencies", [])
    for dep in dependencies:
        api_name = dep.get("api_name", "")
        if api_name:
            named_endpoints[api_name] = {
                "parameters": dep.get("inputs", []),
                "returns": dep.get("outputs", []),
                "fn_index": dep.get("id", 0),
                "triggers": dep.get("triggers", ""),
            }
    return named_endpoints
