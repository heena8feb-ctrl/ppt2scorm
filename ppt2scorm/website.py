"""Brand detection from a company website: logo, colours and fonts.

Reads the home page, its stylesheets (a few, size-limited) and its icons. Colours and fonts are scored by how
often and where they're used; logo candidates come from logo images, the header, touch icons and social cards.
When an AI provider is available, the model picks the best colours, fonts and logo from those candidates
(it can only choose from what was found, never invent). Without AI the scores decide.

Safety: only http(s); every address (including each redirect) must resolve to a public IP, and the connection is
made to that same checked IP (so a site can't swap in a private address between the check and the connection,
"DNS rebinding"). A hosted copy of the app therefore can't be used to reach internal systems. Sizes, counts and
time are all capped.
"""
import colorsys
import functools
import http.client
import ssl
import ipaddress
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from .brand import STACKS, BrandError, clean_logo, contrast, luminance, mix, normalize

UA = "Mozilla/5.0 (compatible; ppt2scorm brand reader; +https://github.com/heena8feb-ctrl/ppt2scorm)"
MAX_HTML, MAX_CSS, MAX_IMG = 2_000_000, 800_000, 4_000_000
MAX_SHEETS, MAX_LOGO_TRIES, TIMEOUT = 6, 5, 12
GENERIC_FONTS = {"sans-serif", "serif", "monospace", "cursive", "fantasy", "system-ui", "inherit", "initial", "unset",
                 "-apple-system", "blinkmacsystemfont", "ui-sans-serif", "ui-serif", "ui-monospace", "emoji",
                 "segoe ui emoji", "apple color emoji", "noto color emoji", "segoe ui symbol", "var"}
ICON_FONT = re.compile(r"(?i)icon|awesome|glyph|material symbols|material icons|fontello|dashicons|ionicons|feather|swiper")
NAMED_STACKS = {"segoe ui": "system", "roboto": "system", "helvetica neue": "arial", "helvetica": "arial", "arial": "arial",
                "verdana": "verdana", "georgia": "georgia", "calibri": "calibri", "cambria": "cambria",
                "trebuchet ms": "trebuchet", "times new roman": "georgia"}


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None  # redirects are followed by hand so every hop is checked


TEST_FLAG = "PPT2SCORM_ALLOW_PRIVATE_FETCH"


def _tests_allow_private():
    # Lets automated tests reach mock servers on 127.0.0.1. It must be exactly "tests-only" so it can't be
    # switched on by accident (e.g. "1" or "true" on a server).
    return os.environ.get(TEST_FLAG) == "tests-only"


