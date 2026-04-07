"""
Watch API 路由单元测试

测试 vat.web.routes.watch 的 API 端点：
- GET /api/watch/sessions — 列表
- GET /api/watch/sessions/{id} — 详情
- GET /api/watch/sessions/{id}/rounds — 轮次记录
- DELETE /api/watch/sessions/{id} — 删除
- POST /api/watch/sessions/{id}/stop — 停止
"""

import json
import sqlite3
import os
from datetime import datetime
from unittest.mock import patch, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from vat.web.routes.watch import router


# ========== Fixture ==========

@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    """限制 anyio 只使用 asyncio 后端"""
    return request.param


@pytest.fixture
def test_db(tmp_path):
    """创建带 watch 表的临时数据库，返回 mock Database 对象"""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    
    conn.execute("""
        CREATE TABLE watch_sessions (
            session_id TEXT PRIMARY KEY,
            playlist_ids TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            pid INTEGER,
            config TEXT,
            started_at TIMESTAMP NOT NULL,
            last_check_at TIMESTAMP,
            next_check_at TIMESTAMP,
            total_rounds INTEGER DEFAULT 0,
            total_new_found INTEGER DEFAULT 0,
            total_jobs_submitted INTEGER DEFAULT 0,
            error TEXT,
            stopped_at TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE watch_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            round_number INTEGER NOT NULL,
            playlist_id TEXT NOT NULL,
            started_at TIMESTAMP NOT NULL,
            finished_at TIMESTAMP,
            new_videos_found INTEGER DEFAULT 0,
            jobs_submitted INTEGER DEFAULT 0,
            submitted_video_ids TEXT,
            submitted_job_ids TEXT,
            retry_video_ids TEXT,
            error TEXT
        )
    """)
    conn.commit()
    conn.close()
    
    # 创建 mock Database 对象
    mock_db = MagicMock()
    
    class _ConnCtx:
        """模拟 db.get_connection() 上下文管理器"""
        def __enter__(self_inner):
            self_inner._conn = sqlite3.connect(db_path)
            self_inner._conn.row_factory = sqlite3.Row
            return self_inner._conn
        def __exit__(self_inner, *args):
            self_inner._conn.commit()
            self_inner._conn.close()
    
    mock_db.get_connection = lambda: _ConnCtx()
    mock_db.db_path = db_path
    
    return mock_db, db_path


@pytest.fixture
def client(test_db):
    """创建 httpx.AsyncClient，mock get_db"""
    mock_db, db_path = test_db
    
    app = FastAPI()
    app.include_router(router)
    
    transport = httpx.ASGITransport(app=app)
    
    with patch('vat.web.routes.watch.get_db', return_value=mock_db):
        yield httpx.AsyncClient(transport=transport, base_url="http://test"), db_path


def _insert_session(db_path, session_id, playlist_ids, status='running', pid=None):
    """插入测试 session 记录"""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT INTO watch_sessions (session_id, playlist_ids, status, pid, config, started_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        session_id,
        json.dumps(playlist_ids),
        status,
        pid or os.getpid(),
        json.dumps({'interval': 60}),
        datetime.now().isoformat(),
    ))
    conn.commit()
    conn.close()


def _insert_round(db_path, session_id, round_number, playlist_id, **kwargs):
    """插入测试 round 记录"""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT INTO watch_rounds (session_id, round_number, playlist_id, started_at,
                                  finished_at, new_videos_found, jobs_submitted, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        session_id,
        round_number,
        playlist_id,
        kwargs.get('started_at', datetime.now().isoformat()),
        kwargs.get('finished_at', datetime.now().isoformat()),
        kwargs.get('new_videos_found', 0),
        kwargs.get('jobs_submitted', 0),
        kwargs.get('error'),
    ))
    conn.commit()
    conn.close()


# ========== 测试 ==========

