import pytest

from scoring import leaderboard, points

HOME_WIN = (24, 17)
HOME_LOSS = (10, 20)
TIE = (20, 20)


@pytest.mark.parametrize(
    "probability, scores, expected",
    [
        (50, HOME_WIN, 0),
        (50, HOME_LOSS, 0),
        (70, HOME_WIN, 16),
        (70, HOME_LOSS, -24),
        (90, HOME_WIN, 24),
        (90, HOME_LOSS, -56),
        (100, HOME_WIN, 25),
        (100, HOME_LOSS, -75),
        (0, HOME_WIN, -75),
        (50, TIE, 25),
        (70, TIE, 21),
    ],
)
def test_points(probability, scores, expected):
    result = points(probability, *scores)
    assert isinstance(result, float)
    assert result == pytest.approx(expected)


ROWS = [
    # username, probability, home_score, away_score, status
    ("alice", 70, 24, 17, "Final"),        # +16
    ("alice", 30, 10, 20, "final/OT"),     # +16
    ("alice", 100, 7, 3, "In Progress"),   # not final: ignored
    ("bob", 90, 24, 17, "Final"),          # +24
    ("bob", 90, 10, 20, "Final"),          # -56
    ("carol", 80, None, None, "Scheduled"),  # carol has nothing scored
]


def test_leaderboard_ignores_games_that_are_not_final():
    entries = leaderboard([("alice", 100, 7, 3, "In Progress"), ("alice", 70, 24, 17, "Final")])
    assert entries == [{"username": "alice", "total": 16.0, "games": 1, "average": 16.0}]


def test_leaderboard_totals_and_averages():
    by_user = {entry["username"]: entry for entry in leaderboard(ROWS)}

    assert by_user["alice"]["total"] == pytest.approx(32)
    assert by_user["alice"]["games"] == 2
    assert by_user["alice"]["average"] == pytest.approx(16)
    assert by_user["bob"]["total"] == pytest.approx(-32)
    assert by_user["bob"]["games"] == 2
    assert by_user["bob"]["average"] == pytest.approx(-16)


def test_leaderboard_order_is_total_then_username():
    rows = ROWS + [("aaron", 70, 24, 17, "Final"), ("aaron", 30, 10, 20, "Final")]  # ties alice on 32

    assert [entry["username"] for entry in leaderboard(rows)] == ["aaron", "alice", "bob"]


def test_leaderboard_leaves_out_users_with_nothing_scored():
    assert "carol" not in [entry["username"] for entry in leaderboard(ROWS)]
