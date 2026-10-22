"""Security for the ppt2scorm web app: who the visitor is, usage limits, an optional password, and browser headers.

Environment (all optional):
  PPT2SCORM_APP_PASSWORD        ask for this password before anyone can use the app (recommended on public hosts)
  PPT2SCORM_CLIENT_IP_HEADER    header your host sets to the visitor's real IP and that visitors can't forge,
                                e.g. cf-connecting-ip behind Cloudflare (Render uses Cloudflare)
  PPT2SCORM_PROXY_HOPS          or: number of proxies in front of the app that append to X-Forwarded-For
                                (0 = use the direct connection, the default)
  PPT2SCORM_DAILY_LIMIT         AI runs per visitor per day on the server's keys (default 10)
  PPT2SCORM_GLOBAL_DAILY_LIMIT  AI runs per day on the server's keys for everyone together (default 100)
  PPT2SCORM_ACTIONS_PER_10MIN   uploads, conversions and look-ups per visitor per 10 minutes (default 30)
  PPT2SCORM_MAX_QUEUE           conversions running or waiting at once (default 6)
  PPT2SCORM_MAX_DISK_MB         total space for jobs and drafts (default 2000)
"""
import hashlib
import hmac
import ipaddress
import os
import secrets
import threading
import time
from collections import deque
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

DAILY_LIMIT = int(os.environ.get("PPT2SCORM_DAILY_LIMIT", "10"))
GLOBAL_DAILY_LIMIT = int(os.environ.get("PPT2SCORM_GLOBAL_DAILY_LIMIT", "100"))
ACTIONS_PER_WINDOW = int(os.environ.get("PPT2SCORM_ACTIONS_PER_10MIN", "30"))
WINDOW = 600
MAX_QUEUE = int(os.environ.get("PPT2SCORM_MAX_QUEUE", "6"))
MAX_DISK = int(os.environ.get("PPT2SCORM_MAX_DISK_MB", "2000")) * 1024 * 1024
PASSWORD = os.environ.get("PPT2SCORM_APP_PASSWORD", "")
IP_HEADER = os.environ.get("PPT2SCORM_CLIENT_IP_HEADER", "").strip().lower()
PROXY_HOPS = int(os.environ.get("PPT2SCORM_PROXY_HOPS", "0"))
COOKIE = "ppt2scorm_session"
SESSION_DAYS = 7

_lock = threading.Lock()
_daily = {}        # (ip, day) -> AI runs on the server's keys
_global = {}       # day -> AI runs on the server's keys
_actions = {}      # ip -> deque of timestamps
_logins = {}       # ip -> deque of failed login timestamps
_secret = secrets.token_bytes(32)   # sessions end when the server restarts


def _valid_ip(value):
    try:
        return str(ipaddress.ip_address(value.strip()))
    except (ValueError, AttributeError):
        return None


def client_ip(request: Request):
    """The visitor's IP, taken only from sources a visitor can't forge."""
    if IP_HEADER:
        ip = _valid_ip((request.headers.get(IP_HEADER) or "").split(",")[0])
        if ip:
            return ip
    if PROXY_HOPS > 0:
        hops = [h for h in (request.headers.get("x-forwarded-for") or "").split(",") if h.strip()]
        # each trusted proxy appends the address it saw, so the visitor is PROXY_HOPS from the right;
        # anything further left was sent by the visitor and can't be trusted
        if len(hops) >= PROXY_HOPS:
            ip = _valid_ip(hops[-PROXY_HOPS])
            if ip:
                return ip
    return request.client.host if request.client else "unknown"


def _today():
    return time.strftime("%Y-%m-%d")


def _prune(day):
    for k in [k for k in _daily if k[1] != day]:
        _daily.pop(k, None)
    for k in [k for k in _global if k != day]:
        _global.pop(k, None)


def check_ai_quota(request: Request, count=True, soft=False):
    """Count one AI run on the server's keys. Raises 429 (or returns False when soft) once a limit is reached."""
    ip, day = client_ip(request), _today()
    with _lock:
        _prune(day)
        if _global.get(day, 0) >= GLOBAL_DAILY_LIMIT:
            if soft:
                return False
            raise HTTPException(429, "This server has used all of today's free AI runs. Paste your own API key "
                                     "to continue, or choose Basic mode.")
        if _daily.get((ip, day), 0) >= DAILY_LIMIT:
            if soft:
                return False
            raise HTTPException(429, f"You've used today's {DAILY_LIMIT} free AI runs. "
                                     "Paste your own API key to continue, or choose Basic mode.")
        if count:
            _daily[(ip, day)] = _daily.get((ip, day), 0) + 1
            _global[day] = _global.get(day, 0) + 1
    return True


def check_rate(request: Request):
    """Limit uploads, conversions and look-ups per visitor, whoever's key they use."""
    ip, now = client_ip(request), time.time()
    with _lock:
        q = _actions.setdefault(ip, deque())
        while q and now - q[0] > WINDOW:
            q.popleft()
        if len(q) >= ACTIONS_PER_WINDOW:
            raise HTTPException(429, "Too many requests in a short time. Wait a few minutes and try again.")
        q.append(now)
        if len(_actions) > 5000:   # forget idle visitors
            for k in [k for k, v in _actions.items() if not v or now - v[-1] > WINDOW]:
                _actions.pop(k, None)


