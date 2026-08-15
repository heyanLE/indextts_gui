from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


TASK_STATES = {"pending", "queued", "generating", "done", "failed", "cancelled"}

TASK_STATE_TRANSITIONS = {
    "pending": {"queued", "cancelled"},
    "queued": {"pending", "generating", "cancelled"},
    "generating": {"pending", "done", "failed", "cancelled"},
    "done": {"pending", "queued"},
    "failed": {"pending", "queued", "cancelled"},
    "cancelled": {"pending", "queued"},
}


@dataclass
class AppConfig:
    webui_url: str = ""
    webui_host: str = "127.0.0.1"
    webui_port: int = 7860
    concurrency: int = 1
    request_timeout_sec: int = 300
    last_task_set_path: str = ""
    last_active_tab: int = 0
    task_editor_draft: dict[str, Any] = field(default_factory=dict)
    last_selected_task_id: str = ""

    def __post_init__(self) -> None:
        self.webui_url = str(self.webui_url or "")
        self.webui_host = str(self.webui_host or "127.0.0.1")
        self.webui_port = max(1, min(65535, _safe_int(self.webui_port, 7860)))
        self.concurrency = max(1, min(16, _safe_int(self.concurrency, 1)))
        self.request_timeout_sec = max(5, _safe_int(self.request_timeout_sec, 300))
        self.last_active_tab = max(0, _safe_int(self.last_active_tab, 0))
        if not isinstance(self.task_editor_draft, dict):
            self.task_editor_draft = {}

    @property
    def base_url(self) -> str:
        direct = _normalize_base_url(self.webui_url)
        if direct:
            return direct
        return f"http://{self.webui_host}:{self.webui_port}".rstrip("/")


@dataclass
class TaskSetDefaults:
    reference_audio: str = ""
    config: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.reference_audio = str(self.reference_audio or "")
        self.config = dict(self.config) if isinstance(self.config, dict) else {}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "TaskSetDefaults":
        raw = raw or {}
        return cls(
            reference_audio=str(raw.get("reference_audio", "")),
            config=dict(raw.get("config")) if isinstance(raw.get("config"), dict) else {},
        )


@dataclass
class TaskRecord:
    task_id: str
    text: str
    reference_audio: str
    config: dict[str, Any] = field(default_factory=dict)
    generated_text: str = ""
    generated_reference_audio: str = ""
    generated_config: dict[str, Any] = field(default_factory=dict)
    audio_file: str = ""
    status: str = "pending"
    progress: int = 0
    error: str = ""
    needs_regen: bool = False
    last_generated_signature: str = ""
    updated_at: str = field(default_factory=lambda: _now_iso())
    order: int = 0
    is_final: bool = False

    def __post_init__(self) -> None:
        self.task_id = str(self.task_id or "")
        self.text = str(self.text or "")
        self.reference_audio = str(self.reference_audio or "")
        self.config = dict(self.config) if isinstance(self.config, dict) else {}
        self.generated_config = dict(self.generated_config) if isinstance(self.generated_config, dict) else {}
        self.ensure_valid()

    def ensure_valid(self) -> None:
        if self.status not in TASK_STATES:
            self.status = "pending"
        self.progress = max(0, min(100, _safe_int(self.progress, 0)))
        self.order = max(0, _safe_int(self.order, 0))
        if self.status in {"pending", "cancelled"}:
            self.progress = 0
        elif self.status == "queued":
            self.progress = min(self.progress, 99)
        elif self.status == "generating":
            self.progress = max(1, min(self.progress, 99))
        elif self.status in {"done", "failed"}:
            self.progress = 100

    def transition_to(self, status: str, *, progress: int | None = None, error: str | None = None) -> None:
        """Move to a valid lifecycle state and keep derived fields consistent."""
        self.ensure_valid()
        if status not in TASK_STATES:
            raise ValueError(f"Unknown task status: {status}")
        if status != self.status and status not in TASK_STATE_TRANSITIONS[self.status]:
            raise ValueError(f"Invalid task transition: {self.status} -> {status}")

        self.status = status
        if progress is not None:
            self.progress = progress
        elif status in {"pending", "cancelled"}:
            self.progress = 0
        elif status == "queued":
            self.progress = 5
        elif status == "generating":
            self.progress = 30
        elif status in {"done", "failed"}:
            self.progress = 100
        if error is not None:
            self.error = error
        self.ensure_valid()

    def json_filename(self) -> str:
        return f"{self.task_id}.json"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "text": self.text,
            "reference_audio": self.reference_audio,
            "config": self.config,
            "generated_text": self.generated_text,
            "generated_reference_audio": self.generated_reference_audio,
            "generated_config": self.generated_config,
            "audio_file": self.audio_file,
            "status": self.status,
            "progress": self.progress,
            "error": self.error,
            "needs_regen": self.needs_regen,
            "last_generated_signature": self.last_generated_signature,
            "updated_at": self.updated_at,
            "order": self.order,
            "is_final": self.is_final,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TaskRecord":
        task = cls(
            task_id=str(raw.get("task_id", "")),
            text=str(raw.get("text", "")),
            reference_audio=str(raw.get("reference_audio", raw.get("ref_audio", ""))),
            order=_safe_int(raw.get("order"), 0),
            config=dict(raw.get("config")) if isinstance(raw.get("config"), dict) else {},
            generated_text=str(raw.get("generated_text", "")),
            generated_reference_audio=str(raw.get("generated_reference_audio", "")),
            generated_config=dict(raw.get("generated_config")) if isinstance(raw.get("generated_config"), dict) else {},
            audio_file=str(raw.get("audio_file", "")),
            status=str(raw.get("status", "pending")),
            progress=_safe_int(raw.get("progress"), 0),
            error=str(raw.get("error", "")),
            needs_regen=bool(raw.get("needs_regen", False)),
            last_generated_signature=str(raw.get("last_generated_signature", "")),
            updated_at=str(raw.get("updated_at", _now_iso())),
            is_final=bool(raw.get("is_final", raw.get("finalized", False))),
        )
        task.ensure_valid()
        return task

    @property
    def audio_path(self) -> Path | None:
        if not self.audio_file:
            return None
        return Path(self.audio_file)


@dataclass
class SynthesisResult:
    audio_bytes: bytes
    content_type: str = "audio/wav"
    request_config: dict[str, Any] = field(default_factory=dict)
    backend: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _normalize_base_url(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""

    candidate = raw
    parsed = urlparse(candidate)
    if not parsed.scheme:
        candidate = f"http://{candidate}"
        parsed = urlparse(candidate)

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return candidate.rstrip("/")
