from __future__ import annotations

import json
from pathlib import Path

from .models import AppConfig


def app_config_path() -> Path:
    return Path.home() / ".indextts_batch_gui" / "app_config.json"


def load_app_config() -> AppConfig:
    cfg_path = app_config_path()
    if not cfg_path.exists():
        return AppConfig()
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return AppConfig()

    return AppConfig(
        webui_url=str(data.get("webui_url", "")),
        webui_host=str(data.get("webui_host", "127.0.0.1")),
        webui_port=int(data.get("webui_port", 7860)),
        concurrency=max(1, int(data.get("concurrency", 1))),
        request_timeout_sec=max(5, int(data.get("request_timeout_sec", 300))),
        last_task_set_path=str(data.get("last_task_set_path", "")),
        last_active_tab=max(0, int(data.get("last_active_tab", 0))),
        task_editor_draft=dict(data.get("task_editor_draft") or {}),
        last_selected_task_id=str(data.get("last_selected_task_id", "")),
    )


def save_app_config(config: AppConfig) -> None:
    cfg_path = app_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        json.dumps(
            {
                "webui_url": config.webui_url,
                "webui_host": config.webui_host,
                "webui_port": config.webui_port,
                "concurrency": config.concurrency,
                "request_timeout_sec": config.request_timeout_sec,
                "last_task_set_path": config.last_task_set_path,
                "last_active_tab": config.last_active_tab,
                "task_editor_draft": config.task_editor_draft,
                "last_selected_task_id": config.last_selected_task_id,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
