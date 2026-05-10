from indextts_batch_gui.models import AppConfig


def test_app_config_base_url_prefers_direct_url() -> None:
    cfg = AppConfig(webui_url="127.0.0.1:7860", webui_host="localhost", webui_port=9000)
    assert cfg.base_url == "http://127.0.0.1:7860"


def test_app_config_base_url_falls_back_to_host_port() -> None:
    cfg = AppConfig(webui_url="", webui_host="localhost", webui_port=9000)
    assert cfg.base_url == "http://localhost:9000"
