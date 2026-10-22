"""ppt2scorm web app: upload a .pptx, convert it in the background, preview the course, download the SCORM zip.

Run locally:   uvicorn web.app:app --reload        (from the project root)
Environment:
  GEMINI_API_KEY / ANTHROPIC_API_KEY / ...   optional server-side key(s); users can also paste their own key
  PPT2SCORM_DATA          where jobs are stored (default: a temp folder)
  PPT2SCORM_MAX_MB        max upload size in MB (default 50)
  PPT2SCORM_DAILY_LIMIT   AI conversions per visitor per day on the server's key (default 10)
  Security settings (password, limits, client IP): see web/security.py
  PPT2SCORM_JOB_TTL_MIN   minutes before a job and its files are deleted (default 120)
"""
import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ppt2scorm.ai_redesign import PROVIDERS, detect_provider
from ppt2scorm.pipeline import add_narration, build_package, design_course, extract, screen_summary
from ppt2scorm.narration import DEFAULT_VOICE, VOICES, narrate_course, prune_stale
from ppt2scorm.schema import CourseError, check_types, resolve_images
from ppt2scorm.extract import DeckError, _save_image, check_archive
from ppt2scorm import brand as brandkit
from ppt2scorm.brand import BrandError
from ppt2scorm.website import brand_from_website
from ppt2scorm.photos import PhotoError, add_photos, download as photo_download, search as photo_search
from web import security
from web.security import check_ai_quota, check_capacity, check_rate

DATA = Path(os.environ.get("PPT2SCORM_DATA") or tempfile.mkdtemp(prefix="ppt2scorm_web_"))
JOBS_DIR = DATA / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
MAX_BYTES = int(os.environ.get("PPT2SCORM_MAX_MB", "50")) * 1024 * 1024
DAILY_LIMIT = security.DAILY_LIMIT
JOB_TTL = int(os.environ.get("PPT2SCORM_JOB_TTL_MIN", "120")) * 60
STATIC = Path(__file__).resolve().parent / "static"
# Brand kits live outside the temporary job folder so they survive restarts. Everyone using this server shares
# them, which suits a team; on a public site set PPT2SCORM_BRAND_SAVING=0 (kits can still be used per course).
BRANDS = brandkit.Store(os.environ.get("PPT2SCORM_BRANDS") or Path.home() / ".ppt2scorm" / "brands")
BRAND_SAVING = os.environ.get("PPT2SCORM_BRAND_SAVING", "1") != "0"
DRAFTS_DIR = DATA / "brand-drafts"
DRAFTS_DIR.mkdir(parents=True, exist_ok=True)

PROVIDER_LABELS = {"gemini": "Google Gemini", "claude": "Anthropic Claude", "openrouter": "OpenRouter",
                   "glm": "Z.ai GLM", "qwen": "Alibaba Qwen"}

app = FastAPI(title="ppt2scorm", docs_url=None, redoc_url=None)
pool = ThreadPoolExecutor(max_workers=int(os.environ.get("PPT2SCORM_WORKERS", "2")))
jobs = {}
lock = threading.Lock()

STEPS = ["upload", "extract", "design", "photos", "narrate", "package", "done"]


def _server_key(provider):
    return bool(os.environ.get(PROVIDERS[provider][0]))


# Outermost first: security headers on every response, then the optional password.
app.middleware("http")(security.password_gate)
app.middleware("http")(security.security_headers)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return security.login_page(request)


@app.post("/login")
async def login(request: Request):
    return await security.login_submit(request)


@app.get("/logout")
def logout():
    return security.logout()


if os.environ.get("PPT2SCORM_ALLOW_PRIVATE_FETCH"):
    import logging
    logging.getLogger("uvicorn.error").warning("PPT2SCORM_ALLOW_PRIVATE_FETCH is set: website checks are relaxed. "
                                               "Use this only for automated tests, never on a server.")


def _busy():
    with lock:
        return sum(1 for j in jobs.values() if j.get("status") == "running" or j.get("narr_status") == "running")


def _cleanup():
    now = time.time()
    with lock:
        old = [jid for jid, j in jobs.items() if now - j["created"] > JOB_TTL]
        for jid in old:
            jobs.pop(jid, None)
            edit_locks.pop(jid, None)
    for jid in old:
        shutil.rmtree(JOBS_DIR / jid, ignore_errors=True)
    for d in DRAFTS_DIR.iterdir() if DRAFTS_DIR.exists() else []:
        if now - d.stat().st_mtime > JOB_TTL:
            shutil.rmtree(d, ignore_errors=True)


def _update(jid, **kw):
    with lock:
        jobs[jid].update(kw)


