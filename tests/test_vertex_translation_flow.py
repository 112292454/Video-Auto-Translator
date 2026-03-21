from unittest.mock import MagicMock

from vat.asr.asr_data import ASRData, ASRDataSeg
from vat.config import Config
from vat.translator.llm_translator import LLMTranslator
from vat.translator.types import str_to_target_language


def _minimal_vertex_config_dict(**llm_overrides):
    data = {
        "storage": {
            "work_dir": "/tmp/work",
            "output_dir": "/tmp/output",
            "database_path": "/tmp/db.db",
            "models_dir": "/tmp/models",
            "resource_dir": "resources",
            "fonts_dir": "fonts",
            "subtitle_style_dir": "styles",
            "cache_dir": "/tmp/cache",
        },
        "downloader": {"youtube": {"format": "best", "max_workers": 1}},
        "asr": {
            "backend": "faster-whisper",
            "model": "large-v3",
            "language": "ja",
            "device": "auto",
            "compute_type": "float16",
            "vad_filter": False,
            "beam_size": 5,
            "models_subdir": "whisper",
            "word_timestamps": True,
            "condition_on_previous_text": False,
            "temperature": [0.0],
            "compression_ratio_threshold": 2.4,
            "log_prob_threshold": -1.0,
            "no_speech_threshold": 0.6,
            "initial_prompt": "",
            "repetition_penalty": 1.0,
            "hallucination_silence_threshold": None,
            "vad_threshold": 0.5,
            "vad_min_speech_duration_ms": 250,
            "vad_max_speech_duration_s": 30,
            "vad_min_silence_duration_ms": 100,
            "vad_speech_pad_ms": 30,
            "enable_chunked": False,
            "chunk_length_sec": 300,
            "chunk_overlap_sec": 10,
            "chunk_concurrency": 1,
            "split": {
                "enable": True,
                "mode": "sentence",
                "max_words_cjk": 30,
                "max_words_english": 15,
                "min_words_cjk": 5,
                "min_words_english": 3,
                "model": "gpt-4",
                "enable_chunking": False,
                "chunk_size_sentences": 50,
                "chunk_overlap_sentences": 2,
                "chunk_min_threshold": 100,
            },
        },
        "translator": {
            "backend_type": "llm",
            "source_language": "ja",
            "target_language": "zh-cn",
            "skip_translate": False,
            "llm": {
                "model": "gemini-2.5-flash",
                "enable_reflect": False,
                "batch_size": 1,
                "thread_num": 1,
                "custom_prompt": "",
                "enable_context": False,
                "enable_fallback": False,
                "optimize": {"enable": False, "custom_prompt": ""},
            },
            "local": {
                "model_filename": "model.gguf",
                "backend": "sakura",
                "n_gpu_layers": 35,
                "context_size": 4096,
            },
        },
        "embedder": {
            "subtitle_formats": ["srt"],
            "embed_mode": "hard",
            "output_container": "mp4",
            "video_codec": "libx265",
            "audio_codec": "copy",
            "crf": 23,
            "preset": "medium",
            "use_gpu": True,
            "subtitle_style": "default",
        },
        "uploader": {"bilibili": {"cookies_file": "", "line": "AUTO", "threads": 3}},
        "gpu": {"device": "auto", "allow_cpu_fallback": False, "min_free_memory_mb": 2000},
        "concurrency": {"gpu_devices": [0], "max_concurrent_per_gpu": 1},
        "logging": {"level": "INFO", "file": "vat.log", "format": "%(message)s"},
        "llm": {
            "provider": "vertex_native",
            "auth_mode": "api_key",
            "api_key": "test-vertex-key",
            "base_url": "",
            "model": "gemini-2.5-flash",
            "location": "global",
            "project_id": "",
            "credentials_path": "",
        },
        "proxy": {"http_proxy": ""},
    }

    def _merge(base, override):
        for key, value in override.items():
            if isinstance(value, dict) and key in base and isinstance(base[key], dict):
                _merge(base[key], value)
            else:
                base[key] = value

    _merge(data, {"llm": llm_overrides})
    return data


def _create_translator(config: Config, output_dir: str) -> LLMTranslator:
    creds = config.get_stage_llm_credentials("translate")
    return LLMTranslator(
        thread_num=1,
        batch_num=1,
        target_language=str_to_target_language(config.translator.target_language),
        output_dir=output_dir,
        model=creds["model"],
        custom_translate_prompt="",
        is_reflect=False,
        enable_optimize=False,
        custom_optimize_prompt="",
        enable_context=False,
        api_key=creds["api_key"],
        base_url=creds["base_url"],
        proxy=config.get_stage_proxy("translate") or "",
        enable_fallback=False,
    )


class TestVertexTranslationFlow:
    def test_vertex_api_key_translate_flow_writes_srt(self, monkeypatch, tmp_path):
        config = Config.from_dict(_minimal_vertex_config_dict())
        translator = _create_translator(config, str(tmp_path))
        captured = {}

        def fake_post(url, json, headers, timeout, proxy=None):
            captured["url"] = url
            captured["headers"] = headers
            response = MagicMock()
            response.json.return_value = {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": '{"1": "早上好"}'}
                            ]
                        }
                    }
                ]
            }
            response.raise_for_status.return_value = None
            return response

        monkeypatch.setattr("vat.llm.client.httpx.post", fake_post)

        result = translator.translate_subtitle(
            ASRData([ASRDataSeg(text="おはようございます", start_time=0, end_time=1000)])
        )

        assert result.segments[0].translated_text == "早上好"
        assert (tmp_path / "translated.srt").exists()
        assert captured["url"] == (
            "https://aiplatform.googleapis.com/v1/publishers/google/models/"
            "gemini-2.5-flash:generateContent?key=test-vertex-key"
        )
        assert "Authorization" not in captured["headers"]

    def test_vertex_adc_translate_flow_writes_srt(self, monkeypatch, tmp_path):
        config = Config.from_dict(
            _minimal_vertex_config_dict(
                auth_mode="adc",
                api_key="",
                project_id="vertex-490203",
                credentials_path="/home/gzy/.ssh/vat_vertex.json",
            )
        )
        translator = _create_translator(config, str(tmp_path))
        captured = {}

        monkeypatch.setattr(
            "vat.llm.client._get_vertex_access_token",
            lambda credentials_path="", proxy="": "test-access-token",
        )

        def fake_post(url, json, headers, timeout, proxy=None):
            captured["url"] = url
            captured["headers"] = headers
            response = MagicMock()
            response.json.return_value = {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": '{"1": "你好"}'}
                            ]
                        }
                    }
                ]
            }
            response.raise_for_status.return_value = None
            return response

        monkeypatch.setattr("vat.llm.client.httpx.post", fake_post)

        result = translator.translate_subtitle(
            ASRData([ASRDataSeg(text="こんにちは", start_time=0, end_time=1000)])
        )

        assert result.segments[0].translated_text == "你好"
        assert (tmp_path / "translated.srt").exists()
        assert captured["url"] == (
            "https://aiplatform.googleapis.com/v1/projects/vertex-490203/locations/global/"
            "publishers/google/models/gemini-2.5-flash:generateContent"
        )
        assert captured["headers"]["Authorization"] == "Bearer test-access-token"
