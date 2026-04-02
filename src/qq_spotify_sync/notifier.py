"""Notification and artifact output for sync results."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import TYPE_CHECKING

import requests

from .config import Config
from .matcher import MatchResult, UnmatchedReason
from .qq_music import QQSong

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_MAX_TELEGRAM_UNMATCHED = 10


@dataclass
class SyncReport:
    date: str
    playlist_id: str
    playlist_url: str
    total: int
    matched_count: int
    unmatched_count: int
    timed_out: bool
    alert_level: str             # "ok", "warning", "critical", "system_error"
    error_message: str = ""      # Set on system errors
    unmatched: list[dict] = field(default_factory=list)

    @classmethod
    def from_match_result(
        cls,
        result: MatchResult,
        playlist_id: str,
        playlist_url: str,
        total: int,
    ) -> "SyncReport":
        unmatched_count = len(result.unmatched)
        unmatched_rate = unmatched_count / total if total > 0 else 0

        if unmatched_rate > 0.8:
            alert_level = "critical"
        elif unmatched_rate > 0.5:
            alert_level = "warning"
        else:
            alert_level = "ok"

        if result.timed_out:
            alert_level = "warning" if alert_level == "ok" else alert_level

        unmatched_items = [
            {
                "title": song.title,
                "artists": song.artists,
                "album": song.album,
                "query": reason.query,
                "candidates": reason.candidates,
                "reason": reason.reason,
            }
            for song, reason in result.unmatched
        ]

        return cls(
            date=date.today().isoformat(),
            playlist_id=playlist_id,
            playlist_url=playlist_url,
            total=total,
            matched_count=len(result.matched),
            unmatched_count=unmatched_count,
            timed_out=result.timed_out,
            alert_level=alert_level,
            unmatched=unmatched_items,
        )

    @classmethod
    def system_error(cls, error_message: str) -> "SyncReport":
        return cls(
            date=date.today().isoformat(),
            playlist_id="",
            playlist_url="",
            total=0,
            matched_count=0,
            unmatched_count=0,
            timed_out=False,
            alert_level="system_error",
            error_message=error_message,
        )


def write_artifact(report: SyncReport) -> str | None:
    """Write unmatched songs to a JSON file. Returns the file path or None."""
    if report.alert_level == "system_error":
        filename = f"result-error-{report.date}.json"
        data = {"date": report.date, "error": report.error_message}
    else:
        filename = f"unmatched-{report.date}.json"
        data = {
            "date": report.date,
            "playlist_url": report.playlist_url,
            "total": report.total,
            "matched": report.matched_count,
            "unmatched": report.unmatched_count,
            "alert_level": report.alert_level,
            "timed_out": report.timed_out,
            "unmatched_songs": report.unmatched,
        }

    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("Wrote artifact: %s", filename)
        return filename
    except OSError as exc:
        logger.error("Failed to write artifact: %s", exc)
        return None


def write_github_summary(report: SyncReport) -> None:
    """Write a Markdown summary to $GITHUB_STEP_SUMMARY (always, including failures)."""
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    lines = [f"# QQ音乐热歌榜 → Spotify 同步报告\n\n"]
    lines.append(f"**日期**: {report.date}\n\n")

    if report.alert_level == "system_error":
        lines.append(f"## 系统错误\n\n```\n{report.error_message}\n```\n")
    else:
        level_emoji = {"ok": "✅", "warning": "⚠️", "critical": "🔴"}.get(report.alert_level, "")
        lines.append(
            f"**状态**: {level_emoji} {report.alert_level.upper()}\n\n"
            f"**匹配**: {report.matched_count} / {report.total} 首\n\n"
            f"**未匹配**: {report.unmatched_count} 首\n\n"
        )
        if report.playlist_url:
            lines.append(f"**歌单**: [{report.playlist_url}]({report.playlist_url})\n\n")
        if report.timed_out:
            lines.append("⚠️ **搜索预算耗尽，部分歌曲未处理**\n\n")
        if report.unmatched:
            lines.append("## 未匹配歌曲\n\n")
            for item in report.unmatched:
                artists = ", ".join(item["artists"])
                lines.append(f"- {item['title']} — {artists} *(原因: {item['reason']})*\n")

    content = "".join(lines)
    try:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(content)
    except OSError as exc:
        logger.warning("Could not write GitHub summary: %s", exc)


def send_telegram(report: SyncReport, config: Config) -> None:
    """Send a concise notification to Telegram (optional)."""
    if not config.telegram_bot_token or not config.telegram_chat_id:
        return

    if report.alert_level == "system_error":
        text = (
            f"❌ QQ音乐→Spotify 同步失败 ({report.date})\n\n"
            f"错误: {report.error_message[:200]}"
        )
    else:
        level_icon = {"ok": "✅", "warning": "⚠️", "critical": "🔴"}.get(report.alert_level, "")
        lines = [
            f"{level_icon} QQ音乐热歌榜同步完成 ({report.date})",
            f"匹配: {report.matched_count}/{report.total} 首",
            f"级别: {report.alert_level.upper()}",
        ]
        if report.timed_out:
            lines.append("⚠️ 搜索预算耗尽")
        if report.unmatched:
            lines.append(f"\n未匹配 ({min(len(report.unmatched), _MAX_TELEGRAM_UNMATCHED)}/{report.unmatched_count}):")
            for item in report.unmatched[:_MAX_TELEGRAM_UNMATCHED]:
                artists = ", ".join(item["artists"])
                lines.append(f"  · {item['title']} — {artists}")
            if report.unmatched_count > _MAX_TELEGRAM_UNMATCHED:
                lines.append(f"  ...（完整列表见 artifact）")
        if report.playlist_url:
            lines.append(f"\n歌单: {report.playlist_url}")
        text = "\n".join(lines)

    url = _TELEGRAM_API.format(token=config.telegram_bot_token)
    try:
        resp = requests.post(
            url,
            json={"chat_id": config.telegram_chat_id, "text": text, "parse_mode": ""},
            timeout=(5, 10),
        )
        if not resp.ok:
            logger.warning("Telegram notification failed: %s %s", resp.status_code, resp.text[:100])
    except requests.RequestException as exc:
        logger.warning("Telegram notification error: %s", exc)


def notify(report: SyncReport, config: Config) -> None:
    """Run all notification channels. Called in finally block, must not raise."""
    try:
        write_artifact(report)
    except Exception as exc:
        logger.error("Artifact write failed: %s", exc)

    try:
        write_github_summary(report)
    except Exception as exc:
        logger.error("GitHub summary write failed: %s", exc)

    try:
        send_telegram(report, config)
    except Exception as exc:
        logger.error("Telegram notification failed: %s", exc)
