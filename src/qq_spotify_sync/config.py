from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # Spotify credentials
    spotify_client_id: str
    spotify_client_secret: str
    spotify_redirect_uri: str
    spotify_refresh_token: str

    # Playlist settings
    spotify_playlist_id: str        # Empty = auto-find or auto-create
    spotify_playlist_name: str

    # QQ Music settings
    qq_music_top_id: int
    qq_music_num: int               # Hardcapped at 100

    # Notification (optional)
    telegram_bot_token: str
    telegram_chat_id: str

    @classmethod
    def from_env(cls) -> "Config":
        raw_num = int(os.getenv("QQ_MUSIC_NUM", "100"))
        return cls(
            spotify_client_id=os.environ["SPOTIPY_CLIENT_ID"],
            spotify_client_secret=os.environ["SPOTIPY_CLIENT_SECRET"],
            spotify_redirect_uri=os.getenv(
                "SPOTIPY_REDIRECT_URI", "http://localhost:8888/callback"
            ),
            spotify_refresh_token=os.environ["SPOTIFY_REFRESH_TOKEN"],
            spotify_playlist_id=os.getenv("SPOTIFY_PLAYLIST_ID", "").strip(),
            spotify_playlist_name=os.getenv("SPOTIFY_PLAYLIST_NAME", "QQ音乐热歌榜"),
            qq_music_top_id=int(os.getenv("QQ_MUSIC_TOP_ID", "26")),
            qq_music_num=min(raw_num, 100),  # Hard cap: Spotify replace_items limit
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        )
