import os
import re
import uuid
import mimetypes
from pathlib import Path
from urllib.parse import urlparse, unquote

import requests
from flask import Flask, render_template, request, jsonify, session, abort, Response, stream_with_context

BASE_DIR = Path(__file__).resolve().parent
ANIME_FILE = BASE_DIR / "anime_urls.txt"
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change-this-secret-before-deploying")
SECRET_PASSWORD = os.environ.get("SECRET_PAGE_PASSWORD", "gojo")
ALLOWED_HOSTS = {"archive.org", "www.archive.org"}
TIMEOUT = 30

SERIES_NAMES = [
    "Jujutsu Kaisen", "Death Note", "My Hero Academia", "Black Clover",
    "Naruto Shippuden", "Solo Leveling", "Dragon Ball"
]

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

@app.get("/")
def home():
    return render_template("index.html", catalog_count=len(CATALOG), series=SERIES_NAMES)

@app.post("/api/unlock")
def unlock():
    data = request.get_json(silent=True) or {}
    if str(data.get("password", "")) == SECRET_PASSWORD:
        session["secret_unlocked"] = True
        return jsonify(ok=True)
    session.pop("secret_unlocked", None)
    return jsonify(ok=False), 401

@app.get("/hidden-library")
def hidden_library():
    if not session.get("secret_unlocked"):
        abort(404)
    return render_template("library.html", catalog_count=len(CATALOG), series=SERIES_NAMES)

@app.get("/api/catalog")
def catalog():
    if not session.get("secret_unlocked"):
        abort(404)
    return jsonify(CATALOG)

@app.get("/api/download/<item_id>")
def download(item_id):
    if not session.get("secret_unlocked"):
        abort(404)
    item = next((x for x in CATALOG if x["id"] == item_id), None)
    if not item:
        abort(404)
    p = urlparse(item["url"])
    if p.scheme not in ("http", "https") or (p.hostname or "").lower() not in ALLOWED_HOSTS:
        abort(403)
    try:
        upstream = requests.get(item["url"], stream=True, timeout=TIMEOUT,
                                headers={"User-Agent": "Mozilla/5.0"})
        upstream.raise_for_status()
    except requests.RequestException:
        abort(502)
    filename = item["filename"].replace('"', "")
    content_type = upstream.headers.get("Content-Type") or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=1024 * 256):
                if chunk:
                    yield chunk
        finally:
            upstream.close()
    headers = {"Content-Disposition": f'attachment; filename="{filename}"',
               "Cache-Control": "no-store"}
    if upstream.headers.get("Content-Length"):
        headers["Content-Length"] = upstream.headers["Content-Length"]
    return Response(stream_with_context(generate()), content_type=content_type, headers=headers)

@app.get("/health")
def health():
    return jsonify(status="ok", catalog_entries=len(CATALOG))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