def _resolve_public(host, port):
    """Look the host up once and return one public IP to connect to. Raises BrandError otherwise."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise BrandError(f"couldn't find the website {host}. Check the address.") from None
    ips = [ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos]
    if not ips:
        raise BrandError(f"couldn't find the website {host}. Check the address.")
    if not _tests_allow_private():
        for ip in ips:
            if not ip.is_global or ip.is_multicast:
                raise BrandError("that address points to a private network, which isn't allowed")
    return str(ips[0])


def _public(host):
    try:
        _resolve_public(host, 443)
        return True
    except BrandError as e:
        if "private" in str(e):
            return False
        raise


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, pinned_ip=None, **kw):
        super().__init__(host, **kw)
        self._pinned = pinned_ip

    def connect(self):
        self.sock = socket.create_connection((self._pinned, self.port), self.timeout, self.source_address)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, pinned_ip=None, **kw):
        super().__init__(host, **kw)
        self._pinned = pinned_ip

    def connect(self):
        sock = socket.create_connection((self._pinned, self.port), self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)   # certificate still checked for the name


class _PinHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, ip):
        super().__init__()
        self.ip = ip

    def http_open(self, req):
        return self.do_open(functools.partial(_PinnedHTTP, pinned_ip=self.ip), req)


class _PinHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, ip):
        super().__init__(context=ssl.create_default_context())
        self.ip = ip

    def https_open(self, req):
        return self.do_open(functools.partial(_PinnedHTTPS, pinned_ip=self.ip), req, context=self._context)


BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def fetch(url, limit, accept="*/*", ua=UA, headers=None):
    """GET with checks on every hop. Returns (final url, content type, bytes)."""
    for _ in range(5):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise BrandError("use a web address starting with http:// or https://")
        if parts.username or parts.password:
            raise BrandError("web addresses with a user name or password aren't supported")
        try:
            port = parts.port or (443 if parts.scheme == "https" else 80)
        except ValueError:
            raise BrandError("that web address isn't valid") from None
        ip = _resolve_public(parts.hostname, port)
        if os.environ.get("PPT2SCORM_FETCH_VIA_PROXY") == "1":
            # For office networks that only reach the internet through a proxy (laptop use, not public hosting):
            # the proxy makes the connection, so the IP can't be pinned here.
            opener = urllib.request.build_opener(_Redirect)
        else:
            # No system proxy: the connection must go to the IP that was just checked.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _Redirect,
                                                 _PinHTTPHandler(ip), _PinHTTPSHandler(ip))
        req = urllib.request.Request(url, headers=dict({"User-Agent": ua, "Accept": accept, "Accept-Language": "en"}, **(headers or {})))
        try:
            r = opener.open(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                url = urllib.parse.urljoin(url, e.headers["Location"])
                continue
            err = BrandError(f"the website answered with error {e.code}")
            err.status_code = e.code
            raise err from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise BrandError(f"couldn't reach the website ({getattr(e, 'reason', e)})") from None
        with r:
            data = r.read(limit + 1)
            if len(data) > limit:
                data = data[:limit]
            return r.geturl(), (r.headers.get("Content-Type") or "").lower(), data
    raise BrandError("the website redirected too many times")


# ---------- parsing ----------
class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sheets, self.styles, self.inline, self.imgs, self.icons, self.meta = [], [], [], [], [], {}
        self.stack, self.title, self._in_style, self._in_title, self.manifest = [], "", False, False, None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        ctx = " ".join(self.stack[-6:])
        if tag in ("header", "nav") or "header" in a.get("class", "").lower() or "header" in a.get("id", "").lower():
            self.stack.append("header")
        elif tag not in ("img", "link", "meta", "br", "hr", "input", "source"):
            self.stack.append(tag)
        if tag == "link":
            rel = a.get("rel", "").lower()
            if "stylesheet" in rel and a.get("href"):
                self.sheets.append(a["href"])
            if "icon" in rel and a.get("href"):
                self.icons.append((rel, a.get("sizes", ""), a["href"]))
            if "manifest" in rel and a.get("href"):
                self.manifest = a["href"]
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key in ("theme-color", "msapplication-tilecolor", "og:image", "og:site_name", "twitter:image", "application-name"):
                self.meta.setdefault(key, a.get("content", ""))
        elif tag == "img":
            src = a.get("src") or a.get("data-src") or (a.get("srcset", "").split(",")[-1].strip().split(" ")[0])
            if src:
                self.imgs.append({"src": src, "alt": a.get("alt", ""), "cls": (a.get("class", "") + " " + a.get("id", "")),
                                  "in_header": "header" in ctx or "header" in self.stack[-1:],
                                  "w": a.get("width", ""), "h": a.get("height", "")})
        elif tag == "style":
            self._in_style = True
        elif tag == "title":
            self._in_title = True
        if a.get("style"):
            self.inline.append(a["style"])

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = False
        if tag == "title":
            self._in_title = False
        if self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if self._in_style:
            self.styles.append(data)
        elif self._in_title:
            self.title += data


# ---------- colours ----------
COLOR = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b|rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})")
RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _norm(m):
    if m.group(1):
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return "#" + h.lower()
    r, g, b = (min(255, int(m.group(i))) for i in (2, 3, 4))
    return f"#{r:02x}{g:02x}{b:02x}"


def _hls(h):
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hls(r, g, b)


def _near(a, b):
    ha, la, sa = _hls(a)
    hb, lb, sb = _hls(b)
    dh = min(abs(ha - hb), 1 - abs(ha - hb))
    return dh < 0.035 and abs(la - lb) < 0.09 and abs(sa - sb) < 0.25


def score_colors(css_texts, meta_colors):
    """Returns (brand candidates [(hex, score)], text colour, background colour)."""
    brand, darks, lights = {}, {}, {}

    def add(table, c, w):
        for k in table:
            if _near(k, c):
                table[k] += w
                return
        table[c] = table.get(c, 0) + w

    for c, w in meta_colors:
        add(brand, c, w)
    for text in css_texts:
        for sel, body in RULE.findall(text):
            sel_l = sel.lower()
            boost = 1
            if re.search(r"btn|button|primary|brand|cta|accent|header|nav|link|\ba\b|logo", sel_l):
                boost = 3
            for decl in body.split(";"):
                if ":" not in decl:
                    continue
                prop, val = decl.split(":", 1)
                prop = prop.strip().lower()
                w = boost * (4 if re.search(r"--.*(primary|brand|accent|main)", prop) else 1)
                for m in COLOR.finditer(val):
                    c = _norm(m)
                    h, l, s = _hls(c)
                    if s > 0.28 and 0.18 < l < 0.82:
                        add(brand, c, w)
                    elif l < 0.28 and prop in ("color",) and re.search(r"body|html|\bp\b|main|article", sel_l):
                        add(darks, c, 3)
                    elif l < 0.28 and prop == "color":
                        add(darks, c, 1)
                    elif l > 0.9 and "background" in prop and re.search(r"body|html|main", sel_l):
                        add(lights, c, 3)
    cands = sorted(brand.items(), key=lambda kv: -kv[1])[:8]
    text = max(darks.items(), key=lambda kv: kv[1])[0] if darks else "#1b2433"
    bg = max(lights.items(), key=lambda kv: kv[1])[0] if lights else None
    return cands, text, bg


# ---------- fonts ----------
FAMILY = re.compile(r"font-family\s*:\s*([^;}]+)", re.I)


SERIF_HINT = {}   # family -> True when it was declared with a serif fallback


def score_fonts(css_texts, google_families):
    heading, body = {}, {}
    for f in google_families:
        body[f] = body.get(f, 0) + 2
        heading[f] = heading.get(f, 0) + 2
    for text in css_texts:
        for sel, decls in RULE.findall(text):
            sel_l = sel.lower()
            for m in FAMILY.finditer(decls):
                first = m.group(1).split(",")[0].strip().strip("'\"").strip()
                rest = m.group(1).lower()
                if re.search(r"(^|,)\s*serif\b", rest) or "georgia" in rest or "times" in rest:
                    SERIF_HINT[first] = True
                if not first or first.lower() in GENERIC_FONTS or first.startswith("var(") or ICON_FONT.search(first):
                    continue
                if len(first) > 60:
                    continue
                if re.search(r"\bh[1-3]\b|heading|title|display|hero", sel_l):
                    heading[first] = heading.get(first, 0) + 3
                if re.search(r"body|html|\bp\b|:root", sel_l):
                    body[first] = body.get(first, 0) + 3
                body[first] = body.get(first, 0) + 1
                heading[first] = heading.get(first, 0) + 1
    hb = sorted(body.items(), key=lambda kv: -kv[1])
    hh = sorted(heading.items(), key=lambda kv: -kv[1])
    return [f for f, _ in hh[:5]], [f for f, _ in hb[:5]]


def font_choice(family):
    if not family:
        return {"stack": "system"}
    key = NAMED_STACKS.get(family.lower())
    if key:
        return {"stack": key}
    if not re.fullmatch(r"[A-Za-z0-9 _.&'()-]{1,60}", family):
        return {"stack": "system"}
    return {"family": family, "files": [], "fallback": "georgia" if SERIF_HINT.get(family) else "system"}


# ---------- Google Fonts (open-licensed, so they can be bundled in the course) ----------
GOOGLE_CSS = os.environ.get("PPT2SCORM_GOOGLE_FONTS_URL", "https://fonts.googleapis.com/css2")
FACE = re.compile(r"(/\*\s*([\w-]+)\s*\*/\s*)?@font-face\s*\{([^}]*)\}", re.S)


def google_font_files(family):
    """Download regular and bold files for a Google Fonts family. Returns {filename: bytes} and file list, or ({}, [])."""
    from .brand import check_font, font_file_name
    q = urllib.parse.urlencode({"family": f"{family}:wght@400;700", "display": "swap"})
    try:
        # a browser user agent makes Google serve compact WOFF2 files (about a tenth the size of TTF)
        _, ctype, data = fetch(f"{GOOGLE_CSS}?{q}", 300_000, "text/css", ua=BROWSER_UA)
    except BrandError:
        return {}, []
    css = data.decode("utf-8", "replace")
    if "@font-face" not in css:
        return {}, []
    picked = {}
    for m in FACE.finditer(css):
        subset, body = (m.group(2) or "").lower(), m.group(3)
        if re.search(r"font-style:\s*italic", body):
            continue
        w = re.search(r"font-weight:\s*(\d{3})", body)
        u = re.search(r"url\(([^)]+)\)", body)
        if not (w and u):
            continue
        weight = int(w.group(1))
        if weight in picked and picked[weight][0] == "latin":
            continue
        if subset and subset != "latin" and weight in picked:
            continue
        picked[weight] = (subset or "latin", u.group(1).strip("'\""))
    files, out = {}, []
    for weight, (_, url) in sorted(picked.items())[:2]:
        try:
            _, _, raw = fetch(url, 3_000_000, "font/*")
            ext = "." + url.split("?")[0].rsplit(".", 1)[-1].lower()
            ext = check_font(raw, "x" + ext)
        except BrandError:
            continue
        name = font_file_name(family, raw, ext)
        files[name] = raw
        out.append({"file": name, "weight": weight, "style": "normal"})
    return files, out


# ---------- logos ----------
def logo_candidates(page, base, site_name):
    out = []
    host = urllib.parse.urlsplit(base).hostname or ""
    brand_word = re.sub(r"^www\.", "", host).split(".")[0].lower()
    for im in page.imgs:
        blob = (im["src"] + " " + im["alt"] + " " + im["cls"]).lower()
        score = 0
        if "logo" in blob:
            score += 10
        if im["in_header"]:
            score += 4
        if brand_word and brand_word in blob:
            score += 3
        if site_name and site_name.lower() in im["alt"].lower():
            score += 2
        if score >= 6:
            out.append({"url": urllib.parse.urljoin(base, im["src"]), "score": score, "why": "logo image on the page",
                        "alt": im["alt"]})
    for rel, sizes, href in page.icons:
        big = max([int(n) for n in re.findall(r"(\d+)x\d+", sizes)] or [0])
        if "apple-touch-icon" in rel:
            out.append({"url": urllib.parse.urljoin(base, href), "score": 6, "why": "app icon", "alt": ""})
        elif big >= 128:
            out.append({"url": urllib.parse.urljoin(base, href), "score": 4, "why": f"site icon ({big}px)", "alt": ""})
    for key in ("og:image", "twitter:image"):
        if page.meta.get(key):
            out.append({"url": urllib.parse.urljoin(base, page.meta[key]), "score": 2, "why": "social sharing image", "alt": ""})
    seen, uniq = set(), []
    for c in sorted(out, key=lambda c: -c["score"]):
        if c["url"] not in seen:
            seen.add(c["url"])
            uniq.append(c)
    return uniq[:8]


def _fetch_logo(c):
    if c["url"].lower().split("?")[0].endswith(".svg"):
        return None, "SVG"
    try:
        _, ctype, data = fetch(c["url"], MAX_IMG, "image/*")
    except BrandError:
        return None, "unreachable"
    if "svg" in ctype:
        return None, "SVG"
    try:
        png = clean_logo(data)
    except BrandError:
        return None, "not an image"
    from PIL import Image
    import io
    w, h = Image.open(io.BytesIO(png)).size
    if max(w, h) < 48:
        return None, "too small"
    if c["why"] == "social sharing image" and w > 3.2 * h:
        return None, "a banner, not a logo"
    return png, f"{w}x{h}"


# ---------- AI choice ----------
AI_SYSTEM = """You are a brand designer. From colours, fonts and logo images found on a company's website, pick
the company's brand kit for e-learning courses. Choose ONLY from the candidates given; never invent values.
Return only JSON: {"primary": "#rrggbb", "accent": "#rrggbb", "heading_font": "<name or empty>",
"body_font": "<name or empty>", "logo": <candidate number or null>, "reason": "<one sentence>"}.
primary = the main brand colour (usually the logo or button colour, not a link-blue default or a warning red).
accent = a second brand colour that pairs with it, or the same as primary if there's only one."""


