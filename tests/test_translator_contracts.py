"""翻译契约测试。"""

import sqlite3
from types import SimpleNamespace

import pytest

from vat.asr.asr_data import ASRDataSeg


@pytest.fixture
def translator():
    from vat.translator.llm_translator import LLMTranslator
    from vat.translator.types import TargetLanguage

    return LLMTranslator(
        thread_num=1,
        batch_num=5,
        target_language=TargetLanguage.SIMPLIFIED_CHINESE,
        output_dir="/tmp/test_translator_contracts",
        model="test-model",
        custom_translate_prompt="",
        is_reflect=False,
        api_key="test-key",
        base_url="http://localhost:1234/v1",
    )


class TestSetSegmentsTranslatedTextContracts:
    def test_sets_translated_text_by_index(self, translator):
        segments = [
            ASRDataSeg(text="a", start_time=0, end_time=100),
            ASRDataSeg(text="b", start_time=100, end_time=200),
        ]
        translated = [
            SimpleNamespace(index=2, translated_text="乙"),
            SimpleNamespace(index=1, translated_text="甲"),
        ]

        result = translator._set_segments_translated_text(segments, translated)

        assert result[0].translated_text == "甲"
        assert result[1].translated_text == "乙"

    def test_missing_translation_raises_instead_of_silent_partial_result(self, translator):
        segments = [
            ASRDataSeg(text="a", start_time=0, end_time=100),
            ASRDataSeg(text="b", start_time=100, end_time=200),
            ASRDataSeg(text="c", start_time=200, end_time=300),
        ]
        translated = [
            SimpleNamespace(index=1, translated_text="甲"),
            SimpleNamespace(index=3, translated_text="丙"),
        ]

        with pytest.raises(RuntimeError, match="缺少翻译"):
            translator._set_segments_translated_text(segments, translated)


class TestTranslatorCacheKeyContracts:
    def _make_chunk(self):
        from vat.translator.base import SubtitleProcessData

        return [
            SubtitleProcessData(index=1, original_text="おはよう"),
            SubtitleProcessData(index=2, original_text="こんにちは"),
        ]

    def test_custom_prompt_changes_cache_key(self, translator):
        chunk = self._make_chunk()
        base_key = translator._get_cache_key(chunk)

        translator.custom_prompt = "speaker=fubuki"
        changed_key = translator._get_cache_key(chunk)

        assert changed_key != base_key

    def test_reflect_mode_changes_cache_key(self, translator):
        chunk = self._make_chunk()
        base_key = translator._get_cache_key(chunk)

        translator.is_reflect = True
        changed_key = translator._get_cache_key(chunk)

        assert changed_key != base_key

    def test_context_payload_changes_cache_key_when_context_enabled(self, translator):
        chunk = self._make_chunk()
        translator.enable_context = True
        translator._previous_batch_result = {"1": "上一句"}
        key_a = translator._get_cache_key(chunk)

        translator._previous_batch_result = {"1": "另一句"}
        key_b = translator._get_cache_key(chunk)

        assert key_b != key_a


class TestSafeTranslateChunkContracts:
    def _make_chunk(self):
        from vat.translator.base import SubtitleProcessData

        return [SubtitleProcessData(index=1, original_text="おはよう")]

    def test_cache_hit_returns_cached_result_without_calling_translate(self, translator, monkeypatch):
        chunk = self._make_chunk()
        cached = [SimpleNamespace(index=1, translated_text="缓存命中")]

        class FakeCache:
            def get(self, key, default=None):
                return cached

        translator._cache = FakeCache()
        monkeypatch.setattr("vat.translator.base.is_cache_enabled", lambda: True)
        translator._translate_chunk = lambda _chunk: (_ for _ in ()).throw(AssertionError("should not translate"))

        result = translator._safe_translate_chunk(chunk)

        assert result is cached
        assert translator._cache_hit_count == 1

    def test_cache_miss_writes_result_and_calls_update_callback(self, translator, monkeypatch):
        chunk = self._make_chunk()
        writes = []
        updates = []

        class FakeCache:
            def get(self, key, default=None):
                return None

            def set(self, key, value, expire=None):
                writes.append((key, value, expire))

        expected = [SimpleNamespace(index=1, translated_text="翻译结果")]
        translator._cache = FakeCache()
        translator.update_callback = lambda result: updates.append(result)
        translator._translate_chunk = lambda _chunk: expected
        monkeypatch.setattr("vat.translator.base.is_cache_enabled", lambda: True)

        result = translator._safe_translate_chunk(chunk)

        assert result is expected
        assert updates == [expected]
        assert len(writes) == 1
        assert writes[0][1] is expected

    def test_sqlite_error_on_cache_get_falls_back_to_translate(self, translator, monkeypatch):
        chunk = self._make_chunk()

        class FakeCache:
            def get(self, key, default=None):
                raise sqlite3.OperationalError("database is locked")

            def set(self, key, value, expire=None):
                return None

        expected = [SimpleNamespace(index=1, translated_text="回退结果")]
        translator._cache = FakeCache()
        translator._translate_chunk = lambda _chunk: expected
        monkeypatch.setattr("vat.translator.base.is_cache_enabled", lambda: True)

        result = translator._safe_translate_chunk(chunk)

        assert result is expected

    def test_sqlite_error_on_cache_set_does_not_fail_translation(self, translator, monkeypatch):
        chunk = self._make_chunk()

        class FakeCache:
            def get(self, key, default=None):
                return None

            def set(self, key, value, expire=None):
                raise sqlite3.OperationalError("database is locked")

        expected = [SimpleNamespace(index=1, translated_text="写缓存失败也返回")]
        translator._cache = FakeCache()
        translator._translate_chunk = lambda _chunk: expected
        monkeypatch.setattr("vat.translator.base.is_cache_enabled", lambda: True)

        result = translator._safe_translate_chunk(chunk)

        assert result is expected


