from pathlib import Path

from indextts_batch_gui.filenames import build_output_audio_path, sanitize_text_to_basename


def test_sanitize_text_to_basename_removes_special_chars() -> None:
    name = sanitize_text_to_basename("Hello, 世界!!!")
    assert "!" not in name
    assert name.endswith(name.split("_")[-1])
    assert "Hello" in name


def test_build_output_audio_path_uses_wav_extension(tmp_path: Path) -> None:
    out = build_output_audio_path(tmp_path, "line 1")
    assert out.parent == tmp_path
    assert out.suffix == ".wav"
