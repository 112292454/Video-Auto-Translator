"""web 测试中心 API 契约测试。"""

from datetime import datetime

import httpx
import pytest
from fastapi import FastAPI

from vat.web.jobs import JobStatus, WebJob


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


def _make_job(
    job_id,
    *,
    status=JobStatus.RUNNING,
    progress=0.0,
    error=None,
    task_type="test-center",
    task_params=None,
):
    return WebJob(
        job_id=job_id,
        video_ids=[],
        steps=[task_type],
        gpu_device="auto",
        force=False,
        status=status,
        pid=4321 if status == JobStatus.RUNNING else None,
        log_file=None,
        progress=progress,
        error=error,
        created_at=datetime(2026, 4, 5, 12, 0, 0),
        started_at=None,
        finished_at=None,
        task_type=task_type,
        task_params=task_params or {},
        cancel_requested=False,
    )


class _FakeJobManager:
    def __init__(self):
        self.jobs = {}
        self.results = {}
        self.submit_tools_calls = []
        self.update_calls = []
        self.log_lines = {}

    def submit_job(self, **kwargs):
        raise AssertionError("test-center route 应通过 submit_tools_job 边界提交 tools 任务")

    def submit_tools_job(self, **kwargs):
        self.submit_tools_calls.append(kwargs)
        job_id = f"job-{len(self.submit_tools_calls)}"
        self.jobs[job_id] = _make_job(
            job_id,
            status=JobStatus.RUNNING,
            task_type=kwargs.get("task_type", "test-center"),
            task_params=kwargs.get("task_params", {}),
        )
        return job_id

    def update_job_status(self, job_id):
        self.update_calls.append(job_id)

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def get_result_payload(self, job_id):
        return self.results.get(job_id)

    def get_log_content(self, job_id, tail_lines=3):
        return self.log_lines.get(job_id, [])

    def find_latest_job(self, task_type, task_params_subset=None, statuses=None, limit=200):
        expected = task_params_subset or {}
        allowed = set(statuses or [])
        for job in self.jobs.values():
            if job.task_type != task_type:
                continue
            if allowed and job.status not in allowed:
                continue
            params = job.task_params or {}
            if all(params.get(key) == value for key, value in expected.items()):
                return job
        return None




