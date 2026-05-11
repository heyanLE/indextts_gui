from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

import requests

from .models import AppConfig, SynthesisResult, TaskRecord


logger = logging.getLogger(__name__)


class IndexTTSClientError(RuntimeError):
    pass


@dataclass
class IndexTTSClient:
    config: AppConfig
    _cached_gradio_endpoint: str | None = None
    _cached_gradio_params: list[dict[str, Any]] | None = None
    _cached_base_url: str | None = None
    _uploaded_file_cache: dict[str, str] = field(default_factory=dict)

    def synthesize(self, task: TaskRecord) -> SynthesisResult:
        # Prefer real Gradio endpoint when available, then fall back to legacy API.
        logger.info("Start synthesize task_id=%s text_len=%d", task.task_id, len(task.text or ""))
        gradio_error: Exception | None = None
        try:
            return self._synthesize_via_gradio(task)
        except Exception as exc:
            logger.warning("Gradio synthesize failed task_id=%s err=%s", task.task_id, exc)
            gradio_error = exc

        try:
            return self._synthesize_via_legacy_endpoint(task)
        except Exception as legacy_exc:
            logger.exception("Legacy synthesize failed task_id=%s", task.task_id)
            if gradio_error is not None:
                raise IndexTTSClientError(
                    f"Gradio API 调用失败: {gradio_error}; 传统 /generate 调用失败: {legacy_exc}"
                ) from legacy_exc
            raise IndexTTSClientError(str(legacy_exc)) from legacy_exc

    def is_webui_generating(self) -> bool | None:
        """Best-effort runtime probe for whether WebUI is actively generating.

        Returns True/False when a supported status payload can be interpreted.
        Returns None when status cannot be determined safely.
        """
        endpoints = (
            "/gradio_api/queue/status",
            "/queue/status",
            "/gradio_api/monitoring",
            "/monitoring",
        )
        probe_timeout = max(2, min(int(self.config.request_timeout_sec), 10))
        had_non_404_response = False

        for endpoint in endpoints:
            url = f"{self.config.base_url}{endpoint}"
            try:
                response = requests.get(url, timeout=probe_timeout)
            except requests.RequestException:
                continue

            if response.status_code in {404, 405}:
                continue

            had_non_404_response = True
            try:
                payload = response.json()
            except ValueError:
                continue

            parsed = self._parse_webui_generating_state(payload)
            if parsed is not None:
                return parsed

        if had_non_404_response:
            return False
        return None

    @classmethod
    def _parse_webui_generating_state(cls, payload: Any) -> bool | None:
        if isinstance(payload, bool):
            return payload

        if isinstance(payload, (int, float)):
            return payload > 0

        if isinstance(payload, str):
            normalized = payload.strip().lower()
            if normalized in {"running", "busy", "generating", "processing"}:
                return True
            if normalized in {"idle", "ready", "stopped", "done", "completed"}:
                return False
            return None

        if isinstance(payload, list):
            known = [cls._parse_webui_generating_state(item) for item in payload]
            known = [item for item in known if item is not None]
            if not known:
                return None
            return any(known)

        if not isinstance(payload, dict):
            return None

        for key in ("is_generating", "generating", "is_running", "running", "busy"):
            if key in payload:
                return bool(payload.get(key))

        for key in (
            "active_jobs",
            "active",
            "running_jobs",
            "processing_jobs",
            "jobs_running",
            "current_jobs",
            "queue_size",
            "pending_jobs",
        ):
            if key in payload:
                try:
                    return float(payload.get(key) or 0) > 0
                except (TypeError, ValueError):
                    pass

        for key in ("status", "queue", "data", "state"):
            if key in payload:
                nested = cls._parse_webui_generating_state(payload.get(key))
                if nested is not None:
                    return nested

        return None

    def _synthesize_via_legacy_endpoint(self, task: TaskRecord) -> SynthesisResult:
        endpoint = f"{self.config.base_url}/generate"
        logger.info("Call legacy endpoint=%s task_id=%s", endpoint, task.task_id)
        payload = {
            "text": task.text,
            "reference_audio": task.reference_audio,
            "config": task.config,
        }
        response = requests.post(endpoint, json=payload, timeout=self.config.request_timeout_sec)
        response.raise_for_status()
        parsed = self._parse_synthesis_response(response)
        if not parsed.request_config:
            parsed.request_config = dict(task.config or {})
        if not parsed.backend:
            parsed.backend = "legacy"
        return parsed

    def _synthesize_via_gradio(self, task: TaskRecord) -> SynthesisResult:
        api_name, params = self._resolve_gradio_generation_endpoint()
        payload_data, request_config = self._build_gradio_payload(task, params)
        payload = {"data": payload_data}
        run_endpoint = f"{self.config.base_url}/gradio_api/run/{api_name}"
        call_endpoint = f"{self.config.base_url}/gradio_api/call/{api_name}"
        logger.info("Call gradio endpoint=%s task_id=%s param_count=%d", run_endpoint, task.task_id, len(payload_data))

        # /run blocks until generation completes; for long texts/audio keep a generous timeout.
        run_timeout = max(int(self.config.request_timeout_sec), 1800)
        try:
            response = requests.post(run_endpoint, json=payload, timeout=run_timeout)
            response.raise_for_status()
            return self._parse_gradio_response(response, api_name, task, request_config)
        except requests.RequestException as exc:
            details = self._extract_http_error_details(exc)
            status = getattr(getattr(exc, "response", None), "status_code", None)
            # Only fallback for deployments that do not support /run.
            if status not in {404, 405}:
                raise IndexTTSClientError(f"/run 调用失败: {details}") from exc
            logger.warning(
                "Gradio /run unsupported endpoint=%s task_id=%s status=%s; fallback to /call",
                run_endpoint,
                task.task_id,
                status,
            )

        logger.info("Call gradio queued endpoint=%s task_id=%s", call_endpoint, task.task_id)
        try:
            result = self._synthesize_via_gradio_call(api_name, payload, task.task_id)
        except Exception as call_exc:
            raise IndexTTSClientError(f"/call 调用失败: {call_exc}") from call_exc

        outputs = result.get("data")
        if not isinstance(outputs, list) or not outputs:
            raise IndexTTSClientError("Gradio /call 返回缺少输出数据")

        parsed = self._parse_gradio_audio_output(outputs[0], api_name)
        parsed.request_config = request_config
        return parsed

    def _parse_gradio_response(
        self,
        response: requests.Response,
        api_name: str,
        task: TaskRecord,
        request_config: dict[str, Any],
    ) -> SynthesisResult:
        # Some deployments may still return direct audio bytes.
        ct = response.headers.get("Content-Type", "")
        if ct.startswith("audio/"):
            return SynthesisResult(
                audio_bytes=response.content,
                content_type=ct,
                request_config=request_config,
                backend=f"gradio:{api_name}",
            )

        try:
            data = response.json()
        except ValueError as exc:
            logger.error("Gradio non-JSON response task_id=%s content_type=%s", task.task_id, ct)
            raise IndexTTSClientError("Gradio 返回了非 JSON 响应") from exc

        outputs = data.get("data")
        if not isinstance(outputs, list) or not outputs:
            if "error" in data:
                raise IndexTTSClientError(f"Gradio 返回错误: {data.get('error')}")
            raise IndexTTSClientError("Gradio 响应缺少输出数据")

        parsed = self._parse_gradio_audio_output(outputs[0], api_name)
        parsed.request_config = request_config
        return parsed

    def _parse_gradio_audio_output(self, audio_obj: Any, api_name: str) -> SynthesisResult:
        if isinstance(audio_obj, dict) and isinstance(audio_obj.get("value"), dict):
            nested = audio_obj.get("value")
            if isinstance(nested, dict):
                audio_obj = nested

        if isinstance(audio_obj, dict):
            url = audio_obj.get("url")
            path = audio_obj.get("path")
            if isinstance(url, str) and url.strip():
                result = self._download_audio(url)
                result.backend = f"gradio:{api_name}"
                return result
            if isinstance(path, str) and path.strip():
                result = self._read_or_fetch_gradio_path(path)
                result.backend = f"gradio:{api_name}"
                return result

        if isinstance(audio_obj, str) and audio_obj.strip():
            result = self._read_or_fetch_gradio_path(audio_obj)
            result.backend = f"gradio:{api_name}"
            return result

        raise IndexTTSClientError("无法从 Gradio 响应中解析生成音频")

    def _synthesize_via_gradio_call(self, api_name: str, payload: dict[str, Any], task_id: str) -> dict[str, Any]:
        start_endpoint = f"{self.config.base_url}/gradio_api/call/{api_name}"
        try:
            start_resp = requests.post(start_endpoint, json=payload, timeout=self.config.request_timeout_sec)
            start_resp.raise_for_status()
        except requests.RequestException as exc:
            details = self._extract_http_error_details(exc)
            logger.exception("Gradio /call start failed endpoint=%s task_id=%s", start_endpoint, task_id)
            raise IndexTTSClientError(details) from exc

        try:
            start_data = start_resp.json()
        except ValueError as exc:
            raise IndexTTSClientError("Gradio /call 启动返回了非 JSON 响应") from exc

        event_id = str(start_data.get("event_id", "")).strip()
        if not event_id:
            raise IndexTTSClientError("Gradio /call 启动响应缺少 event_id")

        poll_endpoint = f"{self.config.base_url}/gradio_api/call/{api_name}/{event_id}"
        try:
            poll_resp = requests.get(poll_endpoint, timeout=self.config.request_timeout_sec)
            poll_resp.raise_for_status()
        except requests.RequestException as exc:
            details = self._extract_http_error_details(exc)
            logger.exception("Gradio /call poll failed endpoint=%s task_id=%s", poll_endpoint, task_id)
            raise IndexTTSClientError(details) from exc

        event_name, event_payload = self._parse_gradio_sse_terminal_event(poll_resp.text)
        if event_name == "complete":
            if isinstance(event_payload, dict):
                return event_payload
            raise IndexTTSClientError("Gradio /call complete 事件数据格式无效")

        if event_name == "error":
            detail = "null"
            if isinstance(event_payload, dict):
                detail = str(event_payload.get("error") or event_payload)
            elif event_payload is not None:
                detail = str(event_payload)
            raise IndexTTSClientError(f"Gradio 后端返回 error 事件: {detail}")

        raise IndexTTSClientError("Gradio /call 未返回 complete/error 终态事件")

    @staticmethod
    def _parse_gradio_sse_terminal_event(text: str) -> tuple[str | None, Any]:
        current_event: str | None = None
        current_data_lines: list[str] = []
        terminal_event: str | None = None
        terminal_payload: Any = None

        def flush() -> None:
            nonlocal current_event, current_data_lines, terminal_event, terminal_payload
            if not current_event:
                current_data_lines = []
                return
            raw = "\n".join(current_data_lines).strip()
            payload: Any = raw
            if raw == "null":
                payload = None
            elif raw:
                try:
                    payload = json.loads(raw)
                except ValueError:
                    payload = raw
            if current_event in {"complete", "error"}:
                terminal_event = current_event
                terminal_payload = payload
            current_event = None
            current_data_lines = []

        for line in text.splitlines():
            if line.startswith("event:"):
                flush()
                current_event = line.split(":", 1)[1].strip()
                continue
            if line.startswith("data:"):
                current_data_lines.append(line.split(":", 1)[1].strip())
                continue
            if not line.strip():
                flush()

        flush()
        return terminal_event, terminal_payload

    @staticmethod
    def _extract_http_error_details(exc: requests.RequestException) -> str:
        response = getattr(exc, "response", None)
        if response is None:
            return str(exc)

        detail = ""
        try:
            body = response.text.strip()
            if body:
                detail = body[:500]
        except Exception:
            detail = ""

        status_part = f"HTTP {response.status_code}"
        if detail:
            return f"{status_part}: {detail}"
        return f"{status_part}: {exc}"

    def _resolve_gradio_generation_endpoint(self) -> tuple[str, list[dict[str, Any]]]:
        if self._cached_base_url != self.config.base_url:
            self._cached_gradio_endpoint = None
            self._cached_gradio_params = None
            self._cached_base_url = self.config.base_url
            self._uploaded_file_cache.clear()

        if self._cached_gradio_endpoint and self._cached_gradio_params is not None:
            return self._cached_gradio_endpoint, self._cached_gradio_params

        info_url = f"{self.config.base_url}/gradio_api/info"
        logger.info("Fetch gradio info url=%s", info_url)
        response = requests.get(info_url, timeout=self.config.request_timeout_sec)
        response.raise_for_status()
        info = response.json()

        named = info.get("named_endpoints")
        if not isinstance(named, dict) or not named:
            raise IndexTTSClientError("Gradio API 信息中没有 named_endpoints")

        preferred_name = None
        preferred_payload: dict[str, Any] | None = None
        for endpoint_name, endpoint_payload in named.items():
            if not isinstance(endpoint_payload, dict):
                continue
            returns = endpoint_payload.get("returns")
            has_audio_return = isinstance(returns, list) and any(
                isinstance(item, dict) and str(item.get("component", "")).lower() == "audio"
                for item in returns
            )
            if not has_audio_return:
                continue

            normalized = endpoint_name.strip("/").lower()
            if normalized == "gen_single":
                preferred_name = endpoint_name
                preferred_payload = endpoint_payload
                break
            if preferred_name is None:
                preferred_name = endpoint_name
                preferred_payload = endpoint_payload

        if preferred_name is None or preferred_payload is None:
            raise IndexTTSClientError("未找到可返回音频的 Gradio 端点")

        params = preferred_payload.get("parameters")
        if not isinstance(params, list):
            raise IndexTTSClientError("选中的 Gradio 端点参数定义无效")

        api_name = preferred_name.strip("/")
        logger.info("Resolved gradio endpoint api_name=%s param_count=%d", api_name, len(params))
        self._cached_gradio_endpoint = api_name
        self._cached_gradio_params = params
        return api_name, params

    def _build_gradio_payload(self, task: TaskRecord, params: list[dict[str, Any]]) -> tuple[list[Any], dict[str, Any]]:
        values: list[Any] = []
        resolved_config: dict[str, Any] = {}
        for index, param in enumerate(params):
            value = self._value_for_gradio_param(param, task)
            values.append(value)
            key = str(param.get("parameter_name", "")).strip() or str(param.get("label", "")).strip() or f"param_{index}"
            resolved_config[key] = value
        return values, resolved_config

    def _value_for_gradio_param(self, param: dict[str, Any], task: TaskRecord) -> Any:
        name = str(param.get("parameter_name", "")).strip()
        param_type = param.get("type")
        default = param.get("parameter_default")
        cfg = task.config or {}

        if self._is_file_param(param_type):
            if name in {"prompt", "reference_audio", "ref_audio", "prompt_audio"}:
                return self._to_gradio_file_data(task.reference_audio)
            if name in {"emo_ref_path", "emotion_ref", "emo_audio"}:
                emo_ref = cfg.get(name)
                if not emo_ref:
                    for alias in ("emotion_ref_audio", "emo_ref_path", "emotion_ref", "emo_audio"):
                        candidate = cfg.get(alias)
                        if isinstance(candidate, str) and candidate.strip():
                            emo_ref = candidate
                            break
                if isinstance(emo_ref, str) and emo_ref.strip():
                    return self._to_gradio_file_data(emo_ref)
                return None
            return None

        if name == "text":
            return task.text

        if name == "emo_control_method":
            raw_value = cfg.get(name, default)
            return self._normalize_emo_control_method_value(raw_value, param, default)

        # Backward compatibility: old task JSON may persist param_16..param_23.
        # Prefer canonical edited keys (top_p, temperature, etc.) when available.
        if name.startswith("param_"):
            alias_value = self._lookup_alias_value(name=name, label="", cfg=cfg)
            if alias_value is not None:
                return alias_value

        if name in cfg:
            return cfg[name]

        label = str(param.get("label", "")).strip()
        if label and label in cfg:
            return cfg[label]
        if label:
            label_key = label.lower()
            if label_key in cfg:
                return cfg[label_key]

        # Common aliases from generation configs.
        alias_value = self._lookup_alias_value(name=name, label=label, cfg=cfg)
        if alias_value is not None:
            return alias_value

        return default

    @staticmethod
    def _lookup_alias_value(name: str, label: str, cfg: dict[str, Any]) -> Any:
        vec_index = IndexTTSClient._extract_vector_index(name)
        if vec_index is None:
            vec_index = IndexTTSClient._extract_vector_index(label)
        if vec_index is not None:
            # Prefer explicit vec keys when present in task config.
            candidate_keys = (
                f"vec{vec_index}",
                f"vec0{vec_index}",
                f"vec_{vec_index}",
                f"vec-{vec_index}",
            )
            for candidate in candidate_keys:
                if candidate in cfg:
                    try:
                        return float(cfg[candidate])
                    except (TypeError, ValueError):
                        return None

            # Fall back to normalized vector list representation.
            index = vec_index - 1
            vector = cfg.get("emotion_vector", cfg.get("emo_vector"))
            if isinstance(vector, list) and 0 <= index < len(vector):
                try:
                    return float(vector[index])
                except (TypeError, ValueError):
                    return None

        if name == "emo_text" and "custom_prompt" in cfg:
            return cfg.get("custom_prompt")

        alias_map = {
            "param_16": "do_sample",
            "param_17": "top_p",
            "param_18": "top_k",
            "param_19": "temperature",
            "param_20": "length_penalty",
            "param_21": "num_beams",
            "param_22": "repetition_penalty",
            "param_23": "max_mel_tokens",
            "emo_weight": "emo_weight",
            "max_text_tokens_per_segment": "max_text_tokens_per_segment",
        }
        if name in alias_map and alias_map[name] in cfg:
            return cfg[alias_map[name]]

        normalized_label = label.lower().strip()
        for key in (
            "top_p",
            "top_k",
            "temperature",
            "length_penalty",
            "num_beams",
            "repetition_penalty",
            "max_mel_tokens",
            "do_sample",
        ):
            if key in cfg and key in normalized_label:
                return cfg[key]
        return None

    @staticmethod
    def _extract_vector_index(text: str) -> int | None:
        raw = (text or "").strip().lower()
        if not raw:
            return None
        match = re.search(r"vec[\s_\-]*0*([1-9][0-9]*)", raw)
        if not match:
            return None
        try:
            value = int(match.group(1))
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        return value

    @staticmethod
    def _normalize_emo_control_method_value(value: Any, param: dict[str, Any], default: Any) -> Any:
        if not isinstance(value, str) or not value.strip():
            value = default
        if not isinstance(value, str) or not value.strip():
            return value

        text = value.strip()
        mapped = IndexTTSClient._map_emo_method_label(text)
        choices = IndexTTSClient._extract_param_choices(param)

        if not choices:
            return mapped

        for candidate in (mapped, text):
            resolved = IndexTTSClient._match_choice(candidate, choices)
            if resolved is not None:
                return resolved

        return choices[0]

    @staticmethod
    def _extract_param_choices(param: dict[str, Any]) -> list[str]:
        choices: list[str] = []

        raw_choices = param.get("choices")
        if isinstance(raw_choices, list):
            for item in raw_choices:
                if isinstance(item, str) and item.strip():
                    choices.append(item.strip())

        param_type = param.get("type")
        if isinstance(param_type, dict):
            raw_enum = param_type.get("enum")
            if isinstance(raw_enum, list):
                for item in raw_enum:
                    if isinstance(item, str) and item.strip():
                        choices.append(item.strip())

        # Keep order while removing duplicates.
        seen: set[str] = set()
        unique: list[str] = []
        for item in choices:
            if item in seen:
                continue
            seen.add(item)
            unique.append(item)
        return unique

    @staticmethod
    def _match_choice(candidate: str, choices: list[str]) -> str | None:
        target = IndexTTSClient._normalize_choice_text(candidate)
        if not target:
            return None

        for choice in choices:
            if choice == candidate:
                return choice

        for choice in choices:
            if IndexTTSClient._normalize_choice_text(choice) == target:
                return choice
        return None

    @staticmethod
    def _normalize_choice_text(text: str) -> str:
        return "".join(ch for ch in text.lower().strip() if ch not in {" ", "_", "-"})

    @staticmethod
    def _map_emo_method_label(text: str) -> str:
        mapping = {
            "与音色参考音频相同": "Same as the voice reference",
            "使用情感参考音频": "Use emotion reference audio",
            "使用情感向量控制": "Use emotion vectors",
            "same as the voice reference": "Same as the voice reference",
            "use emotion reference audio": "Use emotion reference audio",
            "use emotion vectors": "Use emotion vectors",
        }
        key = text.strip()
        if key in mapping:
            return mapping[key]
        lower_key = key.lower()
        if lower_key in mapping:
            return mapping[lower_key]
        return key

    def _to_gradio_file_data(self, path: str) -> dict[str, Any]:
        raw = (path or "").strip()
        if raw and not raw.startswith(("http://", "https://")):
            file_path = Path(raw)
            if not file_path.exists():
                raise IndexTTSClientError(f"音频文件不存在: {raw}")
            raw = self._upload_file_to_gradio(file_path)
        file_data: dict[str, Any] = {
            "path": raw,
            "meta": {"_type": "gradio.FileData"},
        }
        if raw.startswith("http://") or raw.startswith("https://"):
            file_data["url"] = raw
        return file_data

    def _upload_file_to_gradio(self, file_path: Path) -> str:
        key = self._local_file_cache_key(file_path)
        cached = self._uploaded_file_cache.get(key)
        if cached:
            return cached

        endpoint = f"{self.config.base_url}/gradio_api/upload"
        logger.info("Upload local file to gradio cache endpoint=%s file=%s", endpoint, key)
        try:
            with file_path.open("rb") as fp:
                response = requests.post(
                    endpoint,
                    files={"files": (file_path.name, fp)},
                    timeout=self.config.request_timeout_sec,
                )
            response.raise_for_status()
        except requests.RequestException as exc:
            details = self._extract_http_error_details(exc)
            logger.exception("Gradio upload failed endpoint=%s file=%s", endpoint, key)
            raise IndexTTSClientError(f"上传参考音频到 Gradio 失败: {details}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise IndexTTSClientError("Gradio 上传接口返回了非 JSON 响应") from exc

        uploaded_path = ""
        if isinstance(data, list) and data and isinstance(data[0], str):
            uploaded_path = data[0].strip()
        if not uploaded_path:
            raise IndexTTSClientError(f"Gradio 上传接口返回格式无效: {data}")

        self._uploaded_file_cache[key] = uploaded_path
        return uploaded_path

    @staticmethod
    def _local_file_cache_key(file_path: Path) -> str:
        resolved = file_path.resolve()
        normalized = os.path.normcase(os.path.normpath(str(resolved)))
        try:
            stat = resolved.stat()
            # Re-upload when the same path points to a modified file.
            return f"{normalized}|{stat.st_size}|{stat.st_mtime_ns}"
        except OSError:
            return normalized

    @staticmethod
    def _is_file_param(param_type: Any) -> bool:
        if not isinstance(param_type, dict):
            return False
        title = str(param_type.get("title", "")).lower()
        return title == "filedata" or "path" in param_type.get("properties", {})

    def _read_or_fetch_gradio_path(self, path_or_url: str) -> SynthesisResult:
        local_path = Path(path_or_url)
        if local_path.exists():
            return SynthesisResult(audio_bytes=local_path.read_bytes(), content_type="audio/wav")

        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return self._download_audio(path_or_url)

        endpoint = f"{self.config.base_url}/gradio_api/file={quote(path_or_url, safe='')}"
        response = requests.get(endpoint, timeout=self.config.request_timeout_sec)
        response.raise_for_status()
        ct = response.headers.get("Content-Type", "audio/wav")
        return SynthesisResult(audio_bytes=response.content, content_type=ct)

    def _parse_synthesis_response(self, response: requests.Response) -> SynthesisResult:
        ct = response.headers.get("Content-Type", "")
        if ct.startswith("audio/"):
            return SynthesisResult(audio_bytes=response.content, content_type=ct)

        try:
            data = response.json()
        except ValueError as exc:
            raise IndexTTSClientError("WebUI 返回了不支持的响应格式") from exc

        if "audio_base64" in data:
            try:
                audio = base64.b64decode(data["audio_base64"])
            except (ValueError, TypeError) as exc:
                raise IndexTTSClientError("响应中的 audio_base64 无效") from exc
            return SynthesisResult(audio_bytes=audio, content_type=data.get("content_type", "audio/wav"))

        if "audio_url" in data:
            return self._download_audio(data["audio_url"])

        raise IndexTTSClientError("响应中缺少音频数据")

    def _download_audio(self, url: str) -> SynthesisResult:
        resolved = urljoin(f"{self.config.base_url}/", url)
        logger.info("Download audio resolved_url=%s", resolved)
        try:
            response = requests.get(resolved, timeout=self.config.request_timeout_sec)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.exception("Download audio failed resolved_url=%s", resolved)
            raise IndexTTSClientError(f"下载生成音频失败: {exc}") from exc
        ct = response.headers.get("Content-Type", "audio/wav")
        return SynthesisResult(audio_bytes=response.content, content_type=ct)
