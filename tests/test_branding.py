"""End-to-end test of brand kits: website and deck detection, the kit dialog, applying kits to courses,
editor switching, import/export and upload checks. Uses a mock company website and a mock Google Fonts service.
Run from the project root:  python tests/test_branding.py [screenshot_dir]"""
import functools
import http.server
import io
import json
import os
import sys
import tempfile
import threading
import time
import urllib.parse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else None
TMP = Path(tempfile.mkdtemp())
os.environ["PPT2SCORM_BRANDS"] = str(TMP / "brands")
os.environ["PPT2SCORM_ALLOW_PRIVATE_FETCH"] = "tests-only"          # the mock site runs on this machine
os.environ["PPT2SCORM_GOOGLE_FONTS_URL"] = "http://127.0.0.1:8912/css2"
os.environ.pop("GEMINI_API_KEY", None)
DECK = ROOT / "samples/data_privacy_essentials.pptx"
FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def make_site(d):
    from PIL import Image, ImageDraw
    (d / "assets").mkdir(parents=True)
    im = Image.new("RGBA", (420, 120), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    dr.rounded_rectangle((0, 10, 100, 110), 20, fill=(0, 90, 170))
    dr.rectangle((130, 45, 410, 75), fill=(0, 90, 170))
    im.save(d / "assets/northwind-logo.png")
    Image.new("RGB", (180, 180), (0, 90, 170)).save(d / "apple-touch-icon.png")
    (d / "index.html").write_text('''<!doctype html><html><head><title>Northwind Appliances | Home</title>
<meta name="theme-color" content="#005aaa"><meta property="og:site_name" content="Northwind Appliances">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Nunito+Sans:wght@400;700&display=swap">
<link rel="stylesheet" href="/assets/site.css"></head><body><header class="site-header"><a href="/">
<img src="/assets/northwind-logo.png" alt="Northwind Appliances" class="header__logo"></a></header>
<main><h1>Built to last</h1></main></body></html>''')
    (d / "assets/site.css").write_text(''':root{--brand-primary:#005aaa;--brand-accent:#ff6b00}
body{font-family:"Nunito Sans",Arial,sans-serif;color:#1d2b3a;background:#ffffff}
h1,h2{font-family:"Playfair Display",Georgia,serif} .btn-primary{background:#005aaa;color:#fff}
.cta{background:#ff6b00} a{color:#005aaa} .alert-danger{color:#dc3545} .badge{background:#28a745}''')


class GoogleMock(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        if u.path == "/css2":
            fam = urllib.parse.parse_qs(u.query).get("family", [""])[0].split(":")[0]
            if fam != "Nunito Sans":
                self.send_response(400); self.end_headers(); return
            body = "/* latin */\n@font-face { font-family: 'Nunito Sans'; font-weight: 400; src: url(http://127.0.0.1:8912/f/a.ttf); }\n"
            self._send(body.encode(), "text/css")
        elif u.path == "/f/a.ttf":
            self._send(FONT.read_bytes(), "font/ttf")
        else:
            self.send_response(404); self.end_headers()

    def _send(self, b, t):
        self.send_response(200); self.send_header("Content-Type", t); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)


def serve(handler, port):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    r = {}
    site = TMP / "site"
    make_site(site)

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a): pass
    serve(functools.partial(Quiet, directory=str(site)), 8911)
    serve(GoogleMock, 8912)
    sys.path.insert(0, str(ROOT / "samples"))
    from make_brand_deck import main as make_brand_deck
    branded = TMP / "Greenfield_Water_Safety.pptx"
    make_brand_deck(str(branded))

    # --- safety: private addresses are refused when the test override is off ---
    from ppt2scorm.brand import BrandError
    from ppt2scorm.website import brand_from_website
    os.environ["PPT2SCORM_ALLOW_PRIVATE_FETCH"] = "0"
    for bad in ("http://127.0.0.1:8911/", "http://169.254.169.254/", "ftp://example.com/"):
        try:
            brand_from_website(bad)
            raise AssertionError("not blocked: " + bad)
        except BrandError:
            pass
    os.environ["PPT2SCORM_ALLOW_PRIVATE_FETCH"] = "tests-only"
    r["safety"] = "private network, cloud metadata and non-web addresses refused"

    import uvicorn
    from playwright.sync_api import sync_playwright
    from web.app import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8769, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.1)
    base = "http://127.0.0.1:8769"

    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1440, "height": 960})
        errors, prompts = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append("CSP: " + m.text) if "Content Security Policy" in m.text else None)
        page.on("dialog", lambda d: (prompts.append(d.message), d.accept("Greenfield (saved)" if d.type == "prompt" else None)))
        page.goto(base)
        page.wait_for_selector("#brandkit option[value=deck]", state="attached")
        get = lambda path: page.evaluate("p => fetch(p).then(r => r.json())", path)

        # --- 1. New kit from a website ---
        page.set_input_files("#file", str(DECK))
        page.click("#bk-new")
        page.wait_for_selector("#bk[open]")
        page.fill("#bk-url", "http://127.0.0.1:8911/")
        page.click("#bk-read")
        page.wait_for_selector(".bk-auto-msg.is-ok", timeout=30000)
        assert page.input_value("#bk-c-primary") == "#005aaa" and page.input_value("#bk-c-accent") == "#ff6b00"
        assert page.input_value("#bk-name") == "Northwind Appliances brand"
        assert page.is_visible("#bk-logo-img")
        assert page.input_value("#bk-f-body") == "custom" and page.input_value("#bk-fam-body") == "Nunito Sans"
        assert "a.ttf" not in page.text_content("#bk-files-body") and "Nunito Sans" in page.text_content("#bk-files-body")
        page.check("input[name=bk-corners][value=square]", force=True)
        page.fill("#bk-name", "Northwind")
        if SHOTS: page.wait_for_timeout(500); page.screenshot(path=str(SHOTS / "brand_1_dialog.png"))
        page.click("#bk-save")
        page.wait_for_selector("#bk:not([open])", state="attached")
        assert page.input_value("#brandkit") == "northwind", page.input_value("#brandkit")
        kits = get("/api/brands")["kits"]
        nw = kits[0]
        assert nw["corners"] == "square" and nw["logo_url"] and nw["fonts"]["body"]["files"], nw
        r["website_kit"] = f"{nw['name']}: main {nw['colors']['primary']}, accent {nw['colors']['accent']}, logo, bundled body font, square corners"

        # --- 2. Convert with the kit: the course carries logo, colours, fonts and corners ---
        page.check("input[name=mode][value=basic]", force=True)
        page.click("#convert")
        page.wait_for_selector("#actions-done:not(.is-hidden)", timeout=60000)
        job = page.evaluate("document.getElementById('dl-zip').getAttribute('href').split('/')[3]")
        frame = page.frame_locator("#preview")
        frame.locator(".brand-logo").wait_for()
        css_vars = frame.locator("html").evaluate("""e => { const s = getComputedStyle(e);
            return [s.getPropertyValue('--brand').trim(), s.getPropertyValue('--rs').trim(), s.getPropertyValue('--font-body').trim()]; }""")
        assert css_vars[0] == "#005aaa" and css_vars[1] in ("0", "0.0") and css_vars[2].startswith('"Nunito Sans"'), css_vars
        frame.locator("text=Next").first.click()
        loaded = frame.locator("html").evaluate("async () => { await document.fonts.ready; return [...document.fonts].filter(f => f.status === 'loaded').map(f => f.family); }")
        assert any("Nunito Sans" in f for f in loaded), loaded
        data = page.evaluate("id => fetch('/api/jobs/' + id + '/download').then(r => r.arrayBuffer()).then(b => Array.from(new Uint8Array(b)))", job)
        z = zipfile.ZipFile(io.BytesIO(bytes(data)))
        names = z.namelist()
        assert "brand.css" in names and "brand/logo.png" in names and any(n.startswith("brand/fonts/") for n in names), names
        assert all(f"<file href=\"{n}\"/>" in z.read("imsmanifest.xml").decode() for n in names if n.startswith("brand/"))
        if SHOTS: page.wait_for_timeout(500); page.screenshot(path=str(SHOTS / "brand_2_course.png"))
        r["course"] = "logo in header, brand colour, square corners, bundled font loaded; zip + manifest include brand files"

        # --- 3. Editor: switch to standard look and back ---
        page.click("#edit")
        page.wait_for_selector("#editor:not(.is-hidden) .ed-item")
        page.click("#ed-course")
        assert "Northwind" in page.text_content(".ed-brand-now")
        page.select_option("#ed-c-kit", "standard")
        page.click("#ed-kit-apply")
        page.wait_for_function("() => document.getElementById('ed-status').textContent.startsWith('Brand applied')", timeout=15000)
        assert page.text_content(".ed-brand-now") == "Standard look"
        page.frame_locator("#ed-preview").locator(".brand-dot").wait_for()
        page.select_option("#ed-c-kit", "kit:northwind")
        page.click("#ed-kit-apply")
        page.wait_for_function("() => document.querySelector('.ed-brand-now') && document.querySelector('.ed-brand-now').textContent.includes('Northwind')", timeout=15000)
        page.frame_locator("#ed-preview").locator(".brand-logo").wait_for()
        r["editor_switch"] = "standard look and back to the kit, preview updates"
        page.click("#ed-done")

        # --- 4. Deck branding, then save it as a kit from the editor ---
        page.click("#again")
        page.set_input_files("#file", str(branded))
        page.select_option("#brandkit", "deck")
        page.click("#convert")
        page.wait_for_selector("#actions-done:not(.is-hidden)", timeout=60000)
        assert "detected from the deck" in page.text_content("#summary")
        frame = page.frame_locator("#preview")
        assert frame.locator("html").evaluate("e => getComputedStyle(e).getPropertyValue('--brand').trim()") == "#0b6e4f"
        page.click("#edit")
        page.wait_for_selector("#editor:not(.is-hidden) .ed-item")
        page.click("#ed-course")
        page.click("#ed-kit-save")
        page.wait_for_function("() => document.getElementById('ed-status').textContent.startsWith('Saved the brand kit')", timeout=15000)
        names = [k["name"] for k in get("/api/brands")["kits"]]
        assert "Greenfield (saved)" in names, names
        r["deck_kit"] = "deck theme detected (#0b6e4f, Montserrat/Georgia, logo), saved from the editor"
        page.click("#ed-done")

        # --- 5. Edit a kit: brand font upload, bad logo and bad font rejected; export and import ---
        page.click("#again")
        assert page.locator("#brandkit option", has_text="Greenfield (saved)").count() == 1, "saved kit listed on the upload page"
        page.select_option("#brandkit", "northwind")
        page.click("#bk-edit")
        page.wait_for_selector("#bk[open]")
        page.select_option("#bk-f-heading", "custom")
        page.fill("#bk-fam-heading", "Brand Serif")
        page.set_input_files("#bk-up-heading", str(FONT))
        assert "DejaVuSans.ttf (new)" in page.text_content("#bk-files-heading")
        page.fill("#bk-c-accent", "#7a2bd6")
        fake = TMP / "logo.png"
        fake.write_text("not really a picture")
        page.set_input_files("#bk-logo", str(fake))
        page.click("#bk-save")
        page.wait_for_selector("#bk-error:not([hidden])")
        assert "couldn't be read as an image" in page.text_content("#bk-error"), page.text_content("#bk-error")
        page.click("#bk-logo-rm")
        page.click("#bk-save")
        page.wait_for_selector("#bk:not([open])", state="attached")
        k = [x for x in get("/api/brands")["kits"] if x["id"] == "northwind"][0]
        assert k["colors"]["accent"] == "#7a2bd6" and k["fonts"]["heading"]["family"] == "Brand Serif" and k["fonts"]["heading"]["files"][0]["weight"] == 400
        assert k["logo_url"] is None
        st = page.evaluate("""async () => { const fd = new FormData();
            fd.append('kit', JSON.stringify({name: 'Bad', fonts: {heading: {family: 'X'}, body: {stack: 'system'}}}));
            fd.append('heading_files', new File(['hello'], 'x.woff2'));
            const r = await fetch('/api/brands', {method: 'POST', body: fd}); return [r.status, (await r.json()).detail]; }""")
        assert st[0] == 422 and "WOFF2" in st[1], st
        exported = get("/api/brands/northwind/export")
        assert exported["ppt2scorm_brand_kit"] == 1 and any(a.startswith("fonts/") for a in exported["assets"])
        exp = TMP / "northwind.brand.json"
        exp.write_text(json.dumps(exported))
        page.set_input_files("#bk-import", str(exp))
        page.wait_for_function("() => document.getElementById('brand-help').textContent.startsWith('Imported')", timeout=15000)
        assert page.input_value("#brandkit") == "northwind-2"
        r["kit_edit"] = "brand font uploaded, fake logo and fake font rejected, exported and re-imported as northwind-2"

        # --- 6. Delete a kit ---
        page.click("#bk-edit")
        page.wait_for_selector("#bk[open]")
        page.click("#bk-delete")
        page.wait_for_selector("#bk:not([open])", state="attached")
        assert "northwind-2" not in [k["id"] for k in get("/api/brands")["kits"]]
        assert page.input_value("#brandkit") == "standard"
        r["delete"] = "ok"

        # mobile: the dialog fits
        page.set_viewport_size({"width": 390, "height": 844})
        page.click("#bk-new")
        page.wait_for_selector("#bk[open]")
        assert page.evaluate("document.getElementById('bk').scrollWidth <= document.getElementById('bk').clientWidth + 2")
        if SHOTS: page.screenshot(path=str(SHOTS / "brand_3_mobile.png"))
        assert not errors, errors
        b.close()
    server.should_exit = True
    print(json.dumps(r, indent=1))
    print("ALL BRANDING CHECKS PASSED")


if __name__ == "__main__":
    main()
