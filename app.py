"""NFL forecasting game: accounts, weekly games and forecasts."""

import os
import re
import secrets
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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
FIRST_WEEK = 1
LAST_WEEK = 18
PACIFIC = ZoneInfo("America/Los_Angeles")

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


def _format_kickoff(kickoff):
    """UTC timestamp -> Pacific time like 'Sun Nov 22, 10:00 AM'."""
    local = kickoff.astimezone(PACIFIC)
    hour = local.hour % 12 or 12
    return f"{local:%a %b} {local.day}, {hour}:{local:%M %p}"


def _is_final(status):
    return (status or "").lower().startswith("final")


def _result_text(game):
    """Final score when the game is over, otherwise its status."""
    if _is_final(game["status"]) and game["home_score"] is not None and game["away_score"] is not None:
        return f"Final: {game['away_team']} {game['away_score']}, {game['home_team']} {game['home_score']}"
    status = game["status"] or "Scheduled"
    return status[:1].upper() + status[1:]


@app.get("/week")
def current_week():
    week = db.get_current_week(SYNC_SEASON, default_week=LAST_WEEK)
    return redirect(url_for("week_games", week=week))


FORECAST_FIELD_PREFIX = "forecast-"
_WHOLE_NUMBER = re.compile(r"\d{1,3}")


def _check_week(week):
    if not FIRST_WEEK <= week <= LAST_WEEK:
        abort(404)


def _render_week(week, submitted=None, errors=None, status=200):
    """Render a week's games; logged-in users also get their own forecasts.

    `submitted` and `errors` ({game_id: text}) are used to redisplay a refused form.
    """
    submitted = submitted or {}
    errors = errors or {}
    rows = db.get_week_games(SYNC_SEASON, week)
    now = datetime.now(timezone.utc)
    own = db.get_user_forecasts(g.user["id"], [row["id"] for row in rows]) if g.user else {}

    games = []
    for row in rows:
        saved = own.get(row["id"])
        games.append({
            "id": row["id"],
            "field": FORECAST_FIELD_PREFIX + row["id"],
            "home_name": row["home_name"],
            "kickoff": _format_kickoff(row["kickoff"]),
            "matchup": f"{row['away_name']} @ {row['home_name']}",
            "result": _result_text(row),
            "open": row["kickoff"] > now,
            "forecast": saved,
            "input_value": submitted.get(row["id"], "" if saved is None else saved),
            "error": errors.get(row["id"]),
        })

    return render_template(
        "week.html",
        week=week,
        games=games,
        any_open=any(game["open"] for game in games),
        errors=list(errors.values()),
        prev_week=week - 1 if week > FIRST_WEEK else None,
        next_week=week + 1 if week < LAST_WEEK else None,
    ), status


@app.get("/week/<int:week>")
def week_games(week):
    _check_week(week)
    return _render_week(week)


@app.post("/week/<int:week>/forecasts")
def save_forecasts(week):
    _check_week(week)
    if not g.user:
        flash("Log in to save forecasts.", "error")
        return redirect(url_for("login"))

    # Every posted value is checked here, whatever the page showed.
    submitted = {
        key[len(FORECAST_FIELD_PREFIX):]: value.strip()
        for key, value in request.form.items()
        if key.startswith(FORECAST_FIELD_PREFIX)
    }
    submitted = {game_id: value for game_id, value in submitted.items() if value != ""}

    games = db.get_games_by_ids(submitted.keys())
    now = datetime.now(timezone.utc)
    errors = {}
    forecasts = {}
    for game_id, value in submitted.items():
        game = games.get(game_id)
        if game is None:
            errors[game_id] = f"Game {game_id} does not exist."
            continue
        matchup = f"{game['away_name']} @ {game['home_name']}"
        if game["kickoff"] <= now:
            errors[game_id] = f"{matchup}: this game has already kicked off, so forecasts are closed."
        elif not _WHOLE_NUMBER.fullmatch(value) or int(value) > 100:
            errors[game_id] = f"{matchup}: the forecast must be a whole number from 0 to 100."
        else:
            forecasts[game_id] = int(value)

    if not errors and forecasts:
        try:
            db.save_forecasts(g.user["id"], forecasts)
        except db.ForecastClosed as exc:
            game = games[exc.game_id]
            errors[exc.game_id] = (
                f"{game['away_name']} @ {game['home_name']}: "
                "this game has already kicked off, so forecasts are closed."
            )

    if errors:
        return _render_week(week, submitted=submitted, errors=errors, status=400)

    if forecasts:
        flash(f"Saved {len(forecasts)} forecast{'' if len(forecasts) == 1 else 's'}.")
    else:
        flash("Nothing to save: every forecast was empty.")
    return redirect(url_for("week_games", week=week))
