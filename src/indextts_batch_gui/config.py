from __future__ import annotations

import json
import os
import tempfile
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

    if not isinstance(data, dict):
        return AppConfig()
    return AppConfig(
        webui_url=str(data.get("webui_url", "") or ""),
        webui_host=str(data.get("webui_host", "127.0.0.1") or "127.0.0.1"),
        webui_port=max(1, min(65535, _safe_int(data.get("webui_port"), 7860))),
        concurrency=max(1, min(16, _safe_int(data.get("concurrency"), 1))),
        request_timeout_sec=max(5, _safe_int(data.get("request_timeout_sec"), 300)),
        last_task_set_path=str(data.get("last_task_set_path", "") or ""),
        last_active_tab=max(0, _safe_int(data.get("last_active_tab"), 0)),
        task_editor_draft=dict(data.get("task_editor_draft") or {}) if isinstance(data.get("task_editor_draft"), dict) else {},
        last_selected_task_id=str(data.get("last_selected_task_id", "") or ""),
    )


def save_app_config(config: AppConfig) -> None:
    cfg_path = app_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
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
        ).encode("utf-8")
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(dir=cfg_path.parent, prefix=f".{cfg_path.name}.", suffix=".tmp", delete=False) as fp:
            temp_name = fp.name
            fp.write(payload)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(temp_name, cfg_path)
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


def _safe_int(value: object, fallback: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
