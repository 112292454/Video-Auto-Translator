"""translation_benchmark.py 的 Vertex 模型配置测试"""

import importlib.util
import os
from pathlib import Path


_module_path = Path(__file__).resolve().parent.parent / "scripts" / "translation_benchmark.py"
_spec = importlib.util.spec_from_file_location("translation_benchmark", _module_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


class TestTranslationBenchmarkScript:
    def test_has_vertex_gemini3_flash_config(self):
        cfg = _mod.MODEL_CONFIGS["gemini-3-flash-vertex"]
        assert cfg["model"] == "gemini-3-flash-preview"
        assert cfg["provider"] == "vertex_native"
        assert cfg["auth_mode"] == "api_key"
        assert cfg["base_url"] == ""

    def test_apply_and_restore_runtime_env_for_vertex_model(self, monkeypatch):
        monkeypatch.setenv("VAT_LLM_PROVIDER", "openai_compatible")
        monkeypatch.delenv("VAT_VERTEX_AUTH_MODE", raising=False)
        monkeypatch.delenv("VAT_VERTEX_LOCATION", raising=False)
        monkeypatch.delenv("VAT_VERTEX_PROJECT_ID", raising=False)
        monkeypatch.delenv("VAT_VERTEX_CREDENTIALS", raising=False)

        cfg = {
            "provider": "vertex_native",
            "auth_mode": "api_key",
            "location": "global",
            "project_id": "",
            "credentials_path": "",
        }
        previous = _mod._apply_model_runtime_env(cfg)

        assert os.environ["VAT_LLM_PROVIDER"] == "vertex_native"
        assert os.environ["VAT_VERTEX_AUTH_MODE"] == "api_key"
        assert os.environ["VAT_VERTEX_LOCATION"] == "global"

        _mod._restore_runtime_env(previous)

        assert os.environ["VAT_LLM_PROVIDER"] == "openai_compatible"
        assert "VAT_VERTEX_AUTH_MODE" not in os.environ
        assert "VAT_VERTEX_LOCATION" not in os.environ

    def test_default_google_proxy_prefers_env(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
        monkeypatch.delenv("VAT_BENCHMARK_GOOGLE_PROXY", raising=False)
        assert _mod._default_google_proxy() == "http://127.0.0.1:7890"
