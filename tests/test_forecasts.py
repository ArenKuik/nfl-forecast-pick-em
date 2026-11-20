from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import csrf_from, logout, register

FUTURE_ID = "9001"  # kicks off tomorrow
PAST_ID = "9002"    # kicked off an hour ago


def _team(team_id, name, abbreviation):
    return {"team_id": team_id, "name": name, "abbreviation": abbreviation}


def _iso(moment):
    return moment.isoformat().replace("+00:00", "Z")


@pytest.fixture(autouse=True)
def clean_forecasts(db_conn):
    """The shared setup empties users and games; forecasts are emptied here."""
    db_conn.execute("DELETE FROM forecasts")
    yield
    db_conn.execute("DELETE FROM forecasts")


@pytest.fixture(autouse=True)
def week_one_games(save_games):
    now = datetime.now(timezone.utc)
    save_games([
        {
            "game_id": int(FUTURE_ID), "season": 2026, "week": 1,
            "start_time": _iso(now + timedelta(days=1)), "status": "Scheduled",
            "home_team": _team(1, "Kansas City Chiefs", "KC"),
            "away_team": _team(2, "Baltimore Ravens", "BAL"),
            "home_score": None, "away_score": None,
        },
        {
            "game_id": int(PAST_ID), "season": 2026, "week": 1,
            "start_time": _iso(now - timedelta(hours=1)), "status": "In Progress",
            "home_team": _team(3, "Philadelphia Eagles", "PHI"),
            "away_team": _team(4, "Dallas Cowboys", "DAL"),
            "home_score": 7, "away_score": 3,
        },
    ])


def _post_forecasts(client, values):
    token = csrf_from(client, "/week/1")
    data = {"csrf_token": token}
    data.update({f"forecast-{game_id}": value for game_id, value in values.items()})
    return client.post("/week/1/forecasts", data=data)


def _forecasts(db_conn):
    return db_conn.execute(
        "SELECT user_id, game_id, probability FROM forecasts ORDER BY user_id, game_id"
    ).fetchall()


def test_saving_a_forecast_stores_it(client, db_conn):
    register(client, "alice", "correct-horse")

    response = _post_forecasts(client, {FUTURE_ID: "65"})

    assert response.status_code == 302
    assert _forecasts(db_conn) == [(1, FUTURE_ID, 65)]
    page = client.get("/week/1").get_data(as_text=True)
    assert "Chance Kansas City Chiefs win (%)" in page
    assert 'value="65"' in page


def test_saving_again_updates_instead_of_duplicating(client, db_conn):
    register(client, "alice", "correct-horse")

    _post_forecasts(client, {FUTURE_ID: "65"})
    _post_forecasts(client, {FUTURE_ID: "40"})

    assert _forecasts(db_conn) == [(1, FUTURE_ID, 40)]


@pytest.mark.parametrize("bad_value", ["150", "-5", "abc"])
def test_invalid_value_is_refused_and_nothing_saved(client, db_conn, save_games, bad_value):
    now = datetime.now(timezone.utc)
    save_games([{
        "game_id": 9003, "season": 2026, "week": 1,
        "start_time": _iso(now + timedelta(days=2)), "status": "Scheduled",
        "home_team": _team(5, "Buffalo Bills", "BUF"),
        "away_team": _team(6, "New York Jets", "NYJ"),
        "home_score": None, "away_score": None,
    }])
    register(client, "alice", "correct-horse")

    # One good value alongside the bad one: the whole submission must be refused.
    response = _post_forecasts(client, {"9003": "55", FUTURE_ID: bad_value})

    assert response.status_code == 400
    page = response.get_data(as_text=True)
    assert "Nothing was saved" in page
    assert "Baltimore Ravens @ Kansas City Chiefs: the forecast must be a whole number from 0 to 100." in page
    assert _forecasts(db_conn) == []


def test_forecast_for_kicked_off_game_is_refused(client, db_conn):
    register(client, "alice", "correct-horse")

    page = client.get("/week/1").get_data(as_text=True)
    assert f'name="forecast-{PAST_ID}"' not in page  # no input is offered...

    response = _post_forecasts(client, {PAST_ID: "50"})  # ...and posting directly fails

    assert response.status_code == 400
    assert "already kicked off" in response.get_data(as_text=True)
    assert _forecasts(db_conn) == []


def test_visitor_cannot_save(client, db_conn):
    page = client.get("/week/1").get_data(as_text=True)
    assert "Save forecasts" not in page
    assert 'href="/login"' in page

    token = csrf_from(client, "/login")
    response = client.post(
        "/week/1/forecasts", data={"csrf_token": token, f"forecast-{FUTURE_ID}": "60"}
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    assert _forecasts(db_conn) == []


def test_users_cannot_see_each_others_forecasts(client, db_conn):
    register(client, "alice", "correct-horse")
    alice_id = db_conn.execute("SELECT id FROM users WHERE username = 'alice'").fetchone()[0]
    _post_forecasts(client, {FUTURE_ID: "37"})
    # A forecast on the kicked-off game, as if made before kickoff.
    db_conn.execute(
        "INSERT INTO forecasts (user_id, game_id, probability) VALUES (%s, %s, 83)",
        (alice_id, PAST_ID),
    )
    alice_page = client.get("/week/1").get_data(as_text=True)
    assert 'value="37"' in alice_page
    assert "Your forecast: 83%" in alice_page
    logout(client)

    register(client, "bob", "battery-staple")
    bob_page = client.get("/week/1").get_data(as_text=True)

    assert 'value="37"' not in bob_page
    assert "83%" not in bob_page
    assert "No forecast" in bob_page
