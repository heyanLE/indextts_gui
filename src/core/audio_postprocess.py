"""Local post-processing for generated speech audio."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


# Removes only a low-energy sound at the start (typically an inhale), leaving
# sentence endings and pauses inside the utterance untouched.
LEADING_BREATH_FILTER = (
    "silenceremove=start_periods=1:start_duration=0.04:start_threshold=-36dB:"
    "start_silence=0.015:detection=rms,afade=t=in:st=0:d=0.015"
)

# Conservative studio-compatible cleanup; it avoids aggressively erasing
# Japanese consonants while reducing low rumble and steady room noise.
LIGHT_DENOISE_FILTER = (
    "highpass=f=70,lowpass=f=15500,afftdn=nr=8:nf=-45:tn=1:ad=0.8:gs=4"
)


class AudioPostprocessError(RuntimeError):
    """Raised when a requested local audio cleanup cannot be completed."""


def postprocess_generated_audio(
    audio_bytes: bytes,
    *,
    trim_leading_breath: bool = False,
    denoise: bool = False,
) -> bytes:
    """Return a cleaned WAV, or the original bytes when no option is selected."""
    if not audio_bytes or not (trim_leading_breath or denoise):
        return audio_bytes

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AudioPostprocessError("已启用音频后处理，但未在 PATH 中找到 ffmpeg")

    filters: list[str] = []
    if denoise:
        filters.append(LIGHT_DENOISE_FILTER)
    if trim_leading_breath:
        filters.append(LEADING_BREATH_FILTER)

    with tempfile.TemporaryDirectory(prefix="indextts-gui-audio-") as temp_dir:
        input_path = Path(temp_dir) / "input.wav"
        output_path = Path(temp_dir) / "output.wav"
        input_path.write_bytes(audio_bytes)
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(input_path),
             "-af", ",".join(filters), "-c:a", "pcm_s16le", str(output_path)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0 or not output_path.is_file():
            detail = (result.stderr or result.stdout or "未知 ffmpeg 错误").strip()
            raise AudioPostprocessError(f"音频后处理失败：{detail}")
        processed = output_path.read_bytes()
        if not processed:
            raise AudioPostprocessError("音频后处理失败：输出为空")
        return processed