class TestJobSupportContracts:
    def test_submit_or_reuse_job_reuses_active_job(self):
        from vat.web.routes.job_support import submit_or_reuse_job

        job_manager = _FakeJobManager()
        running_job = _make_job(
            "job-existing",
            status=JobStatus.RUNNING,
            task_params={"kind": "llm", "target_id": "translate"},
        )
        job_manager.jobs[running_job.job_id] = running_job

        result = submit_or_reuse_job(
            job_manager,
            task_type="test-center",
            task_params={"kind": "llm", "target_id": "translate"},
            active_statuses=[JobStatus.PENDING, JobStatus.RUNNING],
            steps=["test-center"],
            limit=20,
        )

        assert result == {"job_id": "job-existing", "status": "running"}
        assert job_manager.update_calls == ["job-existing"]
        assert job_manager.submit_tools_calls == []


    def test_submit_or_reuse_job_uses_submit_tools_boundary(self):
        from vat.web.routes.job_support import submit_or_reuse_job

        job_manager = _FakeJobManager()

        result = submit_or_reuse_job(
            job_manager,
            task_type="test-center",
            task_params={"kind": "llm-all"},
            steps=["test-center"],
            limit=20,
        )

        assert result == {"job_id": "job-1", "status": "submitted"}


    def test_build_job_status_payload_supports_result_loader(self):
        from vat.web.routes.job_support import build_job_status_payload

        job_manager = _FakeJobManager()
        job_manager.jobs["job-1"] = _make_job(
            "job-1",
            status=JobStatus.COMPLETED,
            progress=1.0,
            error=None,
            task_params={"kind": "llm-all"},
        )
        job_manager.results["job-1"] = {"ok": True}

        payload = build_job_status_payload(
            job_manager,
            "job-1",
            result_loader=job_manager.get_result_payload,
        )

        assert payload == {
            "job_id": "job-1",
            "status": "completed",
            "progress": 1.0,
            "message": "",
            "result": {"ok": True},
        }

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
    async def test_run_llm_target_submits_background_job(self, app, client, monkeypatch):
        from vat.web.routes.test_center import router

        job_manager = _FakeJobManager()
        app.include_router(router)
        monkeypatch.setattr("vat.web.routes.test_center.get_job_manager", lambda: job_manager, raising=False)
        monkeypatch.setattr(
            "vat.web.routes.test_center.test_center_service.run_llm_connectivity_test",
            lambda target_id: (_ for _ in ()).throw(AssertionError("should not call service inline")),
        )

        async with client as ac:
            response = await ac.post("/api/test-center/llm/run", json={"target_id": "translate"})

        assert response.status_code == 200
        assert response.json()["job_id"] == "job-1"
        assert response.json()["status"] == "submitted"
        assert job_manager.submit_tools_calls == [
            {
                "task_type": "test-center",
                "task_params": {"kind": "llm", "target_id": "translate"},
                "steps": ["test-center"],
            }
        ]

    @pytest.mark.anyio
    async def test_run_llm_target_reuses_existing_running_job(self, app, client, monkeypatch):
        from vat.web.routes.test_center import router

        job_manager = _FakeJobManager()
        app.include_router(router)
        running_job = _make_job(
            "job-existing",
            status=JobStatus.RUNNING,
            task_params={"kind": "llm", "target_id": "translate"},
        )
        job_manager.jobs[running_job.job_id] = running_job
        monkeypatch.setattr("vat.web.routes.test_center.get_job_manager", lambda: job_manager, raising=False)

        async with client as ac:
            response = await ac.post("/api/test-center/llm/run", json={"target_id": "translate"})

        assert response.status_code == 200
        assert response.json()["job_id"] == "job-existing"
        assert response.json()["status"] == "running"
        assert job_manager.submit_tools_calls == []


    @pytest.mark.anyio
    async def test_run_all_llm_targets_submits_background_job(self, app, client, monkeypatch):
        from vat.web.routes.test_center import router

        job_manager = _FakeJobManager()
        app.include_router(router)
        monkeypatch.setattr("vat.web.routes.test_center.get_job_manager", lambda: job_manager, raising=False)

        async with client as ac:
            response = await ac.post("/api/test-center/llm/run-all")

        assert response.status_code == 200
        assert response.json()["job_id"] == "job-1"
        assert response.json()["status"] == "submitted"
        assert job_manager.submit_tools_calls[0]["task_params"] == {"kind": "llm-all"}

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

        job_manager = _FakeJobManager()
        app.include_router(router)
        monkeypatch.setattr("vat.web.routes.test_center.get_job_manager", lambda: job_manager, raising=False)

        async with client as ac:
            response = await ac.post("/api/test-center/environment/ffmpeg")

        assert response.status_code == 200
        assert response.json()["job_id"] == "job-1"
        assert response.json()["status"] == "submitted"
        assert job_manager.submit_tools_calls[0]["task_params"] == {"kind": "ffmpeg"}

    @pytest.mark.anyio
    async def test_run_whisper_check_submits_background_job(self, app, client, monkeypatch):
        from vat.web.routes.test_center import router

        job_manager = _FakeJobManager()
        app.include_router(router)
        monkeypatch.setattr("vat.web.routes.test_center.get_job_manager", lambda: job_manager, raising=False)

        async with client as ac:
            response = await ac.post("/api/test-center/environment/whisper")

        assert response.status_code == 200
        assert response.json()["job_id"] == "job-1"
        assert response.json()["status"] == "submitted"
        assert job_manager.submit_tools_calls[0]["task_params"] == {"kind": "whisper"}

    @pytest.mark.anyio
    async def test_run_video_probe_submits_background_job(self, app, client, monkeypatch):
        from vat.web.routes.test_center import router

        job_manager = _FakeJobManager()
        app.include_router(router)
        monkeypatch.setattr("vat.web.routes.test_center.get_job_manager", lambda: job_manager, raising=False)

        async with client as ac:
            response = await ac.post(
                "/api/test-center/environment/video-probe",
                json={"video_id": "vid-1", "path": "/tmp/sample.mp4"},
            )

        assert response.status_code == 200
        assert response.json()["job_id"] == "job-1"
        assert response.json()["status"] == "submitted"
        assert job_manager.submit_tools_calls[0]["task_params"] == {
            "kind": "video-probe",
            "video_id": "vid-1",
            "path": "/tmp/sample.mp4",
        }

    @pytest.mark.anyio
    async def test_get_test_job_status_returns_result_payload(self, app, client, monkeypatch):
        from vat.web.routes.test_center import router

        job_manager = _FakeJobManager()
        app.include_router(router)
        job = _make_job(
            "job-1",
            status=JobStatus.COMPLETED,
            progress=1.0,
            task_params={"kind": "whisper"},
        )
        job_manager.jobs[job.job_id] = job
        job_manager.results[job.job_id] = {"ok": True, "transcription": {"segments": 3}}
        monkeypatch.setattr("vat.web.routes.test_center.get_job_manager", lambda: job_manager, raising=False)

        async with client as ac:
            response = await ac.get("/api/test-center/jobs/job-1")

        assert response.status_code == 200
        assert response.json() == {
            "job_id": "job-1",
            "status": "completed",
            "progress": 1.0,
            "message": "",
            "result": {"ok": True, "transcription": {"segments": 3}},
        }
