"""ppt2scorm web app: upload a .pptx, convert it in the background, preview the course, download the SCORM zip.

Run locally:   uvicorn web.app:app --reload        (from the project root)
Environment:
  GEMINI_API_KEY / ANTHROPIC_API_KEY / ...   optional server-side key(s); users can also paste their own key
  PPT2SCORM_DATA          where jobs are stored (default: a temp folder)
  PPT2SCORM_MAX_MB        max upload size in MB (default 50)
  PPT2SCORM_DAILY_LIMIT   AI conversions per visitor per day on the server's key (default 10)
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
from ppt2scorm.extract import _save_image

DATA = Path(os.environ.get("PPT2SCORM_DATA") or tempfile.mkdtemp(prefix="ppt2scorm_web_"))
JOBS_DIR = DATA / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
MAX_BYTES = int(os.environ.get("PPT2SCORM_MAX_MB", "50")) * 1024 * 1024
DAILY_LIMIT = int(os.environ.get("PPT2SCORM_DAILY_LIMIT", "10"))
JOB_TTL = int(os.environ.get("PPT2SCORM_JOB_TTL_MIN", "120")) * 60
STATIC = Path(__file__).resolve().parent / "static"

PROVIDER_LABELS = {"gemini": "Google Gemini", "claude": "Anthropic Claude", "openrouter": "OpenRouter",
                   "glm": "Z.ai GLM", "qwen": "Alibaba Qwen"}

app = FastAPI(title="ppt2scorm", docs_url=None, redoc_url=None)
pool = ThreadPoolExecutor(max_workers=int(os.environ.get("PPT2SCORM_WORKERS", "2")))
jobs = {}
usage = {}  # (ip, day) -> count of AI conversions on the server's key
lock = threading.Lock()

STEPS = ["upload", "extract", "design", "narrate", "package", "done"]


def _server_key(provider):
    return bool(os.environ.get(PROVIDERS[provider][0]))


def _client_ip(request):
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


def _cleanup():
    now = time.time()
    with lock:
        old = [jid for jid, j in jobs.items() if now - j["created"] > JOB_TTL]
        for jid in old:
            jobs.pop(jid, None)
            edit_locks.pop(jid, None)
    for jid in old:
        shutil.rmtree(JOBS_DIR / jid, ignore_errors=True)


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


def _run(jid, use_ai, provider, api_key, brand, instructions, narration="off", voice=None, tts_key=None):
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
        if narration == "audio":
            _update(jid, step="narrate", narr_done=0, narr_total=0)
        warnings = warnings + add_narration(course, folder / "media", mode=narration, voice=voice, api_key=tts_key,
                                            progress=lambda k, n: _update(jid, narr_done=k, narr_total=n))
        tts_key = None
        _update(jid, step="package")
        text = json.dumps(course, indent=2, ensure_ascii=False)
        (folder / "course.json").write_text(text, encoding="utf-8")
        (folder / "course.draft.json").write_text(text, encoding="utf-8")  # untouched copy for "revert"
        build_package(course, folder / "course.zip", media_dir=folder / "media", brand=brand,
                      keep_dir=folder / "package")
        _update(jid, step="done", status="done", how=how, warnings=warnings, title=course["title"], version=1,
                voiceover=course.get("voiceover", {}).get("mode", "off"), needs_audio=prune_stale(course),
                summary=screen_summary(course), screens=len(course["screens"]),
                size_kb=(folder / "course.zip").stat().st_size // 1024)
    except Exception as e:  # noqa: BLE001 - every failure is reported to the user
        _update(jid, status="error", error=_friendly(e))


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html")


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
    }


@app.post("/api/convert")
async def convert(request: Request, file: UploadFile = File(...), mode: str = Form("ai"),
                  provider: str = Form("gemini"), api_key: str = Form(""), brand: str = Form(""),
                  instructions: str = Form(""), narration: str = Form("off"), voice: str = Form("")):
    _cleanup()
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
        key = (_client_ip(request), time.strftime("%Y-%m-%d"))
        with lock:
            if usage.get(key, 0) >= DAILY_LIMIT:
                raise HTTPException(429, f"You've used today's {DAILY_LIMIT} free AI conversions. "
                                         "Paste your own API key to continue, or choose Basic mode.")
            usage[key] = usage.get(key, 0) + 1

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

    with lock:
        jobs[jid] = {"id": jid, "status": "running", "step": "upload", "created": time.time(),
                     "filename": Path(file.filename).name, "mode": mode, "brand": brand or None,
                     "provider": PROVIDER_LABELS[provider] if use_ai else None}
    pool.submit(_run, jid, use_ai, provider if use_ai else None, api_key or None, brand or None, instructions,
                narration, voice, tts_key)
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
        build_package(course, nxt_zip, media_dir=folder / "media", brand=brand, keep_dir=nxt_pkg)
        old_pkg = folder / "package.old"
        shutil.rmtree(old_pkg, ignore_errors=True)
        if (folder / "package").exists():
            os.replace(folder / "package", old_pkg)
        os.replace(nxt_pkg, folder / "package")
        os.replace(nxt_zip, folder / "course.zip")
        shutil.rmtree(old_pkg, ignore_errors=True)
        course.pop("theme", None)
        (folder / "course.json").write_text(json.dumps(course, indent=2, ensure_ascii=False), encoding="utf-8")
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
    return {"course": course, "brand": j.get("brand") or "#1f5eff", "edited": edited, "version": j.get("version", 1),
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
        quota = (_client_ip(request), time.strftime("%Y-%m-%d"))
        with lock:
            if not j.get("voice_quota_counted"):   # re-recording edited screens of the same course is free
                if usage.get(quota, 0) >= DAILY_LIMIT:
                    raise HTTPException(429, f"You've used today's {DAILY_LIMIT} free AI runs. Paste your own Gemini key to continue.")
                usage[quota] = usage.get(quota, 0) + 1
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
async def upload_media(jid: str, file: UploadFile = File(...), alt: str = Form("")):
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


@app.get("/healthz")
def health():
    return {"ok": True}


app.mount("/static", StaticFiles(directory=STATIC), name="static")
