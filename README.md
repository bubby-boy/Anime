# MediaVault — Flask Manga + Anime Downloader

## Local setup (Windows / VS Code)

1. Extract this folder and open it in VS Code.
2. Make sure `app.py` and `anime_urls.txt` stay in the same folder.
3. In the VS Code terminal, run:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   python app.py
   ```

4. Open `http://127.0.0.1:5000` in Chrome or Edge.
5. The math cover opens first. Click the yellow circle at the top-right to enter MediaVault.

## Download folders

- On localhost, enter an absolute Windows path such as `C:\\Users\\YourName\\Downloads\\MediaVault` or a relative path such as `downloads\\manga`.
- On a remote deployment, the app only accepts folders under `DOWNLOAD_ROOT` (default `/app/downloads`). The files are stored on the server, not directly on your PC. Use the generated ZIP link to retrieve them.
- For a persistent Blitz deployment, configure a persistent volume if Blitz provides one. Container-local files may be cleared on rebuild/restart.

## Manga

Enter a MangaTown slug and chapter number. Page 1 is requested as `/c{chapter}/`; page 2 onward is requested as `/c{chapter}/{page}.html`. It stops on a missing page or when no reader image is found. It saves each image and creates a ZIP.

## Anime

The supplied `anime_urls.txt` is loaded at startup. The catalog accepts direct `archive.org` URLs from that file. Search/filter, select up to 25 at a time, and download the selected original files into the chosen folder. A ZIP is also created for browser retrieval.

## GitHub / Blitz

Commit `app.py`, `anime_urls.txt`, `requirements.txt`, `Dockerfile`, and this README to your repository. Build the app with the Dockerfile and expose port 5000 (or let the platform provide `PORT`). Set `DOWNLOAD_ROOT=/app/downloads`.

## Notes

- Works in current Chrome and Microsoft Edge; it is a normal Flask web app.
- Use only media you own or have permission to download. The app does not bypass DRM, logins, paywalls, or other access controls.
- Site markup and access policies can change. If MangaTown changes its reader markup, the image selector may need updating.
- The app is intended for personal/local use. Before making it public, add authentication and rate limiting; otherwise other visitors could use your server's download capacity.
