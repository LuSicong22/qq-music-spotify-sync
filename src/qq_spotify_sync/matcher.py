"""Match QQ Music songs to Spotify tracks using two-phase search + scoring."""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .qq_music import QQSong
from .spotify_client import SpotifyClient, SpotifyTrack

logger = logging.getLogger(__name__)

# Tags that indicate a special version we want to exclude unless the source also has them
_SPECIAL_VERSION_TAGS = re.compile(
    r"\b(live|remaster(?:ed)?|karaoke|instrumental|dj\s*version|remix|cover|acoustic|demo)\b",
    re.IGNORECASE,
)

# Patterns to strip from titles during normalization
_BRACKET_CONTENT = re.compile(r"[(\[（【][^)\]）】]*[)\]）】]")
_FEAT_SUFFIX = re.compile(r"\s+(feat\.?|ft\.?|featuring)\s+.*", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")

_MAX_DURATION_DIFF_MS = 15_000   # 15 seconds
_TITLE_SIMILARITY_THRESHOLD = 0.8
_SEARCH_PACING = 0.1             # seconds between API calls
_TOTAL_BUDGET_SECONDS = 300      # 5-minute total budget for all searches
_MAX_RETRIES_PER_SONG = 2


@dataclass
class UnmatchedReason:
    query: str
    candidates: list[dict]       # Raw candidate info for artifact logging
    reason: str                  # Human-readable explanation


@dataclass
class MatchResult:
    matched: list[tuple[QQSong, SpotifyTrack]] = field(default_factory=list)
    unmatched: list[tuple[QQSong, UnmatchedReason]] = field(default_factory=list)
    timed_out: bool = False      # True if budget was exhausted mid-run


def _normalize(text: str) -> str:
    """Normalize a track/artist name for fuzzy comparison."""
    # Convert full-width characters to ASCII equivalents
    text = unicodedata.normalize("NFKC", text)
    # Remove content inside brackets
    text = _BRACKET_CONTENT.sub("", text)
    # Remove feat. / ft. suffixes
    text = _FEAT_SUFFIX.sub("", text)
    # Lowercase and collapse whitespace
    text = _WHITESPACE.sub(" ", text.lower()).strip()
    return text


def _title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _artists_overlap(qq_artists: list[str], sp_artists: list[str]) -> int:
    """Return count of matching artists (case-insensitive, whitespace-collapsed)."""
    norm_qq = {_normalize(a) for a in qq_artists}
    norm_sp = {_normalize(a) for a in sp_artists}
    return len(norm_qq & norm_sp)


def _has_special_version_tag(title: str) -> bool:
    return bool(_SPECIAL_VERSION_TAGS.search(title))


def _duration_score(qq_ms: int, sp_ms: int) -> float:
    """Return a 0..1 score; 1 = perfect match, 0 = >= MAX_DURATION_DIFF_MS apart."""
    if qq_ms == 0 or sp_ms == 0:
        return 0.5  # Unknown: neutral score
    diff = abs(qq_ms - sp_ms)
    if diff >= _MAX_DURATION_DIFF_MS:
        return 0.0
    return 1.0 - diff / _MAX_DURATION_DIFF_MS


def _score_candidate(
    song: QQSong, candidate: SpotifyTrack, source_has_special: bool
) -> float | None:
    """
    Return a composite score [0..1] for the candidate, or None if it fails
    the acceptance criteria.
    """
    # Acceptance: title similarity
    title_sim = _title_similarity(song.title, candidate.name)
    if title_sim < _TITLE_SIMILARITY_THRESHOLD:
        return None

    # Acceptance: artist overlap
    overlap = _artists_overlap(song.artists, candidate.artists)
    if overlap == 0:
        return None

    # Acceptance: exclude special versions unless source also has the tag
    if not source_has_special and _has_special_version_tag(candidate.name):
        return None

    # Acceptance: duration check (only when we have QQ duration)
    if song.duration_ms > 0:
        diff = abs(song.duration_ms - candidate.duration_ms)
        if diff > _MAX_DURATION_DIFF_MS:
            return None
        dur_score = _duration_score(song.duration_ms, candidate.duration_ms)
    else:
        dur_score = 0.5

    # Composite score
    max_overlap = max(len(song.artists), len(candidate.artists), 1)
    normalized_overlap = min(overlap / max_overlap, 1.0)
    score = title_sim * 0.4 + normalized_overlap * 0.3 + dur_score * 0.3
    return score


def _best_candidate(
    song: QQSong, candidates: list[SpotifyTrack]
) -> SpotifyTrack | None:
    source_has_special = _has_special_version_tag(song.title)
    scored = []
    for c in candidates:
        s = _score_candidate(song, c, source_has_special)
        if s is not None:
            scored.append((s, c))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def _candidates_for_log(candidates: list[SpotifyTrack]) -> list[dict]:
    return [
        {"uri": c.uri, "name": c.name, "artists": c.artists, "duration_ms": c.duration_ms}
        for c in candidates
    ]


def match_songs(songs: list[QQSong], spotify: SpotifyClient) -> MatchResult:
    """
    For each QQ song, run two-phase Spotify search and pick the best match.
    Respects a 5-minute total budget.
    """
    result = MatchResult()
    deadline = time.monotonic() + _TOTAL_BUDGET_SECONDS

    for i, song in enumerate(songs):
        if time.monotonic() > deadline:
            logger.warning(
                "Search budget exhausted after %d/%d songs. Skipping remainder.",
                i,
                len(songs),
            )
            result.timed_out = True
            # Remaining songs are unmatched due to timeout
            for remaining in songs[i:]:
                result.unmatched.append(
                    (remaining, UnmatchedReason(query="", candidates=[], reason="budget_timeout"))
                )
            break

        primary_query = f"{song.title} {song.artists[0]}" if song.artists else song.title
        fallback_query = song.title

        track, reason = _search_with_retry(song, primary_query, fallback_query, spotify)

        if track:
            result.matched.append((song, track))
        else:
            result.unmatched.append((song, reason))

        # Pacing between songs
        time.sleep(_SEARCH_PACING)

    return result


def _search_with_retry(
    song: QQSong,
    primary_query: str,
    fallback_query: str,
    spotify: SpotifyClient,
) -> tuple[SpotifyTrack | None, UnmatchedReason]:
    """Run primary and fallback searches, return the best match or an unmatched reason."""
    all_candidates: list[SpotifyTrack] = []

    for query in [primary_query, fallback_query]:
        for attempt in range(_MAX_RETRIES_PER_SONG):
            try:
                candidates = spotify.search_tracks(query)
                break
            except Exception as exc:
                if attempt < _MAX_RETRIES_PER_SONG - 1:
                    logger.warning(
                        "Search attempt %d failed for '%s': %s. Retrying...",
                        attempt + 1, query, exc,
                    )
                    time.sleep(1.5 ** attempt)
                else:
                    logger.error("Search failed for '%s' after %d attempts: %s", query, _MAX_RETRIES_PER_SONG, exc)
                    candidates = []

        all_candidates.extend(candidates)
        best = _best_candidate(song, candidates)
        if best:
            logger.debug("Matched '%s' via query '%s' -> '%s'", song.title, query, best.name)
            return best, None  # type: ignore[return-value]

    # Both phases failed
    reason = UnmatchedReason(
        query=primary_query,
        candidates=_candidates_for_log(all_candidates),
        reason="no_candidate_passed_acceptance_criteria"
        if all_candidates
        else "no_search_results",
    )
    logger.info(
        "No match for '%s' by %s (%d candidates checked)",
        song.title,
        ", ".join(song.artists),
        len(all_candidates),
    )
    return None, reason
