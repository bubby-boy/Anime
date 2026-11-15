import csv
import io
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse, unquote
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

import requests
from flask import (
    Flask, render_template, request, jsonify, session, abort, Response,
    stream_with_context, redirect, url_for, flash, send_from_directory
)

BASE_DIR = Path(__file__).resolve().parent
ANIME_FILE = BASE_DIR / "anime_urls.txt"
DB_PATH = Path(os.environ.get("DATABASE_PATH", str(BASE_DIR / "data" / "mathlab.sqlite3")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder=str(BASE_DIR), static_folder=None)
STATIC_FILES = {
    "admin.js": "static_admin.js",
    "home.js": "static_home.js",
    "library.js": "static_library.js",
    "site.js": "static_site.js",
    "popup.js": "static_popup.js",
    "style.css": "static_style.css",
    "style-additions.css": "static_style-additions.css",
}

@app.get("/static/<path:filename>", endpoint="static")
def static_asset(filename):
    # Only public assets may be served from the folder containing the app and DB.
    if filename not in STATIC_FILES:
        abort(404)
    return send_from_directory(BASE_DIR, STATIC_FILES[filename])

app.secret_key = os.environ.get("FLASK_SECRET", "development-only-change-this")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "1") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=14),
)
# Blitz is expected to sit behind one trusted reverse proxy. This makes the
# client address available for audit/allowlist checks; do not deploy behind
# an untrusted proxy chain.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

SECRET_PASSWORD = os.environ.get("SECRET_PAGE_PASSWORD", "gojo")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "overlord").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_ALLOWED_IPS = {
    x.strip() for x in os.environ.get("ADMIN_ALLOWED_IPS", "72.74.193.5").split(",") if x.strip()
}
ALLOWED_HOSTS = {"archive.org", "www.archive.org"}
TIMEOUT = 30
SERIES_NAMES = [
    "Jujutsu Kaisen", "Death Note", "My Hero Academia", "Black Clover",
    "Naruto Shippuden", "Solo Leveling", "Dragon Ball"
]

