"""B站 Web API 契约测试。"""

import os
import tempfile
from types import SimpleNamespace
from datetime import datetime

import httpx
import pytest
from fastapi import FastAPI

os.environ["HOME"] = os.path.join(tempfile.gettempdir(), "vat-test-home")

from vat.web.routes.bilibili import router
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


class _FakeDatabase:
    def __init__(self, db_path, output_base_dir=None):
        self.db_path = db_path
        self.output_base_dir = output_base_dir


class _FakeJobManager:
    def __init__(self):
        self.jobs = {}
        self.update_calls = []
        self.log_lines = {}
        self.submit_tools_calls = []

    def submit_job(self, **kwargs):
        raise AssertionError("bilibili route 应通过 submit_tools_job 边界提交 tools 任务")

    def submit_tools_job(self, **kwargs):
        self.submit_tools_calls.append(kwargs)
        return f"job-{len(self.submit_tools_calls)}"

    def update_job_status(self, job_id):
        self.update_calls.append(job_id)

    def get_job(self, job_id):
        return self.jobs.get(job_id)

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


def _make_job(job_id, *, task_type, task_params, status=JobStatus.RUNNING, error=None):
    return WebJob(
        job_id=job_id,
        video_ids=[],
        steps=[task_type],
        gpu_device="auto",
        force=False,
        status=status,
        pid=4321 if status == JobStatus.RUNNING else None,
        log_file=None,
        progress=0.5,
        error=error,
        created_at=datetime(2026, 3, 24, 12, 0, 0),
        started_at=None,
        finished_at=None,
        task_type=task_type,
        task_params=task_params,
        cancel_requested=False,
    )


