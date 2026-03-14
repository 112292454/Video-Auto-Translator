import pytest
from unittest.mock import MagicMock

from vat.llm.client import call_llm


class TestVertexNativeClient:
    def test_call_llm_vertex_native_adapts_response(self, monkeypatch):
        monkeypatch.setenv("VAT_LLM_PROVIDER", "vertex_native")
        monkeypatch.setenv("OPENAI_API_KEY", "test-vertex-key")
        monkeypatch.setenv("VAT_VERTEX_LOCATION", "global")
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

        captured = {}

        def fake_post(url, json, headers, timeout, proxy=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            captured["timeout"] = timeout
            captured["proxy"] = proxy
            response = MagicMock()
            response.json.return_value = {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "vertex response text"}
                            ]
                        }
                    }
                ]
            }
            response.raise_for_status.return_value = None
            return response

        monkeypatch.setattr("vat.llm.client.httpx.post", fake_post)

        response = call_llm(
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello from user"},
            ],
            model="gemini-2.5-flash",
            temperature=0.3,
        )

        assert response.choices[0].message.content == "vertex response text"
        assert captured["url"] == (
            "https://aiplatform.googleapis.com/v1/publishers/google/models/"
            "gemini-2.5-flash:generateContent?key=test-vertex-key"
        )
        assert captured["json"]["systemInstruction"] == {
            "parts": [{"text": "You are a helpful assistant."}]
        }
        assert captured["json"]["contents"] == [
            {"role": "user", "parts": [{"text": "Hello from user"}]}
        ]
        assert captured["json"]["generationConfig"]["temperature"] == 0.3

    def test_explicit_base_url_still_uses_openai_compatible_client(self, monkeypatch):
        monkeypatch.setenv("VAT_LLM_PROVIDER", "vertex_native")

        fake_response = MagicMock()
        fake_response.choices = [MagicMock()]
        fake_response.choices[0].message.content = "openai compatible text"

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = fake_response

        monkeypatch.setattr("vat.llm.client.get_or_create_client", lambda *args, **kwargs: fake_client)

        response = call_llm(
            messages=[{"role": "user", "content": "Hello"}],
            model="test-model",
            api_key="override-key",
            base_url="https://example.com/v1",
        )

        assert response.choices[0].message.content == "openai compatible text"
        fake_client.chat.completions.create.assert_called_once()

    def test_call_llm_vertex_native_adc_uses_project_scoped_endpoint(self, monkeypatch):
        monkeypatch.setenv("VAT_LLM_PROVIDER", "vertex_native")
        monkeypatch.setenv("VAT_VERTEX_AUTH_MODE", "adc")
        monkeypatch.setenv("VAT_VERTEX_LOCATION", "global")
        monkeypatch.setenv("VAT_VERTEX_PROJECT_ID", "vertex-490203")
        monkeypatch.setenv("VAT_VERTEX_CREDENTIALS", "/home/gzy/.ssh/vat_vertex.json")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

        captured = {}

        monkeypatch.setattr(
            "vat.llm.client._get_vertex_access_token",
            lambda credentials_path="": "test-access-token",
            raising=False,
        )

        def fake_post(url, json, headers, timeout, proxy=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            captured["timeout"] = timeout
            captured["proxy"] = proxy
            response = MagicMock()
            response.json.return_value = {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "vertex adc response"}
                            ]
                        }
                    }
                ]
            }
            response.raise_for_status.return_value = None
            return response

        monkeypatch.setattr("vat.llm.client.httpx.post", fake_post)

        response = call_llm(
            messages=[{"role": "user", "content": "Hello from adc"}],
            model="gemini-2.5-flash",
            temperature=0.1,
        )

        assert response.choices[0].message.content == "vertex adc response"
        assert captured["url"] == (
            "https://aiplatform.googleapis.com/v1/projects/vertex-490203/locations/global/"
            "publishers/google/models/gemini-2.5-flash:generateContent"
        )
        assert captured["headers"]["Authorization"] == "Bearer test-access-token"
        assert "key=" not in captured["url"]