def _friendly(err):
    msg = str(err) or err.__class__.__name__
    low = msg.lower()
    if "401" in msg or "api key" in low or "unauthorized" in low or "permission" in low:
        return "The AI provider rejected the API key. Check the key and try again."
    if "503" in msg or "high demand" in low or "unavailable" in low or "overloaded" in low or "529" in msg:
        return ("The AI provider's servers are busy right now. This is on their side, not yours, and usually "
                "clears within a few minutes. Try again shortly, choose Basic mode, or pick another provider.")
    if "429" in msg or "quota" in low or "rate" in low:
        return "The AI provider's rate limit or free quota was reached. Wait a while, use your own key, or choose Basic mode."
    if "not found" in low and "model" in low:
        return "The AI model name wasn't recognised by the provider. It may have been renamed."
    if "package not found" in low or "not a zip" in low or "badzipfile" in low:
        return "That file couldn't be opened as a PowerPoint (.pptx) file."
    return msg[:300]


def _run(jid, use_ai, provider, api_key, brand, instructions, narration="off", voice=None, tts_key=None, kit_choice="",
         photo_key=None):
    folder = JOBS_DIR / jid
    try:
        _update(jid, step="extract")
        deck = extract(folder / "deck.pptx", folder / "media")
        if not deck["slides"]:
            raise ValueError("The presentation has no slides.")
        media = [dict(im, slide=sl["index"], slide_title=sl["title"]) for sl in deck["slides"] for im in sl["images"]]
        seen = set()
        media = [m for m in media if not (m["src"] in seen or seen.add(m["src"]))]
        (folder / "media.json").write_text(json.dumps(media, ensure_ascii=False), encoding="utf-8")
        _update(jid, step="design", slides=len(deck["slides"]), title=deck["title"],
                design_started=time.time())
        course, warnings, how = design_course(deck, use_ai=use_ai, provider=provider, api_key=api_key,
                                              instructions=instructions, log=lambda m: None)
        api_key = None  # not kept beyond this point
        if photo_key:
            _update(jid, step="photos", photo_done=0, photo_total=0)
            added, pw = add_photos(course, folder / "media", photo_key,
                                   progress=lambda k, n: _update(jid, photo_done=k, photo_total=n))
            photo_key = None
            warnings = warnings + pw
            if added:
                media = json.loads((folder / "media.json").read_text(encoding="utf-8")) + added
                (folder / "media.json").write_text(json.dumps(media, ensure_ascii=False), encoding="utf-8")
                _update(jid, photos=len(added))
        if narration == "audio":
            _update(jid, step="narrate", narr_done=0, narr_total=0)
        warnings = warnings + add_narration(course, folder / "media", mode=narration, voice=voice, api_key=tts_key,
                                            progress=lambda k, n: _update(jid, narr_done=k, narr_total=n))
        tts_key = None
        _update(jid, step="package")
        bdir = folder / "brand"
        if kit_choice == "deck":
            kit, logo, notes = brandkit.from_deck(folder / "deck.pptx")
            brandkit.write_kit_dir(bdir, kit, logo)
            warnings = warnings + ["Branding: " + n for n in notes]
            _update(jid, brand_detected=True)
        elif kit_choice:
            brandkit.copy_kit_dir(BRANDS.path(kit_choice), bdir)
        if bdir.exists():
            _update(jid, brand_name=json.loads((bdir / "kit.json").read_text(encoding="utf-8"))["name"])
        text = json.dumps(course, indent=2, ensure_ascii=False)
        (folder / "course.json").write_text(text, encoding="utf-8")
        (folder / "course.draft.json").write_text(text, encoding="utf-8")  # untouched copy for "revert"
        build_package(course, folder / "course.zip", media_dir=folder / "media", brand=None if bdir.exists() else brand,
                      keep_dir=folder / "package", brand_dir=bdir if bdir.exists() else None)
        course.pop("theme", None)
        _update(jid, step="done", status="done", how=how, warnings=warnings, title=course["title"], version=1,
                voiceover=course.get("voiceover", {}).get("mode", "off"), needs_audio=prune_stale(course),
                summary=screen_summary(course), screens=len(course["screens"]),
                size_kb=(folder / "course.zip").stat().st_size // 1024)
    except Exception as e:  # noqa: BLE001 - every failure is reported to the user
        _update(jid, status="error", error=_friendly(e))


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/player/icons.js")
def player_icons():
    from ppt2scorm.package import PLAYER_DIR
    return FileResponse(PLAYER_DIR / "icons.js", media_type="text/javascript",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/config")
def config():
    return {
        "providers": [{"id": p, "label": PROVIDER_LABELS.get(p, p), "default_model": PROVIDERS[p][2],
                       "server_key": _server_key(p)} for p in PROVIDERS],
        "default_provider": detect_provider() or "gemini",
        "max_mb": MAX_BYTES // (1024 * 1024),
        "daily_limit": DAILY_LIMIT,
        "voices": [{"id": v, "label": f"{v} ({d.lower()})"} for v, d in VOICES.items()],
        "default_voice": DEFAULT_VOICE,
        "tts_server_key": _server_key("gemini"),
        "photos_server_key": bool(os.environ.get("PEXELS_API_KEY")),
        "signed_in": bool(security.PASSWORD),
    }


@app.post("/api/convert")
async def convert(request: Request, file: UploadFile = File(...), mode: str = Form("ai"),
                  provider: str = Form("gemini"), api_key: str = Form(""), brand: str = Form(""),
                  instructions: str = Form(""), narration: str = Form("off"), voice: str = Form(""),
                  brand_kit: str = Form(""), photos: str = Form("off"), pexels_key: str = Form("")):
    _cleanup()
    check_rate(request)
    check_capacity(_busy(), DATA)
    photo_key = None
    if photos == "pexels":
        photo_key = pexels_key.strip() or os.environ.get("PEXELS_API_KEY")
        if not photo_key:
            raise HTTPException(400, "Stock photos need a free Pexels API key. Paste one, or turn photos off.")
    if brand_kit not in ("", "standard", "deck"):
        try:
            BRANDS.get(brand_kit)
        except BrandError:
            raise HTTPException(400, "That brand kit no longer exists. Choose another one.")
    brand_kit = "" if brand_kit == "standard" else brand_kit
    if not (file.filename or "").lower().endswith(".pptx"):
        raise HTTPException(400, "Please upload a PowerPoint .pptx file. Older .ppt files must be saved as .pptx first.")
    if mode not in ("ai", "basic"):
        raise HTTPException(400, "Unknown mode.")
    if provider not in PROVIDERS:
        raise HTTPException(400, "Unknown AI provider.")
    if brand and not re.fullmatch(r"#[0-9a-fA-F]{6}", brand):
        raise HTTPException(400, "Brand colour must look like #1f5eff.")
    instructions = instructions.strip()[:600]
    api_key = api_key.strip()

    if narration not in ("off", "audio", "device"):
        raise HTTPException(400, "Unknown narration option.")
    voice = voice if voice in VOICES else DEFAULT_VOICE
    use_ai = mode == "ai"
    # AI voice uses Google's text-to-speech: the pasted key when the provider is Gemini, else the server's key.
    tts_key = None
    if narration == "audio":
        tts_key = api_key if (provider == "gemini" and api_key) else os.environ.get(PROVIDERS["gemini"][0])
        if not tts_key:
            raise HTTPException(400, "AI voice needs a Google Gemini key. Choose Google Gemini as the provider and "
                                     "paste your key, or choose Device voice.")
    uses_server_key = (use_ai and not api_key) or (narration == "audio" and not (provider == "gemini" and api_key))
    if use_ai and not api_key and not _server_key(provider):
        raise HTTPException(400, f"No server key is set up for {PROVIDER_LABELS[provider]}. "
                                 "Paste your own API key, or choose Basic mode.")
    if uses_server_key:
        check_ai_quota(request)

    jid = uuid.uuid4().hex
    folder = JOBS_DIR / jid
    folder.mkdir(parents=True)
    size = 0
    with open(folder / "deck.pptx", "wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                f.close()
                shutil.rmtree(folder, ignore_errors=True)
                raise HTTPException(413, f"File is larger than {MAX_BYTES // (1024 * 1024)} MB.")
            f.write(chunk)

    try:
        check_archive(folder / "deck.pptx")
    except DeckError as e:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(400, str(e))
    with lock:
        jobs[jid] = {"id": jid, "status": "running", "step": "upload", "created": time.time(),
                     "filename": Path(file.filename).name, "mode": mode, "brand": brand or None,
                     "provider": PROVIDER_LABELS[provider] if use_ai else None}
    pool.submit(_run, jid, use_ai, provider if use_ai else None, api_key or None, brand or None, instructions,
                narration, voice, tts_key, brand_kit, photo_key)
    return {"job_id": jid}


def _job(jid):
    if not re.fullmatch(r"[0-9a-f]{32}", jid):
        raise HTTPException(404, "Not found")
    with lock:
        j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "This conversion has expired or doesn't exist. Please upload the deck again.")
    return j


@app.get("/api/jobs/{jid}")
def job_status(jid: str):
    j = dict(_job(jid))
    if j.get("step") == "design" and j.get("design_started"):
        j["design_seconds"] = int(time.time() - j["design_started"])
    j["steps"] = STEPS
    return JSONResponse(j)


def _slug(text):
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:60] or "course"


@app.get("/api/jobs/{jid}/download")
def download(jid: str):
    j = _job(jid)
    if j.get("status") != "done":
        raise HTTPException(409, "The course isn't ready yet.")
    return FileResponse(JOBS_DIR / jid / "course.zip", media_type="application/zip",
                        filename=f"{_slug(j.get('title', 'course'))}_SCORM12.zip")


@app.get("/api/jobs/{jid}/course.json")
def course_json(jid: str):
    j = _job(jid)
    if j.get("status") != "done":
        raise HTTPException(409, "The course isn't ready yet.")
    return FileResponse(JOBS_DIR / jid / "course.json", media_type="application/json",
                        filename=f"{_slug(j.get('title', 'course'))}.course.json")


@app.get("/preview/{jid}/{path:path}")
def preview(jid: str, path: str):
    j = _job(jid)
    if j.get("status") != "done":
        raise HTTPException(409, "The course isn't ready yet.")
    root = (JOBS_DIR / jid / "package").resolve()
    target = (root / (path or "index.html")).resolve()
    if root not in target.parents and target != root or not target.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(target, headers={"Cache-Control": "no-store"})


# ---------- editor: load, save (validate + rebuild), revert ----------
MAX_EDIT_BYTES = 2 * 1024 * 1024
HEX = re.compile(r"#[0-9a-fA-F]{6}")
edit_locks = {}


def _edit_lock(jid):
    with lock:
        return edit_locks.setdefault(jid, threading.Lock())


def _ready(jid):
    j = _job(jid)
    if j.get("status") != "done":
        raise HTTPException(409, "The course isn't ready yet.")
    return j


def _rebuild(jid, course, brand):
    """Validate a course, rebuild the package beside the live one, then swap it in."""
    if not isinstance(course, dict):
        raise HTTPException(422, "The course data is missing or damaged.")
    course.pop("theme", None)  # the brand colour comes only from the validated `brand` value
    folder = JOBS_DIR / jid
    try:
        warnings = check_types(course)
        warnings += resolve_images(course, {m["src"]: m for m in _media(jid)})
        vo = course.get("voiceover") or {}
        if vo.get("voice") and vo["voice"] not in VOICES:
            raise CourseError("pick a voice from the list")
        needs_audio = prune_stale(course)
    except CourseError as e:
        raise HTTPException(422, str(e)[:1].upper() + str(e)[1:] + ".")
    with _edit_lock(jid):
        nxt_pkg, nxt_zip = folder / "package.next", folder / "course.next.zip"
        bdir = folder / "brand"
        build_package(course, nxt_zip, media_dir=folder / "media", brand=brand, keep_dir=nxt_pkg,
                      brand_dir=bdir if bdir.exists() else None)
        old_pkg = folder / "package.old"
        shutil.rmtree(old_pkg, ignore_errors=True)
        if (folder / "package").exists():
            os.replace(folder / "package", old_pkg)
        os.replace(nxt_pkg, folder / "package")
        os.replace(nxt_zip, folder / "course.zip")
        shutil.rmtree(old_pkg, ignore_errors=True)
        course.pop("theme", None)
        (folder / "course.json").write_text(json.dumps(course, indent=2, ensure_ascii=False), encoding="utf-8")
        if brand is None:
            jobs[jid]["brand"] = None
        with lock:
            j = jobs[jid]
            j.update(title=course["title"], warnings=warnings, summary=screen_summary(course),
                     screens=len(course["screens"]), size_kb=(folder / "course.zip").stat().st_size // 1024,
                     brand=brand, version=j.get("version", 1) + 1, needs_audio=needs_audio,
                     voiceover=(course.get("voiceover") or {}).get("mode", "off"))
            return dict(j)


@app.get("/api/jobs/{jid}/course")
def get_course(jid: str):
    j = _ready(jid)
    folder = JOBS_DIR / jid
    course = json.loads((folder / "course.json").read_text(encoding="utf-8"))
    edited = (folder / "course.json").read_bytes() != (folder / "course.draft.json").read_bytes()
    kit = _course_kit(jid)
    return {"course": course, "brand": j.get("brand") or (kit["colors"]["primary"] if kit else "#1f5eff"),
            "brand_kit": {"name": kit["name"], "logo": bool(kit.get("logo"))} if kit else None,
            "brand_detected": bool(j.get("brand_detected")), "brand_saving": BRAND_SAVING,
            "edited": edited, "version": j.get("version", 1),
            "needs_audio": prune_stale(json.loads(json.dumps(course))), "recording": j.get("narr_status") == "running",
            "tts_server_key": _server_key("gemini")}


@app.put("/api/jobs/{jid}/course")
async def save_course(jid: str, request: Request):
    _ready(jid)
    raw = await request.body()
    if len(raw) > MAX_EDIT_BYTES:
        raise HTTPException(413, "The course is too large to save.")
    try:
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "The editor sent data the server couldn't read. Reload the page and try again.")
    brand = body.get("brand") or None
    if brand is not None and not (isinstance(brand, str) and HEX.fullmatch(brand)):
        raise HTTPException(422, "Brand colour must look like #1f5eff.")
    j = _rebuild(jid, body.get("course"), brand)
    return {"version": j["version"], "title": j["title"], "screens": j["screens"], "summary": j["summary"],
            "size_kb": j["size_kb"], "warnings": j["warnings"], "edited": True, "needs_audio": j["needs_audio"]}


