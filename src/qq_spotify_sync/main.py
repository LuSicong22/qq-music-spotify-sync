"""Orchestrator entry point for QQ Music → Spotify sync."""
from __future__ import annotations

import argparse
import logging
import sys

from .config import Config
from .matcher import match_songs
from .notifier import SyncReport, notify
from .qq_music import QQMusicError, fetch_hot_chart
from .spotify_client import SpotifyClient, SpotifyError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync QQ Music Hot Songs chart to a Spotify playlist."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and match songs, but do NOT update the Spotify playlist.",
    )
    return parser.parse_args()


def run(dry_run: bool = False) -> int:
    """
    Main sync logic. Returns exit code (0 = success, 1 = system error).

    Uses try/except/finally to ensure notification is always sent, even on crash.
    """
    config: Config | None = None
    report: SyncReport | None = None
    playlist_id = ""
    playlist_url = ""

    try:
        config = Config.from_env()
    except KeyError as exc:
        error_msg = f"Missing required environment variable: {exc}"
        logger.error(error_msg)
        # Can't load config, so can't notify properly — just log and exit
        print(f"ERROR: {error_msg}", file=sys.stderr)
        return 1

    try:
        # ── Step 1: Fetch QQ Music chart ─────────────────────────────────────
        logger.info("Step 1/4: Fetching QQ Music chart (topId=%d, num=%d)",
                    config.qq_music_top_id, config.qq_music_num)
        songs = fetch_hot_chart(config.qq_music_top_id, config.qq_music_num)
        logger.info("Fetched %d songs from QQ Music", len(songs))

        # ── Step 2: Init Spotify + find/create playlist ───────────────────────
        logger.info("Step 2/4: Initialising Spotify client")
        spotify = SpotifyClient(config)

        if dry_run:
            logger.info("[DRY RUN] Skipping playlist lookup; no playlist will be modified.")
            playlist_id = "DRY_RUN"
            playlist_url = ""
        else:
            playlist_id = spotify.ensure_playlist()
            playlist_url = spotify.get_playlist_url(playlist_id)
            logger.info("Target playlist: %s", playlist_url)

        # ── Step 3: Match songs ───────────────────────────────────────────────
        logger.info("Step 3/4: Matching %d songs on Spotify", len(songs))
        result = match_songs(songs, spotify)

        matched_uris = [track.uri for _, track in result.matched]
        logger.info(
            "Matched %d/%d songs (%d unmatched)",
            len(result.matched),
            len(songs),
            len(result.unmatched),
        )

        # ── Step 4: Update playlist ───────────────────────────────────────────
        if dry_run:
            logger.info("[DRY RUN] Would update playlist with %d tracks.", len(matched_uris))
            for i, (song, track) in enumerate(result.matched, 1):
                logger.info("  %3d. %s — %s -> %s (%s)",
                            i, song.title, ", ".join(song.artists), track.name, track.uri)
        else:
            logger.info("Step 4/4: Updating Spotify playlist with %d tracks", len(matched_uris))
            spotify.replace_playlist_tracks(playlist_id, matched_uris)

        if result.unmatched:
            logger.info("Unmatched songs:")
            for song, reason in result.unmatched:
                logger.info("  - %s (%s): %s", song.title, ", ".join(song.artists), reason.reason)

        report = SyncReport.from_match_result(
            result=result,
            playlist_id=playlist_id,
            playlist_url=playlist_url,
            total=len(songs),
        )

        logger.info(
            "Sync complete. Alert level: %s. Playlist: %s",
            report.alert_level,
            playlist_url or "(dry-run)",
        )
        return 0

    except (QQMusicError, SpotifyError) as exc:
        error_msg = str(exc)
        logger.error("System error: %s", error_msg)
        report = SyncReport.system_error(error_msg)
        return 1

    except Exception as exc:
        error_msg = f"Unexpected error: {exc}"
        logger.exception(error_msg)
        report = SyncReport.system_error(error_msg)
        return 1

    finally:
        # Always notify, even on crash — ensures artifact/summary are written
        if config is not None and report is not None:
            notify(report, config)


def main() -> None:
    args = _parse_args()
    sys.exit(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
