"""Tests for the QQ Music chart fetcher."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from qq_spotify_sync.qq_music import QQMusicError, QQSong, _parse_response, fetch_hot_chart

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TestParseResponse:
    def test_parses_songs_correctly(self):
        data = load_fixture("qq_chart_response.json")
        songs = _parse_response(data, top_id=26)

        assert len(songs) == 5
        assert songs[0].title == "漠河舞厅"
        assert songs[0].artists == ["柳爽"]
        assert songs[0].duration_ms == 270_000

    def test_duration_zero_when_missing(self):
        data = load_fixture("qq_chart_response.json")
        # Remove interval from first song
        data["detail"]["data"]["songInfoList"][0].pop("interval", None)
        songs = _parse_response(data, top_id=26)
        assert songs[0].duration_ms == 0

    def test_fail_closed_on_wrong_chart_name(self):
        data = load_fixture("qq_wrong_chart_response.json")
        with pytest.raises(QQMusicError, match="Chart validation failed"):
            _parse_response(data, top_id=26)

    def test_no_validation_for_custom_top_id(self):
        data = load_fixture("qq_wrong_chart_response.json")
        # topId != 26, so no "热歌" check
        songs = _parse_response(data, top_id=99)
        assert len(songs) == 1

    def test_raises_on_empty_song_list(self):
        data = load_fixture("qq_chart_response.json")
        data["detail"]["data"]["songInfoList"] = []
        with pytest.raises(QQMusicError, match="No songs returned"):
            _parse_response(data, top_id=26)

    def test_multiple_artists(self):
        data = load_fixture("qq_chart_response.json")
        data["detail"]["data"]["songInfoList"][0]["singer"] = [
            {"name": "Artist A"},
            {"name": "Artist B"},
        ]
        songs = _parse_response(data, top_id=26)
        assert songs[0].artists == ["Artist A", "Artist B"]

    def test_skips_songs_with_empty_title(self):
        data = load_fixture("qq_chart_response.json")
        data["detail"]["data"]["songInfoList"].insert(0, {
            "title": "",
            "singer": [{"name": "Nobody"}],
            "album": {},
            "interval": 0,
        })
        songs = _parse_response(data, top_id=26)
        assert len(songs) == 5  # Empty-title entry skipped


class TestFetchHotChart:
    def _make_mock_response(self, fixture_name: str, status_code: int = 200):
        mock_resp = MagicMock()
        mock_resp.ok = status_code < 400
        mock_resp.status_code = status_code
        mock_resp.json.return_value = load_fixture(fixture_name)
        mock_resp.headers = {}
        return mock_resp

    def test_successful_fetch(self):
        mock_resp = self._make_mock_response("qq_chart_response.json")
        with patch("qq_spotify_sync.qq_music.requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            mock_session.get.return_value = mock_resp
            mock_session.mount = MagicMock()

            songs = fetch_hot_chart(top_id=26, num=5)

        assert len(songs) == 5
        assert isinstance(songs[0], QQSong)

    def test_raises_on_http_error(self):
        mock_resp = self._make_mock_response("qq_chart_response.json", status_code=500)
        mock_resp.text = "Internal Server Error"
        with patch("qq_spotify_sync.qq_music.requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            mock_session.get.return_value = mock_resp
            mock_session.mount = MagicMock()

            with pytest.raises(QQMusicError, match="HTTP 500"):
                fetch_hot_chart()
