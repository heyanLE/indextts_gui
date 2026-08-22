"""引擎适配器单元测试"""

import pytest
from src.engines.base_engine import EngineException, ParamField
from src.engines.indextts_engine import IndexTTSEngine
from src.engines.gpt_sovits_engine import GPTSovitsEngine  # 预留接口，引擎文件保留
from src.engines import EngineRegistry, engine_registry


class TestEngineRegistry:
    """引擎注册表"""

    def test_discover(self):
        engines = engine_registry.discover()
        assert "indextts" in engines
        # GPT-SoVITS 已从注册表移除，仅保留引擎接口文件

    def test_singleton(self):
        r1 = EngineRegistry()
        r2 = EngineRegistry()
        assert r1 is r2

    def test_get_existing(self):
        engine = engine_registry.get("indextts")
        assert engine is not None
        assert engine.meta.engine_id == "indextts"

    def test_get_nonexistent(self):
        engine = engine_registry.get("unknown_engine")
        assert engine is None

    def test_list_engines(self):
        engines = engine_registry.list_engines()
        assert len(engines) >= 1

    def test_engine_ids(self):
        ids = engine_registry.engine_ids()
        assert "indextts" in ids
        # GPT-SoVITS 已从注册表移除


class TestIndexTTSEngine:
    """IndexTTS 引擎"""

    def test_schema_has_required_fields(self):
        engine = IndexTTSEngine()
        schema = engine.get_param_schema()
        names = [f.name for f in schema]
        assert "reference_audio" in names
        assert "emotion_mode" in names
        assert "postprocess_trim_leading_breath" in names
        assert "postprocess_denoise" in names
        assert "language" in names
        assert "duration_factor" in names

    def test_new_webui_language_and_duration_factor_arg_positions(self):
        engine = IndexTTSEngine()
        args = engine._build_http_26_args({
            "text": "こんにちは",
            "reference_audio": "C:/audio/ref.wav",
            "language": "JA",
            "duration_factor": 1.25,
        })

        assert len(args) == 26
        assert args[3] == "JA"  # lang_choice
        assert args[17] == 1.25  # duration_factor

    def test_validate_duration_factor_range(self):
        engine = IndexTTSEngine()
        errors = engine.validate_params({
            "text": "你好",
            "reference_audio": "ref.wav",
            "duration_factor": 2.1,
        })

        assert any("时长系数" in error for error in errors)
        # text 字段已移至 TaskDetailPanel 的「文案内容」区域，不再出现在 engine schema 中

    def test_schema_emotion_vector_fields(self):
        engine = IndexTTSEngine()
        schema = engine.get_param_schema()
        vector_fields = [
            f.name for f in schema
            if (
                f.visible_when.get("emotion_mode") == "emotion_vector"
                or "emotion_vector" in (
                    f.visible_when.get("emotion_mode")
                    if isinstance(f.visible_when.get("emotion_mode"), list)
                    else []
                )
            )
        ]
        assert "calm" in vector_fields
        assert "happy" in vector_fields
        assert len(vector_fields) == 9  # weight + 8 emotions

    def test_validate_empty_text(self):
        engine = IndexTTSEngine()
        errors = engine.validate_params({"text": "", "reference_audio": "ref.wav"})
        assert any("文案" in e for e in errors)

    def test_validate_missing_ref_audio(self):
        engine = IndexTTSEngine()
        errors = engine.validate_params({"text": "你好", "reference_audio": ""})
        assert any("参考音频" in e for e in errors)

    def test_validate_emotion_vector_range(self):
        engine = IndexTTSEngine()
        errors = engine.validate_params({
            "text": "你好",
            "reference_audio": "ref.wav",
            "emotion_mode": "emotion_vector",
            "emotion_control_weight": 0.5,
            "calm": 0.5,
            "surprised": 1.5,  # 超出范围
            "melancholic": 0.0,
            "disgusted": 0.0,
            "afraid": 0.0,
            "sad": 0.0,
            "angry": 0.0,
            "happy": 0.0,
        })
        assert any("surprised" in e for e in errors)

    def test_validate_emotion_ref_audio_missing(self):
        engine = IndexTTSEngine()
        errors = engine.validate_params({
            "text": "你好",
            "reference_audio": "ref.wav",
            "emotion_mode": "emotion_ref_audio",
            "emotion_audio": "",
        })
        assert any("情感参考音频" in e for e in errors)

    def test_validate_passes_valid(self):
        engine = IndexTTSEngine()
        errors = engine.validate_params({
            "text": "你好",
            "reference_audio": "ref.wav",
            "emotion_mode": "same_as_ref",
        })
        assert len(errors) == 0

    def test_generate_propagates_configured_timeout(self):
        class CapturingEngine(IndexTTSEngine):
            def __init__(self):
                self.seen_timeout = None

            async def _generate_via_gradio_client(
                self, base_url, params, *, timeout=360.0
            ):
                self.seen_timeout = timeout
                return b"RIFFaudio"

        import asyncio

        engine = CapturingEngine()
        result = asyncio.run(
            engine.generate("http://localhost:7860", {}, timeout=42.0)
        )
        assert result == b"RIFFaudio"
        assert engine.seen_timeout == 42.0


class TestGPTSoVitsEngine:
    """GPT-SoVITS 引擎"""

    def test_schema(self):
        engine = GPTSovitsEngine()
        schema = engine.get_param_schema()
        names = [f.name for f in schema]
        assert "reference_audio" in names
        assert "text" in names

    def test_validate_empty_text(self):
        engine = GPTSovitsEngine()
        errors = engine.validate_params({"text": "", "reference_audio": "ref.wav"})
        assert len(errors) > 0

    def test_generate_not_implemented(self):
        engine = GPTSovitsEngine()
        import asyncio
        with pytest.raises(EngineException):
            asyncio.new_event_loop().run_until_complete(
                engine.generate("http://test.local", {})
            )


class TestParamField:
    def test_default_creation(self):
        field = ParamField(name="test", label="测试", field_type="text")
        assert field.name == "test"
        assert field.required is False
        assert field.visible_when == {}

    def test_visible_when_condition(self):
        field = ParamField(
            name="calm",
            label="平静",
            field_type="slider",
            visible_when={"field": "emotion_mode", "value": "emotion_vector"},
        )
        assert field.visible_when["field"] == "emotion_mode"
        assert field.visible_when["value"] == "emotion_vector"