@app.post("/api/jobs/{jid}/revert")
def revert_course(jid: str):
    j = _ready(jid)
    draft = json.loads((JOBS_DIR / jid / "course.draft.json").read_text(encoding="utf-8"))
    j = _rebuild(jid, draft, j.get("brand"))
    return {"version": j["version"], "title": j["title"], "screens": j["screens"], "summary": j["summary"],
            "size_kb": j["size_kb"], "warnings": j["warnings"], "edited": False, "needs_audio": j["needs_audio"]}


def _record(jid, tts_key):
    try:
        course = json.loads((JOBS_DIR / jid / "course.json").read_text(encoding="utf-8"))
        done, total, warnings = narrate_course(course, JOBS_DIR / jid / "media", tts_key,
                                               progress=lambda k, n: _update(jid, narr_done=k, narr_total=n))
        tts_key = None
        j = _rebuild(jid, course, _job(jid).get("brand"))
        _update(jid, narr_status="done", narr_warnings=warnings, narr_result=f"{done} of {total}")
        del j
    except Exception as e:  # noqa: BLE001
        _update(jid, narr_status="error", narr_warnings=[_friendly(getattr(e, "detail", e))])


@app.post("/api/jobs/{jid}/narration")
async def record_narration(jid: str, request: Request):
    """Record audio for every screen whose script has no matching audio yet (runs in the background)."""
    j = _ready(jid)
    check_rate(request)
    if j.get("narr_status") == "running":
        raise HTTPException(409, "Audio is already being recorded.")
    try:
        body = await request.json()
    except ValueError:
        body = {}
    key = (body.get("api_key") or "").strip() if isinstance(body, dict) else ""
    if not key:
        key = os.environ.get(PROVIDERS["gemini"][0])
        if not key:
            raise HTTPException(400, "Paste a Google Gemini API key to record audio.")
        if not j.get("voice_quota_counted"):   # re-recording edited screens of the same course is free
            check_ai_quota(request)
            with lock:
                jobs[jid]["voice_quota_counted"] = True
    _update(jid, narr_status="running", narr_done=0, narr_total=0, narr_warnings=[])
    pool.submit(_record, jid, key)
    return {"recording": True}


