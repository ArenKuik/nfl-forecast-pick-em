from tests.conftest import SAMPLE_GAMES


def test_week_page_lists_that_weeks_games(client, save_games):
    save_games(SAMPLE_GAMES)

    response = client.get("/week/1")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    # Both week-1 games, earliest kickoff first, in Pacific time.
    first = page.index("Dallas Cowboys @ Philadelphia Eagles")
    second = page.index("Baltimore Ravens @ Kansas City Chiefs")
    assert first < second
    assert "Wed Sep 9, 5:20 PM" in page
    assert "Thu Sep 10, 5:20 PM" in page
    assert "Final: BAL 24, KC 17" in page
    # Games from other weeks are not listed.
    assert "New York Jets" not in page


def test_week_out_of_range_is_404(client):
    assert client.get("/week/25").status_code == 404


def test_week_redirects_to_earliest_unfinished_week(client, save_games):
    save_games(SAMPLE_GAMES)  # week 1 final, weeks 2 and 3 scheduled

    response = client.get("/week")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/week/2")
