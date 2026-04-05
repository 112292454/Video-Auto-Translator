"""web 测试中心 API 契约测试。"""

import httpx
import pytest
from fastapi import FastAPI


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


@pytest.fixture
def app():
    return FastAPI()


@pytest.fixture
def client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


class TestWebTestCenterApi:
    @pytest.mark.anyio
    async def test_list_llm_targets_returns_service_payload(self, app, client, monkeypatch):
        from vat.web.routes.test_center import router

        app.include_router(router)
        monkeypatch.setattr(
            "vat.web.routes.test_center.test_center_service.list_llm_targets",
            lambda: {
                "targets": [
                    {"id": "translate", "model": "m1"},
                    {"id": "optimize", "model": "m2"},
                ]
            },
        )

        async with client as ac:
            response = await ac.get("/api/test-center/llm/targets")

        assert response.status_code == 200
        assert [item["id"] for item in response.json()["targets"]] == ["translate", "optimize"]

    @pytest.mark.anyio
    async def test_run_llm_target_dispatches_request_body(self, app, client, monkeypatch):
        from vat.web.routes.test_center import router

        captured = {}
        app.include_router(router)

        def fake_run(target_id):
            captured["target_id"] = target_id
            return {"target_id": target_id, "ok": True, "response_text": "OK"}

        monkeypatch.setattr(
            "vat.web.routes.test_center.test_center_service.run_llm_connectivity_test",
            fake_run,
        )

        async with client as ac:
            response = await ac.post("/api/test-center/llm/run", json={"target_id": "translate"})

        assert response.status_code == 200
        assert captured["target_id"] == "translate"
        assert response.json()["response_text"] == "OK"

    @pytest.mark.anyio
    async def test_run_llm_target_returns_structured_error_when_service_raises(self, app, client, monkeypatch):
        from vat.web.routes.test_center import router

        app.include_router(router)
        monkeypatch.setattr(
            "vat.web.routes.test_center.test_center_service.run_llm_connectivity_test",
            lambda target_id: (_ for _ in ()).throw(ValueError(f"boom:{target_id}")),
        )

        async with client as ac:
            response = await ac.post("/api/test-center/llm/run", json={"target_id": "translate"})

        assert response.status_code == 200
        assert response.json() == {"ok": False, "error": "boom:translate"}

    @pytest.mark.anyio
    async def test_run_all_llm_targets_dispatches_service(self, app, client, monkeypatch):
        from vat.web.routes.test_center import router

        app.include_router(router)
        monkeypatch.setattr(
            "vat.web.routes.test_center.test_center_service.run_all_llm_connectivity_tests",
            lambda: {"results": [{"target_id": "split", "ok": True}]},
        )

        async with client as ac:
            response = await ac.post("/api/test-center/llm/run-all")

        assert response.status_code == 200
        assert response.json()["results"][0]["target_id"] == "split"

    @pytest.mark.anyio
    async def test_prompt_preview_returns_service_result(self, app, client, monkeypatch):
        from vat.web.routes.test_center import router

        app.include_router(router)
        monkeypatch.setattr(
            "vat.web.routes.test_center.test_center_service.build_prompt_preview",
            lambda stage, overrides=None: {
                "stage": stage,
                "request": {"model": "preview-model", "messages": [{"role": "system", "content": "x"}]},
            },
        )

        async with client as ac:
            response = await ac.post(
                "/api/test-center/prompt-preview",
                json={"stage": "translate", "overrides": {"title": "示例标题"}},
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["stage"] == "translate"
        assert payload["request"]["model"] == "preview-model"

    @pytest.mark.anyio
    async def test_get_main_config_state_returns_reference_and_backups(self, app, client, monkeypatch):
        from vat.web.routes.test_center import router

        app.include_router(router)
        monkeypatch.setattr(
            "vat.web.routes.test_center.test_center_service.get_main_config_editor_state",
            lambda: {
                "path": "/tmp/config.yaml",
                "yaml_text": "storage:\\n  work_dir: ./work\\n",
                "structured_data": {"storage": {"work_dir": "./work"}},
                "reference_items": [{"path": "storage.work_dir", "type": "str"}],
                "backups": [{"name": "config.yaml.20260405_120000.bak"}],
            },
        )

        async with client as ac:
            response = await ac.get("/api/test-center/config/main")

        assert response.status_code == 200
        payload = response.json()
        assert payload["path"] == "/tmp/config.yaml"
        assert payload["structured_data"]["storage"]["work_dir"] == "./work"
        assert payload["reference_items"][0]["path"] == "storage.work_dir"
        assert payload["backups"][0]["name"].endswith(".bak")

    @pytest.mark.anyio
    async def test_save_main_config_form_returns_backup_metadata(self, app, client, monkeypatch):
        from vat.web.routes.test_center import router

        app.include_router(router)
        monkeypatch.setattr(
            "vat.web.routes.test_center.test_center_service.save_main_config_form_data",
            lambda updates: {
                "path": "/tmp/config.yaml",
                "backup": {"name": "config.yaml.20260405_120000.bak"},
                "reloaded": True,
                "applied_updates": updates,
            },
        )

        async with client as ac:
            response = await ac.post(
                "/api/test-center/config/main/form",
                json={"updates": {"storage.work_dir": "/tmp/work"}},
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["applied_updates"]["storage.work_dir"] == "/tmp/work"

    @pytest.mark.anyio
    async def test_save_main_config_returns_backup_metadata(self, app, client, monkeypatch):
        from vat.web.routes.test_center import router

        app.include_router(router)
        monkeypatch.setattr(
            "vat.web.routes.test_center.test_center_service.save_main_config_text",
            lambda text: {
                "path": "/tmp/config.yaml",
                "backup": {"name": "config.yaml.20260405_120000.bak"},
                "reloaded": True,
            },
        )

        async with client as ac:
            response = await ac.post(
                "/api/test-center/config/main",
                json={"content": "storage:\\n  work_dir: ./work\\n"},
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["reloaded"] is True
        assert payload["backup"]["name"].endswith(".bak")

    @pytest.mark.anyio
    async def test_get_upload_config_state_returns_reference_and_backups(self, app, client, monkeypatch):
        from vat.web.routes.test_center import router

        app.include_router(router)
        monkeypatch.setattr(
            "vat.web.routes.test_center.test_center_service.get_upload_config_editor_state",
            lambda: {
                "path": "/tmp/upload.yaml",
                "yaml_text": "bilibili:\\n  default_tid: 21\\n",
                "structured_data": {"bilibili": {"default_tid": 21}},
                "reference_items": [{"path": "bilibili.default_tid", "type": "int"}],
                "backups": [{"name": "upload.yaml.20260405_120000.bak"}],
            },
        )

        async with client as ac:
            response = await ac.get("/api/test-center/config/upload")

        assert response.status_code == 200
        payload = response.json()
        assert payload["path"] == "/tmp/upload.yaml"
        assert payload["structured_data"]["bilibili"]["default_tid"] == 21
        assert payload["reference_items"][0]["path"] == "bilibili.default_tid"

    @pytest.mark.anyio
    async def test_save_upload_config_returns_backup_metadata(self, app, client, monkeypatch):
        from vat.web.routes.test_center import router

        app.include_router(router)
        monkeypatch.setattr(
            "vat.web.routes.test_center.test_center_service.save_upload_config_text",
            lambda text: {
                "path": "/tmp/upload.yaml",
                "backup": {"name": "upload.yaml.20260405_120000.bak"},
                "reloaded": True,
            },
        )

        async with client as ac:
            response = await ac.post(
                "/api/test-center/config/upload",
                json={"content": "bilibili:\\n  default_tid: 21\\n"},
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["reloaded"] is True
        assert payload["backup"]["name"].endswith(".bak")

    @pytest.mark.anyio
    async def test_save_upload_config_form_returns_backup_metadata(self, app, client, monkeypatch):
        from vat.web.routes.test_center import router

        app.include_router(router)
        monkeypatch.setattr(
            "vat.web.routes.test_center.test_center_service.save_upload_config_form_data",
            lambda updates: {
                "path": "/tmp/upload.yaml",
                "backup": {"name": "upload.yaml.20260405_120000.bak"},
                "reloaded": True,
                "applied_updates": updates,
            },
        )

        async with client as ac:
            response = await ac.post(
                "/api/test-center/config/upload/form",
                json={"updates": {"bilibili.default_tid": 22}},
            )

        assert response.status_code == 200
        assert response.json()["applied_updates"]["bilibili.default_tid"] == 22

    @pytest.mark.anyio
    async def test_restore_upload_config_calls_service(self, app, client, monkeypatch):
        from vat.web.routes.test_center import router

        captured = {}
        app.include_router(router)

        def fake_restore(backup_name):
            captured["backup_name"] = backup_name
            return {"restored": True, "backup_name": backup_name}

        monkeypatch.setattr(
            "vat.web.routes.test_center.test_center_service.restore_upload_config_backup",
            fake_restore,
        )

        async with client as ac:
            response = await ac.post(
                "/api/test-center/config/upload/restore",
                json={"backup_name": "upload.yaml.20260405_120000.bak"},
            )

        assert response.status_code == 200
        assert captured["backup_name"] == "upload.yaml.20260405_120000.bak"
        assert response.json()["restored"] is True

    @pytest.mark.anyio
    async def test_restore_main_config_calls_service(self, app, client, monkeypatch):
        from vat.web.routes.test_center import router

        captured = {}
        app.include_router(router)

        def fake_restore(backup_name):
            captured["backup_name"] = backup_name
            return {"restored": True, "backup_name": backup_name}

        monkeypatch.setattr(
            "vat.web.routes.test_center.test_center_service.restore_main_config_backup",
            fake_restore,
        )

        async with client as ac:
            response = await ac.post(
                "/api/test-center/config/main/restore",
                json={"backup_name": "config.yaml.20260405_120000.bak"},
            )

        assert response.status_code == 200
        assert captured["backup_name"] == "config.yaml.20260405_120000.bak"
        assert response.json()["restored"] is True

    @pytest.mark.anyio
    async def test_run_ffmpeg_check_calls_service(self, app, client, monkeypatch):
        from vat.web.routes.test_center import router

        app.include_router(router)
        monkeypatch.setattr(
            "vat.web.routes.test_center.test_center_service.run_ffmpeg_check",
            lambda: {"ok": True, "ffmpeg": {"available": True}},
        )

        async with client as ac:
            response = await ac.post("/api/test-center/environment/ffmpeg")

        assert response.status_code == 200
        assert response.json()["ffmpeg"]["available"] is True

    @pytest.mark.anyio
    async def test_run_whisper_check_calls_service(self, app, client, monkeypatch):
        from vat.web.routes.test_center import router

        app.include_router(router)
        monkeypatch.setattr(
            "vat.web.routes.test_center.test_center_service.run_whisper_check",
            lambda: {"ok": True, "transcription": {"segments": 0}, "model_source": {"repo_id": "large-v3"}},
        )

        async with client as ac:
            response = await ac.post("/api/test-center/environment/whisper")

        assert response.status_code == 200
        assert response.json()["transcription"]["segments"] == 0
        assert response.json()["model_source"]["repo_id"] == "large-v3"