def ai_pick(found, provider, api_key=None, model=None):
    from .ai_redesign import _make_caller, _parse_json
    call, _ = _make_caller(provider, model, api_key, system=AI_SYSTEM)
    msg = json.dumps({
        "site": found["site"], "title": found["title"],
        "colours": [{"hex": c, "score": s} for c, s in found["colors"]],
        "theme_color_meta": found["theme_color"],
        "heading_fonts": found["heading_fonts"], "body_fonts": found["body_fonts"],
        "logos": [{"number": i, "from": c["why"], "url": c["url"][-120:], "alt": c["alt"][:80], "size": c.get("size")}
                  for i, c in enumerate(found["logos"]) if c.get("png")],
    }, ensure_ascii=False)
    pick = _parse_json(call([{"role": "user", "content": msg}]))
    return pick if isinstance(pick, dict) else {}


# ---------- main ----------
def brand_from_website(url, provider=None, api_key=None, model=None, site_name=None):
    """Returns (kit, logo_png or None, fonts {filename: bytes}, notes, how)."""
    url = url.strip()
    if re.match(r"(?i)^[a-z][a-z0-9+.-]*://", url) and not re.match(r"(?i)^https?://", url):
        raise BrandError("use a web address starting with http:// or https://")
    if not re.match(r"(?i)^https?://", url):
        url = "https://" + url
    final, ctype, html = fetch(url, MAX_HTML, "text/html,application/xhtml+xml")
    if "html" not in ctype and not html.lstrip()[:200].lower().startswith((b"<!doctype", b"<html")):
        raise BrandError("that address didn't return a web page")
    page = _Page()
    page.feed(html.decode("utf-8", "replace"))
    notes = []
    site = (page.meta.get("og:site_name") or page.meta.get("application-name") or page.title.split("|")[0].split(" - ")[0]).strip()[:50]
    host = urllib.parse.urlsplit(final).hostname or "site"

    css_texts = list(page.styles) + [" x{" + s + "}" for s in page.inline]
    google = []
    for href in page.sheets[:MAX_SHEETS + 4]:
        full = urllib.parse.urljoin(final, href)
        if "fonts.googleapis.com" in full:
            google += [urllib.parse.unquote_plus(f.split(":")[0]) for f in re.findall(r"family=([^&]+)", full)]
            continue
        if len(css_texts) - len(page.styles) - len(page.inline) >= MAX_SHEETS:
            break
        try:
            _, _, data = fetch(full, MAX_CSS, "text/css")
            css_texts.append(data.decode("utf-8", "replace"))
        except BrandError:
            continue
    meta_colors = []
    tc = page.meta.get("theme-color") or page.meta.get("msapplication-tilecolor") or ""
    m = COLOR.search(tc)
    theme_color = _norm(m) if m else None
    if theme_color:
        meta_colors.append((theme_color, 25))
    if page.manifest:
        try:
            _, _, mdata = fetch(urllib.parse.urljoin(final, page.manifest), 200_000, "application/json")
            mt = json.loads(mdata.decode("utf-8", "replace")).get("theme_color", "")
            m = COLOR.search(mt or "")
            if m:
                meta_colors.append((_norm(m), 15))
        except (BrandError, ValueError, AttributeError):
            pass

    colors, text, bg = score_colors(css_texts, meta_colors)
    heading_fonts, body_fonts = score_fonts(css_texts, google)
    logos = logo_candidates(page, final, site or host)
    tried, svg_only = 0, False
    for c in logos:
        if tried >= MAX_LOGO_TRIES:
            break
        tried += 1
        png, info = _fetch_logo(c)
        if png:
            c["png"], c["size"] = png, info
        elif info == "SVG":
            svg_only = True

    found = {"site": host, "title": site, "colors": colors, "theme_color": theme_color, "heading_fonts": heading_fonts,
             "body_fonts": body_fonts, "logos": logos}
    primary = colors[0][0] if colors else (theme_color or "#1f5eff")
    accent = next((c for c, _ in colors[1:] if abs(_hls(c)[0] - _hls(primary)[0]) > 0.08), primary)
    hfont, bfont = (heading_fonts[0] if heading_fonts else ""), (body_fonts[0] if body_fonts else "")
    logo = next((c["png"] for c in logos if c.get("png")), None)
    how = "the website's colours and fonts, ranked by how much they're used"

    if provider:
        try:
            pick = ai_pick(found, provider, api_key, model)
            allowed = {c for c, _ in colors} | ({theme_color} if theme_color else set())
            if pick.get("primary") in allowed:
                primary = pick["primary"]
            if pick.get("accent") in allowed:
                accent = pick["accent"]
            if pick.get("heading_font") in heading_fonts + body_fonts:
                hfont = pick["heading_font"]
            if pick.get("body_font") in heading_fonts + body_fonts:
                bfont = pick["body_font"]
            li = pick.get("logo")
            if isinstance(li, int) and 0 <= li < len(logos) and logos[li].get("png"):
                logo = logos[li]["png"]
            how = "AI's pick from the website's colours, fonts and logos"
            if pick.get("reason"):
                notes.append("AI: " + str(pick["reason"])[:200])
        except Exception as e:  # noqa: BLE001 - AI is optional; the scores still give a good kit
            notes.append(f"The AI couldn't help this time ({str(e)[:80]}), so the most-used colours and fonts were chosen.")

    if not colors:
        notes.append("No clear brand colour was found (the site may build its pages with JavaScript). Pick colours by hand.")
    if not logo:
        notes.append("No usable logo was found" + (" (the site's logo is an SVG, which isn't accepted for safety)" if svg_only else "") +
                     ". Upload a PNG of your logo.")
    font_files, roles = {}, {}
    for role, fam in (("heading", hfont), ("body", bfont or hfont)):
        choice = font_choice(fam)
        if choice.get("family"):
            got, listed = google_font_files(fam)
            if listed:
                font_files.update(got)
                choice["files"] = listed
            else:
                notes.append(f"\"{fam}\" is the website's font. Upload its font files to show it on every computer; "
                             "until then it's used only where it's installed.")
        roles[role] = choice
    if any(r.get("files") for r in roles.values()):
        notes.append("Free Google Fonts used by the website were downloaded and will be bundled in your courses.")
    if contrast(primary, "#ffffff") < 1.6:
        notes.append("The main colour is very light; check that buttons are easy to read.")
    page_bg = bg if bg and luminance(bg) > 0.85 else mix("#ffffff", primary, 0.05)
    kit = normalize({"name": f"{site or host} brand"[:60], "colors": {"primary": primary, "accent": accent,
                     "text": text, "background": page_bg},
                     "fonts": roles,
                     "corners": "rounded", "logo": "logo.png" if logo else None, "logo_alt": f"{site or host} logo"})
    seen = set()
    notes = [n for n in notes if not (n in seen or seen.add(n))]
    return kit, logo, font_files, notes, how


__all__ = ["brand_from_website", "fetch", "STACKS"]
