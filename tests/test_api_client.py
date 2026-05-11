from __future__ import annotations

from typing import Any

import requests

from indextts_batch_gui.api_client import IndexTTSClient
from indextts_batch_gui.models import AppConfig, TaskRecord


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any | None = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_is_webui_generating_true_when_active_jobs(monkeypatch) -> None:
    def fake_get(_url: str, timeout: int):
        return _FakeResponse(200, {"active_jobs": 1})

    monkeypatch.setattr(requests, "get", fake_get)
    client = IndexTTSClient(AppConfig())

    assert client.is_webui_generating() is True


def test_is_webui_generating_false_when_zero_jobs(monkeypatch) -> None:
    def fake_get(_url: str, timeout: int):
        return _FakeResponse(200, {"active_jobs": 0})

    monkeypatch.setattr(requests, "get", fake_get)
    client = IndexTTSClient(AppConfig())

    assert client.is_webui_generating() is False


def test_is_webui_generating_none_when_status_unsupported(monkeypatch) -> None:
    def fake_get(_url: str, timeout: int):
        return _FakeResponse(404, None)

    monkeypatch.setattr(requests, "get", fake_get)
    client = IndexTTSClient(AppConfig())

    assert client.is_webui_generating() is None


def test_emo_control_method_maps_zh_to_remote_choice() -> None:
    param = {
        "parameter_name": "emo_control_method",
        "choices": [
            "Same as the voice reference",
            "Use emotion reference audio",
            "Use emotion vectors",
        ],
    }

    value = IndexTTSClient._normalize_emo_control_method_value("使用情感向量控制", param, None)
    assert value == "Use emotion vectors"


def test_emo_control_method_matches_choice_robustly() -> None:
    param = {
        "parameter_name": "emo_control_method",
        "type": {
            "enum": [
                "Same as the voice reference",
                "Use emotion reference audio",
                "Use emotion vectors",
            ]
        },
    }

    value = IndexTTSClient._normalize_emo_control_method_value("use_emotion_vectors", param, None)
    assert value == "Use emotion vectors"


def test_param_alias_prefers_canonical_config_over_stale_param_value() -> None:
    client = IndexTTSClient(AppConfig())
    task = TaskRecord(
        task_id="t1",
        text="hello",
        reference_audio="ref.wav",
        config={
            "param_17": 0.2,
            "top_p": 0.95,
        },
    )
    param = {"parameter_name": "param_17", "parameter_default": 0.8}

    value = client._value_for_gradio_param(param, task)
    assert value == 0.95


def test_vec_alias_supports_vec_underscore_format() -> None:
    client = IndexTTSClient(AppConfig())
    task = TaskRecord(
        task_id="t2",
        text="hello",
        reference_audio="ref.wav",
        config={"emotion_vector": [0.11, 0.22, 0.33]},
    )
    param = {"parameter_name": "vec_2", "parameter_default": 0.0}

    value = client._value_for_gradio_param(param, task)
    assert value == 0.22


def test_vec_alias_prefers_explicit_vec_key() -> None:
    client = IndexTTSClient(AppConfig())
    task = TaskRecord(
        task_id="t3",
        text="hello",
        reference_audio="ref.wav",
        config={
            "vec1": 0.91,
            "emotion_vector": [0.11, 0.22, 0.33],
        },
    )
    param = {"parameter_name": "vec1", "parameter_default": 0.0}

    value = client._value_for_gradio_param(param, task)
    assert value == 0.91
