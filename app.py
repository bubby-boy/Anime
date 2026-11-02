from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template_string, request, send_file, abort
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
ANIME_URLS_FILE = BASE_DIR / "anime_urls.txt"
DOWNLOAD_ROOT = Path(os.environ.get("DOWNLOAD_ROOT", str(BASE_DIR / "downloads"))).resolve()
PORT = int(os.environ.get("PORT", "5000"))
MAX_MANGA_PAGES = int(os.environ.get("MAX_MANGA_PAGES", "1500"))
MAX_ANIME_ITEMS_PER_JOB = int(os.environ.get("MAX_ANIME_ITEMS_PER_JOB", "25"))
REQUEST_TIMEOUT = (15, 45)
USER_AGENT = "MediaVaultDownloader/1.0 (personal media utility)"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

jobs: dict[str, dict] = {}
jobs_lock = threading.RLock()


def clean_name(value: str, fallback: str = "download") -> str:
    value = unquote(str(value or ""))
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value[:120] or fallback)


def get_destination(raw: str, category: str) -> Path:
    """Localhost can choose an absolute path. Remote deployments are sandboxed under DOWNLOAD_ROOT."""
    raw = (raw or "").strip()
    is_local = request.remote_addr in {"127.0.0.1", "::1"}
    if not raw:
        target = DOWNLOAD_ROOT / category
    else:
        candidate = Path(raw).expanduser()
        if is_local and candidate.is_absolute():
            target = candidate
        else:
            # Remote users may only choose a subfolder under DOWNLOAD_ROOT.
            target = (DOWNLOAD_ROOT / raw).resolve()
            if target != DOWNLOAD_ROOT and DOWNLOAD_ROOT not in target.parents:
                raise ValueError("Choose a folder inside the app's downloads directory.")
    target.mkdir(parents=True, exist_ok=True)
    return target


def load_anime_catalog() -> list[dict]:
    items = []
    if not ANIME_URLS_FILE.exists():
        return items
    seen = set()
    for raw in ANIME_URLS_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        url = raw.strip()
        if not url or url.startswith("#") or url.lower() == "mp4" or url in seen:
            continue
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or parsed.hostname not in {"archive.org", "www.archive.org"}:
            continue
        seen.add(url)
        parts = parsed.path.strip("/").split("/")
        collection = parts[1] if len(parts) > 1 and parts[0] == "download" else "Archive.org"
        filename = unquote(parts[-1]) if parts else "video"
        stem = re.sub(r"\.(mp4|mkv|webm|mov|m4v)$", "", filename, flags=re.I)
        title = re.sub(r"[_]+", " ", stem).strip()
        series = collection.replace("-", " ").replace("_", " ").title()
        # Derive a friendlier series label from common filename patterns.
        lower = title.lower()
        known = [
            ("jujutsu kaisen", "Jujutsu Kaisen"),
            ("death note", "Death Note"),
            ("boku no hero", "My Hero Academia"),
            ("my hero academia", "My Hero Academia"),
            ("black clover", "Black Clover"),
            ("naruto shippuden", "Naruto Shippuden"),
            ("solo leveling", "Solo Leveling"),
            ("dragon ball", "Dragon Ball"),
        ]
        for needle, label in known:
            if needle in lower:
                series = label
                break
        episode = re.search(r"(?:episode|ep|e)[ _.-]*(\d{1,4})", title, re.I)
        if episode:
            pretty_title = f"Episode {int(episode.group(1)):03d} · {title}"
        else:
            pretty_title = title or filename
        items.append({
            "id": uuid.uuid5(uuid.NAMESPACE_URL, url).hex,
            "series": series,
            "title": pretty_title,
            "filename": filename,
            "url": url,
        })
    return items


ANIME_CATALOG = load_anime_catalog()
ANIME_BY_ID = {item["id"]: item for item in ANIME_CATALOG}


def new_job(kind: str, name: str) -> str:
    jid = uuid.uuid4().hex[:12]
    with jobs_lock:
        jobs[jid] = {
            "id": jid, "kind": kind, "name": name, "status": "queued",
            "percent": 0, "message": "Waiting to start", "current": "",
            "done": 0, "total": 0, "files": [], "created": datetime.utcnow().isoformat() + "Z",
        }
    return jid


