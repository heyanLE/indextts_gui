"""IndexTTS 引擎适配器 — 对接 Gradio API (支持 3.x/4.x/5.x)"""

from __future__ import annotations

import base64
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

from .base_engine import (
    BaseEngine,
    EngineException,
    EngineMeta,
    ParamField,
)

_logger = logging.getLogger(__name__)


class IndexTTSEngine(BaseEngine):
    """IndexTTS Gradio API 适配器

    兼容 Gradio 3.x ~ 5.x，自动检测 API 版本：
    - Gradio 5.x: /config 端点, /gradio_api/run/{api_name} (SSE)
    - Gradio 3.x/4.x: /info 端点, /api/predict
    """

    meta = EngineMeta(
        engine_id="indextts",
        engine_name="IndexTTS",
        version="1.0",
        description="支持情感控制的语音合成引擎，通过 Gradio API 调用",
    )

    # ------------------------------------------------------------------
    # 参数 Schema
    # ------------------------------------------------------------------
    def get_param_schema(self) -> list[ParamField]:
        return [
            ParamField(
                name="reference_audio",
                label="参考音频",
                field_type="file",
                required=True,
            ),
            ParamField(
                name="language",
                label="语言",
                field_type="select",
                default="ZH",
                options=["ZH", "EN", "JA", "AR", "ES"],
            ),
            # 注：text（目标文案）字段已移至 TaskDetailPanel 的「文案内容」区域，
            # 避免重复输入。生成时 task.text 会自动合并到 engine_params["text"]。
            ParamField(
                name="emotion_mode",
                label="情感指定模式",
                field_type="select",
                required=True,
                default="same_as_ref",
                options=["same_as_ref", "emotion_ref_audio", "emotion_vector"],
            ),
            # --- emotion_ref_audio / emotion_vector 共用 ---
            ParamField(
                name="emotion_control_weight",
                label="情感控制权重",
                field_type="slider",
                default=0.65,
                min_val=0.0,
                max_val=1.0,
                step=0.01,
                visible_when={"emotion_mode": ["emotion_ref_audio", "emotion_vector"]},
            ),
            # --- emotion_ref_audio 模式的子字段 ---
            ParamField(
                name="emotion_audio",
                label="情感参考音频",
                field_type="file",
                required=False,
                visible_when={"emotion_mode": "emotion_ref_audio"},
            ),
            # --- emotion_vector 模式的子字段 ---
            ParamField(
                name="calm",
                label="平静 (Calm)",
                field_type="slider",
                default=0.0,
                min_val=0.0,
                max_val=1.0,
                step=0.01,
                visible_when={"emotion_mode": "emotion_vector"},
            ),
            ParamField(
                name="surprised",
                label="惊讶 (Surprised)",
                field_type="slider",
                default=0.0,
                min_val=0.0,
                max_val=1.0,
                step=0.01,
                visible_when={"emotion_mode": "emotion_vector"},
            ),
            ParamField(
                name="melancholic",
                label="忧郁 (Melancholic)",
                field_type="slider",
                default=0.0,
                min_val=0.0,
                max_val=1.0,
                step=0.01,
                visible_when={"emotion_mode": "emotion_vector"},
            ),
            ParamField(
                name="disgusted",
                label="厌恶 (Disgusted)",
                field_type="slider",
                default=0.0,
                min_val=0.0,
                max_val=1.0,
                step=0.01,
                visible_when={"emotion_mode": "emotion_vector"},
            ),
            ParamField(
                name="afraid",
                label="害怕 (Afraid)",
                field_type="slider",
                default=0.0,
                min_val=0.0,
                max_val=1.0,
                step=0.01,
                visible_when={"emotion_mode": "emotion_vector"},
            ),
            ParamField(
                name="sad",
                label="悲伤 (Sad)",
                field_type="slider",
                default=0.0,
                min_val=0.0,
                max_val=1.0,
                step=0.01,
                visible_when={"emotion_mode": "emotion_vector"},
            ),
            ParamField(
                name="angry",
                label="愤怒 (Angry)",
                field_type="slider",
                default=0.0,
                min_val=0.0,
                max_val=1.0,
                step=0.01,
                visible_when={"emotion_mode": "emotion_vector"},
            ),
            ParamField(
                name="happy",
                label="快乐 (Happy)",
                field_type="slider",
                default=0.0,
                min_val=0.0,
                max_val=1.0,
                step=0.01,
                visible_when={"emotion_mode": "emotion_vector"},
            ),
            ParamField(
                name="duration_factor",
                label="时长系数",
                field_type="slider",
                default=1.0,
                min_val=0.5,
                max_val=2.0,
                step=0.01,
            ),
            ParamField(
                name="postprocess_trim_leading_breath",
                label="生成后去句首气口",
                field_type="checkbox",
                default=False,
            ),
            ParamField(
                name="postprocess_denoise",
                label="生成后轻度降噪",
                field_type="checkbox",
                default=False,
            ),
        ]

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        errors: list[str] = []

        if not params.get("text", "").strip():
            errors.append("目标文案不能为空")
        if not params.get("reference_audio", ""):
            errors.append("参考音频不能为空")

        language = str(params.get("language", "ZH")).upper()
        if language not in {"ZH", "EN", "JA", "AR", "ES"}:
            errors.append("语言必须为 ZH、EN、JA、AR 或 ES")

        try:
            duration_factor = float(params.get("duration_factor", 1.0))
            if not 0.5 <= duration_factor <= 2.0:
                errors.append("时长系数取值范围为 0.5~2.0")
        except (TypeError, ValueError):
            errors.append("时长系数必须为数字")

        mode = params.get("emotion_mode", "same_as_ref")
        if mode == "emotion_ref_audio" and not params.get("emotion_audio"):
            errors.append("情感参考音频不能为空（emotion_ref_audio 模式）")

        if mode == "emotion_vector":
            for key in [
                "emotion_control_weight",
                "calm", "surprised", "melancholic",
                "disgusted", "afraid", "sad", "angry", "happy",
            ]:
                val = params.get(key, 0.0)
                try:
                    val = float(val)
                except (TypeError, ValueError):
                    errors.append(f"{key} 必须为数字")
                    continue
                if val < 0.0 or val > 1.0:
                    errors.append(f"{key} 取值范围为 0~1")
        return errors

    # ------------------------------------------------------------------
    # API 调用 — 连接测试
    # ------------------------------------------------------------------
    async def test_connection(self, url: str) -> tuple[bool, str]:
        """测试 Gradio API 连接

        按优先级探测不同 Gradio 版本的端点：
        1. /config — Gradio 5.x
        2. /info   — Gradio 3.x / 4.x
        3. 基础页面 — 兜底可达性测试
        """
        base_url = url.rstrip("/")
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            # 策略 1: Gradio 5.x /config 端点
            try:
                resp = await client.get(f"{base_url}/config")
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict) and (
                        "components" in data or "dependencies" in data
                    ):
                        ver = data.get("version", "unknown")
                        return True, f"连接成功 (Gradio {ver})"
            except Exception:
                pass

            # 策略 2: Gradio 3.x/4.x /info 端点
            try:
                resp = await client.get(f"{base_url}/info")
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict) and (
                        "named_endpoints" in data or "unnamed_endpoints" in data
                    ):
                        ver = data.get("version", "unknown")
                        return True, f"连接成功 (Gradio {ver})"
            except Exception:
                pass

            # 策略 3: 基础页面可达性
            try:
                resp = await client.head(base_url)
                if resp.status_code < 500:
                    return False, "服务器可达，但未检测到兼容的 Gradio API 端点"
            except Exception:
                pass

            # 策略 4: 基础 GET
            try:
                resp = await client.get(base_url)
                if resp.status_code < 500:
                    return False, "服务器可达，但未检测到兼容的 Gradio API 端点"
            except httpx.ConnectError:
                return False, "无法连接到服务器"
            except httpx.TimeoutException:
                return False, "连接超时"
            except Exception as e:
                return False, f"连接失败: {e}"

        return False, "未知错误"

    # ------------------------------------------------------------------
    # API 调用 — 生成
    # ------------------------------------------------------------------
    @staticmethod
    def _is_remote_server(url: str) -> bool:
        """检测是否为远程 Gradio 服务器（.gradio.live 等）"""
        return (
            ".gradio.live" in url
            or ".hf.space" in url
            or url.startswith("https://") and ("localhost" not in url and "127.0.0.1" not in url)
        )

    async def generate(
        self,
        url: str,
        params: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> bytes:
        """调用 Gradio API 生成音频

        策略：
        1. 优先使用 gradio_client（自动上传文件，适配远程服务器）
        2. 仅在本地服务器时回退到 HTTP API（远程服务器无法访问本地文件路径）
        """
        base_url = url.rstrip("/")
        is_remote = self._is_remote_server(base_url)
        request_timeout = max(float(timeout if timeout is not None else 360.0), 1.0)

        _logger.info("▶ 开始生成: url=%s, text=%s, emotion_mode=%s remote=%s",
                      base_url, params.get("text", "")[:30], params.get("emotion_mode", "same_as_ref"), is_remote)

        # 策略 1: gradio_client（推荐 — 自动处理文件上传）
        gradio_client_error: Exception | None = None
        try:
            _logger.info("策略1: gradio_client...")
            result = await self._generate_via_gradio_client(
                base_url, params, timeout=request_timeout
            )
            _logger.info("✓ gradio_client 生成成功, 音频大小=%d bytes", len(result))
            return result
        except Exception as e:
            gradio_client_error = e
            _logger.warning("策略1 gradio_client 失败: %s", e)

        # 策略 2: 原始 HTTP API（仅本地服务器可用）
        if is_remote:
            raise EngineException(
                f"远程服务器 {base_url} 不支持 HTTP 文件路径传输。"
                f"gradio_client 上传已失败，无法回退到 HTTP: {gradio_client_error}"
            ) from gradio_client_error

        try:
            _logger.info("策略2: 回退到 HTTP API (本地服务器)...")
            result = await self._generate_via_http(
                base_url, params, timeout=request_timeout
            )
            _logger.info("✓ HTTP API 生成成功, 音频大小=%d bytes", len(result))
            return result
        except Exception as e:
            _logger.error("策略2 HTTP API 也失败: %s", e)
            raise EngineException(
                "所有生成方式均失败: "
                f"gradio_client={gradio_client_error}; HTTP={e}"
            ) from e

    # ------------------------------------------------------------------
    # 策略 1: gradio_client 库（推荐 — 自动上传文件）
    # ------------------------------------------------------------------
    async def _generate_via_gradio_client(
        self,
        base_url: str,
        params: dict[str, Any],
        *,
        timeout: float = 360.0,
    ) -> bytes:
        """通过 gradio_client 库调用（兼容 Gradio 3.x ~ 5.x）"""
        import asyncio

        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as pool:
            return await loop.run_in_executor(
                pool, self._gradio_client_predict, base_url, params, timeout
            )

    def _gradio_client_predict(
        self, base_url: str, params: dict[str, Any], timeout: float = 360.0
    ) -> bytes:
        """在独立线程中执行 gradio_client 预测（同步调用）

        失败时直接抛出异常，由上层 generate() 回退到 HTTP 策略。
        不再使用 client.submit() 降级——submit 会触发队列二次请求导致参数变形。
        """
        from gradio_client import Client

        _logger.info("▶ gradio_client: 连接 %s", base_url)
        client = Client(base_url, httpx_kwargs={"timeout": timeout})

        # 获取 API 信息
        api_info = client.view_api(return_format="dict", print_info=False)
        _logger.info("命名端点: %s", list(api_info.get("named_endpoints", {}).keys()))
        predict_api = self._find_gradio_client_api(api_info)
        _logger.info("API 端点: %s", predict_api)

        # 打印 API 参数信息
        ep_info = self._resolve_api_endpoint_info(api_info, predict_api)
        param_names = [p.get("parameter_name", "?") for p in ep_info.get("parameters", [])]
        _logger.info("API 参数 (%d): %s", len(param_names), param_names)

        # Keep server-specific labels local to this request. EngineRegistry
        # reuses one adapter instance and different queues may overlap.
        emotion_mode_labels = self._fetch_radio_choices(base_url)
        _logger.info("Radio 标签: %s", emotion_mode_labels)

        # 构建新版 WebUI 的完整 26 参数（已内置 sanitize）
        args = self._build_gradio_client_args_full(
            params, emotion_mode_labels=emotion_mode_labels
        )

        # 防御性日志：输出前几个参数类型
        _logger.info("参数[0]=%s (type=%s), [1]=%s, [2]=%s...%s, 总计=%d",
                       repr(args[0])[:50], type(args[0]).__name__,
                       type(args[1]).__name__,
                       repr(args[2])[:30], "...", len(args))

        if len(args) != 26:
            _logger.warning("⚠ 参数数量=%d (期望26)", len(args))

        # 调用 predict — 不再用 submit 降级
        try:
            result = client.predict(*args, api_name=predict_api)
        except Exception as e:
            _logger.error("gradio_client predict 调用失败: %s", e)
            # 输出前 4 个参数帮助诊断
            for i in range(min(4, len(args))):
                arg_val = args[i]
                val_repr = repr(arg_val)[:120]
                _logger.error("  arg[%d]: type=%s, value=%s", i, type(arg_val).__name__, val_repr)
            raise

        _logger.info("gradio_client predict 返回: type=%s", type(result).__name__)

        # 调试：打印返回值的结构
        if isinstance(result, dict):
            _logger.info("返回dict keys: %s", list(result.keys()))
            for k, v in result.items():
                v_str = str(v)[:200]
                _logger.info("  dict[%s]: type=%s, value=%s", k, type(v).__name__, v_str)
        elif isinstance(result, (list, tuple)):
            _logger.info("返回序列长度=%d, [0] type=%s", len(result), type(result[0]).__name__ if result else "empty")
            if result and isinstance(result[0], dict):
                _logger.info("  [0] dict keys: %s, sample=%s", list(result[0].keys())[:10], str(dict(list(result[0].items())[:3]))[:200])

        audio = self._extract_audio_from_result(result, download_timeout=timeout)
        if not audio:
            raise EngineException(f"gradio_client 未返回有效音频数据, 返回类型={type(result).__name__}")
        _logger.info("✓ gradio_client 提取音频: %d bytes", len(audio))
        return audio

    @staticmethod
    def _fetch_radio_choices(base_url: str) -> list[str] | None:
        """从 Gradio 服务器获取第一个 Radio 组件的 choices（显示标签）

        用于将内部 emotion_mode 值映射为 Gradio 期望的显示标签。
        """
        try:
            with httpx.Client(timeout=10.0, follow_redirects=True) as _client:
                # 尝试 Gradio 5.x /config
                resp = _client.get(f"{base_url}/config")
                if resp.status_code == 200:
                    config = resp.json()
                    choices = IndexTTSEngine._extract_radio_choices(config)
                    if choices:
                        _logger.debug("从 /config 获取 Radio choices: %s", choices)
                        return choices

                # 回退 Gradio 3.x/4.x /info
                resp = _client.get(f"{base_url}/info")
                if resp.status_code == 200:
                    config = resp.json()
                    choices = IndexTTSEngine._extract_radio_choices(config)
                    if choices:
                        _logger.debug("从 /info 获取 Radio choices: %s", choices)
                        return choices

            _logger.debug("无法获取 Radio choices (Gradio 2.x?)")
        except Exception as e:
            _logger.debug("获取 Radio choices 失败: %s", e)
        return None

    @staticmethod
    def _find_gradio_client_api(api_info: dict[str, Any]) -> str:
        """从 gradio_client 的 API 信息中找到正确的端点名"""
        named_endpoints = api_info.get("named_endpoints", {})
        # 优先匹配 gen_single / predict 等常见名称
        priority = ["/gen_single", "gen_single", "/predict", "predict", "synthesize"]
        for name in priority:
            if name in named_endpoints:
                return name
        # 回退：按名称中包含 gen 或 predict
        for name in named_endpoints:
            lower = name.lower()
            if "gen" in lower or "predict" in lower or "synthesize" in lower:
                return name
        # 最后的回退
        if named_endpoints:
            return list(named_endpoints.keys())[0]
        return "/predict"

    @staticmethod
    def _resolve_api_endpoint_info(
        api_info: dict[str, Any], predict_api: str
    ) -> dict[str, Any]:
        """解析 endpoint 参数信息（兼容带/不带斜杠的 key）"""
        named_endpoints = api_info.get("named_endpoints", {})
        ep_info = named_endpoints.get(predict_api)
        if not ep_info:
            ep_info = named_endpoints.get(predict_api.lstrip("/"))
        if not ep_info:
            ep_info = named_endpoints.get("/" + predict_api.lstrip("/"))
        return ep_info or {}

    @staticmethod
    def _get_all_param_defaults(
        api_info: dict[str, Any], predict_api: str
    ) -> list[Any]:
        """从 API 信息中提取所有参数的默认值列表"""
        ep_info = IndexTTSEngine._resolve_api_endpoint_info(api_info, predict_api)
        param_list = ep_info.get("parameters", [])

        defaults: list[Any] = []
        for p in param_list:
            if p.get("parameter_has_default", False):
                defaults.append(p.get("parameter_default"))
            else:
                defaults.append(None)
        return defaults

    def _build_gradio_client_args(
        self,
        params: dict[str, Any],
        *,
        emotion_mode_labels: list[str] | None = None,
    ) -> list[Any]:
        """构建新版 WebUI 已知的前 15 个参数。

        参数顺序必须与 Gradio UI 组件布局严格一致：
         1. emotion_mode           (Radio)
         2. reference_audio        (Audio)
         3. text                   (TextArea)
         4. language               (Dropdown) — lang_choice
         5. emotion_audio          (Audio / None)
         6. emotion_control_weight (Slider) — emo_weight
         7-14. emotion vectors     (Slider) — vec1~vec8
        15. emotion_description    (TextArea) — emo_text
        """
        import os as _os
        from gradio_client import handle_file

        def _safe_handle_file(path: str, label: str) -> Any:
            """安全包装 handle_file：检查文件是否存在"""
            if not path:
                return None
            if not _os.path.isfile(path):
                _logger.warning("%s 文件不存在: %s", label, path)
                raise FileNotFoundError(f"{label} 文件不存在: {path}")
            _logger.debug("%s: %s (%d bytes)", label, path, _os.path.getsize(path))
            return handle_file(path)

        mode = params.get("emotion_mode", "same_as_ref")

        # 将内部值映射为 Gradio Radio 的显示标签
        emotion_label = self._map_emotion_mode_to_label(
            mode, emotion_mode_labels=emotion_mode_labels
        )

        _logger.debug("构建 gradio_client 参数: emotion_mode=%s → label=%s", mode, emotion_label)

        # 1. 情感模式 (Radio)
        args: list[Any] = [emotion_label]

        # 2. 参考音频 (Audio) — 必须上传
        ref_path = params.get("reference_audio", "")
        if ref_path:
            args.append(_safe_handle_file(ref_path, "参考音频"))
        else:
            raise EngineException("缺少参考音频, 无法调用 Gradio API")

        # 3. 目标文案 (TextArea)
        text = params.get("text", "")
        args.append(text)
        _logger.debug("目标文案: %s", text[:50] if text else "(空)")

        # 4. 语言（新版 WebUI 的 lang_choice）
        args.append(str(params.get("language", "ZH")).upper())

        # 5. 情感参考音频 (Audio) — 仅 emotion_ref_audio 模式有效
        if mode == "emotion_ref_audio":
            emo_path = params.get("emotion_audio", "")
            args.append(_safe_handle_file(emo_path, "情感参考音频") if emo_path else None)
        else:
            args.append(None)

        # 6-14. 情感向量 — 9 个独立 slider 值
        # 顺序必须匹配 API: emo_weight, happy, angry, sad, afraid, disgusted, melancholic, surprised, calm
        emotion_keys = [
            "emotion_control_weight",
            "happy", "angry", "sad",
            "afraid", "disgusted", "melancholic", "surprised", "calm",
        ]
        vec_values: list[float] = []
        for key in emotion_keys:
            if mode == "emotion_vector":
                val = float(params.get(key, 0.0))
            elif key == "emotion_control_weight" and mode == "emotion_ref_audio":
                # 情感参考音频模式：使用用户配置的 weight
                val = float(params.get(key, 0.65))
            elif key == "emotion_control_weight" and mode == "same_as_ref":
                # "与参考音频相同"模式：权重固定为 1.0
                val = 1.0
            else:
                val = 0.0
            vec_values.append(val)
        args.extend(vec_values)
        _logger.debug("情感向量 %s: %s", mode, vec_values)

        # 15. 情感描述文本 (TextArea) — emo_text
        emo_text = params.get("emotion_description", "") if mode == "emotion_vector" else ""
        args.append(emo_text)

        _logger.info("构建 %d 个已知参数 (emotion_mode=%s)", len(args), mode)
        return args  # 15 items

    # ------------------------------------------------------------------
    # emotion_mode 值映射（内部值 → Gradio Radio 显示标签）
    # ------------------------------------------------------------------
    # Gradio Radio 组件的 choices 使用显示标签而非内部值。
    # 我们根据索引映射：same_as_ref(0)→标签0, emotion_ref_audio(1)→标签1, ...
    _INTERNAL_TO_LABEL_INDEX = {
        "same_as_ref": 0,
        "emotion_ref_audio": 1,
        "emotion_vector": 2,
    }

    # Gradio Radio 标签的硬编码回退映射（内部值 → 英文显示标签）
    # 当无法从 /config 获取 Radio choices 时使用
    _HARDCODED_EMOTION_LABELS: dict[str, str] = {
        "same_as_ref": "Same as the voice reference",
        "emotion_ref_audio": "Use emotion reference audio",
        "emotion_vector": "Use emotion vectors",
    }

    @staticmethod
    def _extract_radio_choices(api_info: dict[str, Any]) -> list[str] | None:
        """从 API 信息中提取 emotion_mode Radio 的 choices 标签列表

        Gradio 5.x 返回格式: [[\"label\", \"value\"], ...]（二维数组）
        Gradio 3.x 返回格式: [\"label1\", \"label2\", ...]（一维数组）
        """
        components = api_info.get("components", [])
        for comp in components:
            if comp.get("type") == "radio":
                choices = comp.get("props", {}).get("choices", [])
                if not choices:
                    continue
                # 归一化：提取每个 choice 的标签（第一项）
                labels: list[str] = []
                for c in choices:
                    if isinstance(c, (list, tuple)):
                        labels.append(str(c[0]) if c else "")
                    else:
                        labels.append(str(c))
                if labels:
                    return labels
        return None

    def _map_emotion_mode_to_label(
        self,
        internal: str,
        *,
        emotion_mode_labels: list[str] | None = None,
    ) -> str:
        """将内部 emotion_mode 值映射为 Gradio Radio 显示标签"""
        # 优先使用从服务器获取的 Radio choices
        if emotion_mode_labels:
            idx = self._INTERNAL_TO_LABEL_INDEX.get(internal, 0)
            if idx < len(emotion_mode_labels):
                return emotion_mode_labels[idx]

        # 回退：使用硬编码的英文标签
        hardcoded = self._HARDCODED_EMOTION_LABELS.get(internal)
        if hardcoded:
            _logger.info("使用硬编码 emotion 标签: %s → %s", internal, hardcoded)
            return hardcoded

        # 最终回退
        _logger.warning("无法映射 emotion_mode: %s，使用原值", internal)
        return internal

    # 新版 gen_single 位置 16-26 的硬编码默认值
    # 直接硬编码，不再依赖不可靠的 /config components 顺序提取
    _TAIL_DEFAULTS: list[Any] = [
        False,   # 16: emo_random (Checkbox, default=false)
        120,     # 17: max_text_tokens_per_segment (Slider, default=120)
        1.0,     # 18: duration_factor (Slider, default=1.0)
        True,    # 19: do_sample (Checkbox, default=true)
        0.8,     # 20: top_p (Slider, default=0.8)
        30,      # 21: top_k (Slider, default=30)
        0.8,     # 22: temperature (Slider, default=0.8)
        0.0,     # 23: length_penalty (Number, default=0.0)
        3,       # 24: num_beams (Slider, default=3)
        10.0,    # 25: repetition_penalty (Number, default=10.0)
        1500,    # 26: max_mel_tokens (Slider, default=1500)
    ]

    # --- 参数「期望类型」定义表（位置 0-25，用于 sanitize） ---
    # 类型标识: "radio", "audio", "text", "number", "bool"
    _ARG_TYPE_MAP: tuple[str, ...] = (
        "radio",   #  0: emotion_mode
        "audio",   #  1: reference_audio
        "text",    #  2: text
        "text",    #  3: language
        "audio",   #  4: emotion_audio
        "number",  #  5: emotion_control_weight
        "number",  #  6: happy
        "number",  #  7: angry
        "number",  #  8: sad
        "number",  #  9: afraid
        "number",  # 10: disgusted
        "number",  # 11: melancholic
        "number",  # 12: surprised
        "number",  # 13: calm
        "text",    # 14: emotion_description
        "bool",    # 15: emo_random
        "number",  # 16: max_text_tokens_per_segment
        "number",  # 17: duration_factor
        "bool",    # 18: do_sample
        "number",  # 19: top_p
        "number",  # 20: top_k
        "number",  # 21: temperature
        "number",  # 22: length_penalty
        "number",  # 23: num_beams
        "number",  # 24: repetition_penalty
        "number",  # 25: max_mel_tokens
    )

    @staticmethod
    def _sanitize_arg(value: Any, idx: int, type_tag: str) -> Any:
        """将单个参数值转换为 gen_single 期望的类型"""
        if value is None:
            # None 回退为安全的零值
            if type_tag == "radio":
                return ""
            if type_tag == "text":
                return ""
            if type_tag == "audio":
                return None  # Audio 组件接收 None 表示"无文件"，转为 0.0 会导致 FileData 校验失败
            if type_tag == "bool":
                return False
            return 0.0

        if type_tag == "radio":
            # Radio 必须是一个纯字符串，不能是 list
            if isinstance(value, list):
                return str(value[0]) if value else ""
            if not isinstance(value, str):
                return str(value)
            return value

        if type_tag == "text":
            if isinstance(value, list):
                return str(value[0]) if value else ""
            return str(value) if not isinstance(value, str) else value

        if type_tag == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, list):
                return bool(value[0]) if value else False
            return bool(value)

        if type_tag == "number":
            if isinstance(value, (int, float)):
                return value
            if isinstance(value, list):
                try:
                    return float(value[0]) if value else 0.0
                except (ValueError, TypeError):
                    return 0.0
            if isinstance(value, bool):
                return int(value)
            try:
                return float(value)
            except (ValueError, TypeError):
                return 0.0

        # 未知类型保留原值
        return value

    @classmethod
    def _sanitize_args(cls, args: list[Any]) -> list[Any]:
        """清洗全部新版 WebUI 参数，确保每个元素的类型都正确。"""
        for i, tag in enumerate(cls._ARG_TYPE_MAP):
            if i < len(args):
                args[i] = cls._sanitize_arg(args[i], i, tag)
        return args

    def _build_gradio_client_args_full(
        self,
        params: dict[str, Any],
        api_info: dict[str, Any] | None = None,
        predict_api: str | None = None,
        *,
        emotion_mode_labels: list[str] | None = None,
    ) -> list[Any]:
        """构建完整 26 参数列表（gradio_client 格式，含 handle_file）

        前 15 个从用户参数构建，后 11 个使用新版 WebUI 默认值。
        使用 _sanitize_args 确保所有参数类型正确。
        """
        known = self._build_gradio_client_args(
            params, emotion_mode_labels=emotion_mode_labels
        )  # 15 items
        known_len = len(known)
        tail = list(self._TAIL_DEFAULTS[: max(0, 26 - known_len)])
        # 时长系数位于尾部默认值中，但必须优先使用详情配置的显式值。
        if tail:
            tail[2] = params.get("duration_factor", 1.0)
        args = list(known) + tail

        _logger.debug("全量参数(gradio_client): 已知=%d + 硬编码=%d → 总计=%d",
                       known_len, len(tail), len(args))

        # 清洗所有参数类型，这是关键：防止 list/None 错位
        args = self._sanitize_args(args)

        _logger.debug("sanitize 后 [0]=%s (type=%s), [1]=%s, [17]=%s (type=%s)",
                       repr(args[0])[:60], type(args[0]).__name__,
                       type(args[1]).__name__,
                       repr(args[17])[:40], type(args[17]).__name__)

        return args

    def _build_http_26_args(
        self,
        params: dict[str, Any],
        *,
        emotion_mode_labels: list[str] | None = None,
    ) -> list[Any]:
        """构建完整 26 参数列表（HTTP JSON 格式，audio 用 {"path":"..."}）

        前 15 个从用户参数构建，后 11 个使用新版 WebUI 默认值。
        """
        known = self._build_payload_for_gradio5(
            params, emotion_mode_labels=emotion_mode_labels
        )  # 15 items
        known_len = len(known)
        tail = list(self._TAIL_DEFAULTS[: max(0, 26 - known_len)])
        if tail:
            tail[2] = params.get("duration_factor", 1.0)
        data = list(known) + tail

        _logger.debug("HTTP 全量参数: 已知=%d + 硬编码=%d → 总计=%d",
                       known_len, len(tail), len(data))

        # 清洗所有参数类型
        data = self._sanitize_args(data)

        return data

    # ------------------------------------------------------------------
    # 策略 2: 原始 HTTP API（备选 — 仅本地 Gradio 服务器有效）
    # ------------------------------------------------------------------
    async def _generate_via_http(
        self,
        base_url: str,
        params: dict[str, Any],
        *,
        timeout: float = 360.0,
    ) -> bytes:
        """通过原始 HTTP API 调用（兼容没有 gradio_client 的环境）"""
        import json as _json

        # 先获取 API 配置信息
        try:
            api_config = await self._get_api_config(base_url)
        except Exception as e:
            raise EngineException(f"获取 API 配置失败: {e}")

        # 判断 Gradio 版本并选择端点
        gradio_version = str(api_config.get("version", "3") or "3")
        try:
            major_version = int(gradio_version.split(".", 1)[0])
        except ValueError:
            _logger.warning("无法解析 Gradio 版本 %r，按 3.x 兼容模式处理", gradio_version)
            major_version = 3

        if major_version >= 5:
            return await self._generate_http_gradio5(
                base_url, api_config, params, timeout=timeout
            )
        else:
            return await self._generate_http_gradio3(
                base_url, api_config, params, timeout=timeout
            )

    async def _get_api_config(self, base_url: str) -> dict[str, Any]:
        """获取 Gradio API 配置（兼容多版本）"""
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            # 先尝试 Gradio 5.x /config
            _logger.debug("尝试获取 /config...")
            resp = await client.get(f"{base_url}/config")
            if resp.status_code == 200:
                config = resp.json()
                _logger.info("检测到 Gradio %s (via /config)", config.get("version", "unknown"))
                return config

            # 回退到 Gradio 3.x/4.x /info
            _logger.debug("回退到 /info...")
            resp = await client.get(f"{base_url}/info")
            if resp.status_code == 200:
                info = resp.json()
                # 统一标记版本号
                info["version"] = info.get("version", "3")
                _logger.info("检测到 Gradio %s (via /info)", info["version"])
                return info

        raise EngineException("无法获取 Gradio API 配置")

    # --- Gradio 5.x HTTP ---
    async def _generate_http_gradio5(
        self,
        base_url: str,
        config: dict[str, Any],
        params: dict[str, Any],
        *,
        timeout: float = 360.0,
    ) -> bytes:
        """Gradio 5.x 的 HTTP API 调用（使用 /run/ 端点 + SSE 协议）"""
        api_prefix = str(config.get("api_prefix", "/gradio_api") or "/gradio_api")
        if not api_prefix.startswith("/"):
            api_prefix = "/" + api_prefix

        # 从 config 中找到生成函数的 api_name
        api_name, fn_index = self._find_predict_endpoint_gradio5(config)
        _logger.info("HTTP (Gradio 5): api_name=%s, fn_index=%s, prefix=%s",
                       api_name, fn_index, api_prefix)

        # 构造新版 WebUI 的完整 26 参数（硬编码 + sanitize）
        data = self._build_http_26_args(
            params,
            emotion_mode_labels=self._extract_radio_choices(config),
        )
        _logger.info("HTTP payload: %d 个参数 (全部硬编码)", len(data))
        _logger.debug("data[0]=%s, data[1]=%s, data[2]=%s...%s",
                       repr(data[0])[:40], type(data[1]).__name__,
                       repr(data[2])[:30], "...")

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            # Gradio 5.x 使用 /gradio_api/run/{api_name} 端点 (SSE 协议)
            session_hash = uuid.uuid4().hex[:16]
            url = f"{base_url}{api_prefix}/run/{api_name}"
            payload = {
                "data": data,
                "event_data": None,
                "fn_index": fn_index,
                "session_hash": session_hash,
            }
            _logger.debug("POST %s (session=%s)", url, session_hash)
            resp = await client.post(url, json=payload)

            if resp.status_code != 200:
                _logger.error("Gradio 5 返回 %d: %s", resp.status_code, resp.text[:500])
                raise EngineException(
                    f"Gradio 5 API 返回错误: {resp.status_code} - {resp.text[:300]}"
                )

            # Gradio 5.x /run/ 可能返回 SSE (text/event-stream) 或 JSON
            content_type = resp.headers.get("content-type", "")
            if "text/event-stream" in content_type:
                result = self._parse_sse_response(resp.text)
            else:
                result = resp.json()
            _logger.debug("Gradio 5 响应: %s", str(result)[:500])
            return self._extract_audio_from_gradio5_response(
                result, download_timeout=timeout
            )

    def _find_predict_endpoint_gradio5(
        self, config: dict[str, Any]
    ) -> tuple[str, int]:
        """从 Gradio 5.x config 中定位预测端点"""
        dependencies = config.get("dependencies", [])
        # 优先找 api_name 含 "gen" 或 "predict" 的端点
        for dep in dependencies:
            api_name = dep.get("api_name", "")
            fn_index = dep.get("id", 0)
            triggers = dep.get("triggers", "")
            if api_name in ("gen_single", "predict", "synthesize") or \
               "gen" in api_name or "predict" in api_name or \
               "synthesize" in api_name:
                return api_name, fn_index

        # 回退：使用最后一个依赖（通常是主按钮）
        if dependencies:
            last = dependencies[-1]
            return last.get("api_name", "/predict"), last.get("id", 0)

        return "/predict", 0

    def _build_payload_for_gradio5(
        self,
        params: dict[str, Any],
        *,
        emotion_mode_labels: list[str] | None = None,
    ) -> list[Any]:
        """构建新版 Gradio HTTP API 参数列表（前 15 个已知参数）

        参数顺序必须与 Gradio UI 组件布局一致：
         1. emotion_mode (Radio)           — 显示标签
         2. reference_audio (Audio)        — {"path": "..."} / None
         3. text (TextArea)                — 目标文案
         4. language (Dropdown)            — lang_choice
         5. emotion_audio (Audio)          — {"path": "..."} / None
         6-14. emotion sliders ×9          — float / 0.0
        15. emotion_description (TextArea) — emo_text
        """
        mode = params.get("emotion_mode", "same_as_ref")

        # 将内部值映射为 Gradio Radio 的显示标签
        emotion_label = self._map_emotion_mode_to_label(
            mode, emotion_mode_labels=emotion_mode_labels
        )

        _logger.debug("构建 HTTP payload: emotion_mode=%s → label=%s", mode, emotion_label)

        data: list[Any] = [emotion_label]

        # 2. 参考音频 (Gradio 5.x Pydantic 要求 meta 字段)
        ref_path = params.get("reference_audio", "")
        if ref_path:
            data.append({"path": ref_path, "meta": {"_type": "gradio.FileData"}})
            _logger.debug("HTTP 参考音频: %s", ref_path)
        else:
            data.append(None)

        # 3. 目标文案
        text = params.get("text", "")
        data.append(text)

        # 4. 语言
        data.append(str(params.get("language", "ZH")).upper())

        # 5. 情感参考音频
        if mode == "emotion_ref_audio":
            emo_path = params.get("emotion_audio", "")
            data.append({"path": emo_path, "meta": {"_type": "gradio.FileData"}} if emo_path else None)
        else:
            data.append(None)

        # 6-14. 情感向量 — 9 个独立值
        # 顺序必须匹配 API: emo_weight, happy, angry, sad, afraid, disgusted, melancholic, surprised, calm
        emotion_keys = [
            "emotion_control_weight",
            "happy", "angry", "sad",
            "afraid", "disgusted", "melancholic", "surprised", "calm",
        ]
        for key in emotion_keys:
            if mode == "emotion_vector":
                val = float(params.get(key, 0.0))
            elif key == "emotion_control_weight" and mode == "emotion_ref_audio":
                val = float(params.get(key, 0.65))
            elif key == "emotion_control_weight" and mode == "same_as_ref":
                val = 1.0
            else:
                val = 0.0
            data.append(val)

        # 15. 情感描述文本 (emo_text)
        emo_text = params.get("emotion_description", "") if mode == "emotion_vector" else ""
        data.append(emo_text)

        _logger.info("HTTP payload: %d 个已知参数 (emotion_mode=%s)", len(data), mode)
        return data  # 15 items

    @staticmethod
    def _get_component_defaults_from_config(config: dict[str, Any]) -> list[Any]:
        """从 Gradio /config 或 /info 响应中提取**输入组件**的默认值

        过滤掉 layout 组件（html/markdown/button/row/column/group/tabs/
        accordion/form/dataset/dataframe），仅返回实际输入组件的默认值。
        这样才能保证默认值数量与 gen_single 的 inputs 列表一致（26个）。
        """
        _INPUT_TYPES = {"radio", "checkbox", "slider", "number",
                         "textbox", "textarea", "audio", "dropdown", "image"}

        components = config.get("components", [])
        defaults: list[Any] = []
        for comp in components:
            if comp.get("type", "").lower() in _INPUT_TYPES:
                props = comp.get("props", {})
                defaults.append(props.get("value"))
        # 如果过滤后仍多于 26，截断到前 26（匹配 gen_single 的 input 数量）
        if len(defaults) > 26:
            defaults = defaults[:26]
        _logger.debug("提取输入组件默认值: %d 个 (从 %d 个总组件中)", len(defaults), len(components))
        return defaults

    def _extract_audio_from_gradio5_response(
        self,
        result: dict[str, Any],
        *,
        download_timeout: float = 30.0,
    ) -> bytes:
        """从 Gradio 5 响应中提取音频"""
        data = result.get("data", [])
        return self._extract_audio_from_result(
            data if data else result, download_timeout=download_timeout
        )

    @staticmethod
    def _parse_sse_response(text: str) -> dict[str, Any]:
        """解析 Gradio 5.x SSE (Server-Sent Events) 响应

        SSE 格式:
            event: data
            data: {"msg":"process_starts"}
            ...
            data: {"msg":"process_completed","output":{"data":[{...}]}}

        返回最后一个 process_completed 事件的 output。
        """
        import json as _json

        last_output: dict[str, Any] = {}
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                json_str = line[5:].strip()
                if json_str:
                    try:
                        event = _json.loads(json_str)
                        if event.get("msg") == "process_completed":
                            last_output = event.get("output", event)
                    except _json.JSONDecodeError:
                        pass
        _logger.debug("SSE 最终输出: %s", str(last_output)[:300])
        return last_output

    # --- Gradio 3.x/4.x HTTP ---
    async def _generate_http_gradio3(
        self,
        base_url: str,
        config: dict[str, Any],
        params: dict[str, Any],
        *,
        timeout: float = 360.0,
    ) -> bytes:
        """Gradio 3.x / 4.x 的 HTTP API 调用"""
        data = self._build_http_26_args(
            params,
            emotion_mode_labels=self._extract_radio_choices(config),
        )
        _logger.info("HTTP (Gradio 3): %d 个参数", len(data))

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            url = f"{base_url}/api/predict"
            payload = {"data": data, "session_hash": None}
            _logger.debug("POST %s", url)
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                _logger.error("Gradio 3 返回 %d: %s", resp.status_code, resp.text[:500])
                raise EngineException(
                    f"Gradio API 返回错误: {resp.status_code} - {resp.text[:200]}"
                )

            result = resp.json()
            audio_data = self._extract_audio_from_result(
                result.get("data", []), download_timeout=timeout
            )

            if not audio_data:
                _logger.error("API 未返回音频数据: %s", str(result)[:500])
                raise EngineException("API 未返回音频数据")

            return audio_data

    # ------------------------------------------------------------------
    # 音频数据提取（公有方法，被两种策略共用）
    # ------------------------------------------------------------------
    def _extract_audio_from_result(
        self, result: Any, *, download_timeout: float = 30.0
    ) -> bytes:
        """从 Gradio 返回结果中提取音频二进制数据

        支持多种返回格式：
        - 文件路径字符串（本地）
        - base64 编码字符串
        - {"name": "xxx", "data": "base64..."} 字典
        - {"path": "...", "url": "...", "meta": {...}} FileData 字典
        - {"value": "..."} Gradio API 响应
        - 包含文件路径的嵌套列表
        - gradio_client 返回的 file-like 对象
        """
        import urllib.request as _urllib_request

        def _try_read_file(path_str: str) -> bytes | None:
            """尝试从本地路径读取音频文件"""
            p = Path(path_str)
            if p.exists() and p.suffix.lower() in (".wav", ".mp3", ".ogg", ".flac"):
                try:
                    return p.read_bytes()
                except Exception:
                    pass
            return None

        def _try_download(url: str) -> bytes | None:
            """尝试从 URL 下载音频文件到临时目录并读取"""
            try:
                with _urllib_request.urlopen(url, timeout=download_timeout) as resp:
                    if resp.status == 200:
                        data = resp.read()
                        content_type = resp.headers.get_content_type()
                        has_audio_header = (
                            data[:4] in (b"RIFF", b"OggS", b"fLaC")
                            or data[:3] == b"ID3"
                            or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
                        )
                        if content_type.startswith("audio/") or has_audio_header:
                            return data
            except Exception:
                pass
            return None

        def _extract_from_dict(d: dict[str, Any]) -> bytes | None:
            """从任意 dict 中尝试提取音频数据"""
            # 1. 直接 base64 数据
            for key in ("data", "value", "audio"):
                val = d.get(key, "")
                if isinstance(val, str) and val:
                    # 先检查 base64
                    try:
                        decoded = base64.b64decode(val)
                        if len(decoded) > 100:
                            _logger.debug("从 dict[%s] base64 提取 %d bytes", key, len(decoded))
                            return decoded
                    except Exception:
                        pass
                    # 再检查文件路径
                    local = _try_read_file(val)
                    if local:
                        _logger.debug("从 dict[%s] 路径读取 %d bytes", key, len(local))
                        return local

            # 2. path + url 组合（gradio_client FileData 格式）
            for path_key in ("path", "file", "filepath"):
                filepath = d.get(path_key, "")
                if filepath:
                    local = _try_read_file(filepath)
                    if local:
                        return local
                    # 如果有 url 可以下载
                    url = d.get("url", "")
                    if url:
                        downloaded = _try_download(url)
                        if downloaded:
                            _logger.debug("从 dict url 下载 %d bytes", len(downloaded))
                            return downloaded

            # 3. name + data 格式
            if "name" in d and "data" in d:
                b64 = d["data"]
                if isinstance(b64, str) and b64:
                    try:
                        return base64.b64decode(b64)
                    except Exception:
                        pass
                if isinstance(b64, bytes):
                    return b64

            # 4. 嵌套 data 列表
            inner_data = d.get("data", None)
            if isinstance(inner_data, list) and inner_data:
                for item in inner_data:
                    if isinstance(item, dict):
                        audio = _extract_from_dict(item)
                        if audio:
                            return audio
                    if isinstance(item, str):
                        local = _try_read_file(item)
                        if local:
                            return local
            return None

        # 如果 result 是列表，递归提取
        if isinstance(result, (list, tuple)):
            for item in result:
                if item is None:
                    continue
                audio = self._extract_audio_from_result(
                    item, download_timeout=download_timeout
                )
                if audio:
                    return audio
            return None

        # 字典格式
        if isinstance(result, dict):
            return _extract_from_dict(result)

        # 文件路径字符串
        if isinstance(result, str):
            local = _try_read_file(result)
            if local:
                return local
            if result.startswith(("http://", "https://")):
                downloaded = _try_download(result)
                if downloaded:
                    return downloaded
            # 尝试 base64
            try:
                decoded = base64.b64decode(result)
                if len(decoded) > 100:
                    return decoded
            except Exception:
                pass

        # gradio_client File 对象
        if hasattr(result, "read"):
            try:
                return result.read()
            except Exception:
                pass
        if hasattr(result, "path"):
            path = Path(getattr(result, "path", ""))
            if path.exists():
                return path.read_bytes()

        # 二进制数据
        if isinstance(result, (bytes, bytearray)):
            return bytes(result)

        return None