MEDIA_NAME = re.compile(r"[A-Za-z0-9_.-]{1,120}")
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _media(jid):
    f = JOBS_DIR / jid / "media.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


@app.get("/api/jobs/{jid}/media")
def list_media(jid: str):
    _ready(jid)
    return {"images": _media(jid)}


@app.get("/api/jobs/{jid}/media/{name}")
def media_file(jid: str, name: str):
    _job(jid)
    if not MEDIA_NAME.fullmatch(name) or ".." in name:
        raise HTTPException(404, "Not found")
    target = JOBS_DIR / jid / "media" / name
    if not target.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(target, headers={"Cache-Control": "private, max-age=3600"})


@app.post("/api/jobs/{jid}/media")
async def upload_media(jid: str, request: Request, file: UploadFile = File(...), alt: str = Form("")):
    check_rate(request)
    _ready(jid)
    data = await file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "That image is over 8 MB. Use a smaller one.")
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in ("png", "jpg", "jpeg", "gif", "webp"):
        raise HTTPException(400, "Use a PNG, JPG, GIF or WebP image.")
    import hashlib
    # Re-encoding through Pillow means only real, browser-safe image data is ever stored or served.
    res = _save_image(data, "png" if ext in ("png", "gif", "webp") else "jpg", JOBS_DIR / jid / "media",
                      "up_" + hashlib.sha1(data).hexdigest()[:12])
    if res is None:
        raise HTTPException(400, "That file couldn't be read as an image.")
    name, w, h = res
    entry = {"src": f"media/{name}", "w": w, "h": h, "alt": alt.strip()[:300], "kind": "upload",
             "slide": None, "slide_title": file.filename[:80] if file.filename else "Uploaded image"}
    with _edit_lock(jid):
        media = [m for m in _media(jid) if m["src"] != entry["src"]] + [entry]
        (JOBS_DIR / jid / "media.json").write_text(json.dumps(media, ensure_ascii=False), encoding="utf-8")
    return entry


