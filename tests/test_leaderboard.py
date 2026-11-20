import pytest

from tests.conftest import login, logout, register


def _team(team_id, name, abbreviation):
    return {"team_id": team_id, "name": name, "abbreviation": abbreviation}


CHIEFS = _team(1, "Kansas City Chiefs", "KC")
RAVENS = _team(2, "Baltimore Ravens", "BAL")
EAGLES = _team(3, "Philadelphia Eagles", "PHI")
COWBOYS = _team(4, "Dallas Cowboys", "DAL")
BILLS = _team(5, "Buffalo Bills", "BUF")
JETS = _team(6, "New York Jets", "NYJ")

GAMES = [
    {   # week 1: home win
        "game_id": 7001, "season": 2026, "week": 1, "start_time": "2026-09-10T00:20:00Z",
        "status": "Final", "home_team": CHIEFS, "away_team": RAVENS, "home_score": 24, "away_score": 17,
    },
    {   # week 2: home loss
        "game_id": 7002, "season": 2026, "week": 2, "start_time": "2026-09-17T00:20:00Z",
        "status": "Final", "home_team": EAGLES, "away_team": COWBOYS, "home_score": 10, "away_score": 20,
    },
    {   # week 2: not final yet
        "game_id": 7003, "season": 2026, "week": 2, "start_time": "2026-09-20T17:00:00Z",
        "status": "In Progress", "home_team": BILLS, "away_team": JETS, "home_score": 7, "away_score": 0,
    },
]

FORECASTS = [
    ("alice", "7001", 70),   # +16
    ("alice", "7002", 30),   # +16
    ("bob", "7001", 90),     # +24
    ("bob", "7002", 90),     # -56
    ("bob", "7003", 100),    # not final: no points
    ("carol", "7003", 80),   # nothing scored: not listed
]


@pytest.fixture(autouse=True)
def clean_forecasts(db_conn):
    """The shared setup empties users and games; forecasts are emptied here."""
    db_conn.execute("DELETE FROM forecasts")
    yield
    db_conn.execute("DELETE FROM forecasts")


@pytest.fixture
def season(client, db_conn, save_games):
    """Three users, three games and their forecasts, written straight to the database
    (the games have kicked off, so the form would refuse them)."""
    save_games(GAMES)
    for username in ("alice", "bob", "carol"):
        register(client, username, "password-123")
        logout(client)
    ids = dict(db_conn.execute("SELECT username, id FROM users").fetchall())
    for username, game_id, probability in FORECASTS:
        db_conn.execute(
            "INSERT INTO forecasts (user_id, game_id, probability) VALUES (%s, %s, %s)",
            (ids[username], game_id, probability),
        )


def _rows(page):
    """Usernames in the order the leaderboard table lists them."""
    body = page.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    listed = [name for name in ("alice", "bob", "carol") if f"<td>{name}</td>" in body]
    return sorted(listed, key=lambda name: body.index(f"<td>{name}</td>"))


def test_season_leaderboard(client, season):
    response = client.get("/leaderboard")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Leaderboard: Season" in page
    assert _rows(page) == ["alice", "bob"]  # carol has nothing scored
    assert '<td class="num">32.0</td>' in page   # alice total
    assert '<td class="num">16.0</td>' in page   # alice average
    assert '<td class="num">-32.0</td>' in page  # bob total
    assert '<td class="num">-16.0</td>' in page  # bob average


def test_week_leaderboard(client, season):
    page = client.get("/leaderboard?week=1").get_data(as_text=True)
    assert "Leaderboard: Week 1" in page
    assert _rows(page) == ["bob", "alice"]
    assert '<td class="num">24.0</td>' in page

    page = client.get("/leaderboard?week=2").get_data(as_text=True)
    assert _rows(page) == ["alice", "bob"]
    assert '<td class="num">-56.0</td>' in page


def test_leaderboard_has_links_to_switch_weeks(client, season):
    page = client.get("/leaderboard?week=1").get_data(as_text=True)

    assert 'href="/leaderboard"' in page
    assert 'href="/leaderboard?week=2"' in page
    assert 'href="/leaderboard?week=18"' in page


def test_leaderboard_is_visible_to_visitors_and_linked_in_nav(client, season):
    page = client.get("/").get_data(as_text=True)
    assert 'href="/leaderboard"' in page
    assert client.get("/leaderboard").status_code == 200


@pytest.mark.parametrize("week", ["0", "19", "abc"])
def test_invalid_week_is_404(client, week):
    assert client.get(f"/leaderboard?week={week}").status_code == 404


def test_week_page_shows_points_on_final_games(client, season):
    login(client, "bob", "password-123")
    page = client.get("/week/2").get_data(as_text=True)

    assert '<span class="points negative">' in page
    assert "-56.0 points" in page

    page = client.get("/week/1").get_data(as_text=True)
    assert '<span class="points positive">' in page
    assert "+24.0 points" in page
