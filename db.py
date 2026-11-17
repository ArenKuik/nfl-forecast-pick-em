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