class TestResyncSeasonInfoRoute:
    @pytest.mark.anyio
    async def test_dispatches_batch_resync_via_threadpool(self, app, client, monkeypatch):
        fake_config = SimpleNamespace(
            storage=SimpleNamespace(database_path="db.sqlite3", output_dir="outputs"),
        )
        fake_uploader = object()
        threadpool_call = {}
        fake_db = _FakeDatabase("db.sqlite3", "outputs")

        app.include_router(router)
        monkeypatch.setattr("vat.web.routes.bilibili.get_web_config", lambda: fake_config)
        monkeypatch.setattr("vat.web.routes.bilibili.get_db", lambda: fake_db)
        monkeypatch.setattr("vat.web.routes.bilibili._get_uploader", lambda with_upload_params=False: fake_uploader)

        def _should_not_be_called_directly(*args, **kwargs):
            raise AssertionError("resync_season_video_infos 应通过线程池调度，而不是直接在 async 路由中调用")

        async def _fake_run_in_threadpool(func, *args, **kwargs):
            threadpool_call["func"] = func
            threadpool_call["args"] = args
            threadpool_call["kwargs"] = kwargs
            return {
                "success": True,
                "season_id": 42,
                "refreshed": 1,
                "failed": 0,
                "skipped": 0,
                "details": [],
                "message": "ok",
            }

        monkeypatch.setattr(
            "vat.web.routes.bilibili.resync_season_video_infos",
            _should_not_be_called_directly,
            raising=False,
        )
        monkeypatch.setattr("vat.web.routes.bilibili.run_in_threadpool", _fake_run_in_threadpool)

        async with client as ac:
            response = await ac.post("/bilibili/season/42/resync-info")

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert threadpool_call["func"] is _should_not_be_called_directly
        assert threadpool_call["kwargs"]["season_id"] == 42
        assert threadpool_call["kwargs"]["delay_seconds"] == 1.0

    @pytest.mark.anyio
    async def test_returns_aggregate_counts_for_completed_batch(self, app, client, monkeypatch):
        fake_config = SimpleNamespace(
            storage=SimpleNamespace(database_path="db.sqlite3", output_dir="outputs"),
        )
        fake_uploader = object()

        app.include_router(router)
        monkeypatch.setattr("vat.web.routes.bilibili.get_web_config", lambda: fake_config)
        monkeypatch.setattr("vat.web.routes.bilibili.Database", _FakeDatabase)
        monkeypatch.setattr("vat.web.routes.bilibili._get_uploader", lambda with_upload_params=False: fake_uploader)
        monkeypatch.setattr(
            "vat.web.routes.bilibili.run_in_threadpool",
            lambda func, *args, **kwargs: __import__("asyncio").sleep(0, result=func(*args, **kwargs)),
        )
        monkeypatch.setattr(
            "vat.web.routes.bilibili.resync_season_video_infos",
            lambda db, uploader, config, season_id, delay_seconds=1.0: {
                "success": True,
                "season_id": season_id,
                "refreshed": 2,
                "failed": 1,
                "skipped": 0,
                "details": [],
                "message": "合集 42 元信息同步完成：成功 2，失败 1，跳过 0",
            },
            raising=False,
        )

        async with client as ac:
            response = await ac.post("/bilibili/season/42/resync-info")

        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "season_id": 42,
            "refreshed": 2,
            "failed": 1,
            "skipped": 0,
            "details": [],
            "message": "合集 42 元信息同步完成：成功 2，失败 1，跳过 0",
        }

    @pytest.mark.anyio
    async def test_returns_error_when_batch_setup_fails(self, app, client, monkeypatch):
        fake_config = SimpleNamespace(
            storage=SimpleNamespace(database_path="db.sqlite3", output_dir="outputs"),
        )

        app.include_router(router)
        monkeypatch.setattr("vat.web.routes.bilibili.get_web_config", lambda: fake_config)
        monkeypatch.setattr("vat.web.routes.bilibili.Database", _FakeDatabase)
        monkeypatch.setattr("vat.web.routes.bilibili._get_uploader", lambda with_upload_params=False: object())
        monkeypatch.setattr(
            "vat.web.routes.bilibili.run_in_threadpool",
            lambda func, *args, **kwargs: __import__("asyncio").sleep(0, result=func(*args, **kwargs)),
        )
        monkeypatch.setattr(
            "vat.web.routes.bilibili.resync_season_video_infos",
            lambda db, uploader, config, season_id, delay_seconds=1.0: {
                "success": False,
                "season_id": season_id,
                "refreshed": 0,
                "failed": 0,
                "skipped": 0,
                "details": [],
                "message": "无法获取合集信息",
            },
            raising=False,
        )

        async with client as ac:
            response = await ac.post("/bilibili/season/42/resync-info")

        assert response.status_code == 200
        assert response.json() == {
            "success": False,
            "season_id": 42,
            "error": "无法获取合集信息",
            "refreshed": 0,
            "failed": 0,
            "skipped": 0,
            "details": [],
        }


class TestSyncSeasonTitlesRoute:
    @pytest.mark.anyio
    async def test_dispatches_season_title_sync_via_threadpool(self, app, client, monkeypatch):
        fake_config = SimpleNamespace(
            storage=SimpleNamespace(database_path="db.sqlite3", output_dir="outputs"),
        )
        fake_uploader = object()
        threadpool_call = {}
        fake_db = _FakeDatabase("db.sqlite3", "outputs")

        app.include_router(router)
        monkeypatch.setattr("vat.web.routes.bilibili.get_web_config", lambda: fake_config)
        monkeypatch.setattr("vat.web.routes.bilibili.get_db", lambda: fake_db)
        monkeypatch.setattr("vat.web.routes.bilibili._get_uploader", lambda with_upload_params=False: fake_uploader)

        def _should_not_be_called_directly(*args, **kwargs):
            raise AssertionError("sync_season_episode_titles_with_recovery 应通过线程池调度，而不是直接在 async 路由中调用")

        async def _fake_run_in_threadpool(func, *args, **kwargs):
            threadpool_call["func"] = func
            threadpool_call["args"] = args
            threadpool_call["kwargs"] = kwargs
            return {
                "success": True,
                "updated": 2,
                "skipped": 1,
                "details": [],
            }

        monkeypatch.setattr(
            "vat.web.routes.bilibili.sync_season_episode_titles_with_recovery",
            _should_not_be_called_directly,
            raising=False,
        )
        monkeypatch.setattr("vat.web.routes.bilibili.run_in_threadpool", _fake_run_in_threadpool)

        async with client as ac:
            response = await ac.post("/bilibili/season/42/sync-titles")

        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "updated": 2,
            "skipped": 1,
            "message": "合集 42 标题同步完成：2 个已更新，1 个已同步",
        }
        assert threadpool_call["func"] is _should_not_be_called_directly
        assert threadpool_call["args"][0] is fake_db
        assert threadpool_call["args"][1] is fake_uploader
        assert threadpool_call["args"][2] == 42