def update_job(jid: str, **changes) -> None:
    with jobs_lock:
        if jid in jobs:
            jobs[jid].update(changes)


def page_image_url(html: str, page_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    # Prefer likely reader containers before considering every image on the page.
    selectors = [
        "#chapterImages img", ".chapter-content img", ".chapter_content img",
        ".reader-area img", ".reader-main img", ".page-break img",
        ".reading-content img", ".manga-page img", "article img",
    ]
    candidates = []
    for selector in selectors:
        for img in soup.select(selector):
            candidates.append(img)
        if candidates:
            break
    if not candidates:
        candidates = soup.find_all("img")

    def img_src(img):
        for attr in ("data-src", "data-original", "data-lazy-src", "data-url", "src"):
            value = img.get(attr)
            if value and not value.startswith("data:"):
                return value.strip()
        srcset = img.get("srcset")
        if srcset:
            return srcset.split(",")[-1].strip().split()[0]
        return None

    rejected = ("logo", "avatar", "banner", "ad-", "ads/", "icon", "loading", "blank")
    for img in candidates:
        src = img_src(img)
        if not src:
            continue
        full = urljoin(page_url, src)
        parsed = urlparse(full)
        if parsed.scheme not in ("http", "https"):
            continue
        if any(word in (parsed.path + " " + str(img.get("class", ""))).lower() for word in rejected):
            continue
        # Ignore tiny known UI images where dimensions are provided.
        try:
            w, h = int(img.get("width", 0)), int(img.get("height", 0))
            if w and h and (w < 100 or h < 100):
                continue
        except (TypeError, ValueError):
            pass
        return full

    og = soup.find("meta", attrs={"property": "og:image"})
    if og and og.get("content"):
        return urljoin(page_url, og["content"])
    return None


def manga_page_url(slug: str, chapter: str, page: int) -> str:
    base = f"https://www.mangatown.com/manga/{slug}/c{chapter}/"
    return base if page == 1 else urljoin(base, f"{page}.html")


def manga_worker(jid: str, slug: str, chapter: str, destination: Path) -> None:
    folder = destination / f"{clean_name(slug)}_chapter_{clean_name(chapter)}"
    folder.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Referer": "https://www.mangatown.com/"})
    downloaded = []
    try:
        update_job(jid, status="running", message="Finding chapter pages…")
        for page_no in range(1, MAX_MANGA_PAGES + 1):
            page_url = manga_page_url(slug, chapter, page_no)
            update_job(jid, current=f"Checking page {page_no}", message=f"Checking page {page_no}", done=page_no - 1, total=0)
            response = session.get(page_url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if response.status_code in (404, 410):
                break
            response.raise_for_status()
            image_url = page_image_url(response.text, response.url)
            if not image_url:
                # A page that loads but has no reader image marks the end of the chapter.
                break
            image_response = session.get(image_url, timeout=REQUEST_TIMEOUT, stream=True)
            image_response.raise_for_status()
            content_type = image_response.headers.get("Content-Type", "").split(";")[0].lower()
            ext = mimetypes.guess_extension(content_type) if content_type.startswith("image/") else None
            if ext == ".jpe": ext = ".jpg"
            if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
                ext = Path(urlparse(image_url).path).suffix.lower()
            if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
                ext = ".img"
            file_path = folder / f"page_{page_no:04d}{ext}"
            total_bytes = 0
            with file_path.open("wb") as out:
                for chunk in image_response.iter_content(128 * 1024):
                    if chunk:
                        out.write(chunk)
                        total_bytes += len(chunk)
                        if total_bytes > 35 * 1024 * 1024:
                            raise ValueError(f"Page {page_no} exceeded the 35 MB image limit.")
            if total_bytes == 0:
                file_path.unlink(missing_ok=True)
                break
            downloaded.append(file_path)
            update_job(jid, done=len(downloaded), message=f"Downloaded page {page_no}", current=f"Page {page_no}")
        if not downloaded:
            raise RuntimeError("No chapter images were found. The page layout may have changed, or the chapter may not be accessible.")
        zip_path = destination / f"{clean_name(slug)}_chapter_{clean_name(chapter)}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as archive:
            for path in downloaded:
                archive.write(path, arcname=f"{folder.name}/{path.name}")
        update_job(jid, status="complete", percent=100, total=len(downloaded), done=len(downloaded),
                   message=f"Saved {len(downloaded)} pages", files=[str(p) for p in downloaded],
                   zip_path=str(zip_path), download_url=f"/api/jobs/{jid}/file")
    except Exception as exc:
        update_job(jid, status="error", message=str(exc)[:300])
    finally:
        session.close()


def anime_worker(jid: str, items: list[dict], destination: Path) -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    saved = []
    try:
        update_job(jid, status="running", total=len(items), message="Starting downloads…")
        for index, item in enumerate(items, 1):
            filename = clean_name(item["filename"], f"episode_{index}.mp4")
            if not Path(filename).suffix:
                filename += ".mp4"
            target = destination / filename
            if target.exists():
                target = destination / f"{Path(filename).stem}_{jid[:5]}{Path(filename).suffix}"
            partial = target.with_suffix(target.suffix + ".part")
            update_job(jid, current=filename, message=f"Downloading {index}/{len(items)}: {filename}", percent=round((index - 1) * 100 / len(items)))
            try:
                with session.get(item["url"], stream=True, timeout=REQUEST_TIMEOUT, allow_redirects=True) as response:
                    response.raise_for_status()
                    with partial.open("wb") as out:
                        for chunk in response.iter_content(512 * 1024):
                            if chunk:
                                out.write(chunk)
                partial.replace(target)
                saved.append(target)
                update_job(jid, done=index, files=[str(p) for p in saved], percent=round(index * 100 / len(items)), message=f"Saved {index}/{len(items)}")
            except Exception:
                partial.unlink(missing_ok=True)
                raise
        zip_path = destination / f"anime_download_{jid}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as archive:
            for path in saved:
                archive.write(path, arcname=path.name)
        update_job(jid, status="complete", percent=100, total=len(items), done=len(saved),
                   message=f"Downloaded {len(saved)} video(s)", zip_path=str(zip_path),
                   download_url=f"/api/jobs/{jid}/file")
    except Exception as exc:
        update_job(jid, status="error", message=str(exc)[:300])
    finally:
        session.close()


COVER_HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MediaVault — Cover</title>
<style>
*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:#080b13;color:#edf1fa;font-family:Inter,Segoe UI,Arial,sans-serif}body{overflow:hidden}.cover{min-height:100vh;position:relative;display:grid;place-items:center;isolation:isolate;background:radial-gradient(ellipse at 50% 45%,#18233b 0,#0b1020 45%,#070910 100%)}.cover:before{content:"";position:absolute;inset:-20%;z-index:-1;opacity:.2;background-image:linear-gradient(rgba(150,177,230,.25) 1px,transparent 1px),linear-gradient(90deg,rgba(150,177,230,.25) 1px,transparent 1px);background-size:54px 54px;transform:perspective(600px) rotateX(58deg) scale(1.4);transform-origin:center 70%}.equations{position:absolute;inset:0;pointer-events:none;color:#94a9d4;opacity:.28;font-family:Georgia,serif}.eq{position:absolute;white-space:nowrap}.eq:nth-child(1){top:15%;left:8%;font-size:clamp(15px,2vw,26px)}.eq:nth-child(2){top:28%;right:8%;font-size:clamp(16px,2vw,28px)}.eq:nth-child(3){bottom:19%;left:10%;font-size:clamp(14px,1.8vw,23px)}.eq:nth-child(4){bottom:24%;right:8%;font-size:clamp(14px,1.8vw,23px)}.center{text-align:center;padding:30px;max-width:850px}.eyebrow{font-size:11px;letter-spacing:.42em;text-transform:uppercase;color:#f2c94c}.center h1{font-size:clamp(46px,9vw,108px);line-height:.95;letter-spacing:-.07em;margin:20px 0 14px;font-weight:850}.center h1 span{color:#f1c84b}.center p{color:#aebbd2;letter-spacing:.16em;text-transform:uppercase;font-size:12px}.formula{margin:40px auto 0;color:#d8e1f5;font:clamp(16px,2vw,24px) Georgia,serif;letter-spacing:.04em}.enter{position:fixed;right:25px;top:23px;width:74px;height:74px;border-radius:50%;background:#f2c94c;color:#17130a;border:0;font-weight:900;cursor:pointer;box-shadow:0 0 0 8px #f2c94c20,0 0 40px #f2c94c66;transition:.2s}.enter:hover{transform:scale(1.08);box-shadow:0 0 0 12px #f2c94c25,0 0 55px #f2c94c99}.enter small{display:block;font-size:9px;letter-spacing:.08em}.bottom{position:fixed;bottom:22px;left:0;right:0;text-align:center;color:#64718b;font-size:10px;letter-spacing:.2em;text-transform:uppercase}
</style></head><body><main class="cover"><div class="equations"><span class="eq">∫₀∞ e⁻ˣ² dx = √π / 2</span><span class="eq">∇ × E = −∂B/∂t</span><span class="eq">eⁱᵖ + 1 = 0</span><span class="eq">x² + y² = r²</span></div><button class="enter" onclick="location.href='/app'"><span>GO</span><small>ENTER ↗</small></button><div class="center"><div class="eyebrow">A personal media workspace</div><h1>MEDIA<span>VAULT</span></h1><p>Collect · organize · preserve</p><div class="formula">f(x) = ∑ aₙxⁿ &nbsp; · &nbsp; Δ = b² − 4ac &nbsp; · &nbsp; lim x→∞</div></div><div class="bottom">Mathematics is the language of the universe</div></main></body></html>"""

APP_HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MediaVault — Downloader</title>
<style>
:root{color-scheme:dark;--bg:#090d18;--panel:#111a2b;--panel2:#162239;--line:#263653;--text:#eef3ff;--muted:#9eacc6;--purple:#9b8cff;--yellow:#f2c94c;--green:#67d6a9;--red:#ff7c91}*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 15% 0,#19213c 0,transparent 38%),var(--bg);color:var(--text);font:14px/1.5 Inter,Segoe UI,Arial,sans-serif}.top{height:70px;padding:0 26px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:20px;background:#090d18dd;position:sticky;top:0;z-index:4;backdrop-filter:blur(12px)}.brand{font-size:20px;font-weight:900;letter-spacing:-.05em;color:var(--text);text-decoration:none}.brand span{color:var(--purple)}.topright{margin-left:auto;display:flex;align-items:center;gap:12px;color:var(--muted);font-size:12px}.back{color:var(--yellow);text-decoration:none}.wrap{max-width:1220px;margin:0 auto;padding:30px 22px 70px}.hero{display:flex;justify-content:space-between;align-items:end;gap:20px;margin:12px 0 24px}.hero h1{font-size:34px;letter-spacing:-.05em;margin:0}.hero p{color:var(--muted);margin:5px 0 0}.tabs{display:flex;gap:8px;margin-bottom:18px}.tab{border:1px solid var(--line);background:var(--panel);color:var(--muted);border-radius:12px;padding:11px 18px;font-weight:800;cursor:pointer}.tab.active{background:#292444;border-color:#7768bd;color:#fff}.panel{background:#10192a;border:1px solid var(--line);border-radius:18px;padding:22px;box-shadow:0 18px 60px #0003}.hidden{display:none!important}.grid{display:grid;grid-template-columns:1.05fr .95fr;gap:18px}.fieldgrid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.field{display:flex;flex-direction:column;gap:7px;margin-bottom:13px}.field label{color:#c4cde0;font-size:12px;font-weight:750}.field input{width:100%;background:#0a1120;border:1px solid #30405f;border-radius:10px;padding:12px;color:var(--text);font:inherit;outline:none}.field input:focus{border-color:var(--purple);box-shadow:0 0 0 3px #9b8cff20}.hint{font-size:11px;color:var(--muted);margin-top:-4px}.btn{border:0;border-radius:10px;background:var(--purple);color:#11101b;padding:12px 17px;font-weight:900;cursor:pointer}.btn.yellow{background:var(--yellow)}.btn:disabled{opacity:.45;cursor:not-allowed}.btn.secondary{background:#263552;color:var(--text)}.notice{padding:12px 14px;border:1px solid #3b4564;background:#151f34;border-radius:11px;color:var(--muted);font-size:12px;margin-top:15px}.sectiontitle{font-size:17px;margin:0 0 14px}.jobs{display:flex;flex-direction:column;gap:12px;margin-top:18px}.job{background:#0b1322;border:1px solid var(--line);border-radius:12px;padding:13px}.jobtop{display:flex;justify-content:space-between;gap:10px}.jobname{font-weight:800;overflow-wrap:anywhere}.jobstatus{font-size:11px;color:var(--muted)}.bar{height:7px;border-radius:99px;background:#26314a;overflow:hidden;margin:10px 0 6px}.bar i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--purple),var(--green));transition:width .25s}.jobmsg{font-size:11px;color:var(--muted)}.downloadlink{display:inline-block;margin-top:8px;color:var(--yellow);font-weight:800;text-decoration:none}.anime-tools{display:flex;gap:9px;flex-wrap:wrap;margin:12px 0}.anime-tools input{flex:1;min-width:180px;background:#0a1120;border:1px solid #30405f;border-radius:10px;padding:11px;color:var(--text)}.catalog{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px;max-height:570px;overflow:auto;padding-right:3px}.animeitem{background:#0b1322;border:1px solid var(--line);border-radius:12px;padding:12px;display:flex;gap:10px;align-items:flex-start}.animeitem input{margin-top:4px;accent-color:var(--purple)}.animeitem strong{display:block;font-size:12px;line-height:1.35}.animeitem small{display:block;color:var(--muted);font-size:10px;margin-top:4px;overflow-wrap:anywhere}.catalogcount{color:var(--muted);font-size:11px;margin:5px 0 10px}.footer{color:#697895;text-align:center;font-size:11px;margin-top:28px}.error{color:var(--red)}@media(max-width:850px){.grid{grid-template-columns:1fr}}@media(max-width:560px){.wrap{padding:18px 12px}.top{padding:0 14px}.hero h1{font-size:27px}.fieldgrid{grid-template-columns:1fr}.panel{padding:15px}}
</style></head><body>
<header class="top"><a class="brand" href="/">MEDIA<span>VAULT</span></a><div class="topright"><a class="back" href="/">← Cover</a><span>LOCAL MEDIA TOOLS</span></div></header>
<main class="wrap"><section class="hero"><div><h1>Media Downloader</h1><p>Collect chapter images and video files into a folder you choose.</p></div></section>
<nav class="tabs"><button class="tab active" data-tab="manga">▤ Manga</button><button class="tab" data-tab="anime">▶ Anime</button></nav>
<section id="manga" class="grid"><div class="panel"><h2 class="sectiontitle">Manga chapter downloader</h2><form id="mangaForm"><div class="fieldgrid"><div class="field"><label>MANGA SLUG</label><input name="slug" placeholder="one_piece" required pattern="[A-Za-z0-9_-]+"><span class="hint">Use underscores between words.</span></div><div class="field"><label>CHAPTER NUMBER</label><input name="chapter" placeholder="1050" required pattern="[0-9]+(?:\.[0-9]+)?"></div></div><div class="field"><label>DOWNLOAD FOLDER</label><input name="destination" placeholder="downloads/manga or C:\\Media\\Manga"><span class="hint">On your local PC, an absolute folder path is accepted. On Blitz, choose a subfolder under the app's downloads directory.</span></div><button class="btn yellow" type="submit">Start chapter download ↗</button></form><div class="notice">The app checks page 1, then page 2, page 3, and continues until the chapter page is missing or no reader image is found. Use this only for material you have permission to download.</div></div>
<div class="panel"><h2 class="sectiontitle">How the manga scan works</h2><p style="color:var(--muted)">Chapter page pattern:</p><code>mangatown.com/manga/{slug}/c{chapter}/</code><p style="color:var(--muted)">Page 2 and onward:</p><code>mangatown.com/manga/{slug}/c{chapter}/{page}.html</code><div class="notice">Images are saved individually in a chapter folder and also packaged into a ZIP so you can retrieve them from the browser.</div></div></section>
<section id="anime" class="hidden"><div class="panel"><h2 class="sectiontitle">Anime video library</h2><p style="color:var(--muted);margin-top:-7px">Loaded from your supplied <b>anime_urls.txt</b> file. Select the video links you want to save.</p><div class="field"><label>DOWNLOAD FOLDER</label><input id="animeDestination" placeholder="downloads/anime or C:\\Media\\Anime"></div><div class="anime-tools"><input id="animeSearch" type="search" placeholder="Search series, episode, or filename…"><button class="btn secondary" id="selectVisible">Select visible</button><button class="btn secondary" id="clearSelection">Clear</button></div><div class="catalogcount" id="catalogCount">Loading catalog…</div><div class="catalog" id="catalog"></div><div style="margin-top:15px"><button class="btn yellow" id="downloadAnime">Download selected videos ↗</button><span class="hint" id="selectedCount" style="margin-left:12px">0 selected</span></div><div class="notice">Your supplied list contains direct Archive.org media URLs. Downloads are made from those original links. Large videos can take time and use significant disk space. Only download media you are authorized to keep.</div></div></section>
<section class="panel" style="margin-top:18px"><h2 class="sectiontitle">Download activity</h2><div id="jobs" class="jobs"><div class="jobstatus">No downloads started yet.</div></div></section>
<div class="footer">MediaVault · Flask local app · Works in Chrome and Microsoft Edge</div></main>
<script>
const tabs=document.querySelectorAll('.tab');tabs.forEach(b=>b.addEventListener('click',()=>{tabs.forEach(x=>x.classList.toggle('active',x===b));document.getElementById('manga').classList.toggle('hidden',b.dataset.tab!=='manga');document.getElementById('anime').classList.toggle('hidden',b.dataset.tab!=='anime')}));
const jobsEl=document.getElementById('jobs');const activeJobs=new Set();
function addJob(id,name){activeJobs.add(id);const el=document.createElement('div');el.className='job';el.id='job-'+id;el.innerHTML=`<div class="jobtop"><div class="jobname"></div><div class="jobstatus">Queued</div></div><div class="bar"><i></i></div><div class="jobmsg">Starting…</div>`;el.querySelector('.jobname').textContent=name;jobsEl.prepend(el);poll(id)}
async function poll(id){const el=document.getElementById('job-'+id);if(!el)return;try{const r=await fetch('/api/jobs/'+id);const j=await r.json();el.querySelector('.jobstatus').textContent=j.status.toUpperCase();el.querySelector('.bar i').style.width=(j.percent||0)+'%';el.querySelector('.jobmsg').textContent=j.message+(j.current?' · '+j.current:'')+(j.total?' · '+j.done+'/'+j.total:'');if(j.download_url){let a=el.querySelector('a');if(!a){a=document.createElement('a');a.className='downloadlink';a.textContent='↓ Download ZIP';el.appendChild(a)}a.href=j.download_url}else if(j.status==='error'){el.querySelector('.jobmsg').classList.add('error')}if(j.status==='running'||j.status==='queued')setTimeout(()=>poll(id),1200)}catch(e){el.querySelector('.jobmsg').textContent='Could not refresh job status.'}}
const mangaForm=document.getElementById('mangaForm');mangaForm.addEventListener('submit',async e=>{e.preventDefault();const data=Object.fromEntries(new FormData(mangaForm));const b=mangaForm.querySelector('button');b.disabled=true;try{const r=await fetch('/api/manga',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const j=await r.json();if(!r.ok)throw new Error(j.error||'Could not start');addJob(j.id,'Manga · '+data.slug+' chapter '+data.chapter)}catch(err){alert(err.message)}finally{b.disabled=false}});
let catalog=[];let selected=new Set();const catalogEl=document.getElementById('catalog');const searchEl=document.getElementById('animeSearch');
function renderCatalog(){const q=searchEl.value.toLowerCase().trim();const shown=catalog.filter(x=>(x.series+' '+x.title+' '+x.filename).toLowerCase().includes(q));document.getElementById('catalogCount').textContent=`Showing ${shown.length} of ${catalog.length} videos`;catalogEl.innerHTML='';for(const x of shown){const label=document.createElement('label');label.className='animeitem';const cb=document.createElement('input');cb.type='checkbox';cb.checked=selected.has(x.id);cb.addEventListener('change',()=>{cb.checked?selected.add(x.id):selected.delete(x.id);updateSelected()});const body=document.createElement('span');const strong=document.createElement('strong');strong.textContent=x.series+' · '+x.title;const small=document.createElement('small');small.textContent=x.filename;body.append(strong,small);label.append(cb,body);catalogEl.append(label)}updateSelected()}
function updateSelected(){document.getElementById('selectedCount').textContent=selected.size+' selected'}
async function loadCatalog(){try{const r=await fetch('/api/anime');const j=await r.json();catalog=j.items||[];renderCatalog()}catch(e){document.getElementById('catalogCount').textContent='Could not load anime_urls.txt'}}
searchEl.addEventListener('input',renderCatalog);document.getElementById('selectVisible').addEventListener('click',()=>{const q=searchEl.value.toLowerCase().trim();catalog.filter(x=>(x.series+' '+x.title+' '+x.filename).toLowerCase().includes(q)).forEach(x=>selected.add(x.id));renderCatalog()});document.getElementById('clearSelection').addEventListener('click',()=>{selected.clear();renderCatalog()});
document.getElementById('downloadAnime').addEventListener('click',async e=>{if(!selected.size){alert('Select at least one video first.');return}const b=e.currentTarget;b.disabled=true;try{const r=await fetch('/api/anime/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids:[...selected],destination:document.getElementById('animeDestination').value})});const j=await r.json();if(!r.ok)throw new Error(j.error||'Could not start');addJob(j.id,'Anime · '+j.total+' selected videos')}catch(err){alert(err.message)}finally{b.disabled=false}});
loadCatalog();
</script></body></html>"""


@app.get("/")
def cover():
    return render_template_string(COVER_HTML)


@app.get("/app")
def home():
    return render_template_string(APP_HTML)


@app.get("/api/anime")
def anime_catalog():
    return jsonify({"count": len(ANIME_CATALOG), "items": ANIME_CATALOG})


@app.post("/api/manga")
def start_manga():
    data = request.get_json(silent=True) or {}
    slug = (data.get("slug") or "").strip().strip("/")
    chapter = (data.get("chapter") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", slug):
        return jsonify(error="Manga slug may contain only letters, numbers, underscores, and hyphens."), 400
    if not re.fullmatch(r"\d+(?:\.\d+)?", chapter):
        return jsonify(error="Enter a valid chapter number, such as 12 or 12.5."), 400
    try:
        destination = get_destination(data.get("destination", ""), "manga")
    except Exception as exc:
        return jsonify(error=str(exc)), 400
    jid = new_job("manga", f"{slug} chapter {chapter}")
    threading.Thread(target=manga_worker, args=(jid, slug, chapter, destination), daemon=True).start()
    return jsonify(id=jid), 202


@app.post("/api/anime/download")
def start_anime_download():
    data = request.get_json(silent=True) or {}
    ids = data.get("ids", [])
    if not isinstance(ids, list) or not ids:
        return jsonify(error="Select at least one video."), 400
    if len(ids) > MAX_ANIME_ITEMS_PER_JOB:
        return jsonify(error=f"Select no more than {MAX_ANIME_ITEMS_PER_JOB} videos per download job."), 400
    items = [ANIME_BY_ID[x] for x in ids if x in ANIME_BY_ID]
    if not items:
        return jsonify(error="No recognized video links were selected."), 400
    try:
        destination = get_destination(data.get("destination", ""), "anime")
    except Exception as exc:
        return jsonify(error=str(exc)), 400
    jid = new_job("anime", f"{len(items)} anime videos")
    threading.Thread(target=anime_worker, args=(jid, items, destination), daemon=True).start()
    return jsonify(id=jid, total=len(items)), 202


@app.get("/api/jobs/<jid>")
def job_status(jid):
    with jobs_lock:
        job = jobs.get(jid)
        if not job:
            abort(404)
        safe = {k: v for k, v in job.items() if k not in {"files", "zip_path"}}
        return jsonify(safe)


@app.get("/api/jobs/<jid>/file")
def job_file(jid):
    with jobs_lock:
        job = jobs.get(jid)
        if not job or job.get("status") != "complete":
            abort(404)
        path = Path(job.get("zip_path", ""))
    if not path.is_file():
        abort(404)
    return send_file(path, as_attachment=True, download_name=path.name)


@app.get("/health")
def health():
    return jsonify(status="ok", anime_links=len(ANIME_CATALOG))


if __name__ == "__main__":
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"MediaVault running at http://127.0.0.1:{PORT}")
    print(f"Loaded {len(ANIME_CATALOG)} anime links from {ANIME_URLS_FILE.name}")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
