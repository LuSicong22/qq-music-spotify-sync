"""Fetch the QQ Music Hot Songs Chart (热歌榜)."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

_API_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
_CONNECT_TIMEOUT = 5   # seconds
_READ_TIMEOUT = 15     # seconds


@dataclass
class QQSong:
    title: str
    artists: list[str]
    album: str
    duration_ms: int  # 0 if unknown


class QQMusicError(Exception):
    """Raised for system-level failures when fetching the chart."""


def _make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods={"GET"},
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _build_payload(top_id: int, num: int, period: str) -> str:
    return json.dumps(
        {
            "detail": {
                "module": "musicToplist.ToplistInfoServer",
                "method": "GetDetail",
                "param": {
                    "topId": top_id,
                    "offset": 0,
                    "num": num,
                    "period": period,
                },
            }
        },
        ensure_ascii=False,
    )


def _parse_response(data: dict, top_id: int) -> list[QQSong]:
    """Parse the API response. Raises QQMusicError on structure problems."""
    detail = data.get("detail", {})
    resp_data = detail.get("data", {})
    nested_chart = resp_data.get("data", {}) if isinstance(resp_data.get("data"), dict) else {}

    # Validate chart identity (fail closed)
    top_info = resp_data.get("topInfo", {})
    list_name: str = (
        top_info.get("listName", "")
        or nested_chart.get("title", "")
        or nested_chart.get("titleDetail", "")
    )
    if top_id == 26 and "热歌" not in list_name:
        raise QQMusicError(
            f"Chart validation failed: expected '热歌' in listName, got '{list_name}'. "
            "Check QQ_MUSIC_TOP_ID or the API endpoint may have changed."
        )

    song_info_list = resp_data.get("songInfoList", [])
    fallback_song_list = nested_chart.get("song", [])
    if not song_info_list and fallback_song_list:
        song_info_list = fallback_song_list
    if not song_info_list:
        raise QQMusicError(
            f"No songs returned from chart (topId={top_id}, listName='{list_name}'). "
            "The API response structure may have changed."
        )

    songs: list[QQSong] = []
    for item in song_info_list:
        title: str = item.get("title", "").strip()
        if not title:
            continue

        singers = item.get("singer", [])
        artists = [s["name"].strip() for s in singers if s.get("name")]
        if not artists and item.get("singerName"):
            artists = [
                name.strip()
                for name in str(item["singerName"]).replace("/", "、").split("、")
                if name.strip()
            ]
        if not artists:
            artists = ["Unknown"]

        album_info = item.get("album", {})
        album = ""
        if isinstance(album_info, dict):
            album = album_info.get("name", "")
        elif item.get("albumMid"):
            album = str(item.get("albumMid", ""))

        # Duration: QQ returns seconds as int in some responses
        duration_sec = item.get("interval", 0) or item.get("duration", 0)
        duration_ms = int(duration_sec) * 1000

        songs.append(QQSong(title=title, artists=artists, album=album, duration_ms=duration_ms))

    logger.info("Parsed %d songs from chart '%s'", len(songs), list_name)
    return songs


def fetch_hot_chart(top_id: int = 26, num: int = 100) -> list[QQSong]:
    """
    Fetch the QQ Music chart and return a list of QQSong objects.

    Raises:
        QQMusicError: on HTTP failure, parse failure, or chart validation failure.
    """
    from datetime import date

    period = date.today().strftime("%Y-%m-%d")
    payload = _build_payload(top_id, num, period)

    session = _make_session()
    logger.info("Fetching QQ Music chart topId=%d num=%d period=%s", top_id, num, period)

    try:
        resp = session.get(
            _API_URL,
            params={"format": "json", "data": payload},
            timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Referer": "https://y.qq.com/",
            },
        )
    except requests.exceptions.Timeout as exc:
        raise QQMusicError(f"Request timed out: {exc}") from exc
    except requests.exceptions.RequestException as exc:
        raise QQMusicError(f"Network error: {exc}") from exc

    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", 60))
        logger.warning("QQ Music rate limited. Waiting %ds...", retry_after)
        time.sleep(retry_after)
        raise QQMusicError("QQ Music rate limit hit after single attempt.")

    if not resp.ok:
        raise QQMusicError(
            f"HTTP {resp.status_code} from QQ Music API: {resp.text[:200]}"
        )

    try:
        data = resp.json()
    except ValueError as exc:
        raise QQMusicError(f"Failed to parse JSON response: {exc}") from exc

    return _parse_response(data, top_id)
