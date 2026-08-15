import pytest

from indextts_batch_gui.models import AppConfig, TaskRecord


def test_app_config_base_url_prefers_direct_url() -> None:
    cfg = AppConfig(webui_url="127.0.0.1:7860", webui_host="localhost", webui_port=9000)
    assert cfg.base_url == "http://127.0.0.1:7860"


def test_app_config_base_url_falls_back_to_host_port() -> None:
    cfg = AppConfig(webui_url="", webui_host="localhost", webui_port=9000)
    assert cfg.base_url == "http://localhost:9000"


def test_task_record_normalizes_inconsistent_loaded_state() -> None:
    task = TaskRecord(task_id="t1", text="hello", reference_audio="ref.wav", status="done", progress=2)
    assert task.progress == 100


def test_task_record_rejects_invalid_transition() -> None:
    task = TaskRecord(task_id="t1", text="hello", reference_audio="ref.wav")
    with pytest.raises(ValueError, match="pending -> done"):
        task.transition_to("done")


def test_to_dict_does_not_change_timestamp() -> None:
    task = TaskRecord(task_id="t1", text="hello", reference_audio="ref.wav")
    before = task.updated_at
    task.to_dict()
    assert task.updated_at == before