# ---------- stock photos in the editor ----------
def _photo_key(body):
    key = (body.get("api_key") or "").strip() if isinstance(body, dict) else ""
    key = key or os.environ.get("PEXELS_API_KEY")
    if not key:
        raise HTTPException(400, "Paste a free Pexels API key to search photos.")
    return key


@app.post("/api/jobs/{jid}/photos/search")
async def editor_photo_search(jid: str, request: Request):
    _ready(jid)
    check_rate(request)
    body = await request.json()
    import asyncio
    try:
        results = await asyncio.get_running_loop().run_in_executor(pool, lambda: photo_search(body.get("query", ""), _photo_key(body), 12))
    except PhotoError as e:
        raise HTTPException(e.status_code if e.status_code in (401, 403, 429) else 502, str(e))
    return {"photos": results}


@app.post("/api/jobs/{jid}/photos/add")
async def editor_photo_add(jid: str, request: Request):
    _ready(jid)
    check_rate(request)
    body = await request.json()
    photo = body.get("photo") if isinstance(body.get("photo"), dict) else {}
    import asyncio
    try:
        entry = await asyncio.get_running_loop().run_in_executor(pool, lambda: photo_download(photo, JOBS_DIR / jid / "media"))
    except PhotoError as e:
        raise HTTPException(400, str(e))
    entry.update(slide=None, slide_title="Pexels: " + photo.get("photographer", "")[:60])
    with _edit_lock(jid):
        media = [m for m in _media(jid) if m["src"] != entry["src"]] + [entry]
        (JOBS_DIR / jid / "media.json").write_text(json.dumps(media, ensure_ascii=False), encoding="utf-8")
    return entry


