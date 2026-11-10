# MathLab + hidden media library

A Flask app with a public math interface and an unadvertised, session-protected media library.

## Files
- `app.py` — Flask routes and catalog loader
- `anime_urls.txt` — supplied archive.org direct video URLs
- `templates/` — public math page and hidden library page
- `static/` — styles and browser JavaScript
- `Dockerfile`, `requirements.txt` — Blitz/container deployment

## Run locally
```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```
Open http://127.0.0.1:5000

## Deploy to Blitz
Import this GitHub repository as a Docker app. The Dockerfile starts Gunicorn.
Set these environment variables:
- `FLASK_SECRET`: a long random secret value
- `SECRET_PAGE_PASSWORD`: `gojo` (or change it to a private password)

The app listens on the platform-provided `PORT`.

## Hidden page
The public UI has no link to the library. In Credits → About Us, enter the password. The server checks it and sets a session flag before serving the unlisted library route.

## Video links
The catalog uses the provided direct Archive.org links. Browser playback depends on the remote host's CORS/media behavior. Download requests are streamed through Flask from Archive.org, without saving a second copy permanently on the app server. Use only links you have permission to access and download. The catalog loader intentionally accepts Archive.org URLs only.

## Important
The 643 URLs are preserved from the supplied `anime_urls.txt`. If an individual source has been removed or restricts access, that item may not play or download.
