from __future__ import annotations

import hashlib
import re
from pathlib import Path


def sanitize_text_to_basename(text: str, max_len: int = 40) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", text.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = "audio"
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip("_")
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return f"{cleaned}_{digest}"


def build_output_audio_path(outputs_dir: Path, text: str) -> Path:
    return outputs_dir / f"{sanitize_text_to_basename(text)}.wav"
