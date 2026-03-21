"""Bilibili 认证 helper 契约测试。"""

import json

import pytest

from vat.uploaders.bilibili import BilibiliUploader


class _CookieJar:
    def __init__(self):
        self.set_calls = []

    def set(self, name, value, domain=None):
        self.set_calls.append((name, value, domain))


class _Session:
    def __init__(self):
        self.cookies = _CookieJar()
        self.headers = {}


class TestLoadCookieContracts:
    def test_load_cookie_raises_when_file_missing(self, tmp_path):
        uploader = BilibiliUploader.__new__(BilibiliUploader)
        uploader.cookies_file = tmp_path / "missing.json"
        uploader._cookie_loaded = False

        with pytest.raises(FileNotFoundError):
            uploader._load_cookie()

    def test_load_cookie_extracts_required_fields_from_stream_gears_format(self, tmp_path):
        cookies_file = tmp_path / "cookies.json"
        cookies_file.write_text(json.dumps({
            "cookie_info": {
                "cookies": [
                    {"name": "SESSDATA", "value": "sess"},
                    {"name": "bili_jct", "value": "csrf"},
                    {"name": "DedeUserID", "value": "uid"},
                    {"name": "ignored", "value": "x"},
                ]
            },
            "token_info": {"access_token": "token-123"},
        }), encoding="utf-8")

        uploader = BilibiliUploader.__new__(BilibiliUploader)
        uploader.cookies_file = cookies_file
        uploader._cookie_loaded = False

        uploader._load_cookie()

        assert uploader.cookie_data == {
            "SESSDATA": "sess",
            "bili_jct": "csrf",
            "DedeUserID": "uid",
            "access_token": "token-123",
        }
        assert uploader._raw_cookie_data["token_info"]["access_token"] == "token-123"
        assert uploader._cookie_loaded is True


class TestAuthenticatedSessionContracts:
    def test_get_authenticated_session_sets_cookie_domain_and_headers(self, tmp_path, monkeypatch):
        uploader = BilibiliUploader.__new__(BilibiliUploader)
        uploader.cookie_data = {
            "SESSDATA": "sess",
            "bili_jct": "csrf",
            "DedeUserID": "uid",
        }
        uploader._load_cookie = lambda: None

        monkeypatch.setattr("requests.Session", _Session)

        session = uploader._get_authenticated_session()

        assert session.cookies.set_calls == [
            ("SESSDATA", "sess", ".bilibili.com"),
            ("bili_jct", "csrf", ".bilibili.com"),
            ("DedeUserID", "uid", ".bilibili.com"),
        ]
        assert "user-agent" in session.headers
        assert session.headers["referer"] == "https://member.bilibili.com/"
        assert session.headers["origin"] == "https://member.bilibili.com"
