"""Database access for the NFL forecasting game (PostgreSQL via psycopg)."""

import psycopg
from psycopg.rows import dict_row

_database_url = None


class UsernameTaken(Exception):
    """Raised when registering a username that already exists."""


def init_db(database_url):
    """Remember the connection string and create the users table if it is missing."""
    global _database_url
    _database_url = database_url
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                username      TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS games (
                id          TEXT PRIMARY KEY,
                season      INTEGER NOT NULL,
                week        INTEGER,
                home_team   TEXT NOT NULL,
                away_team   TEXT NOT NULL,
                home_name   TEXT NOT NULL,
                away_name   TEXT NOT NULL,
                kickoff     TIMESTAMPTZ NOT NULL,
                status      TEXT,
                home_score  INTEGER,
                away_score  INTEGER,
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS forecasts (
                user_id     BIGINT NOT NULL,
                game_id     TEXT NOT NULL,
                probability INTEGER NOT NULL CHECK (probability BETWEEN 0 AND 100),
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (user_id, game_id)
            )
            """
        )


def _connect():
    return psycopg.connect(_database_url, row_factory=dict_row)


def create_user(username, password_hash):
    """Insert a user and return its id. Raises UsernameTaken if the name exists."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id",
                (username, password_hash),
            ).fetchone()
            return row["id"]
    except psycopg.errors.UniqueViolation:
        raise UsernameTaken(username)


def get_user_by_username(username):
    with _connect() as conn:
        return conn.execute(
            "SELECT id, username, password_hash FROM users WHERE username = %s",
            (username,),
        ).fetchone()


def get_user_by_id(user_id):
    with _connect() as conn:
        return conn.execute(
            "SELECT id, username FROM users WHERE id = %s",
            (user_id,),
        ).fetchone()


def upsert_games(games):
    """Insert or update games by id in one transaction. Returns how many were saved."""
    if not games:
        return 0
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO games (id, season, week, home_team, away_team, home_name,
                                   away_name, kickoff, status, home_score, away_score, updated_at)
                VALUES (%(id)s, %(season)s, %(week)s, %(home_team)s, %(away_team)s, %(home_name)s,
                        %(away_name)s, %(kickoff)s, %(status)s, %(home_score)s, %(away_score)s, now())
                ON CONFLICT (id) DO UPDATE SET
                    season     = EXCLUDED.season,
                    week       = EXCLUDED.week,
                    home_team  = EXCLUDED.home_team,
                    away_team  = EXCLUDED.away_team,
                    home_name  = EXCLUDED.home_name,
                    away_name  = EXCLUDED.away_name,
                    kickoff    = EXCLUDED.kickoff,
                    status     = EXCLUDED.status,
                    home_score = EXCLUDED.home_score,
                    away_score = EXCLUDED.away_score,
                    updated_at = now()
                """,
                games,
            )
    return len(games)


# A game counts as final when its status starts with "final" (e.g. "Final", "final/OT").
_IS_FINAL = "lower(coalesce(status, '')) LIKE 'final%%'"


def get_week_games(season, week):
    """Return the games of one week, earliest kickoff first."""
    with _connect() as conn:
        return conn.execute(
            """
            SELECT id, home_team, away_team, home_name, away_name, kickoff,
                   status, home_score, away_score
            FROM games
            WHERE season = %s AND week = %s
            ORDER BY kickoff, id
            """,
            (season, week),
        ).fetchall()


def get_current_week(season, default_week):
    """Return the earliest week with a game that is not final, or default_week if none."""
    with _connect() as conn:
        row = conn.execute(
            f"SELECT min(week) AS week FROM games WHERE season = %s AND week IS NOT NULL AND NOT {_IS_FINAL}",
            (season,),
        ).fetchone()
    return row["week"] if row["week"] is not None else default_week


class ForecastClosed(Exception):
    """Raised when saving a forecast for a game that is missing or has kicked off."""

    def __init__(self, game_id):
        super().__init__(game_id)
        self.game_id = game_id


def get_games_by_ids(game_ids):
    """Return {game_id: game} for the given ids that exist."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, home_name, away_name, kickoff FROM games WHERE id = ANY(%s)",
            (list(game_ids),),
        ).fetchall()
    return {row["id"]: row for row in rows}


def get_user_forecasts(user_id, game_ids):
    """Return {game_id: probability} for one user's forecasts on the given games."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT game_id, probability FROM forecasts WHERE user_id = %s AND game_id = ANY(%s)",
            (user_id, list(game_ids)),
        ).fetchall()
    return {row["game_id"]: row["probability"] for row in rows}


def save_forecasts(user_id, forecasts):
    """Upsert {game_id: probability} for one user, all or nothing.

    The kickoff check is repeated inside the insert, so a game that kicks off
    between validation and saving is still refused (ForecastClosed) and the
    whole transaction is rolled back.
    """
    with _connect() as conn:
        for game_id, probability in forecasts.items():
            cur = conn.execute(
                """
                INSERT INTO forecasts (user_id, game_id, probability)
                SELECT %s, id, %s FROM games WHERE id = %s AND kickoff > now()
                ON CONFLICT (user_id, game_id) DO UPDATE SET
                    probability = EXCLUDED.probability,
                    updated_at  = now()
                """,
                (user_id, probability, game_id),
            )
            if cur.rowcount != 1:
                raise ForecastClosed(game_id)
    return len(forecasts)


def get_forecast_results(season, week=None):
    """Return (username, probability, home_score, away_score, status) for every
    forecast in a season, or in one week of it."""
    query = """
        SELECT u.username, f.probability, g.home_score, g.away_score, g.status
        FROM forecasts f
        JOIN users u ON u.id = f.user_id
        JOIN games g ON g.id = f.game_id
        WHERE g.season = %s
    """
    params = [season]
    if week is not None:
        query += " AND g.week = %s"
        params.append(week)
    with psycopg.connect(_database_url) as conn:  # plain tuples, as scoring.leaderboard expects
        return conn.execute(query, params).fetchall()
