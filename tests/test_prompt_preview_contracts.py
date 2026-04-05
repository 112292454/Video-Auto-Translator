"""Prompt 预览构造契约测试。"""

from pathlib import Path
from types import SimpleNamespace

from vat.asr.split import build_split_llm_request
from vat.llm.scene_identifier import SceneIdentifier
from vat.llm.video_info_translator import VideoInfoTranslator
from vat.translator.llm_translator import LLMTranslator
from vat.translator.types import TargetLanguage


def _make_translator(tmp_path: Path, **overrides) -> LLMTranslator:
    params = dict(
        thread_num=1,
        batch_num=2,
        target_language=TargetLanguage.SIMPLIFIED_CHINESE,
        output_dir=tmp_path,
        model="translate-model",
        custom_translate_prompt="保留主播口癖",
        is_reflect=True,
        enable_optimize=True,
        custom_optimize_prompt="统一专有名词",
        enable_context=True,
        api_key="translate-key",
        base_url="https://translate.example/v1",
        optimize_model="optimize-model",
        optimize_api_key="opt-key",
        optimize_base_url="https://optimize.example/v1",
        proxy="http://translate-proxy:8080",
        optimize_proxy="http://optimize-proxy:8081",
    )
    params.update(overrides)
    return LLMTranslator(**params)


def test_build_split_llm_request_includes_scene_prompt_and_runtime_fields():
    request = build_split_llm_request(
        text="今日はみんなで雑談するよ。最近のことを話そう。",
        model="split-model",
        max_word_count_cjk=24,
        max_word_count_english=18,
        min_word_count_cjk=3,
        min_word_count_english=1,
        recommend_word_count_cjk=12,
        recommend_word_count_english=8,
        scene_prompt="当前是杂谈场景，尽量保持自然口语节奏。",
        mode="sentence",
        api_key="split-key",
        base_url="https://split.example/v1",
        proxy="http://split-proxy:9000",
    )

    assert request["stage"] == "split"
    assert request["model"] == "split-model"
    assert request["temperature"] == 0.1
    assert request["api_key"] == "split-key"
    assert request["base_url"] == "https://split.example/v1"
    assert request["proxy"] == "http://split-proxy:9000"
    assert request["messages"][0]["role"] == "system"
    assert "<scene_specific>" in request["messages"][0]["content"]
    assert "当前是杂谈场景" in request["messages"][0]["content"]
    assert request["messages"][1]["role"] == "user"
    assert "Please use multiple <br> tags" in request["messages"][1]["content"]


def test_build_translate_llm_request_reuses_context_and_reflect_prompt(tmp_path):
    translator = _make_translator(tmp_path)
    translator._previous_batch_result = {"1": "前文译文A", "2": "前文译文B"}

    request = translator.build_translate_llm_request(
        [
            SimpleNamespace(index=3, original_text="今日は楽しく歌うよ"),
            SimpleNamespace(index=4, original_text="次は新曲です"),
        ]
    )

    assert request["stage"] == "translate"
    assert request["model"] == "translate-model"
    assert request["api_key"] == "translate-key"
    assert request["base_url"] == "https://translate.example/v1"
    assert request["proxy"] == "http://translate-proxy:8080"
    assert request["expected_keys"] == {"3", "4"}
    assert request["messages"][0]["role"] == "system"
    assert "简体中文" in request["messages"][0]["content"]
    assert "保留主播口癖" in request["messages"][0]["content"]
    assert request["messages"][1]["role"] == "user"
    assert "Previous context" in request["messages"][1]["content"]
    assert '"3": "今日は楽しく歌うよ"' in request["messages"][1]["content"]


def test_build_optimize_llm_request_includes_reference_prompt_and_runtime_fields(tmp_path):
    translator = _make_translator(tmp_path)

    request = translator.build_optimize_llm_request({"1": "こんフブキ", "2": "今日は雑談です"})

    assert request["stage"] == "optimize"
    assert request["model"] == "optimize-model"
    assert request["temperature"] == 0.2
    assert request["api_key"] == "opt-key"
    assert request["base_url"] == "https://optimize.example/v1"
    assert request["proxy"] == "http://optimize-proxy:8081"
    assert request["messages"][0]["role"] == "system"
    assert request["messages"][1]["role"] == "user"
    assert "Reference content" in request["messages"][1]["content"]
    assert "统一专有名词" in request["messages"][1]["content"]


def test_scene_identifier_build_request_matches_detect_scene_call_shape():
    identifier = SceneIdentifier(
        model="scene-model",
        api_key="scene-key",
        base_url="https://scene.example/v1",
        proxy="http://scene-proxy:7000",
    )

    request = identifier.build_detect_scene_llm_request(
        title="【Minecraft】建造天空城堡！",
        description="今天继续盖房子",
    )

    assert request["stage"] == "scene_identify"
    assert request["model"] == "scene-model"
    assert request["api_key"] == "scene-key"
    assert request["base_url"] == "https://scene.example/v1"
    assert request["proxy"] == "http://scene-proxy:7000"
    assert request["messages"][0]["role"] == "system"
    assert request["messages"][1]["role"] == "user"
    assert "Title: 【Minecraft】建造天空城堡！" in request["messages"][1]["content"]


def test_video_info_translator_build_request_contains_rendered_prompt_and_runtime_fields():
    translator = VideoInfoTranslator(
        model="video-info-model",
        api_key="video-info-key",
        base_url="https://video-info.example/v1",
        proxy="http://video-info-proxy:6060",
    )

    request = translator.build_translate_llm_request(
        title="【初配信】みんなとおしゃべり",
        description="最初の配信です",
        tags=["雑談", "初配信"],
        uploader="白上フブキ",
        default_tid=21,
    )

    assert request["stage"] == "video_info_translate"
    assert request["model"] == "video-info-model"
    assert request["temperature"] == 0.7
    assert request["api_key"] == "video-info-key"
    assert request["base_url"] == "https://video-info.example/v1"
    assert request["proxy"] == "http://video-info-proxy:6060"
    assert request["messages"][0]["role"] == "system"
    assert request["messages"][1]["role"] == "user"
    assert "频道/主播: 白上フブキ" in request["messages"][1]["content"]
    assert "【初配信】みんなとおしゃべり" in request["messages"][1]["content"]
