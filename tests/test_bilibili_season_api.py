"""Bilibili 合集 API 底座契约测试。"""

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


class TestGetSeasonEpisodesContracts:
    def test_returns_none_when_seasons_api_fails(self):
        uploader = _make_uploader()
        session = MagicMock()
        session.get.return_value = _make_response({"code": -1, "message": "fail"})
        uploader._get_authenticated_session.return_value = session

        assert uploader.get_season_episodes(42) is None

    def test_returns_none_when_section_id_not_found(self):
        uploader = _make_uploader()
        session = MagicMock()
        session.get.return_value = _make_response({
            "code": 0,
            "data": {"seasons": [{"season": {"id": 99}, "sections": {"sections": [{"id": 7}]}}]},
        })
        uploader._get_authenticated_session.return_value = session

        assert uploader.get_season_episodes(42) is None

    def test_returns_none_when_section_api_fails(self):
        uploader = _make_uploader()
        session = MagicMock()
        session.get.side_effect = [
            _make_response({
                "code": 0,
                "data": {"seasons": [{"season": {"id": 42}, "sections": {"sections": [{"id": 7}]}}]},
            }),
            _make_response({"code": -2, "message": "section fail"}),
        ]
        uploader._get_authenticated_session.return_value = session

        assert uploader.get_season_episodes(42) is None

    def test_returns_section_id_and_episodes_on_success(self):
        uploader = _make_uploader()
        session = MagicMock()
        session.get.side_effect = [
            _make_response({
                "code": 0,
                "data": {"seasons": [{"season": {"id": 42}, "sections": {"sections": [{"id": 7}]}}]},
            }),
            _make_response({"code": 0, "data": {"episodes": [{"aid": 1001}, {"aid": 1002}]}}),
        ]
        uploader._get_authenticated_session.return_value = session

        result = uploader.get_season_episodes(42)

        assert result == {"section_id": 7, "episodes": [{"aid": 1001}, {"aid": 1002}]}


class TestAddToSeasonContracts:
    def test_returns_true_without_readding_when_video_already_in_season(self):
        uploader = _make_uploader()
        session = MagicMock()
        uploader._get_authenticated_session.return_value = session
        uploader.get_season_episodes = MagicMock(return_value={
            "section_id": 7,
            "episodes": [{"aid": 1001, "id": 11}],
        })

        result = uploader.add_to_season(1001, 42)

        assert result is True
        session.get.assert_not_called()
        session.post.assert_not_called()

    def test_fallback_to_creative_api_when_public_api_cannot_resolve_cid(self):
        uploader = _make_uploader()
        session = MagicMock()
        session.get.side_effect = [
            _make_response({"code": -404, "message": "not found"}),
            _make_response({
                "code": 0,
                "data": {
                    "archive": {"title": "标题"},
                    "videos": [{"cid": 999}],
                },
            }),
        ]
        session.post.return_value = _make_response({"code": 0})
        uploader._get_authenticated_session.return_value = session
        uploader.get_season_episodes = MagicMock(return_value={
            "section_id": 7,
            "episodes": [],
        })

        result = uploader.add_to_season(1002, 42)

        assert result is True
        payload = session.post.call_args.kwargs["json"]
        assert payload["sectionId"] == 7
        assert payload["episodes"][0]["aid"] == 1002
        assert payload["episodes"][0]["cid"] == 999
        assert payload["episodes"][0]["title"] == "标题"

    def test_returns_false_when_cid_cannot_be_resolved(self):
        uploader = _make_uploader()
        session = MagicMock()
        session.get.side_effect = [
            _make_response({"code": -404, "message": "not found"}),
            _make_response({"code": -404, "message": "still not found"}),
        ]
        uploader._get_authenticated_session.return_value = session
        uploader.get_season_episodes = MagicMock(return_value={
            "section_id": 7,
            "episodes": [],
        })

        result = uploader.add_to_season(1003, 42)

        assert result is False
        session.post.assert_not_called()


class TestRemoveFromSeasonContracts:
    def test_returns_true_when_all_requested_aids_removed(self):
        uploader = _make_uploader()
        session = MagicMock()
        session.post.side_effect = [
            _make_response({"code": 0}),
            _make_response({"code": 0}),
        ]
        uploader._get_authenticated_session.return_value = session
        uploader.get_season_episodes = MagicMock(return_value={
            "episodes": [
                {"aid": 1001, "id": 11},
                {"aid": 1002, "id": 12},
            ]
        })

        result = uploader.remove_from_season([1001, 1002], 42)

        assert result is True
        assert session.post.call_args_list[0].kwargs["data"]["id"] == 11
        assert session.post.call_args_list[1].kwargs["data"]["id"] == 12

    def test_returns_false_when_any_requested_aid_missing_or_delete_fails(self):
        uploader = _make_uploader()
        session = MagicMock()
        session.post.return_value = _make_response({"code": -1, "message": "fail"})
        uploader._get_authenticated_session.return_value = session
        uploader.get_season_episodes = MagicMock(return_value={
            "episodes": [
                {"aid": 1001, "id": 11},
            ]
        })

        result = uploader.remove_from_season([1001, 9999], 42)

        assert result is False
        assert session.post.call_count == 1
