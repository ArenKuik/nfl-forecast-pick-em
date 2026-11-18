"""Test setup: point the app at the test database and never at the real API.

Settings are put into the environment *before* app.py is imported. app.py's
load_dotenv() never overrides variables that are already set, so these test
values win over anything in .env.
"""

import copy
import os
import re
from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values
from psycopg.conninfo import conninfo_to_dict

ADMIN = "commissioner"

# TEST_DATABASE_URL may come from the shell or from .env; nothing else from .env is used.
_env_file = Path(__file__).resolve().parent.parent / ".env"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or dotenv_values(_env_file).get("TEST_DATABASE_URL")

if not TEST_DATABASE_URL:
    pytest.exit("TEST_DATABASE_URL is not set; refusing to run tests.", returncode=2)

try:
    _test_db_name = conninfo_to_dict(TEST_DATABASE_URL).get("dbname") or ""
except psycopg.ProgrammingError:
    pytest.exit("TEST_DATABASE_URL could not be parsed; refusing to run tests.", returncode=2)
if not _test_db_name.endswith("_test"):
    pytest.exit(
        f"Test database name {_test_db_name!r} does not end in _test; refusing to run tests.",
        returncode=2,
    )

os.environ.update(
    DATABASE_URL=TEST_DATABASE_URL,  # the app only ever sees the test database
    SECRET_KEY="test-secret-key",
    ADMIN_USERNAME=ADMIN,
    SITE_NAME="Test Pick'em",
    NFL_API_KEY="fake-test-key",
)
os.environ.pop("K_SERVICE", None)

import app as app_module  # noqa: E402  (must come after the environment is set)
import nfl_api  # noqa: E402


# --- sample games in the real API's shape ---------------------------------

def _team(team_id, name, abbreviation):
    return {"team_id": team_id, "name": name, "abbreviation": abbreviation}


CHIEFS = _team(1, "Kansas City Chiefs", "KC")
RAVENS = _team(2, "Baltimore Ravens", "BAL")
EAGLES = _team(3, "Philadelphia Eagles", "PHI")
COWBOYS = _team(4, "Dallas Cowboys", "DAL")
BILLS = _team(5, "Buffalo Bills", "BUF")
JETS = _team(6, "New York Jets", "NYJ")

SAMPLE_GAMES = [
    {   # week 1, finished
        "game_id": 1001, "season": 2026, "week": 1,
        "start_time": "2026-09-11T00:20:00Z", "status": "Final",
        "home_team": CHIEFS, "away_team": RAVENS, "home_score": 17, "away_score": 24,
    },
    {   # week 1, finished, kicks off earlier than game 1001 in the list order test
        "game_id": 1000, "season": 2026, "week": 1,
        "start_time": "2026-09-10T00:20:00Z", "status": "Final",
        "home_team": EAGLES, "away_team": COWBOYS, "home_score": 30, "away_score": 20,
    },
    {   # week 2, not played yet
        "game_id": 2001, "season": 2026, "week": 2,
        "start_time": "2026-09-14T17:00:00Z", "status": "Scheduled",
        "home_team": BILLS, "away_team": JETS, "home_score": None, "away_score": None,
    },
    {   # week 3, not played yet
        "game_id": 3001, "season": 2026, "week": 3,
        "start_time": "2026-09-21T17:00:00Z", "status": "Scheduled",
        "home_team": RAVENS, "away_team": CHIEFS, "home_score": None, "away_score": None,
    },
]


class FakeGamesApi:
    """Stands in for nfl_api.fetch_season_games and records every call."""

    def __init__(self):
        self.raw_games = copy.deepcopy(SAMPLE_GAMES)
        self.calls = []

    def __call__(self, season):
        self.calls.append(season)
        return [nfl_api._to_game(raw, season) for raw in self.raw_games]


# --- fixtures --------------------------------------------------------------

@pytest.fixture(autouse=True)
def fake_api(monkeypatch):
    """Every test gets the fake; the real HTTP call is made impossible too."""
    fake = FakeGamesApi()
    monkeypatch.setattr(nfl_api, "fetch_season_games", fake)

    def _no_network(*args, **kwargs):
        raise AssertionError("Tests must not call the real games API.")

    monkeypatch.setattr(nfl_api.requests, "get", _no_network)
    return fake


@pytest.fixture
def db_conn():
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        yield conn


@pytest.fixture(autouse=True)
def empty_tables(db_conn):
    db_conn.execute("TRUNCATE users, games RESTART IDENTITY")


@pytest.fixture
def client():
    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()


@pytest.fixture
def save_games():
    """Put games straight into the database, in the real API's shape."""
    def _save(raw_games):
        app_module.db.upsert_games([nfl_api._to_game(raw, 2026) for raw in raw_games])
    return _save


# --- helpers for driving the forms like a browser --------------------------

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def csrf_from(client, path):
    """Load a page and return the CSRF token from its form, as a browser would."""
    response = client.get(path)
    match = _CSRF_RE.search(response.get_data(as_text=True))
    assert match, f"No CSRF token found on {path}"
    return match.group(1)


def register(client, username, password):
    token = csrf_from(client, "/register")
    return client.post(
        "/register",
        data={"csrf_token": token, "username": username, "password": password},
        follow_redirects=True,
    )


def login(client, username, password):
    token = csrf_from(client, "/login")
    return client.post(
        "/login",
        data={"csrf_token": token, "username": username, "password": password},
        follow_redirects=True,
    )


def logout(client):
    token = csrf_from(client, "/")
    return client.post("/logout", data={"csrf_token": token}, follow_redirects=True)


def sync(client):
    token = csrf_from(client, "/")
    return client.post("/admin/sync", data={"csrf_token": token}, follow_redirects=True)