class TestBuildInputWithContextContracts:
    def test_returns_plain_json_when_context_disabled(self, translator):
        subtitle_dict = {"1": "おはよう", "2": "こんにちは"}

        result = translator._build_input_with_context(subtitle_dict)

        assert result == '{"1": "おはよう", "2": "こんにちは"}'

    def test_includes_previous_batch_context_when_enabled(self, translator):
        subtitle_dict = {"3": "こんばんは"}
        translator.enable_context = True
        translator._previous_batch_result = {"1": "早上好", "2": "你好"}

        result = translator._build_input_with_context(subtitle_dict)

        assert "Previous context" in result
        assert "[1]: 早上好" in result
        assert "[2]: 你好" in result
        assert "Translate the following" in result
        assert '{"3": "こんばんは"}' in result


class TestValidateLlmResponseContracts:
    def test_validate_llm_response_rejects_non_dict(self, translator):
        ok, msg = translator._validate_llm_response(["bad"], {"1"})

        assert ok is False
        assert "dict" in msg

    def test_validate_llm_response_rejects_missing_and_extra_keys(self, translator):
        ok, msg = translator._validate_llm_response({"1": "a", "3": "c"}, {"1", "2"})

        assert ok is False
        assert "Missing keys" in msg
        assert "Extra keys" in msg

    def test_validate_llm_response_reflect_mode_requires_native_translation(self, translator):
        translator.is_reflect = True

        ok, msg = translator._validate_llm_response({"1": {"wrong": "x"}}, {"1"})

        assert ok is False
        assert "native_translation" in msg


class TestOptimizationValidationContracts:
    def test_normalize_kana_converts_katakana_to_hiragana(self, translator):
        assert translator._normalize_kana("ルルドライオン") == "るるどらいおん"

    def test_validate_optimization_result_accepts_kana_normalization_equivalent_text(self, translator):
        ok, msg = translator._validate_optimization_result(
            {"1": "ルルドライオン"},
            {"1": "るるどらいおん"},
        )

        assert ok is True
        assert msg == ""

    def test_validate_optimization_result_rejects_key_mismatch(self, translator):
        ok, msg = translator._validate_optimization_result(
            {"1": "原文", "2": "第二句"},
            {"1": "原文"},
        )

        assert ok is False
        assert "Missing keys" in msg

    def test_validate_optimization_result_rejects_excessive_change(self, translator):
        ok, msg = translator._validate_optimization_result(
            {"1": "これは十分に長い元の文章です"},
            {"1": "完全不同的另一段内容"},
        )

        assert ok is False
        assert "similarity" in msg


class TestRealignShiftedKeysContracts:
    def test_try_realign_shifted_keys_returns_none_when_not_systemic(self, translator):
        result = translator._try_realign_shifted_keys(
            {"1": "alpha", "2": "beta", "3": "gamma"},
            {"1": "alpha", "2": "beta-fixed", "3": "gamma"},
        )

        assert result is None

    def test_try_realign_shifted_keys_repairs_systemic_shift(self, translator):
        original = {
            "1": "apple zebra unique",
            "2": "beta ocean quartz",
            "3": "gamma sunset violin",
            "4": "delta rocket willow",
        }
        shifted = {
            "1": "beta ocean quartz",
            "2": "gamma sunset violin",
            "3": "delta rocket willow",
            "4": "apple zebra unique",
        }

        result = translator._try_realign_shifted_keys(original, shifted)

        assert result == original


