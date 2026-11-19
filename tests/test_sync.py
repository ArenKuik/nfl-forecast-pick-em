from tests.conftest import ADMIN, csrf_from, register, sync


def _games(db_conn):
    return db_conn.execute(
        "SELECT id, home_score, away_score FROM games ORDER BY id"
    ).fetchall()


def test_non_admin_cannot_sync(client, db_conn, fake_api):
    register(client, "alice", "correct-horse")
    token = csrf_from(client, "/")

    response = client.post("/admin/sync", data={"csrf_token": token})

    assert response.status_code == 403
    assert fake_api.calls == []
    assert _games(db_conn) == []


def test_admin_sync_saves_games(client, db_conn, fake_api):
    register(client, ADMIN, "admin-password")

    response = sync(client)

    assert fake_api.calls == [2026]
    assert "4 games saved" in response.get_data(as_text=True)
    assert [row[0] for row in _games(db_conn)] == ["1000", "1001", "2001", "3001"]


def test_second_sync_updates_without_duplicates(client, db_conn, fake_api):
    register(client, ADMIN, "admin-password")
    sync(client)

    week2 = next(g for g in fake_api.raw_games if g["game_id"] == 2001)
    week2.update(status="Final", home_score=27, away_score=3)
    sync(client)

    rows = _games(db_conn)
    assert len(rows) == 4
    assert ("2001", 27, 3) in rows
    status = db_conn.execute("SELECT status FROM games WHERE id = '2001'").fetchone()[0]
    assert status == "Final"
