"""Tests for SpotifyClient."""
from unittest.mock import MagicMock, patch, call

import pytest

from qq_spotify_sync.config import Config
from qq_spotify_sync.spotify_client import (
    PLAYLIST_MARKER,
    SpotifyClient,
    SpotifyError,
    SpotifyTrack,
    _build_client,
)


def _make_config(**overrides) -> Config:
    defaults = dict(
        spotify_client_id="cid",
        spotify_client_secret="csecret",
        spotify_redirect_uri="http://localhost:8888/callback",
        spotify_refresh_token="rtoken",
        spotify_playlist_id="",
        spotify_playlist_name="QQ音乐热歌榜",
        qq_music_top_id=26,
        qq_music_num=100,
        telegram_bot_token="",
        telegram_chat_id="",
    )
    defaults.update(overrides)
    return Config(**defaults)


def _make_client(sp_mock: MagicMock, **config_overrides) -> SpotifyClient:
    config = _make_config(**config_overrides)
    client = SpotifyClient.__new__(SpotifyClient)
    client._config = config
    client._sp = sp_mock
    client._current_user_id = "testuser"
    return client


class TestSearchTracks:
    def test_returns_tracks(self):
        sp = MagicMock()
        sp.search.return_value = {
            "tracks": {
                "items": [
                    {
                        "uri": "spotify:track:abc",
                        "name": "漠河舞厅",
                        "artists": [{"name": "柳爽"}],
                        "duration_ms": 270_000,
                        "popularity": 80,
                    }
                ]
            }
        }
        client = _make_client(sp)
        results = client.search_tracks("漠河舞厅 柳爽")
        assert len(results) == 1
        assert results[0].uri == "spotify:track:abc"
        assert results[0].artists == ["柳爽"]

    def test_returns_empty_list_when_no_results(self):
        sp = MagicMock()
        sp.search.return_value = {"tracks": {"items": []}}
        client = _make_client(sp)
        assert client.search_tracks("nonexistent song xyz") == []


class TestEnsurePlaylist:
    def test_returns_configured_playlist_id_directly(self):
        sp = MagicMock()
        client = _make_client(sp, spotify_playlist_id="existing-id")
        result = client.ensure_playlist()
        assert result == "existing-id"
        sp.current_user_playlists.assert_not_called()

    def test_finds_existing_managed_playlist(self):
        sp = MagicMock()
        sp.current_user_playlists.return_value = {
            "items": [
                {
                    "id": "managed-pl-id",
                    "name": "QQ音乐热歌榜",
                    "owner": {"id": "testuser"},
                    "description": f"Daily sync {PLAYLIST_MARKER}",
                }
            ],
            "next": None,
        }
        client = _make_client(sp)
        result = client.ensure_playlist()
        assert result == "managed-pl-id"
        sp.user_playlist_create.assert_not_called()

    def test_ignores_playlist_owned_by_other_user(self):
        sp = MagicMock()
        sp.current_user_playlists.return_value = {
            "items": [
                {
                    "id": "other-pl-id",
                    "name": "QQ音乐热歌榜",
                    "owner": {"id": "someone_else"},
                    "description": PLAYLIST_MARKER,
                }
            ],
            "next": None,
        }
        sp.current_user_playlist_create.return_value = {"id": "new-pl-id"}
        client = _make_client(sp)
        result = client.ensure_playlist()
        assert result == "new-pl-id"

    def test_creates_playlist_when_none_found(self):
        sp = MagicMock()
        sp.current_user_playlists.return_value = {"items": [], "next": None}
        sp.current_user_playlist_create.return_value = {"id": "brand-new-id"}
        client = _make_client(sp)
        result = client.ensure_playlist()
        assert result == "brand-new-id"
        sp.current_user_playlist_create.assert_called_once()
        call_kwargs = sp.current_user_playlist_create.call_args
        assert PLAYLIST_MARKER in call_kwargs.kwargs.get("description", "")

    def test_paginates_through_playlists(self):
        sp = MagicMock()
        sp.current_user_playlists.side_effect = [
            {
                "items": [{"id": "other", "name": "other", "owner": {"id": "testuser"}, "description": ""}],
                "next": "page2",
            },
            {
                "items": [
                    {
                        "id": "target-pl",
                        "name": "QQ音乐热歌榜",
                        "owner": {"id": "testuser"},
                        "description": PLAYLIST_MARKER,
                    }
                ],
                "next": None,
            },
        ]
        client = _make_client(sp)
        result = client.ensure_playlist()
        assert result == "target-pl"


class TestReplacePlaylistTracks:
    def test_replaces_tracks(self):
        sp = MagicMock()
        client = _make_client(sp)
        uris = [f"spotify:track:{i}" for i in range(10)]
        client.replace_playlist_tracks("pl-id", uris)
        sp.playlist_replace_items.assert_called_once_with("pl-id", uris)

    def test_truncates_to_100(self):
        sp = MagicMock()
        client = _make_client(sp)
        uris = [f"spotify:track:{i}" for i in range(150)]
        client.replace_playlist_tracks("pl-id", uris)
        actual_uris = sp.playlist_replace_items.call_args[0][1]
        assert len(actual_uris) == 100