def check_capacity(busy_jobs, data_dir: Path):
    """Refuse new work when the queue is full or the disk budget is used up."""
    if busy_jobs >= MAX_QUEUE:
        raise HTTPException(503, "The server is busy converting other decks. Try again in a few minutes.")
    used = 0
    for f in Path(data_dir).rglob("*"):
        try:
            if f.is_file():
                used += f.stat().st_size
        except OSError:
            pass
        if used > MAX_DISK:
            raise HTTPException(503, "The server is out of space for now. Try again later.")


# ---------- optional password ----------
def _sign(expires):
    return hmac.new(_secret, f"{expires}".encode(), hashlib.sha256).hexdigest()


def _session_ok(request: Request):
    raw = request.cookies.get(COOKIE, "")
    exp, _, sig = raw.partition(".")
    return exp.isdigit() and int(exp) > time.time() and hmac.compare_digest(sig, _sign(exp))


LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>ppt2scorm: sign in</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;background:#f3f4f2;color:#16302b;display:grid;place-items:center;min-height:100vh;margin:0}}
form{{background:#fff;border:1.5px solid #16302b;border-radius:8px;padding:28px;width:min(360px,90vw)}}
h1{{font-size:22px;margin:0 0 6px}}p{{margin:0 0 16px;color:#4b5d58}}label{{font-weight:700;display:block;margin-bottom:6px}}
input{{font:inherit;width:100%;box-sizing:border-box;padding:10px 12px;border:1.5px solid #9aa7a3;border-radius:6px}}
button{{font:inherit;font-weight:700;margin-top:14px;width:100%;padding:11px;border:0;border-radius:6px;background:#0f6b5c;color:#fff;cursor:pointer}}
.err{{color:#b3261e;font-weight:700}}</style></head><body>
<form method="post" action="/login"><h1>ppt2scorm</h1><p>Enter the password you were given.</p>{error}
<label for="pw">Password</label><input id="pw" name="password" type="password" autocomplete="current-password" autofocus required>
<input type="hidden" name="next" value="{next}"><button type="submit">Sign in</button></form></body></html>"""

OPEN_PATHS = ("/login", "/healthz")


def _safe_next(value):
    value = value or "/"
    return value if value.startswith("/") and not value.startswith("//") and "\\" not in value else "/"


async def password_gate(request: Request, call_next):
    if not PASSWORD or request.url.path in OPEN_PATHS or _session_ok(request):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Please sign in again."}, status_code=401)
    nxt = request.url.path + (("?" + request.url.query) if request.url.query else "")
    return RedirectResponse("/login?next=" + _quote(nxt), status_code=303)


def _quote(s):
    from urllib.parse import quote
    return quote(s, safe="/")


def _esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def login_page(request: Request, error=""):
    nxt = _safe_next(request.query_params.get("next"))
    return HTMLResponse(LOGIN_PAGE.format(error=f'<p class="err" role="alert">{error}</p>' if error else "", next=_esc(nxt)),
                        status_code=401 if error else 200)


async def login_submit(request: Request):
    from urllib.parse import parse_qs
    ip, now = client_ip(request), time.time()
    with _lock:
        q = _logins.setdefault(ip, deque())
        while q and now - q[0] > WINDOW:
            q.popleft()
        if len(q) >= 8:
            return HTMLResponse(LOGIN_PAGE.format(error='<p class="err" role="alert">Too many attempts. Wait 10 minutes.</p>',
                                                  next="/"), status_code=429)
    form = parse_qs((await request.body())[:4096].decode("utf-8", "replace"))
    given = (form.get("password") or [""])[0]
    nxt = _safe_next((form.get("next") or ["/"])[0])
    if not PASSWORD or not hmac.compare_digest(given.encode(), PASSWORD.encode()):
        with _lock:
            _logins[ip].append(now)
        return HTMLResponse(LOGIN_PAGE.format(error='<p class="err" role="alert">That password isn\'t right.</p>', next=_esc(nxt)),
                            status_code=401)
    exp = str(int(now + SESSION_DAYS * 86400))
    resp = RedirectResponse(nxt, status_code=303)
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie(COOKIE, f"{exp}.{_sign(exp)}", max_age=SESSION_DAYS * 86400, httponly=True, secure=secure,
                    samesite="lax", path="/")
    return resp


def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE, path="/")
    return resp


# ---------- browser security headers ----------
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' data: https://fonts.gstatic.com; img-src 'self' data: blob: https://images.pexels.com"
       + ((" " + os.environ["PPT2SCORM_CSP_EXTRA_IMG"]) if os.environ.get("PPT2SCORM_CSP_EXTRA_IMG") else "") + "; "
       "media-src 'self' blob:; connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; "
       "object-src 'none'; base-uri 'self'; form-action 'self'")
# Files from decks, uploads and brand kits are shown as images only: nothing in them may run.
FILE_CSP = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"


async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    path = request.url.path
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("X-Frame-Options", "SAMEORIGIN")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    is_file = ("/media/" in path or "/brand/" in path or "/logo" in path or "/fonts/" in path) and not path.endswith(".css")
    h.setdefault("Content-Security-Policy", FILE_CSP if is_file else CSP)
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        h.setdefault("Strict-Transport-Security", "max-age=31536000")
    return resp
