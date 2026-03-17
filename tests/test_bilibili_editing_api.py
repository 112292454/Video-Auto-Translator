"""Bilibili 编辑相关 API 契约测试。"""

from unittest.mock import MagicMock

from vat.uploaders.bilibili import BilibiliUploader


def _make_response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    return resp


def _make_uploader():
    uploader = BilibiliUploader.__new__(BilibiliUploader)
    uploader.cookie_data = {"bili_jct": "csrf-token"}
    uploader._get_authenticated_session = MagicMock()
    return uploader


class TestEditVideoInfoContracts:
    def test_returns_false_when_archive_view_unavailable(self):
        uploader = _make_uploader()
        session = MagicMock()
        session.get.return_value = _make_response({"code": -404})
        uploader._get_authenticated_session.return_value = session

        assert uploader.edit_video_info(12345, title="新标题") is False

    def test_returns_false_when_videos_empty(self):
        uploader = _make_uploader()
        session = MagicMock()
        session.get.return_value = _make_response({
            "code": 0,
            "data": {
                "archive": {"title": "原标题", "desc": "简介", "tid": 21, "copyright": 1},
                "videos": [],
            },
        })
        uploader._get_authenticated_session.return_value = session

        assert uploader.edit_video_info(12345, title="新标题") is False

    def test_uses_public_detail_tags_and_longer_desc_when_tags_not_passed(self):
        uploader = _make_uploader()
        session = MagicMock()
        session.get.return_value = _make_response({
            "code": 0,
            "data": {
                "archive": {
                    "title": "原标题",
                    "desc": "短简介",
                    "tid": 21,
                    "copyright": 1,
                    "source": "",
                    "cover": "cover.jpg",
                },
                "videos": [
                    {"filename": "f1", "title": "P1", "desc": "", "cid": 888},
                ],
            },
        })
        session.post.return_value = _make_response({"code": 0})
        uploader._get_authenticated_session.return_value = session
        uploader.get_video_detail = MagicMock(return_value={
            "tag": [{"tag_name": "标签A"}, {"tag_name": "标签B"}],
            "desc": "这是更长的完整简介",
        })

        result = uploader.edit_video_info(12345, title="新标题")

        assert result is True
        payload = session.post.call_args.kwargs["json"]
        assert payload["title"] == "新标题"
        assert payload["tag"] == "标签A,标签B"
        assert payload["desc"] == "这是更长的完整简介"
        assert payload["videos"] == [{"filename": "f1", "title": "P1", "desc": "", "cid": 888}]

    def test_prefers_explicit_desc_tags_and_tid_over_existing_values(self):
        uploader = _make_uploader()
        session = MagicMock()
        session.get.return_value = _make_response({
            "code": 0,
            "data": {
                "archive": {
                    "title": "原标题",
                    "desc": "旧简介",
                    "tid": 21,
                    "copyright": 1,
                    "source": "",
                    "cover": "cover.jpg",
                },
                "videos": [
                    {"filename": "f1", "title": "P1", "desc": "", "cid": 888},
                ],
            },
        })
        session.post.return_value = _make_response({"code": 0})
        uploader._get_authenticated_session.return_value = session
        uploader.get_video_detail = MagicMock(return_value={"tag": "旧标签", "desc": "完整简介"})

        result = uploader.edit_video_info(
            12345,
            title="新标题",
            desc="显式简介",
            tags=["tag1", "tag2"],
            tid=17,
        )

        assert result is True
        payload = session.post.call_args.kwargs["json"]
        assert payload["desc"] == "显式简介"
        assert payload["tag"] == "tag1,tag2"
        assert payload["tid"] == 17