class TestBilibiliJobStatusRoutes:
    @pytest.mark.anyio
    async def test_rejected_list_derives_fix_status_from_web_jobs(self, app, client, monkeypatch):
        job_manager = _FakeJobManager()
        job_manager.jobs["job-fix"] = _make_job(
            "job-fix",
            task_type="fix-violation",
            task_params={"aid": 123},
            status=JobStatus.RUNNING,
        )

        class _FakeUploader:
            def get_rejected_videos(self, keyword=""):
                return [{
                    "aid": 123,
                    "bvid": "BV1xx",
                    "title": "测试视频",
                    "reject_reason": "违规",
                    "problems": [{
                        "reason": "违规",
                        "violation_time": "00:01",
                        "violation_position": "片头",
                        "modify_advise": "遮罩",
                        "time_ranges": [[1, 3]],
                        "is_full_video": False,
                    }],
                }]

        app.include_router(router)
        monkeypatch.setattr("vat.web.routes.bilibili._get_job_manager", lambda: job_manager)
        monkeypatch.setattr("vat.web.routes.bilibili._get_uploader", lambda with_upload_params=False: _FakeUploader())

        async with client as ac:
            response = await ac.get("/bilibili/rejected")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["videos"][0]["fix_status"] == "masking"

    @pytest.mark.anyio
    async def test_rejected_list_ignores_completed_fix_job_for_still_rejected_video(self, app, client, monkeypatch):
        job_manager = _FakeJobManager()
        job_manager.jobs["job-fix"] = _make_job(
            "job-fix",
            task_type="fix-violation",
            task_params={"aid": 123},
            status=JobStatus.COMPLETED,
        )

        class _FakeUploader:
            def get_rejected_videos(self, keyword=""):
                return [{
                    "aid": 123,
                    "bvid": "BV1xx",
                    "title": "测试视频",
                    "reject_reason": "违规",
                    "problems": [{
                        "reason": "违规",
                        "violation_time": "00:01",
                        "violation_position": "片头",
                        "modify_advise": "遮罩",
                        "time_ranges": [[1, 3]],
                        "is_full_video": False,
                    }],
                }]

        app.include_router(router)
        monkeypatch.setattr("vat.web.routes.bilibili._get_job_manager", lambda: job_manager)
        monkeypatch.setattr("vat.web.routes.bilibili._get_uploader", lambda with_upload_params=False: _FakeUploader())

        async with client as ac:
            response = await ac.get("/bilibili/rejected")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["videos"][0]["fix_status"] is None

    @pytest.mark.anyio
    async def test_fix_status_can_resolve_job_without_legacy_memory_state(self, app, client, monkeypatch):
        job_manager = _FakeJobManager()
        job_manager.jobs["job-fix"] = _make_job(
            "job-fix",
            task_type="fix-violation",
            task_params={"aid": 123},
            status=JobStatus.RUNNING,
        )

        app.include_router(router)
        monkeypatch.setattr("vat.web.routes.bilibili._get_job_manager", lambda: job_manager)

        async with client as ac:
            response = await ac.get("/bilibili/fix/123/status")

        assert response.status_code == 200
        assert response.json()["status"] == "masking"
        assert response.json()["job_id"] == "job-fix"
        assert job_manager.update_calls == ["job-fix"]

    @pytest.mark.anyio
    async def test_season_sync_status_can_resolve_job_without_legacy_memory_state(self, app, client, monkeypatch):
        job_manager = _FakeJobManager()
        job_manager.jobs["job-sync"] = _make_job(
            "job-sync",
            task_type="season-sync",
            task_params={"playlist_id": "PL1"},
            status=JobStatus.FAILED,
            error="sync failed",
        )

        app.include_router(router)
        monkeypatch.setattr("vat.web.routes.bilibili._get_job_manager", lambda: job_manager)

        async with client as ac:
            response = await ac.get("/bilibili/season-sync/PL1/status")

        assert response.status_code == 200
        assert response.json()["status"] == "failed"
        assert response.json()["job_id"] == "job-sync"
        assert response.json()["message"] == "sync failed"
        assert job_manager.update_calls == ["job-sync"]


