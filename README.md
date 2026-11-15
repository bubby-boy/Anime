# MathLab + hidden anime library + OVERLORD admin

## Easy GitHub upload (no folders)
Extract this ZIP, select all the files inside it, and upload them together to
the root of your GitHub repository using Add file -> Upload files.
Do not upload the ZIP itself. Do not rename any files or create subfolders.
Commit the upload, then rebuild/redeploy on Blitz.

Files such as `templates_index.html`, `static_admin.js`, and `static_style.css`
are deliberately flat. This version of `app.py` loads those exact names while
keeping the website's existing URLs. Upload this `app.py` along with the assets.
Old `templates` or `static` folders, if present, are not used by this version.
Preserve your database and environment settings. The included `anime_urls.txt`
now contains your supplied 643 Archive.org URLs.

## Features
- Public MathLab calculator and linear equation solver
- Secret library unlock in Credits → About Us using `SECRET_PAGE_PASSWORD` (default `gojo`)
- Account registration/login with password hashes
- Per-account favorites and saved episode progress
- Session tracking, activity audit trail and recorded client IPs
- OVERLORD admin dashboard: users, sessions, live activity, kick, ban/unban, delete, reset progress, clear favorites, role changes, CSV export

## Run locally
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```
Open http://127.0.0.1:5000.
For local HTTP testing, set `COOKIE_SECURE=0` before starting Python
(PowerShell: `$env:COOKIE_SECURE="0"`). Keep `COOKIE_SECURE=1` on Blitz HTTPS.

## Blitz environment variables
Set these in the app's environment settings:
- `FLASK_SECRET`: long random secret; do not reuse the example
- `SECRET_PAGE_PASSWORD`: `gojo` or a different private unlock phrase
- `ADMIN_USERNAME`: `overlord` (optional; default is `overlord`)
- `ADMIN_PASSWORD`: strong unique password; REQUIRED to bootstrap the admin account
- `ADMIN_ALLOWED_IPS`: `72.74.193.5` (comma-separated if more than one trusted admin IP)
- `DATABASE_PATH`: persistent mounted path, e.g. `/data/mathlab.sqlite3` if Blitz provides a persistent volume
- `COOKIE_SECURE`: `1` on HTTPS

The admin panel is at `/overlord`, but the route returns 404 unless the signed-in account is an admin and the client IP is allowlisted. The IP allowlist is an additional check, not authentication. Configure the real trusted proxy behavior before relying on IP restrictions. If your public IP changes, update `ADMIN_ALLOWED_IPS` in Blitz. Do not use an IP address as a user identity.

## Persistence
Use a persistent disk/volume for `DATABASE_PATH`. If the host filesystem is ephemeral, accounts, favorites, progress and audit logs may be lost on redeploy/restart.

Admin bootstrap uses an atomic SQLite upsert so multiple Gunicorn workers can
initialize the same account concurrently. When `ADMIN_PASSWORD` is set, startup
updates the configured account's password, role, and status while retaining its
ID, creation date, and related data. No database reset is needed for this fix.

## Tests
Run `python -m unittest discover -s . -v` after installing the requirements.
The bootstrap tests use temporary SQLite databases and a test password-hash
stub. Page tests use Flask's test client and an isolated temporary database.

## Reconstructed interface and deployment
The homepage, library template, and base stylesheet are new replacements for
files omitted from the original paste. The existing account/admin pages and
library script are retained. The homepage includes a two-number calculator
and a coefficient-based linear equation solver (`ax + b = c`).

Deploy all the flat files together, including `requirements.txt` and `Dockerfile`,
rather than only `app.py`. Preserve your existing database and environment
variables. Configure `/health` as the health-check path.

To open the library, sign in or create an account first, then open the homepage's
Credits -> About Us, enter `SECRET_PAGE_PASSWORD`, and select Continue.
Signing out or signing in again clears the unlock state.

`static_style-additions.css` contains the original appended account/admin CSS
and is imported by `static_style.css` through the `/static/` asset route.
That route serves only explicitly listed public assets, never backend code,
templates, environment files, or database files. The database may create a
runtime `data` directory; no upload folders are needed.
The historical generator is omitted because it can overwrite the fixes.

## Activity and privacy
The app records login attempts, logins, logouts, favorites, episode plays, progress saves, download requests, and administrative actions. The admin dashboard shows the recorded IP address, username, event details, and timestamp. It does not collect passwords or record keystrokes. Tell users that activity and IP logging is enabled.

## Targeted popups and user responses
In `/overlord`, use **User popups and playback controls**:
1. Select one active user (not your own admin account).
2. Enter a plain-text title and message.
3. Choose **Pause once**, **Until user dismisses**, or **Until I release it**.
4. Send. The selected user's signed-in homepage/library/admin tabs poll every
   2 seconds; background browser tabs may take longer.

Users can send one plain-text response per popup. The dashboard shows that
response and delivery status, refreshed every 5 seconds. Responses are visible
to site administrators. HTML in messages or responses is displayed as text.

Only one popup can remain active per user. Use **Release / close popup** to
clear it before sending another. A reply does not release a playback hold.
Dismissal or release applies across that user's tabs and devices when they
next poll. Playback is never automatically resumed; the user presses Play.
Pause-once mode pauses each page when it first receives the message, but allows
the user to resume while the message remains open.

Pending popups and holds survive reloads and restarts, including offline users'
next visits, until dismissed or released. Administrators should release holds
promptly. Network errors are shown, and an existing hold remains until the page
can check its status again.

These are in-page controls, not operating-system controls or an access-control
boundary. They cannot control other sites/apps, and users can bypass JavaScript.
Inform users that playback can be paused and their responses are stored.
Existing database files are upgraded automatically; do not delete the database.

## Video catalog
`anime_urls.txt` contains the 643 unique Archive.org URLs you supplied, copied
unchanged. Add or edit video URLs you are authorized to use, one per line,
then restart the app. Catalog loading was verified; availability and playback
of individual remote videos have not been verified.
The loader only accepts Archive.org hosts. An empty catalog does not prevent
the site from running. Playback uses the direct source URL; downloads stream
through Flask. Availability depends on the source host and your rights to the
content.

## Security notes
- Change all default secrets before deployment.
- Never publish `ADMIN_PASSWORD` or `FLASK_SECRET` in GitHub.
- Keep admin access limited to people you trust.
- IP addresses may be shared, dynamic, or inaccurate; use account authentication as the primary identity check.
