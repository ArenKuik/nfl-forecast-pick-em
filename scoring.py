"""Scoring rules for forecasts. Pure functions: no database or Flask code."""


def is_final(status):
    """A game is final when its status starts with "final" (e.g. "Final", "final/OT")."""
    return (status or "").lower().startswith("final")


def points(probability, home_score, away_score):
    """Points for a 0–100 home-win forecast on a finished game.

    result is 1 for a home win, 0 for a loss and 0.5 for a tie, and
    points = 25 − (100 × (probability/100 − result))² / 100.
    Computed as (probability − 100 × result) so the arithmetic stays exact.
    """
    if home_score > away_score:
        result_pct = 100
    elif home_score < away_score:
        result_pct = 0
    else:
        result_pct = 50
    miss = probability - result_pct
    return 25.0 - (miss * miss) / 100


def leaderboard(rows):
    """Build leaderboard entries from (username, probability, home_score, away_score, status) rows.

    Only rows for final games with both scores count. Returns a list of dicts
    with username, total, games and average, sorted by total (highest first),
    then username. Users with no scored forecasts are left out.
    """
    totals = {}
    for username, probability, home_score, away_score, status in rows:
        if not is_final(status) or home_score is None or away_score is None:
            continue
        entry = totals.setdefault(username, {"username": username, "total": 0.0, "games": 0})
        entry["total"] += points(probability, home_score, away_score)
        entry["games"] += 1

    entries = list(totals.values())
    for entry in entries:
        entry["average"] = entry["total"] / entry["games"]
    entries.sort(key=lambda entry: (-entry["total"], entry["username"]))
    return entries