class TestBilibiliJobSubmissionRoutes:
    @pytest.mark.anyio
    async def test_fix_route_submits_tools_job(self, app, client, monkeypatch, tmp_path):
        job_manager = _FakeJobManager()
        video_path = tmp_path / "local.mp4"
        video_path.write_text("video")

        app.include_router(router)
        monkeypatch.setattr("vat.web.routes.bilibili._get_job_manager", lambda: job_manager)
        monkeypatch.setattr("vat.web.routes.bilibili._find_local_video", lambda aid: video_path)

        async with client as ac:
            response = await ac.post(
                "/bilibili/fix/123",
                json={
                    "margin": 2.0,
                    "mask_text": "自定义遮罩",
                },
            )

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["job_id"] == "job-1"
        assert response.json()["video_path"] == str(video_path)
        assert job_manager.submit_tools_calls == [{
            "task_type": "fix-violation",
            "task_params": {
                "aid": 123,
                "margin": 2.0,
                "mask_text": "自定义遮罩",
                "video_path": str(video_path),
            },
            "steps": ["fix-violation"],
        }]

    @pytest.mark.anyio
    async def test_season_sync_route_submits_tools_job(self, app, client, monkeypatch):
        job_manager = _FakeJobManager()
        fake_db = SimpleNamespace(update_video=lambda *args, **kwargs: None)
        fake_playlist = SimpleNamespace(
            id="PL1",
            metadata={"upload_config": {"season_id": 42}},
        )
        fake_video = SimpleNamespace(id="vid-1", metadata={"bilibili_aid": 1001})

        class _FakePlaylistService:
            def __init__(self, db):
                self.db = db

            def get_playlist(self, playlist_id):
                assert playlist_id == "PL1"
                return fake_playlist

            def get_playlist_videos(self, playlist_id):
                assert playlist_id == "PL1"
                return [fake_video]

        app.include_router(router)
        monkeypatch.setattr("vat.web.routes.bilibili._get_job_manager", lambda: job_manager)
        monkeypatch.setattr("vat.web.routes.bilibili.get_db", lambda: fake_db)
        monkeypatch.setattr("vat.web.routes.bilibili.get_upload_config_manager", lambda: SimpleNamespace(load=lambda: SimpleNamespace(bilibili=SimpleNamespace(season_id=None))))
        monkeypatch.setattr("vat.services.playlist_service.PlaylistService", _FakePlaylistService)

        async with client as ac:
            response = await ac.post("/bilibili/season-sync/PL1")

        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "message": "同步任务已启动 (playlist=PL1, season=42)",
            "job_id": "job-1",
            "patched": 1,
        }
        assert job_manager.submit_tools_calls == [{
            "task_type": "season-sync",
            "task_params": {
                "playlist_id": "PL1",
            },
            "steps": ["season-sync"],
        }]