def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def get_db():
    db = sqlite3.connect(DB_PATH, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db

def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            last_seen TEXT
        );
        CREATE TABLE IF NOT EXISTS user_sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            ip_address TEXT,
            user_agent TEXT,
            created_at TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            action TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '{}',
            ip_address TEXT,
            user_agent TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            item_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(user_id, item_id)
        );
        CREATE TABLE IF NOT EXISTS progress (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            item_id TEXT NOT NULL,
            position REAL NOT NULL DEFAULT 0,
            duration REAL NOT NULL DEFAULT 0,
            completed INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(user_id, item_id)
        );
        CREATE INDEX IF NOT EXISTS idx_activity_created ON activity(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON user_sessions(user_id);
        CREATE TABLE IF NOT EXISTS screen_popups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            sender_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            pause_mode TEXT NOT NULL CHECK(pause_mode IN ('once', 'dismiss', 'admin')),
            created_at TEXT NOT NULL,
            received_at TEXT,
            dismissed_at TEXT,
            released_at TEXT,
            reply TEXT,
            replied_at TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_popup_active_user
            ON screen_popups(user_id) WHERE dismissed_at IS NULL AND released_at IS NULL;
        CREATE INDEX IF NOT EXISTS idx_popups_user ON screen_popups(user_id, id DESC);
        """)
        if ADMIN_PASSWORD:
            hashed = generate_password_hash(ADMIN_PASSWORD)
            db.execute("""
                INSERT INTO users(username,password_hash,role,status,created_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(username) DO UPDATE SET
                    password_hash=excluded.password_hash,
                    role=excluded.role,
                    status=excluded.status
            """, (ADMIN_USERNAME, hashed, "admin", "active", utcnow()))

init_db()

def client_ip():
    return request.remote_addr or "unknown"

def log_event(action, details=None, user=None):
    details = details or {}
    with get_db() as db:
        db.execute(
            "INSERT INTO activity(user_id,username,action,details,ip_address,user_agent,created_at) VALUES(?,?,?,?,?,?,?)",
            (user["id"] if user else None, user["username"] if user else None,
             action, json.dumps(details, ensure_ascii=False), client_ip(),
             request.headers.get("User-Agent", "")[:300], utcnow())
        )

def load_current_user():
    uid, sid = session.get("uid"), session.get("sid")
    if not uid or not sid:
        return None
    with get_db() as db:
        row = db.execute("""
            SELECT u.*, s.id AS session_id, s.revoked
            FROM users u JOIN user_sessions s ON s.user_id=u.id
            WHERE u.id=? AND s.id=?
        """, (uid, sid)).fetchone()
        if not row or row["revoked"] or row["status"] != "active":
            session.clear()
            return None
        db.execute("UPDATE user_sessions SET last_seen=? WHERE id=?", (utcnow(), sid))
        db.execute("UPDATE users SET last_seen=? WHERE id=?", (utcnow(), uid))
        return dict(row)

@app.before_request
def identify_user():
    from flask import g
    g.user = load_current_user()

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        from flask import g
        if not g.user:
            if request.path.startswith("/api/"):
                return jsonify(error="Login required"), 401
            return redirect(url_for("account", next=request.path))
        return fn(*args, **kwargs)
    return wrapped

def library_required(fn):
    @wraps(fn)
    @login_required
    def wrapped(*args, **kwargs):
        if not session.get("secret_unlocked"):
            abort(404)
        return fn(*args, **kwargs)
    return wrapped

def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapped(*args, **kwargs):
        from flask import g
        if g.user["role"] != "admin":
            abort(404)
        # The IP list is a second gate, never a substitute for the admin password.
        if ADMIN_ALLOWED_IPS and client_ip() not in ADMIN_ALLOWED_IPS:
            log_event("admin_ip_denied", {"path": request.path}, g.user)
            abort(404)
        return fn(*args, **kwargs)
    return wrapped

def load_catalog():
    items, seen = [], set()
    if not ANIME_FILE.exists():
        return items
    for line in ANIME_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        url = line.strip()
        if not url or url.startswith("#") or url.lower() == "mp4" or url in seen:
            continue
        p = urlparse(url)
        if p.scheme not in ("http", "https") or (p.hostname or "").lower() not in ALLOWED_HOSTS:
            continue
        seen.add(url)
        filename = unquote(p.path.rsplit("/", 1)[-1]) or "video"
        title = re.sub(r"\.(mp4|mkv|webm|mov|m4v|zip)$", "", filename, flags=re.I)
        title = re.sub(r"[_]+", " ", title).strip()
        low = title.lower()
        series = next((s for needle, s in [
            ("jujutsu kaisen", "Jujutsu Kaisen"), ("death note", "Death Note"),
            ("boku no hero", "My Hero Academia"), ("my hero academia", "My Hero Academia"),
            ("black clover", "Black Clover"), ("naruto shippuden", "Naruto Shippuden"),
            ("solo leveling", "Solo Leveling"), ("dragon ball", "Dragon Ball")
        ] if needle in low), "Other")
        ep = re.search(r"(?:episode|ep|e)[ _.-]*(\d{1,4})", title, re.I)
        display = f"Episode {int(ep.group(1)):03d} · {title}" if ep else title
        items.append({"id": uuid.uuid5(uuid.NAMESPACE_URL, url).hex, "series": series,
                      "title": display, "filename": filename, "url": url})
    return items

CATALOG = load_catalog()
CATALOG_BY_ID = {x["id"]: x for x in CATALOG}

@app.context_processor
def inject_user():
    from flask import g
    return {"current_user": getattr(g, "user", None)}

@app.get("/")
def home():
    return render_template("templates_index.html", catalog_count=len(CATALOG), series=SERIES_NAMES)

@app.get("/account")
def account():
    if getattr(__import__("flask").g, "user", None):
        return redirect(request.args.get("next") or url_for("home"))
    return render_template("templates_account.html", next_path=request.args.get("next", ""))

@app.post("/api/register")
def register():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    if not re.fullmatch(r"[A-Za-z0-9_]{3,24}", username):
        return jsonify(error="Username must be 3–24 letters, numbers, or underscores."), 400
    if len(password) < 10:
        return jsonify(error="Password must be at least 10 characters."), 400
    try:
        with get_db() as db:
            cur = db.execute("INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)",
                             (username, generate_password_hash(password), utcnow()))
            uid = cur.lastrowid
    except sqlite3.IntegrityError:
        return jsonify(error="That username is already taken."), 409
    create_session(uid, username)
    log_event("account_created", {}, {"id": uid, "username": username})
    return jsonify(ok=True)

def create_session(uid, username):
    sid = uuid.uuid4().hex
    with get_db() as db:
        db.execute("INSERT INTO user_sessions(id,user_id,ip_address,user_agent,created_at,last_seen) VALUES(?,?,?,?,?,?)",
                   (sid, uid, client_ip(), request.headers.get("User-Agent", "")[:300], utcnow(), utcnow()))
    session.clear()
    session.permanent = True
    session["uid"], session["sid"] = uid, sid
    session["username"] = username

@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        log_event("login_failed", {"username_attempt": username[:24]})
        return jsonify(error="Incorrect username or password."), 401
    if user["status"] != "active":
        log_event("login_blocked", {"username": user["username"]}, dict(user))
        return jsonify(error="This account is suspended."), 403
    create_session(user["id"], user["username"])
    log_event("login", {}, {"id": user["id"], "username": user["username"]})
    return jsonify(ok=True)

@app.post("/api/logout")
@login_required
def logout():
    from flask import g
    log_event("logout", {}, g.user)
    with get_db() as db:
        db.execute("UPDATE user_sessions SET revoked=1 WHERE id=?", (session.get("sid"),))
    session.clear()
    return jsonify(ok=True)

@app.get("/api/me")
@login_required
def me():
    from flask import g
    return jsonify(username=g.user["username"], role=g.user["role"])

@app.post("/api/unlock")
def unlock():
    data = request.get_json(silent=True) or {}
    if str(data.get("password", "")) == SECRET_PASSWORD:
        session["secret_unlocked"] = True
        return jsonify(ok=True, logged_in=bool(session.get("uid")))
    session.pop("secret_unlocked", None)
    return jsonify(ok=False), 401

@app.get("/hidden-library")
@library_required
def hidden_library():
    return render_template("templates_library.html", catalog_count=len(CATALOG), series=SERIES_NAMES)

@app.get("/api/catalog")
@library_required
def catalog():
    return jsonify(CATALOG)

@app.get("/api/favorites")
@library_required
def get_favorites():
    from flask import g
    with get_db() as db:
        rows = db.execute("SELECT item_id FROM favorites WHERE user_id=?", (g.user["id"],)).fetchall()
    return jsonify([r["item_id"] for r in rows])

@app.post("/api/favorites/<item_id>")
@library_required
def toggle_favorite(item_id):
    from flask import g
    if item_id not in CATALOG_BY_ID:
        abort(404)
    with get_db() as db:
        found = db.execute("SELECT 1 FROM favorites WHERE user_id=? AND item_id=?",
                           (g.user["id"], item_id)).fetchone()
        if found:
            db.execute("DELETE FROM favorites WHERE user_id=? AND item_id=?", (g.user["id"], item_id))
            action, favorite = "favorite_removed", False
        else:
            db.execute("INSERT OR IGNORE INTO favorites(user_id,item_id,created_at) VALUES(?,?,?)",
                       (g.user["id"], item_id, utcnow()))
            action, favorite = "favorite_added", True
    log_event(action, {"item_id": item_id}, g.user)
    return jsonify(favorite=favorite)

@app.get("/api/progress")
@library_required
def get_progress():
    from flask import g
    with get_db() as db:
        rows = db.execute("SELECT item_id,position,duration,completed,updated_at FROM progress WHERE user_id=?",
                          (g.user["id"],)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.post("/api/progress/<item_id>")
@library_required
def save_progress(item_id):
    from flask import g
    if item_id not in CATALOG_BY_ID:
        abort(404)
    data = request.get_json(silent=True) or {}
    position = max(0, min(float(data.get("position", 0) or 0), 10**9))
    duration = max(0, min(float(data.get("duration", 0) or 0), 10**9))
    completed = bool(data.get("completed")) or (duration > 0 and position / duration >= .95)
    with get_db() as db:
        db.execute("""
            INSERT INTO progress(user_id,item_id,position,duration,completed,updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(user_id,item_id) DO UPDATE SET
              position=excluded.position,duration=excluded.duration,
              completed=excluded.completed,updated_at=excluded.updated_at
        """, (g.user["id"], item_id, position, duration, int(completed), utcnow()))
    log_event("episode_completed" if completed else "progress_saved",
              {"item_id": item_id, "position": round(position), "duration": round(duration)}, g.user)
    return jsonify(ok=True, completed=completed)

@app.get("/api/download/<item_id>")
@library_required
def download(item_id):
    from flask import g
    item = CATALOG_BY_ID.get(item_id)
    if not item:
        abort(404)
    p = urlparse(item["url"])
    if p.scheme not in ("http", "https") or (p.hostname or "").lower() not in ALLOWED_HOSTS:
        abort(403)
    log_event("download_requested", {"item_id": item_id, "title": item["title"]}, g.user)
    try:
        upstream = requests.get(item["url"], stream=True, timeout=TIMEOUT,
                                headers={"User-Agent": "Mozilla/5.0"})
        upstream.raise_for_status()
    except requests.RequestException:
        abort(502)
    filename = item["filename"].replace('"', "")
    content_type = upstream.headers.get("Content-Type") or "application/octet-stream"
    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=1024 * 256):
                if chunk:
                    yield chunk
        finally:
            upstream.close()
    headers = {"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"}
    if upstream.headers.get("Content-Length"):
        headers["Content-Length"] = upstream.headers["Content-Length"]
    return Response(stream_with_context(generate()), content_type=content_type, headers=headers)

@app.post("/api/track/play/<item_id>")
@library_required
def track_play(item_id):
    from flask import g
    if item_id not in CATALOG_BY_ID:
        abort(404)
    log_event("episode_played", {"item_id": item_id, "title": CATALOG_BY_ID[item_id]["title"]}, g.user)
    return jsonify(ok=True)

@app.get("/overlord")
@admin_required
def overlord():
    return render_template("templates_admin.html")

@app.get("/api/admin/overview")
@admin_required
def admin_overview():
    with get_db() as db:
        users = db.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        active = db.execute("SELECT COUNT(*) n FROM user_sessions WHERE revoked=0 AND last_seen>=?",
                            ((datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat(timespec="seconds"),)).fetchone()["n"]
        events = db.execute("SELECT COUNT(*) n FROM activity").fetchone()["n"]
        favorites = db.execute("SELECT COUNT(*) n FROM favorites").fetchone()["n"]
        completed = db.execute("SELECT COUNT(*) n FROM progress WHERE completed=1").fetchone()["n"]
    return jsonify(users=users, online=active, events=events, favorites=favorites,
                   completed=completed, catalog=len(CATALOG))

@app.get("/api/screen-popup")
@login_required
def screen_popup():
    from flask import g
    with get_db() as db:
        row = db.execute("""
            SELECT id,title,message,pause_mode,reply,replied_at FROM screen_popups
            WHERE user_id=? AND dismissed_at IS NULL AND released_at IS NULL
        """, (g.user["id"],)).fetchone()
        if row:
            db.execute("UPDATE screen_popups SET received_at=COALESCE(received_at,?) WHERE id=?",
                       (utcnow(), row["id"]))
    response = jsonify(popup=dict(row) if row else None)
    response.headers["Cache-Control"] = "no-store"
    return response

@app.get("/api/admin/users/<int:user_id>/popups")
@admin_required
def admin_user_popups(user_id):
    with get_db() as db:
        if not db.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone():
            return jsonify(error="User not found."), 404
        rows = db.execute("""
            SELECT * FROM screen_popups WHERE user_id=? ORDER BY id DESC LIMIT 20
        """, (user_id,)).fetchall()
    response = jsonify([dict(row) for row in rows])
    response.headers["Cache-Control"] = "no-store"
    return response

@app.post("/api/admin/users/<int:user_id>/popups")
@admin_required
def admin_send_popup(user_id):
    from flask import g
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="A JSON object is required."), 400
    title, message, mode = data.get("title"), data.get("message"), data.get("pause_mode")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
        return jsonify(error="Title must contain 1-120 characters."), 400
    if not isinstance(message, str) or not 1 <= len(message.strip()) <= 2000:
        return jsonify(error="Message must contain 1-2000 characters."), 400
    if mode not in ("once", "dismiss", "admin"):
        return jsonify(error="Choose a supported pause mode."), 400
    if user_id == g.user["id"]:
        return jsonify(error="Choose another user, not your own admin account."), 400
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        target = db.execute("SELECT status FROM users WHERE id=?", (user_id,)).fetchone()
        if not target:
            return jsonify(error="User not found."), 404
        if target["status"] != "active":
            return jsonify(error="This user is not active."), 409
        if db.execute("""
            SELECT id FROM screen_popups
            WHERE user_id=? AND dismissed_at IS NULL AND released_at IS NULL
        """, (user_id,)).fetchone():
            return jsonify(error="Release the user's current popup before sending another."), 409
        cur = db.execute("""
            INSERT INTO screen_popups(user_id,sender_id,title,message,pause_mode,created_at)
            VALUES(?,?,?,?,?,?)
        """, (user_id, g.user["id"], title.strip(), message.strip(), mode, utcnow()))
        popup_id = cur.lastrowid
    log_event("admin_popup_sent", {"popup_id": popup_id, "target_user_id": user_id, "pause_mode": mode}, g.user)
    return jsonify(ok=True, id=popup_id), 201

@app.post("/api/admin/popups/<int:popup_id>/release")
@admin_required
def admin_release_popup(popup_id):
    from flask import g
    if not isinstance(request.get_json(silent=True), dict):
        return jsonify(error="A JSON object is required."), 400
    with get_db() as db:
        cur = db.execute("""
            UPDATE screen_popups SET released_at=COALESCE(released_at,?) WHERE id=?
        """, (utcnow(), popup_id))
        if not cur.rowcount:
            return jsonify(error="Popup not found."), 404
    log_event("admin_popup_released", {"popup_id": popup_id}, g.user)
    return jsonify(ok=True)

@app.post("/api/popups/<int:popup_id>/dismiss")
@login_required
def dismiss_popup(popup_id):
    from flask import g
    if not isinstance(request.get_json(silent=True), dict):
        return jsonify(error="A JSON object is required."), 400
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM screen_popups WHERE id=? AND user_id=?",
                         (popup_id, g.user["id"])).fetchone()
        if not row:
            return jsonify(error="Popup not found."), 404
        if row["pause_mode"] == "admin" and not row["released_at"]:
            return jsonify(error="The administrator must release this playback hold."), 409
        db.execute("UPDATE screen_popups SET dismissed_at=COALESCE(dismissed_at,?) WHERE id=?",
                   (utcnow(), popup_id))
    log_event("popup_dismissed", {"popup_id": popup_id}, g.user)
    return jsonify(ok=True)

@app.post("/api/popups/<int:popup_id>/reply")
@login_required
def reply_to_popup(popup_id):
    from flask import g
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="A JSON object is required."), 400
    reply = data.get("reply")
    if not isinstance(reply, str) or not 1 <= len(reply.strip()) <= 2000:
        return jsonify(error="Reply must contain 1-2000 characters."), 400
    reply = reply.strip()
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM screen_popups WHERE id=? AND user_id=?",
                         (popup_id, g.user["id"])).fetchone()
        if not row:
            return jsonify(error="Popup not found."), 404
        if row["reply"] is not None and row["reply"] != reply:
            return jsonify(error="A reply has already been sent for this popup."), 409
        # Allow an in-flight reply to finish even if the admin just released it.
        if row["reply"] is None:
            db.execute("UPDATE screen_popups SET reply=?,replied_at=? WHERE id=?",
                       (reply, utcnow(), popup_id))
    log_event("popup_replied", {"popup_id": popup_id}, g.user)
    return jsonify(ok=True)

@app.get("/api/admin/users")
@admin_required
def admin_users():
    with get_db() as db:
        rows = db.execute("""
            SELECT u.id,u.username,u.role,u.status,u.created_at,u.last_seen,
              (SELECT COUNT(*) FROM favorites f WHERE f.user_id=u.id) favorites,
              (SELECT COUNT(*) FROM progress p WHERE p.user_id=u.id) watched,
              (SELECT COUNT(*) FROM user_sessions s WHERE s.user_id=u.id AND s.revoked=0) sessions
            FROM users u ORDER BY u.id DESC
        """).fetchall()
    return jsonify([dict(r) for r in rows])

@app.get("/api/admin/sessions")
@admin_required
def admin_sessions():
    with get_db() as db:
        rows = db.execute("""
          SELECT s.id,s.user_id,u.username,s.ip_address,s.user_agent,s.created_at,s.last_seen,s.revoked
          FROM user_sessions s JOIN users u ON u.id=s.user_id ORDER BY s.last_seen DESC LIMIT 500
        """).fetchall()
    return jsonify([dict(r) for r in rows])

@app.get("/api/admin/activity")
@admin_required
def admin_activity():
    limit = min(max(int(request.args.get("limit", 150)), 1), 500)
    with get_db() as db:
        rows = db.execute("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.post("/api/admin/users/<int:user_id>/kick")
@admin_required
def admin_kick(user_id):
    with get_db() as db:
        db.execute("UPDATE user_sessions SET revoked=1 WHERE user_id=?", (user_id,))
    log_event("admin_kick", {"target_user_id": user_id})
    return jsonify(ok=True)

@app.post("/api/admin/users/<int:user_id>/ban")
@admin_required
def admin_ban(user_id):
    with get_db() as db:
        db.execute("UPDATE users SET status='banned' WHERE id=? AND role!='admin'", (user_id,))
        db.execute("UPDATE user_sessions SET revoked=1 WHERE user_id=?", (user_id,))
    log_event("admin_ban", {"target_user_id": user_id})
    return jsonify(ok=True)

@app.post("/api/admin/users/<int:user_id>/unban")
@admin_required
def admin_unban(user_id):
    with get_db() as db:
        db.execute("UPDATE users SET status='active' WHERE id=? AND role!='admin'", (user_id,))
    log_event("admin_unban", {"target_user_id": user_id})
    return jsonify(ok=True)

@app.post("/api/admin/users/<int:user_id>/reset-progress")
@admin_required
def admin_reset_progress(user_id):
    with get_db() as db:
        db.execute("DELETE FROM progress WHERE user_id=?", (user_id,))
    log_event("admin_reset_progress", {"target_user_id": user_id})
    return jsonify(ok=True)

@app.post("/api/admin/users/<int:user_id>/clear-favorites")
@admin_required
def admin_clear_favorites(user_id):
    with get_db() as db:
        db.execute("DELETE FROM favorites WHERE user_id=?", (user_id,))
    log_event("admin_clear_favorites", {"target_user_id": user_id})
    return jsonify(ok=True)

@app.post("/api/admin/users/<int:user_id>/delete")
@admin_required
def admin_delete_user(user_id):
    with get_db() as db:
        db.execute("DELETE FROM users WHERE id=? AND role!='admin'", (user_id,))
    log_event("admin_delete_user", {"target_user_id": user_id})
    return jsonify(ok=True)

@app.post("/api/admin/users/<int:user_id>/role")
@admin_required
def admin_change_role(user_id):
    data = request.get_json(silent=True) or {}
    role = data.get("role")
    if role not in ("user", "admin"):
        return jsonify(error="Role must be user or admin."), 400
    with get_db() as db:
        db.execute("UPDATE users SET role=? WHERE id=? AND username!=?",
                   (role, user_id, ADMIN_USERNAME))
    log_event("admin_role_changed", {"target_user_id": user_id, "role": role})
    return jsonify(ok=True)

@app.get("/api/admin/export")
@admin_required
def admin_export():
    with get_db() as db:
        rows = db.execute("SELECT id,user_id,username,action,details,ip_address,user_agent,created_at FROM activity ORDER BY id DESC").fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id","user_id","username","action","details","ip_address","user_agent","created_at"])
    writer.writerows([tuple(r) for r in rows])
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=mathlab_activity.csv"})

@app.get("/health")
def health():
    return jsonify(status="ok", catalog_entries=len(CATALOG))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
