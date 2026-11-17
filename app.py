"""NFL forecasting game — accounts only (register, log in, log out)."""

import os
import secrets
import sys

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

import db
import nfl_api

# On the laptop the settings come from .env; on Cloud Run they are real
# environment variables (load_dotenv never overrides those).
load_dotenv()

REQUIRED_SETTINGS = ("DATABASE_URL", "SECRET_KEY", "SITE_NAME", "ADMIN_USERNAME", "NFL_API_KEY")
_missing = [name for name in REQUIRED_SETTINGS if not os.environ.get(name)]
if _missing:
    sys.exit(f"Missing required environment variable(s): {', '.join(_missing)}")

SITE_NAME = os.environ["SITE_NAME"]
ADMIN_USERNAME = os.environ["ADMIN_USERNAME"]
MIN_PASSWORD_LENGTH = 8
MAX_USERNAME_LENGTH = 50
SYNC_SEASON = 2026

# Cloud Run sets K_SERVICE in every container it runs.
ON_CLOUD_RUN = "K_SERVICE" in os.environ

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ["SECRET_KEY"],
    SESSION_COOKIE_SECURE=ON_CLOUD_RUN,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

db.init_db(os.environ["DATABASE_URL"])


# --- helpers ---------------------------------------------------------------

def _csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


@app.before_request
def _check_csrf_and_load_user():
    if request.method == "POST":
        sent = request.form.get("csrf_token", "").encode()
        expected = session.get("csrf_token", "").encode()
        if not expected or not secrets.compare_digest(sent, expected):
            abort(400, "Invalid or missing form token. Reload the page and try again.")

    g.user = None
    user_id = session.get("user_id")
    if user_id is not None:
        g.user = db.get_user_by_id(user_id)
        if g.user is None:  # account no longer exists
            session.pop("user_id", None)


@app.context_processor
def _template_globals():
    return {
        "site_name": SITE_NAME,
        "csrf_token": _csrf_token,
        "current_user": g.get("user"),
        "is_admin": _is_admin(),
    }


def _is_admin():
    user = g.get("user")
    return bool(user) and user["username"] == ADMIN_USERNAME


def _log_in(user_id):
    session.clear()  # fresh session on login to avoid session fixation
    session["user_id"] = user_id


# --- routes ----------------------------------------------------------------

@app.get("/")
def home():
    return render_template("home.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("home"))

    username = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        error = None
        if not username:
            error = "Please choose a username."
        elif len(username) > MAX_USERNAME_LENGTH:
            error = f"Usernames can be at most {MAX_USERNAME_LENGTH} characters."
        elif len(password) < MIN_PASSWORD_LENGTH:
            error = f"Passwords must be at least {MIN_PASSWORD_LENGTH} characters."

        if error is None:
            try:
                user_id = db.create_user(username, generate_password_hash(password))
            except db.UsernameTaken:
                error = "That username is already taken."
            else:
                _log_in(user_id)
                flash("Welcome! Your account has been created.")
                return redirect(url_for("home"))

        flash(error, "error")

    return render_template("register.html", username=username)


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("home"))

    username = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = db.get_user_by_username(username)
        if user and check_password_hash(user["password_hash"], password):
            _log_in(user["id"])
            return redirect(url_for("home"))

        flash("Incorrect username or password.", "error")

    return render_template("login.html", username=username)


@app.post("/logout")
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("home"))


@app.post("/admin/sync")
def admin_sync():
    if not _is_admin():
        abort(403)

    try:
        games = nfl_api.fetch_season_games(SYNC_SEASON)
    except nfl_api.NFLApiError as exc:
        flash(f"Sync failed: {exc}", "error")
        return redirect(url_for("home"))

    # The same game could appear on two pages if the schedule shifts mid-sync.
    unique_games = list({game["id"]: game for game in games}.values())
    saved = db.upsert_games(unique_games)
    flash(f"Synced {SYNC_SEASON} season: {saved} games saved.")
    return redirect(url_for("home"))