# ---------- brand kits ----------
def _course_kit(jid):
    f = JOBS_DIR / jid / "brand" / "kit.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None


def _brand_err(e):
    msg = str(e)
    return HTTPException(422, msg[:1].upper() + msg[1:] + ("" if msg.endswith(".") else "."))


def _summary(kit, base):
    return {"id": kit["id"], "name": kit["name"], "colors": kit["colors"], "fonts": kit["fonts"], "corners": kit["corners"],
            "logo_alt": kit.get("logo_alt", ""), "logo_url": f"{base}/logo" if kit.get("logo") else None,
            "font_base": f"{base}/fonts/", "warnings": brandkit.check(kit)}


@app.get("/api/brands")
def list_brands():
    return {"kits": [_summary(k, f"/api/brands/{k['id']}") for k in BRANDS.list()], "saving": BRAND_SAVING,
            "stacks": [{"id": k, "label": v[0], "css": v[1]} for k, v in brandkit.STACKS.items()],
            "default": brandkit.DEFAULT}


def _asset(folder, kind, name=None):
    if kind == "logo":
        target = folder / "logo.png"
    else:
        if not re.fullmatch(r"[a-z0-9_-]{1,60}\.(woff2|woff|ttf|otf)", name or ""):
            raise HTTPException(404, "Not found")
        target = folder / "fonts" / name
    if not target.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(target, headers={"Cache-Control": "no-store"})


@app.get("/api/brands/{kid}/logo")
def brand_logo(kid: str):
    try:
        return _asset(BRANDS.path(kid), "logo")
    except BrandError:
        raise HTTPException(404, "Not found")


@app.get("/api/brands/{kid}/fonts/{name}")
def brand_font(kid: str, name: str):
    try:
        return _asset(BRANDS.path(kid), "font", name)
    except BrandError:
        raise HTTPException(404, "Not found")


def _draft_dir(did):
    if not re.fullmatch(r"[0-9a-f]{32}", did or ""):
        raise HTTPException(404, "This brand draft has expired. Start again.")
    d = DRAFTS_DIR / did
    if not d.is_dir():
        raise HTTPException(404, "This brand draft has expired. Start again.")
    return d


@app.get("/api/brand-drafts/{did}/logo")
def draft_logo(did: str):
    return _asset(_draft_dir(did), "logo")


