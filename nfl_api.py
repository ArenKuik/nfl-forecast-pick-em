"""Fetch NFL games from the API Ninjas American football games endpoint."""

import os
from datetime import datetime, timezone

import requests

API_URL = "https://api.api-ninjas.com/v1/americanfootballgames"
PAGE_SIZE = 50  # fixed page size on the free plan (the limit parameter is not allowed)
TIMEOUT_SECONDS = 20


class NFLApiError(Exception):
    """Raised when the games API returns an error or an unexpected response."""


def fetch_season_games(season):
    """Return every regular-season NFL game of `season` as a list of plain dicts.

    Each dict has: id, season, week, home_team, away_team, home_name, away_name,
    kickoff (timezone-aware UTC datetime), status, home_score, away_score.
    """
    api_key = os.environ.get("NFL_API_KEY")
    if not api_key:
        raise NFLApiError("NFL_API_KEY is not set.")

    games = []
    offset = 0
    while True:
        page = _fetch_page(api_key, season, offset)
        games.extend(_to_game(raw, season) for raw in page)
        if len(page) < PAGE_SIZE:
            return games
        offset += PAGE_SIZE


def _fetch_page(api_key, season, offset):
    try:
        response = requests.get(
            API_URL,
            params={
                "league": "nfl",
                "season": season,
                "season_type": "regular",
                "offset": offset,
            },
            headers={"X-Api-Key": api_key},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise NFLApiError(f"Could not reach the games API: {exc}") from exc

    try:
        data = response.json()
    except ValueError:
        data = None

    if not response.ok or (isinstance(data, dict) and "error" in data):
        message = data.get("error") if isinstance(data, dict) else None
        raise NFLApiError(message or f"Games API returned HTTP {response.status_code}: {response.text[:200]}")

    if not isinstance(data, list):
        raise NFLApiError(f"Unexpected response from games API: {str(data)[:200]}")
    return data


def _to_game(raw, season):
    """Map one API game to our row. Missing required fields raise NFLApiError."""
    try:
        home = raw["home_team"]
        away = raw["away_team"]
        return {
            "id": str(raw["game_id"]),
            "season": int(raw.get("season") or season),
            "week": _int_or_none(raw.get("week")),
            "home_team": home["abbreviation"],
            "away_team": away["abbreviation"],
            "home_name": home["name"],
            "away_name": away["name"],
            "kickoff": _parse_utc(raw["start_time"]),
            "status": raw.get("status"),
            "home_score": _int_or_none(raw.get("home_score")),
            "away_score": _int_or_none(raw.get("away_score")),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise NFLApiError(f"Unexpected game data for game {raw.get('game_id')!r}: {exc!r}") from exc


def _parse_utc(value):
    """Parse start_time (UTC) as an ISO string or a Unix timestamp."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    text = str(value).strip()
    if text.isdigit():
        return datetime.fromtimestamp(int(text), tz=timezone.utc)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
