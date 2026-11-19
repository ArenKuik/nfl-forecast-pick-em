from tests.conftest import ADMIN, login, logout, register


def _user_count(db_conn):
    return db_conn.execute("SELECT count(*) FROM users").fetchone()[0]


def test_short_password_is_refused(client, db_conn):
    response = register(client, "alice", "short12")  # 7 characters

    assert "Passwords must be at least 8 characters." in response.get_data(as_text=True)
    assert _user_count(db_conn) == 0


def test_taken_username_is_refused(client, db_conn):
    register(client, "alice", "password-one")
    logout(client)

    response = register(client, "alice", "password-two")

    assert "That username is already taken." in response.get_data(as_text=True)
    assert _user_count(db_conn) == 1


def test_home_shows_username_after_register_and_login(client):
    register(client, "alice", "correct-horse")
    logout(client)

    response = login(client, "alice", "correct-horse")

    page = response.get_data(as_text=True)
    assert "You are logged in as <strong>alice</strong>." in page


def test_only_admin_sees_admin_text_and_sync_button(client):
    register(client, "alice", "correct-horse")
    page = client.get("/").get_data(as_text=True)
    assert "You are the administrator" not in page
    assert "Sync games" not in page
    logout(client)

    register(client, ADMIN, "admin-password")
    page = client.get("/").get_data(as_text=True)
    assert "You are the administrator" in page
    assert "Sync games" in page