@app.get("/api/brand-drafts/{did}/fonts/{name}")
def draft_font(did: str, name: str):
    return _asset(_draft_dir(did), "font", name)


def _new_draft(kit, logo, fonts, notes, how):
    did = uuid.uuid4().hex
    kit = brandkit.write_kit_dir(DRAFTS_DIR / did, kit, logo, fonts)
    out = _summary(kit, f"/api/brand-drafts/{did}")
    out.update(draft_id=did, notes=notes, how=how)
    return out


@app.post("/api/brand-drafts/website")
async def draft_from_website(request: Request):
    _cleanup()
    check_rate(request)
    body = await request.json()
    url = (body.get("url") or "").strip()[:500]
    if not url:
        raise HTTPException(400, "Type the company's website address.")
    provider, key = body.get("provider"), (body.get("api_key") or "").strip()
    use_ai = provider in PROVIDERS and (key or _server_key(provider))
    if use_ai and not key:
        use_ai = check_ai_quota(request, soft=True)   # out of free AI uses today: the scores still give a good kit
    import asyncio
    try:
        kit, logo, fonts, notes, how = await asyncio.get_running_loop().run_in_executor(
            pool, lambda: brand_from_website(url, provider if use_ai else None, key or None))
    except BrandError as e:
        raise _brand_err(e)
    return _new_draft(kit, logo, fonts, notes, how)


@app.post("/api/brand-drafts/deck")
async def draft_from_deck(request: Request, file: UploadFile = File(...)):
    check_rate(request)
    _cleanup()
    if not (file.filename or "").lower().endswith(".pptx"):
        raise HTTPException(400, "Choose a PowerPoint .pptx file.")
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "That file is too large.")
    import io as _io
    try:
        kit, logo, notes = brandkit.from_deck(_io.BytesIO(data))
    except BrandError as e:
        raise _brand_err(e)
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "That file couldn't be opened as a PowerPoint (.pptx) file.")
    kit = dict(kit, name=Path(file.filename).stem.replace("_", " ")[:50] + " brand")
    return _new_draft(kit, logo, {}, notes, "the deck's theme colours, theme fonts and repeated logo")


def _font_meta(raw, filename):
    """Weight and style of an uploaded font, from the file itself where possible, else from its name."""
    weight, italic = 400, bool(re.search(r"(?i)italic|oblique", filename))
    try:
        from fontTools.ttLib import TTFont
        import io as _io
        f = TTFont(_io.BytesIO(raw), lazy=True)
        weight = int(f["OS/2"].usWeightClass)
        italic = italic or bool(f["OS/2"].fsSelection & 1)
    except Exception:  # noqa: BLE001 - WOFF2 without brotli, or no fontTools: use the name
        n = filename.lower()
        for word, w in (("thin", 100), ("extralight", 200), ("light", 300), ("medium", 500), ("semibold", 600),
                        ("extrabold", 800), ("black", 900), ("bold", 700)):
            if word in n:
                weight = w
                break
    weight = min(900, max(100, round(weight / 100) * 100))
    return weight, "italic" if italic else "normal"


@app.post("/api/brands")
async def save_brand(request: Request):
    check_rate(request)
    """Create or update a kit. Multipart: kit (JSON), source ('kit:<id>' | 'draft:<id>' | ''), replace ('1' to overwrite
    the source kit), logo (file), remove_logo ('1'), heading_files / body_files (font files)."""
    if not BRAND_SAVING:
        raise HTTPException(403, "Saving brand kits is turned off on this server. You can still use a kit for one course.")
    form = await request.form()
    try:
        kit = json.loads(form.get("kit") or "{}")
    except ValueError:
        raise HTTPException(400, "The brand kit data couldn't be read.")
    source = form.get("source") or ""
    src_dir = None
    if source.startswith("kit:"):
        try:
            src_dir = BRANDS.path(source[4:])
        except BrandError as e:
            raise _brand_err(e)
    elif source.startswith("draft:"):
        src_dir = _draft_dir(source[6:])
    replace = form.get("replace") == "1" and source.startswith("kit:")
    try:
        logo = None
        up = form.get("logo")
        if up is not None and hasattr(up, "read"):
            logo = brandkit.clean_logo(await up.read(brandkit.MAX_LOGO + 1))
        elif src_dir is not None and (src_dir / "logo.png").is_file() and form.get("remove_logo") != "1":
            logo = (src_dir / "logo.png").read_bytes()
        fonts = {}
        if src_dir is not None and (src_dir / "fonts").is_dir():
            fonts = {f.name: f.read_bytes() for f in (src_dir / "fonts").iterdir()}
        for role in ("heading", "body"):
            f = kit.get("fonts", {}).get(role, {})
            for upf in form.getlist(f"{role}_files"):
                if not hasattr(upf, "read"):
                    continue
                raw = await upf.read(brandkit.MAX_FONT + 1)
                ext = brandkit.check_font(raw, upf.filename)
                if "family" not in f:
                    raise BrandError(f"give the {role} font a name before adding its files")
                name = brandkit.font_file_name(f["family"], raw, ext)
                fonts[name] = raw
                weight, style = _font_meta(raw, upf.filename or "")
                f.setdefault("files", []).append({"file": name, "weight": weight, "style": style})
        kit["logo"] = "logo.png" if logo else None
        if replace:
            target = BRANDS.path(source[4:])
            shutil.rmtree(target, ignore_errors=True)
            saved = BRANDS.save(dict(kit, id=source[4:]), logo=logo, fonts=fonts, replace_id=source[4:])
        else:
            saved = BRANDS.save(kit, logo=logo, fonts=fonts)
    except BrandError as e:
        raise _brand_err(e)
    return _summary(saved, f"/api/brands/{saved['id']}")