class TestListSessions:
    
    @pytest.mark.anyio
    async def test_empty_list(self, client):
        tc, _ = client
        resp = await tc.get("/api/watch/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert data['sessions'] == []
    
    @pytest.mark.anyio
    async def test_list_with_sessions(self, client):
        tc, db_path = client
        _insert_session(db_path, "s1", ["PL_A"], status="stopped", pid=1)
        _insert_session(db_path, "s2", ["PL_B", "PL_C"], status="stopped", pid=2)
        
        resp = await tc.get("/api/watch/sessions")
        assert resp.status_code == 200
        sessions = resp.json()['sessions']
        assert len(sessions) == 2
        ids = [s['session_id'] for s in sessions]
        assert "s1" in ids
        assert "s2" in ids
    
    @pytest.mark.anyio
    async def test_filter_by_status(self, client):
        tc, db_path = client
        _insert_session(db_path, "s1", ["PL_A"], status="stopped", pid=1)
        _insert_session(db_path, "s2", ["PL_B"], status="error", pid=2)
        
        resp = await tc.get("/api/watch/sessions?status=stopped")
        sessions = resp.json()['sessions']
        assert len(sessions) == 1
        assert sessions[0]['session_id'] == "s1"
    
    @pytest.mark.anyio
    async def test_playlist_ids_parsed(self, client):
        tc, db_path = client
        _insert_session(db_path, "s1", ["PL_A", "PL_B"], status="stopped", pid=1)
        
        resp = await tc.get("/api/watch/sessions")
        sess = resp.json()['sessions'][0]
        assert sess['playlist_ids'] == ["PL_A", "PL_B"]
        assert isinstance(sess['config'], dict)


class TestGetSession:
    
    @pytest.mark.anyio
    async def test_get_existing(self, client):
        tc, db_path = client
        _insert_session(db_path, "s1", ["PL_A"], status="stopped", pid=1)
        
        resp = await tc.get("/api/watch/sessions/s1")
        assert resp.status_code == 200
        assert resp.json()['session_id'] == "s1"
    
    @pytest.mark.anyio
    async def test_get_nonexistent(self, client):
        tc, _ = client
        resp = await tc.get("/api/watch/sessions/nonexistent")
        assert resp.status_code == 404


class TestGetRounds:
    
    @pytest.mark.anyio
    async def test_get_rounds(self, client):
        tc, db_path = client
        _insert_session(db_path, "s1", ["PL_A"], status="stopped", pid=1)
        _insert_round(db_path, "s1", 1, "PL_A", new_videos_found=3, jobs_submitted=1)
        _insert_round(db_path, "s1", 2, "PL_A", error="sync failed")
        
        resp = await tc.get("/api/watch/sessions/s1/rounds")
        assert resp.status_code == 200
        rounds = resp.json()['rounds']
        assert len(rounds) == 2
    
    @pytest.mark.anyio
    async def test_round_with_error(self, client):
        tc, db_path = client
        _insert_session(db_path, "s1", ["PL_A"], status="stopped", pid=1)
        _insert_round(db_path, "s1", 1, "PL_A", error="Network timeout")
        
        resp = await tc.get("/api/watch/sessions/s1/rounds")
        rounds = resp.json()['rounds']
        assert rounds[0]['error'] == "Network timeout"


class TestDeleteSession:
    
    @pytest.mark.anyio
    async def test_delete_stopped_session(self, client):
        tc, db_path = client
        _insert_session(db_path, "s1", ["PL_A"], status="stopped", pid=1)
        _insert_round(db_path, "s1", 1, "PL_A")
        
        resp = await tc.delete("/api/watch/sessions/s1")
        assert resp.status_code == 200
        
        # 验证已删除
        resp2 = await tc.get("/api/watch/sessions/s1")
        assert resp2.status_code == 404
    
    @pytest.mark.anyio
    async def test_delete_running_session_blocked(self, client):
        tc, db_path = client
        _insert_session(db_path, "s1", ["PL_A"], status="running")
        
        resp = await tc.delete("/api/watch/sessions/s1")
        assert resp.status_code == 400
        assert "running" in resp.json()['error']
    
    @pytest.mark.anyio
    async def test_delete_nonexistent(self, client):
        tc, _ = client
        resp = await tc.delete("/api/watch/sessions/nonexistent")
        assert resp.status_code == 404
    
    @pytest.mark.anyio
    async def test_delete_cascades_rounds(self, client):
        """删除 session 同时删除关联的 rounds"""
        tc, db_path = client
        _insert_session(db_path, "s1", ["PL_A"], status="stopped", pid=1)
        _insert_round(db_path, "s1", 1, "PL_A")
        _insert_round(db_path, "s1", 2, "PL_A")
        
        await tc.delete("/api/watch/sessions/s1")
        
        # 验证 rounds 也被删除
        resp = await tc.get("/api/watch/sessions/s1/rounds")
        assert resp.json()['rounds'] == []


class TestStopSession:
    
    @pytest.mark.anyio
    async def test_stop_nonexistent(self, client):
        tc, _ = client
        resp = await tc.post("/api/watch/sessions/nonexistent/stop")
        assert resp.status_code == 404
    
    @pytest.mark.anyio
    async def test_stop_already_stopped(self, client):
        tc, db_path = client
        _insert_session(db_path, "s1", ["PL_A"], status="stopped", pid=1)
        
        resp = await tc.post("/api/watch/sessions/s1/stop")
        assert resp.status_code == 400
    


class _FakeWatchJobManager:
    def __init__(self):
        self.submit_tools_calls = []

    def submit_job(self, **kwargs):
        raise AssertionError("watch route 应通过 submit_tools_job 边界提交 tools 任务")

    def submit_tools_job(self, **kwargs):
        self.submit_tools_calls.append(kwargs)
        return "job-watch-1"


class TestStartWatch:

    @pytest.mark.anyio
    async def test_start_watch_submits_tools_job(self, client, monkeypatch):
        tc, _ = client
        job_manager = _FakeWatchJobManager()

        class _FakePlaylistService:
            def __init__(self, db, config):
                self.db = db
                self.config = config

            def get_playlist(self, playlist_id):
                return {"id": playlist_id}

        monkeypatch.setattr("vat.web.routes.watch._get_job_manager", lambda: job_manager)
        monkeypatch.setattr("vat.web.routes.watch.get_web_config", lambda: object())
        monkeypatch.setattr("vat.services.PlaylistService", _FakePlaylistService)

        resp = await tc.post(
            "/api/watch/start",
            json={
                "playlist_ids": ["PL1", "PL2"],
                "interval": 120,
                "stages": "download,translate",
                "gpu_device": "cuda:0",
                "concurrency": 2,
                "force": True,
                "fail_fast": True,
                "once": True,
            },
        )

        assert resp.status_code == 200
        assert resp.json() == {"job_id": "job-watch-1", "message": "Watch 任务已提交"}
        assert job_manager.submit_tools_calls == [{
            "task_type": "watch",
            "task_params": {
                "playlist_ids": ["PL1", "PL2"],
                "once": True,
                "force": True,
                "fail_fast": True,
                "interval": 120,
                "stages": "download,translate",
                "gpu": "cuda:0",
                "concurrency": 2,
            },
            "steps": [],
        }]
