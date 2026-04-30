"""replace_video 契约测试。"""

from pathlib import Path
from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest

from vat.uploaders.bilibili import BilibiliUploader


def _make_response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    return resp


def _make_uploader(tmp_path):
    uploader = BilibiliUploader.__new__(BilibiliUploader)
    uploader.cookie_data = {"bili_jct": "csrf-token"}
    uploader._raw_cookie_data = {"cookie_info": {"cookies": []}}
    uploader.line = "AUTO"
    uploader.threads = 3
    uploader._get_authenticated_session = MagicMock()
    uploader._load_cookie = MagicMock()
    uploader.get_archive_detail = MagicMock()
    uploader.get_video_detail = MagicMock(return_value=None)
    uploader._get_full_desc = MagicMock(return_value=None)
    uploader.cookies_file = tmp_path / "cookies.json"
    uploader.lock_db_path = str(tmp_path / "locks.db")
    uploader._upload_lock = MagicMock(return_value=nullcontext())
    return uploader


class _FakeBiliClient:
    def __init__(self, upload_result):
        self.upload_result = upload_result
        self.login_calls = []
        self.upload_calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def login_by_cookies(self, cookie_data):
        self.login_calls.append(cookie_data)

    def upload_file(self, path, lines, tasks):
        self.upload_calls.append((path, lines, tasks))
        return self.upload_result


class _FakeData:
    def __init__(self):
        self.title = ""
        self.desc = ""


class TestReplaceVideoContracts:
    def test_returns_false_when_archive_detail_missing(self, tmp_path):
        uploader = _make_uploader(tmp_path)
        uploader.get_archive_detail.return_value = None

        assert uploader.replace_video(12345, tmp_path / "new.mp4") is False

    def test_returns_false_when_archive_has_no_videos(self, tmp_path):
        uploader = _make_uploader(tmp_path)
        uploader.get_archive_detail.return_value = {
            "archive": {"title": "稿件"},
            "videos": [],
        }

        assert uploader.replace_video(12345, tmp_path / "new.mp4") is False

    def test_returns_false_when_upload_succeeds_but_filename_missing(self, tmp_path, monkeypatch):
        uploader = _make_uploader(tmp_path)
        uploader.get_archive_detail.return_value = {
            "archive": {"title": "稿件", "desc": "", "tag": "", "tid": 21, "copyright": 1},
            "videos": [{"title": "P1", "desc": "", "filename": "oldfile"}],
        }
        session = MagicMock()
        uploader._get_authenticated_session.return_value = session
        client = _FakeBiliClient(upload_result={"unexpected": "value"})
        monkeypatch.setattr("vat.uploaders.bilibili.BiliBili", lambda _data: client)
        monkeypatch.setattr("vat.uploaders.bilibili.Data", _FakeData)

        assert uploader.replace_video(12345, tmp_path / "new.mp4") is False
        session.post.assert_not_called()

    def test_uses_public_api_tags_and_desc_when_editing(self, tmp_path, monkeypatch):
        uploader = _make_uploader(tmp_path)
        uploader.get_archive_detail.return_value = {
            "archive": {
                "title": "原稿标题",
                "desc": "短简介",
                "tag": "",
                "tid": 21,
                "copyright": 1,
                "source": "",
                "cover": "cover.jpg",
            },
            "videos": [{"title": "P1", "desc": "分P简介", "filename": "oldfile"}],
        }
        uploader.get_video_detail.return_value = {
            "tag": [{"tag_name": "标签A"}, {"tag_name": "标签B"}],
            "desc": "这是公共 API 返回的更长简介",
        }
        uploader._get_full_desc.return_value = "这是通过 _get_full_desc 获取的更加完整而且更长的简介正文"
        session = MagicMock()
        session.post.return_value = _make_response({"code": 0})
        uploader._get_authenticated_session.return_value = session
        client = _FakeBiliClient(upload_result={"filename": "new-file-name"})
        monkeypatch.setattr("vat.uploaders.bilibili.BiliBili", lambda _data: client)
        monkeypatch.setattr("vat.uploaders.bilibili.Data", _FakeData)

        result = uploader.replace_video(12345, tmp_path / "new.mp4")

        assert result is True
        payload = session.post.call_args.kwargs["json"]
        assert payload["tag"] == "标签A,标签B"
        assert payload["desc"] == "这是通过 _get_full_desc 获取的更加完整而且更长的简介正文"
        assert payload["videos"] == [{"filename": "new-file-name", "title": "P1", "desc": "分P简介"}]

    def test_returns_false_when_edit_api_fails_after_upload(self, tmp_path, monkeypatch):
        uploader = _make_uploader(tmp_path)
        uploader.get_archive_detail.return_value = {
            "archive": {
                "title": "原稿标题",
                "desc": "简介",
                "tag": "标签",
                "tid": 21,
                "copyright": 1,
                "source": "",
                "cover": "cover.jpg",
            },
            "videos": [{"title": "P1", "desc": "分P简介", "filename": "oldfile"}],
        }
        session = MagicMock()
        session.post.return_value = _make_response({"code": -1, "message": "edit failed"})
        uploader._get_authenticated_session.return_value = session
        client = _FakeBiliClient(upload_result={"filename": "new-file-name"})
        monkeypatch.setattr("vat.uploaders.bilibili.BiliBili", lambda _data: client)
        monkeypatch.setattr("vat.uploaders.bilibili.Data", _FakeData)

        result = uploader.replace_video(12345, tmp_path / "new.mp4")

        assert result is False
        assert session.post.called is True
