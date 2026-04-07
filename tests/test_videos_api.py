"""videos API 路由契约测试。"""

import httpx
import pytest
from fastapi import FastAPI

from vat.models import Video, Task, TaskStep, TaskStatus, SourceType
from vat.web.routes.videos import router, get_playlist_service
from vat.web.deps import get_db


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


class _FakeDb:
    def __init__(self):
        self.videos = []
        self.tasks = {}
        self.progress = {}

    def list_videos(self, playlist_id=None):
        return self.videos

    def get_tasks(self, video_id):
        return self.tasks.get(video_id, [])

    def batch_get_video_progress(self, video_ids=None):
        return {vid: self.progress.get(vid, {"progress": 0}) for vid in (video_ids or [])}

    def get_video_playlists(self, video_id):
        return []

    def get_video(self, video_id):
        for video in self.videos:
            if video.id == video_id:
                return video
        return None

    def update_video(self, video_id, **kwargs):
        video = self.get_video(video_id)
        assert video is not None
        for key, value in kwargs.items():
            setattr(video, key, value)


class _FakePlaylistService:
    def __init__(self):
        self.default_playlist_calls = 0
        self.attach_calls = []
        self.fetch_calls = []

    def ensure_default_manual_playlist(self):
        self.default_playlist_calls += 1
        return type("Playlist", (), {"id": "manual-default"})()

    def attach_video_to_playlist(self, video_id, playlist_id):
        self.attach_calls.append((video_id, playlist_id))
        return {"playlist_id": playlist_id, "attached": True, "playlist_index": len(self.attach_calls)}

    def fetch_video_source_info(self, video_id, force=False, submit_translate=True):
        self.fetch_calls.append((video_id, force, submit_translate))
        return {"status": "ok", "video_id": video_id}


@pytest.fixture
def app():
    return FastAPI()


@pytest.fixture
def client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