@app.delete("/api/brands/{kid}")
def delete_brand(kid: str):
    if not BRAND_SAVING:
        raise HTTPException(403, "Brand kits can't be changed on this server.")
    try:
        BRANDS.get(kid)
        BRANDS.delete(kid)
    except BrandError as e:
        raise _brand_err(e)
    return {"deleted": kid}


@app.get("/api/brands/{kid}/export")
def export_brand(kid: str):
    try:
        doc = BRANDS.export(kid)
    except BrandError as e:
        raise _brand_err(e)
    return JSONResponse(doc, headers={"Content-Disposition": f'attachment; filename="{kid}.brand.json"'})


@app.post("/api/brands/import")
async def import_brand(request: Request, file: UploadFile = File(...)):
    check_rate(request)
    if not BRAND_SAVING:
        raise HTTPException(403, "Saving brand kits is turned off on this server.")
    data = await file.read(40 * 1024 * 1024)
    try:
        saved = BRANDS.import_(data)
    except BrandError as e:
        raise _brand_err(e)
    return _summary(saved, f"/api/brands/{saved['id']}")


@app.post("/api/jobs/{jid}/brand")
async def set_course_brand(jid: str, request: Request):
    """Switch a course to a saved kit ('kit:<id>'), back to the standard look ('standard'), or save its current
    brand as a new kit ({"save_as": name})."""
    _ready(jid)
    body = await request.json()
    bdir = JOBS_DIR / jid / "brand"
    if body.get("save_as"):
        if not BRAND_SAVING:
            raise HTTPException(403, "Saving brand kits is turned off on this server.")
        kit = _course_kit(jid)
        if not kit:
            raise HTTPException(400, "This course uses the standard look; there's no brand to save.")
        try:
            saved = BRANDS.save(dict(kit, name=str(body["save_as"])[:60], id=None),
                                logo=(bdir / "logo.png").read_bytes() if (bdir / "logo.png").is_file() else None,
                                fonts={f.name: f.read_bytes() for f in (bdir / "fonts").iterdir()} if (bdir / "fonts").is_dir() else {})
        except BrandError as e:
            raise _brand_err(e)
        return _summary(saved, f"/api/brands/{saved['id']}")
    choice = body.get("use", "")
    with _edit_lock(jid):
        if choice == "standard":
            shutil.rmtree(bdir, ignore_errors=True)
        elif choice.startswith("kit:"):
            try:
                brandkit.copy_kit_dir(BRANDS.path(choice[4:]), bdir)
            except (BrandError, FileNotFoundError):
                raise HTTPException(400, "That brand kit no longer exists.")
        else:
            raise HTTPException(400, "Choose a brand kit.")
    course = json.loads((JOBS_DIR / jid / "course.json").read_text(encoding="utf-8"))
    j = _rebuild(jid, course, None)
    kit = _course_kit(jid)
    _update(jid, brand_detected=False, brand_name=kit["name"] if kit else None)
    return {"version": j["version"], "brand": kit["colors"]["primary"] if kit else "#1f5eff",
            "brand_kit": {"name": kit["name"], "logo": bool(kit.get("logo"))} if kit else None}


@app.get("/api/whoami")
def whoami(request: Request):
    """The visitor's own IP as the app sees it, to check PPT2SCORM_CLIENT_IP_HEADER / PPT2SCORM_PROXY_HOPS."""
    return {"ip": security.client_ip(request)}


@app.get("/healthz")
def health():
    return {"ok": True}


app.mount("/static", StaticFiles(directory=STATIC), name="static")
