"""文件工具单元测试"""

import tempfile
from pathlib import Path

from src.utils.file_utils import (
    sanitize_filename,
    make_audio_filename,
    ensure_dir,
    atomic_write,
    safe_delete,
)


class TestSanitizeFilename:
    def test_normal_text(self):
        assert sanitize_filename("你好世界") == "你好世界"

    def test_english(self):
        assert sanitize_filename("HelloWorld") == "HelloWorld"

    def test_mixed(self):
        result = sanitize_filename("Hello, 你好!")
        assert "," not in result
        assert "!" not in result

    def test_max_length(self):
        result = sanitize_filename("a" * 100, max_length=20)
        assert len(result) <= 20

    def test_empty(self):
        assert sanitize_filename("") == "untitled"

    def test_only_special_chars(self):
        assert sanitize_filename("###") == "untitled"


class TestMakeAudioFilename:
    def test_basic(self):
        name = make_audio_filename("task_001", "你好世界")
        assert name == "task_001_你好世界.wav"

    def test_with_ext(self):
        name = make_audio_filename("task_002", "test", "mp3")
        assert name == "task_002_test.mp3"


class TestEnsureDir:
    def test_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a" / "b" / "c"
            result = ensure_dir(path)
            assert result == path
            assert path.exists()
            assert path.is_dir()


class TestAtomicWrite:
    def test_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.json"
            atomic_write(path, '{"key": "value"}')
            assert path.exists()
            assert path.read_text(encoding="utf-8") == '{"key": "value"}'

    def test_no_tmp_leftover(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.txt"
            atomic_write(path, "hello")
            # tmp 文件应该已被 replace 移除
            tmps = list(Path(tmp).glob("*.tmp"))
            assert len(tmps) == 0


class TestSafeDelete:
    def test_delete_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "to_delete.txt"
            path.write_text("bye")
            assert safe_delete(path) is True
            assert not path.exists()

    def test_delete_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "not_there.txt"
            assert safe_delete(path) is True  # 不报错