class TestVideosApiProgress:
    @pytest.mark.anyio
    async def test_list_videos_uses_batch_progress_and_counts_skipped(self, app, client):
        fake_db = _FakeDb()
        video = Video(
            id="v1",
            source_type=SourceType.YOUTUBE,
            source_url="https://youtube.com/watch?v=v1",
            title="Video 1",
            metadata={},
        )
        fake_db.videos = [video]
        fake_db.tasks["v1"] = [
            Task(video_id="v1", step=TaskStep.DOWNLOAD, status=TaskStatus.COMPLETED),
            Task(video_id="v1", step=TaskStep.WHISPER, status=TaskStatus.SKIPPED),
        ]
        fake_db.progress["v1"] = {"progress": 28}

        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: fake_db

        async with client as ac:
            response = await ac.get("/api/videos")

        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 1
        assert payload["videos"][0]["progress"] == 0.28
        assert payload["videos"][0]["tasks"][1]["status"] == "skipped"

    @pytest.mark.anyio
    async def test_add_video_supports_batch_sources_and_default_playlist(self, app, client, monkeypatch):
        fake_db = _FakeDb()
        service = _FakePlaylistService()

        created_sources = []

        def _fake_create_video_from_source(source, db, source_type, title=""):
            created_sources.append((source, source_type, title))
            video = Video(
                id=f"video-{len(created_sources)}",
                source_type=source_type,
                source_url=source,
                title=title or None,
                metadata={},
            )
            fake_db.videos.append(video)
            return video.id

        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: fake_db
        app.dependency_overrides[get_playlist_service] = lambda: service
        monkeypatch.setattr(
            "vat.web.routes.videos.detect_source_type",
            lambda source: SourceType.YOUTUBE if "youtube.com" in source else SourceType.LOCAL,
        )
        monkeypatch.setattr(
            "vat.web.routes.videos.create_video_from_source",
            _fake_create_video_from_source,
        )
        monkeypatch.setattr(
            "vat.web.routes.videos.resolve_video_identity_from_source",
            lambda source, source_type: (source, f"id::{source}"),
        )

        async with client as ac:
            response = await ac.post(
                "/api/videos",
                json={
                    "sources": [
                        "https://www.youtube.com/watch?v=abc",
                        "/tmp/demo.mp4",
                    ],
                    "playlist_mode": "default",
                },
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "created"
        assert payload["count"] == 2
        assert payload["playlist_id"] == "manual-default"
        assert [item["video_id"] for item in payload["items"]] == ["video-1", "video-2"]
        assert created_sources == [
            ("https://www.youtube.com/watch?v=abc", SourceType.YOUTUBE, ""),
            ("/tmp/demo.mp4", SourceType.LOCAL, "demo"),
        ]
        assert service.default_playlist_calls == 1
        assert service.attach_calls == [("video-1", "manual-default"), ("video-2", "manual-default")]
        assert service.fetch_calls == [("video-1", False, True)]

    @pytest.mark.anyio
    async def test_fetch_source_info_endpoint_delegates_to_service(self, app, client):
        fake_db = _FakeDb()
        fake_db.videos = [
            Video(
                id="yt-1",
                source_type=SourceType.YOUTUBE,
                source_url="https://www.youtube.com/watch?v=yt-1",
                title=None,
                metadata={},
            )
        ]
        service = _FakePlaylistService()

        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: fake_db
        app.dependency_overrides[get_playlist_service] = lambda: service

        async with client as ac:
            response = await ac.post("/api/videos/yt-1/fetch-source-info")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert service.fetch_calls == [("yt-1", True, True)]

    @pytest.mark.anyio
    async def test_add_video_rejects_existing_identity_with_conflict(self, app, client, monkeypatch):
        fake_db = _FakeDb()
        fake_db.videos = [
            Video(
                id="yt-existing",
                source_type=SourceType.YOUTUBE,
                source_url="https://www.youtube.com/watch?v=abc",
                title="Already There",
                metadata={},
            )
        ]
        service = _FakePlaylistService()

        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: fake_db
        app.dependency_overrides[get_playlist_service] = lambda: service
        monkeypatch.setattr(
            "vat.web.routes.videos.detect_source_type",
            lambda _source: SourceType.YOUTUBE,
        )
        monkeypatch.setattr(
            "vat.web.routes.videos.resolve_video_identity_from_source",
            lambda source, source_type: (source, "yt-existing"),
        )

        async with client as ac:
            response = await ac.post(
                "/api/videos",
                json={"url": "https://www.youtube.com/watch?v=abc", "playlist_mode": "none"},
            )

        assert response.status_code == 409
        assert "已存在" in response.json()["detail"]

    @pytest.mark.anyio
    async def test_add_video_batch_preflight_rejects_late_conflict_without_partial_creation(self, app, client, monkeypatch):
        fake_db = _FakeDb()
        fake_db.videos = [
            Video(
                id="yt-existing",
                source_type=SourceType.YOUTUBE,
                source_url="https://www.youtube.com/watch?v=dup",
                title="Already There",
                metadata={},
            )
        ]
        service = _FakePlaylistService()

        created_sources = []

        def _fake_create_video_from_source(source, db, source_type, title=""):
            created_sources.append((source, source_type, title))
            video = Video(
                id=f"video-{len(created_sources)}",
                source_type=source_type,
                source_url=source,
                title=title or None,
                metadata={},
            )
            fake_db.videos.append(video)
            return video.id

        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: fake_db
        app.dependency_overrides[get_playlist_service] = lambda: service
        monkeypatch.setattr(
            "vat.web.routes.videos.detect_source_type",
            lambda source: SourceType.YOUTUBE if "youtube.com" in source else SourceType.LOCAL,
        )
        monkeypatch.setattr(
            "vat.web.routes.videos.resolve_video_identity_from_source",
            lambda source, source_type: (
                source,
                "yt-existing" if source.endswith("dup") else f"id::{source}",
            ),
        )
        monkeypatch.setattr(
            "vat.web.routes.videos.create_video_from_source",
            _fake_create_video_from_source,
        )

        async with client as ac:
            response = await ac.post(
                "/api/videos",
                json={
                    "sources": [
                        "/tmp/demo.mp4",
                        "https://www.youtube.com/watch?v=dup",
                    ],
                    "playlist_mode": "none",
                },
            )

        assert response.status_code == 409
        assert "已存在" in response.json()["detail"]
        assert created_sources == []
        assert [video.id for video in fake_db.videos] == ["yt-existing"]

    @pytest.mark.anyio
    async def test_add_video_default_playlist_not_created_when_preflight_fails(self, app, client, monkeypatch):
        fake_db = _FakeDb()
        fake_db.videos = [
            Video(
                id="yt-existing",
                source_type=SourceType.YOUTUBE,
                source_url="https://www.youtube.com/watch?v=dup",
                title="Already There",
                metadata={},
            )
        ]
        service = _FakePlaylistService()

        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: fake_db
        app.dependency_overrides[get_playlist_service] = lambda: service
        monkeypatch.setattr(
            "vat.web.routes.videos.detect_source_type",
            lambda source: SourceType.YOUTUBE if "youtube.com" in source else SourceType.LOCAL,
        )
        monkeypatch.setattr(
            "vat.web.routes.videos.resolve_video_identity_from_source",
            lambda source, source_type: (
                source,
                "yt-existing" if source.endswith("dup") else f"id::{source}",
            ),
        )

        async with client as ac:
            response = await ac.post(
                "/api/videos",
                json={
                    "sources": [
                        "/tmp/demo.mp4",
                        "https://www.youtube.com/watch?v=dup",
                    ],
                    "playlist_mode": "default",
                },
            )

        assert response.status_code == 409
        assert service.default_playlist_calls == 0
