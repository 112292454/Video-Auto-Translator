"""sync_season_episode_titles 契约测试。"""

from unittest.mock import MagicMock

import pytest

from vat.uploaders.bilibili import BilibiliUploader


def _make_uploader():
    uploader = BilibiliUploader.__new__(BilibiliUploader)
    uploader.get_season_episodes = MagicMock()
    uploader.remove_from_season = MagicMock()
    uploader.add_to_season = MagicMock()
    uploader.sort_season_episodes = MagicMock()
    return uploader


class TestSyncSeasonEpisodeTitles:
    def test_already_synced_titles_return_skipped(self):
        uploader = _make_uploader()
        uploader.get_season_episodes.return_value = {
            "section_id": 10,
            "episodes": [
                {"id": 1, "aid": 101, "title": "A", "archiveTitle": "A"},
                {"id": 2, "aid": 102, "title": "B", "archiveTitle": "B"},
            ],
        }

        result = uploader.sync_season_episode_titles(42)

        assert result == {"success": True, "updated": 0, "skipped": 2, "details": []}
        uploader.remove_from_season.assert_not_called()
        uploader.add_to_season.assert_not_called()
        uploader.sort_season_episodes.assert_not_called()

    def test_remove_failure_stops_flow(self):
        uploader = _make_uploader()
        uploader.get_season_episodes.return_value = {
            "section_id": 10,
            "episodes": [
                {"id": 1, "aid": 101, "title": "Old", "archiveTitle": "New"},
            ],
        }
        uploader.remove_from_season.return_value = False

        result = uploader.sync_season_episode_titles(42)

        assert result == {"success": False, "error": "删除视频失败"}
        uploader.remove_from_season.assert_called_once_with([101], 42)
        uploader.add_to_season.assert_not_called()
        uploader.sort_season_episodes.assert_not_called()

    def test_partial_readd_failure_reports_failure_and_skips_sort(self, monkeypatch):
        uploader = _make_uploader()
        uploader.get_season_episodes.return_value = {
            "section_id": 10,
            "episodes": [
                {"id": 1, "aid": 101, "title": "Old1", "archiveTitle": "New1"},
                {"id": 2, "aid": 102, "title": "Old2", "archiveTitle": "New2"},
            ],
        }
        uploader.remove_from_season.return_value = True
        uploader.add_to_season.side_effect = [True, False]
        monkeypatch.setattr("time.sleep", lambda _seconds: None)

        result = uploader.sync_season_episode_titles(42)

        assert result["success"] is False
        assert result["updated"] == 1
        assert result["failed"] == [102]
        uploader.sort_season_episodes.assert_not_called()

    def test_successful_readd_restores_original_order(self, monkeypatch):
        uploader = _make_uploader()
        uploader.get_season_episodes.return_value = {
            "section_id": 10,
            "episodes": [
                {"id": 1, "aid": 301, "title": "Old1", "archiveTitle": "New1"},
                {"id": 2, "aid": 302, "title": "Old2", "archiveTitle": "New2"},
                {"id": 3, "aid": 303, "title": "Stable", "archiveTitle": "Stable"},
            ],
        }
        uploader.remove_from_season.return_value = True
        uploader.add_to_season.side_effect = [True, True]
        uploader.sort_season_episodes.return_value = True
        monkeypatch.setattr("time.sleep", lambda _seconds: None)

        result = uploader.sync_season_episode_titles(42)

        assert result["success"] is True
        assert result["updated"] == 2
        assert result["skipped"] == 1
        assert [item["aid"] for item in result["details"]] == [301, 302]
        uploader.remove_from_season.assert_called_once_with([301, 302], 42)
        assert uploader.add_to_season.call_args_list[0].args == (301, 42)
        assert uploader.add_to_season.call_args_list[1].args == (302, 42)
        uploader.sort_season_episodes.assert_called_once_with(42, [301, 302, 303])