class TestAgentLoopContracts:
    def test_agent_loop_retries_after_validation_failure(self, translator, monkeypatch):
        calls = []

        def fake_call_llm(*, messages, model, api_key, base_url, proxy):
            calls.append(messages)
            resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"1": "ok"}'))])
            return resp

        validation_results = iter([
            (False, "missing key 2"),
            (True, ""),
        ])

        monkeypatch.setattr("vat.translator.llm_translator.call_llm", fake_call_llm)
        monkeypatch.setattr(
            translator,
            "_validate_llm_response",
            lambda response_dict, expected_keys: next(validation_results),
        )

        result = translator._agent_loop("system", '{"1": "原文"}', expected_keys={"1"})

        assert result == {"1": "ok"}
        assert len(calls) == 2
        assert calls[1][-1]["role"] == "user"
        assert "Fix the errors above" in calls[1][-1]["content"]

    def test_agent_loop_raises_on_empty_choices(self, translator, monkeypatch):
        monkeypatch.setattr(
            "vat.translator.llm_translator.call_llm",
            lambda **kwargs: SimpleNamespace(choices=[]),
        )

        with pytest.raises(RuntimeError, match="LLM 未返回有效响应"):
            translator._agent_loop("system", '{"1": "原文"}', expected_keys={"1"})

    def test_agent_loop_raises_on_empty_content(self, translator, monkeypatch):
        monkeypatch.setattr(
            "vat.translator.llm_translator.call_llm",
            lambda **kwargs: SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="   "))]
            ),
        )

        with pytest.raises(RuntimeError, match="LLM 返回内容为空"):
            translator._agent_loop("system", '{"1": "原文"}', expected_keys={"1"})


class TestOptimizeChunkContracts:
    def test_optimize_chunk_retries_after_validation_failure(self, translator, monkeypatch):
        responses = iter([
            '{"1": "first try"}',
            '{"1": "second try"}',
        ])
        validation = iter([
            (False, "still wrong"),
            (True, ""),
        ])
        calls = []

        def fake_call_llm(*, messages, model, temperature, api_key, base_url, proxy):
            calls.append(messages)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=next(responses)))])

        monkeypatch.setattr("vat.translator.llm_translator.call_llm", fake_call_llm)
        monkeypatch.setattr(
            translator,
            "_validate_optimization_result",
            lambda original_chunk, optimized_chunk: next(validation),
        )

        result = translator._optimize_chunk({"1": "原文"})

        assert result == {"1": "second try"}
        assert len(calls) == 2
        assert calls[1][-1]["role"] == "user"
        assert "Validation failed" in calls[1][-1]["content"]

    def test_optimize_chunk_uses_realign_result_after_feedback_loop_exhausted(self, translator, monkeypatch):
        shifted = {"1": "b", "2": "a"}
        monkeypatch.setattr(
            "vat.translator.llm_translator.call_llm",
            lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"1": "b", "2": "a"}'))]),
        )
        monkeypatch.setattr(
            translator,
            "_validate_optimization_result",
            lambda original_chunk, optimized_chunk: (False, "bad"),
        )
        monkeypatch.setattr(
            translator,
            "_try_realign_shifted_keys",
            lambda original_chunk, optimized_chunk: {"1": "a", "2": "b"},
        )

        result = translator._optimize_chunk({"1": "a", "2": "b"})

        assert result == {"1": "a", "2": "b"}


class TestTranslateChunkReflectContracts:
    def test_translate_chunk_reflect_mode_extracts_native_translation_and_updates_context(self, translator, monkeypatch):
        translator.is_reflect = True
        chunk = [
            SimpleNamespace(index=1, original_text="原文1", translated_text=""),
            SimpleNamespace(index=2, original_text="原文2", translated_text=""),
        ]
        monkeypatch.setattr("vat.translator.llm_translator.get_prompt", lambda *args, **kwargs: "prompt")
        monkeypatch.setattr(
            translator,
            "_build_input_with_context",
            lambda subtitle_dict: '{"1": "原文1", "2": "原文2"}',
        )
        monkeypatch.setattr(
            translator,
            "_agent_loop",
            lambda prompt, user_input, expected_keys=None: {
                "1": {"native_translation": "译文1"},
                "2": {"native_translation": "译文2"},
            },
        )

        result = translator._translate_chunk(chunk)

        assert result[0].translated_text == "译文1"
        assert result[1].translated_text == "译文2"
        assert translator._previous_batch_result == {"1": "译文1", "2": "译文2"}
