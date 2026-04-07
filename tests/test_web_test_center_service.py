"""web 测试中心服务层测试。"""

from pathlib import Path

import yaml

from vat.config import load_config, write_starter_config
from vat.web.services import test_center as service


def _make_config(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    write_starter_config(str(config_path))
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["storage"]["database_path"] = str(tmp_path / "db.sqlite3")
    data["storage"]["output_dir"] = str(tmp_path / "outputs")
    data["storage"]["work_dir"] = str(tmp_path / "work")
    data["storage"]["models_dir"] = str(tmp_path / "models")
    data["llm"]["api_key"] = "global-secret-key"
    data["llm"]["base_url"] = "https://global.example/v1"
    data["llm"]["model"] = "global-model"
    data["asr"]["split"]["api_key"] = "split-secret-key"
    data["asr"]["split"]["base_url"] = "https://split.example/v1"
    data["asr"]["split"]["model"] = "split-model"
    data["translator"]["llm"]["api_key"] = "translate-secret-key"
    data["translator"]["llm"]["base_url"] = "https://translate.example/v1"
    data["translator"]["llm"]["model"] = "translate-model"
    data["translator"]["llm"]["optimize"]["api_key"] = "opt-secret-key"
    data["translator"]["llm"]["optimize"]["base_url"] = "https://opt.example/v1"
    data["translator"]["llm"]["optimize"]["model"] = "opt-model"
    data["downloader"]["scene_identify"]["api_key"] = "scene-secret-key"
    data["downloader"]["scene_identify"]["base_url"] = "https://scene.example/v1"
    data["downloader"]["scene_identify"]["model"] = "scene-model"
    data["downloader"]["video_info_translate"]["api_key"] = "video-secret-key"
    data["downloader"]["video_info_translate"]["base_url"] = "https://video.example/v1"
    data["downloader"]["video_info_translate"]["model"] = "video-model"
    config_path.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    return config_path, load_config(str(config_path))


def test_list_llm_targets_returns_all_stage_targets(monkeypatch, tmp_path):
    _, config = _make_config(tmp_path)
    monkeypatch.setattr("vat.web.services.test_center.get_web_config", lambda: config)

    payload = service.list_llm_targets()

    assert [item["id"] for item in payload["targets"]] == [
        "global",
        "split",
        "translate",
        "optimize",
        "scene_identify",
        "video_info_translate",
    ]
    assert payload["targets"][0]["api_key_masked"].startswith("glo")


def test_run_llm_connectivity_test_includes_curl_reproduction(monkeypatch, tmp_path):
    _, config = _make_config(tmp_path)
    monkeypatch.setattr("vat.web.services.test_center.get_web_config", lambda: config)
    monkeypatch.setattr(
        "vat.web.services.test_center.call_llm",
        lambda **kwargs: type(
            "Resp",
            (),
            {"choices": [type("Choice", (), {"message": type("Msg", (), {"content": "OK"})()})()]},
        )(),
    )

    payload = service.run_llm_connectivity_test("translate")

    assert payload["ok"] is True
    assert payload["reproduction"]["endpoint"].endswith("/chat/completions")
    assert "curl -X POST" in payload["reproduction"]["curl_command"]
    assert "translate-model" in payload["reproduction"]["curl_command"]
    assert "Reply with EXACTLY OK" in payload["reproduction"]["curl_command"]


def test_build_prompt_preview_translate_masks_api_key(monkeypatch, tmp_path):
    _, config = _make_config(tmp_path)
    monkeypatch.setattr("vat.web.services.test_center.get_web_config", lambda: config)

    payload = service.build_prompt_preview("translate")

    assert payload["stage"] == "translate"
    assert payload["request"]["api_key"] == "***"
    assert payload["request"]["api_key_masked"].startswith("tra")
    assert payload["request"]["messages"][0]["role"] == "system"


def test_build_prompt_preview_split_applies_override_text(monkeypatch, tmp_path):
    _, config = _make_config(tmp_path)
    monkeypatch.setattr("vat.web.services.test_center.get_web_config", lambda: config)

    payload = service.build_prompt_preview("split", {"text": "自定义断句文本", "scene_prompt": "自定义场景"})

    assert payload["effective_input"]["text"] == "自定义断句文本"
    assert payload["effective_input"]["scene_prompt"] == "自定义场景"
    assert "自定义断句文本" in payload["request"]["messages"][1]["content"]


def test_save_main_config_text_creates_backup_and_reloads(monkeypatch, tmp_path):
    config_path, _ = _make_config(tmp_path)
    original_text = config_path.read_text(encoding="utf-8")
    updated_text = original_text.replace("global-model", "global-model-updated")
    reloaded = {}

    monkeypatch.setattr("vat.web.services.test_center._resolve_active_config_path", lambda: config_path)
    monkeypatch.setattr(
        "vat.web.services.test_center._reload_web_runtime",
        lambda path: reloaded.update({"path": str(path)}),
    )

    result = service.save_main_config_text(updated_text)

    assert config_path.read_text(encoding="utf-8") == updated_text
    assert result["backup"]["name"].endswith(".bak")
    assert reloaded["path"] == str(config_path)
    assert len(result["backups"]) == 1


def test_get_main_config_editor_state_returns_structured_data(monkeypatch, tmp_path):
    config_path, _ = _make_config(tmp_path)
    monkeypatch.setattr("vat.web.services.test_center._resolve_active_config_path", lambda: config_path)

    payload = service.get_main_config_editor_state()

    assert payload["structured_data"]["storage"]["work_dir"] == str(tmp_path / "work")
    work_dir_item = next(item for item in payload["reference_items"] if item["path"] == "storage.work_dir")
    assert work_dir_item["source"] == "显式配置"


def test_get_main_config_editor_state_marks_missing_field_as_default_or_inherited(monkeypatch, tmp_path):
    config_path, _ = _make_config(tmp_path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["downloader"]["scene_identify"].pop("api_key", None)
    config_path.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setattr("vat.web.services.test_center._resolve_active_config_path", lambda: config_path)

    payload = service.get_main_config_editor_state()

    item = next(item for item in payload["reference_items"] if item["path"] == "downloader.scene_identify.api_key")
    assert item["source"] == "默认/继承"


def test_save_main_config_form_data_updates_nested_values(monkeypatch, tmp_path):
    config_path, _ = _make_config(tmp_path)
    reloaded = {}
    monkeypatch.setattr("vat.web.services.test_center._resolve_active_config_path", lambda: config_path)
    monkeypatch.setattr("vat.web.services.test_center._reload_web_runtime", lambda path: reloaded.update({"path": str(path)}))

    result = service.save_main_config_form_data({
        "storage.work_dir": "/tmp/changed-work",
        "asr.split.enable": False,
    })

    updated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert updated["storage"]["work_dir"] == "/tmp/changed-work"
    assert updated["asr"]["split"]["enable"] is False
    assert result["backup"]["name"].endswith(".bak")
    assert reloaded["path"] == str(config_path)


def test_save_main_config_form_data_uses_overlay_config_when_source_is_default(monkeypatch, tmp_path):
    source_default = tmp_path / "default.yaml"
    source_default.write_text("storage:\n  work_dir: /src/work\n", encoding="utf-8")
    save_target = tmp_path / "config.yaml"
    monkeypatch.setattr(
        "vat.web.services.test_center._resolve_main_config_io_paths",
        lambda: {"source_path": source_default, "save_path": save_target},
    )
    monkeypatch.setattr("vat.web.services.test_center.Config.from_dict", lambda data: data)
    monkeypatch.setattr("vat.web.services.test_center._reload_web_runtime", lambda path: None)

    result = service.save_main_config_form_data({"storage.work_dir": "/dst/work"})

    saved = yaml.safe_load(save_target.read_text(encoding="utf-8"))
    assert saved["storage"]["work_dir"] == "/dst/work"
    assert result["save_path"] == str(save_target)
    assert result["source_path"] == str(source_default)


def test_get_upload_config_editor_state_returns_reference_items(monkeypatch, tmp_path):
    upload_path = tmp_path / "upload.yaml"
    upload_path.write_text("bilibili:\n  default_tid: 21\n", encoding="utf-8")
    monkeypatch.setattr("vat.web.services.test_center._resolve_upload_config_path", lambda: upload_path)

    payload = service.get_upload_config_editor_state()

    assert payload["path"] == str(upload_path)
    assert payload["structured_data"]["bilibili"]["default_tid"] == 21
    assert payload["reference_items"][0]["path"].startswith("bilibili")
    item = next(item for item in payload["reference_items"] if item["path"] == "bilibili.default_tid")
    assert item["source"] == "显式配置"


def test_save_upload_config_text_creates_backup(monkeypatch, tmp_path):
    upload_path = tmp_path / "upload.yaml"
    upload_path.write_text("bilibili:\n  default_tid: 21\n", encoding="utf-8")
    monkeypatch.setattr("vat.web.services.test_center._resolve_upload_config_path", lambda: upload_path)

    result = service.save_upload_config_text("bilibili:\n  default_tid: 22\n")

    assert upload_path.read_text(encoding="utf-8") == "bilibili:\n  default_tid: 22\n"
    assert result["backup"]["name"].endswith(".bak")
    assert len(result["backups"]) == 1


def test_save_upload_config_form_data_updates_nested_values(monkeypatch, tmp_path):
    upload_path = tmp_path / "upload.yaml"
    upload_path.write_text(
        "bilibili:\n  default_tid: 21\n  auto_cover: true\n  templates:\n    title: ${translated_title}\n    description: ${translated_desc}\n    custom_vars: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("vat.web.services.test_center._resolve_upload_config_path", lambda: upload_path)

    result = service.save_upload_config_form_data({
        "bilibili.default_tid": 22,
        "bilibili.auto_cover": False,
        "bilibili.templates.title": "新标题模板",
    })

    updated = yaml.safe_load(upload_path.read_text(encoding="utf-8"))
    assert updated["bilibili"]["default_tid"] == 22
    assert updated["bilibili"]["auto_cover"] is False
    assert updated["bilibili"]["templates"]["title"] == "新标题模板"
    assert result["backup"]["name"].endswith(".bak")


def test_run_whisper_check_returns_detailed_structure(monkeypatch, tmp_path):
    _, config = _make_config(tmp_path)
    monkeypatch.setattr("vat.web.services.test_center.get_web_config", lambda: config)
    monkeypatch.setattr("vat.web.services.test_center._ensure_sample_audio", lambda: tmp_path / "sample.wav")
    monkeypatch.setattr("vat.web.services.test_center._ensure_sample_video", lambda: tmp_path / "sample.mp4")
    monkeypatch.setattr(
        "vat.web.services.test_center.get_available_gpus",
        lambda: [type("GPU", (), {"__dict__": {"index": 0, "name": "RTX", "memory_free_mb": 16000}})()],
    )
    monkeypatch.setattr("vat.web.services.test_center.is_cuda_available", lambda: True)
    monkeypatch.setattr("vat.web.services.test_center.resolve_faster_whisper_repo_id", lambda model_name: f"repo/{model_name}")
    monkeypatch.setattr("vat.web.services.test_center.has_local_whisper_cache", lambda model_name, download_root: True)
    monkeypatch.setattr("vat.web.services.test_center.can_access_huggingface", lambda timeout_seconds=5.0: False)
    gpu_reads = iter([
        type("GPU", (), {"memory_used_mb": 1000})(),
        type("GPU", (), {"memory_used_mb": 1600})(),
        type("GPU", (), {"memory_used_mb": 2100})(),
    ])
    monkeypatch.setattr("vat.web.services.test_center.get_gpu_info", lambda gpu_id: next(gpu_reads, type("GPU", (), {"memory_used_mb": 2100})()))

    class FakeWhisper:
        def __init__(self, **kwargs):
            self.device = "cuda"
            self.gpu_id = 0
            self.model_name = kwargs["model_name"]
            self.download_root = kwargs["download_root"]
            self.model = None

        def _ensure_model_loaded(self):
            self.model = object()

        def asr_audio(self, audio_path, language=None):
            return type("ASRResult", (), {"segments": [type("Seg", (), {"text": "测试文本", "start_time": 0, "end_time": 1000})()]})()

    monkeypatch.setattr("vat.web.services.test_center.WhisperASR", FakeWhisper)

    payload = service.run_whisper_check()

    assert payload["ok"] is True
    assert payload["model_source"]["repo_id"] == f"repo/{config.asr.model}"
    assert payload["model_source"]["local_cache_available"] is True
    assert payload["gpu"]["cuda_available"] is True
    assert payload["load"]["ok"] is True
    assert payload["transcription"]["segments"] == 1
    assert payload["load"]["memory"]["peak_used_mb"] >= 1000
    assert payload["transcription"]["memory"]["peak_used_mb"] >= 1000


def test_run_whisper_check_marks_oom_on_transcription_failure(monkeypatch, tmp_path):
    _, config = _make_config(tmp_path)
    monkeypatch.setattr("vat.web.services.test_center.get_web_config", lambda: config)
    monkeypatch.setattr("vat.web.services.test_center._ensure_sample_audio", lambda: tmp_path / "sample.wav")
    monkeypatch.setattr("vat.web.services.test_center._ensure_sample_video", lambda: tmp_path / "sample.mp4")
    monkeypatch.setattr("vat.web.services.test_center.get_available_gpus", lambda: [])
    monkeypatch.setattr("vat.web.services.test_center.get_gpu_info", lambda gpu_id: None)
    monkeypatch.setattr("vat.web.services.test_center.is_cuda_available", lambda: True)
    monkeypatch.setattr("vat.web.services.test_center.resolve_faster_whisper_repo_id", lambda model_name: f"repo/{model_name}")
    monkeypatch.setattr("vat.web.services.test_center.has_local_whisper_cache", lambda model_name, download_root: True)
    monkeypatch.setattr("vat.web.services.test_center.can_access_huggingface", lambda timeout_seconds=5.0: True)

    class FakeWhisper:
        def __init__(self, **kwargs):
            self.device = "cuda"
            self.gpu_id = 0
            self.model = None

        def _ensure_model_loaded(self):
            self.model = object()

        def asr_audio(self, audio_path, language=None):
            raise RuntimeError("CUDA out of memory while allocating tensor")

    monkeypatch.setattr("vat.web.services.test_center.WhisperASR", FakeWhisper)

    payload = service.run_whisper_check()

    assert payload["ok"] is False
    assert payload["transcription"]["oom"] is True
