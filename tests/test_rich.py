"""End-to-end test of rich content: infographics, module dividers, sort activities, icons and Pexels photos.
A mock AI returns the drain-filter showcase course and a mock Pexels serves photos, so no real keys are needed.
Run from the project root:  python tests/test_rich.py [screenshot_dir]"""
import http.server
import io
import json
import os
import sys
import threading
import time
import urllib.parse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else None
DECK = ROOT / "samples/data_privacy_essentials.pptx"

COURSE = json.loads((ROOT / "samples/drain_filter_showcase.course.json").read_text(encoding="utf-8"))
COURSE["screens"][2]["photo_query"] = "technician safety gloves"          # section "Do it safely"
COURSE["screens"][10]["photo_query"] = "woman at washing machine"         # scenario
COURSE_TEXT = json.dumps(COURSE)
SEEN = {"pexels_auth": set(), "queries": []}


def jpeg(color):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (960, 640), color).save(b, "JPEG")
    return b.getvalue()


IMAGES = {"1.jpg": jpeg((40, 120, 180)), "2.jpg": jpeg((200, 120, 40)), "3.jpg": jpeg((60, 160, 90))}


class Mock(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):   # OpenAI-compatible chat completion
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send(200, json.dumps({"id": "1", "object": "chat.completion", "created": 0, "model": "m",
                                   "choices": [{"index": 0, "finish_reason": "stop",
                                                "message": {"role": "assistant", "content": COURSE_TEXT}}]}).encode())

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        port = self.server.server_address[1]
        if u.path == "/v1/search":
            key = self.headers.get("Authorization", "")
            SEEN["pexels_auth"].add(key)
            if key != "pexels-test-key":
                return self.send(401, b'{"error":"bad key"}')
            q = urllib.parse.parse_qs(u.query)["query"][0]
            SEEN["queries"].append(q)
            photos = [{"id": 100 + i, "url": f"https://www.pexels.com/photo/{100 + i}/", "photographer": f"Asha {n}",
                       "alt": f"{q} ({n})", "src": {"large": f"http://127.0.0.1:{port}/img/{n}", "medium": f"http://127.0.0.1:{port}/img/{n}"}}
                      for i, n in enumerate(IMAGES)]
            return self.send(200, json.dumps({"photos": photos}).encode())
        if u.path.startswith("/img/") and u.path[5:] in IMAGES:
            return self.send(200, IMAGES[u.path[5:]], "image/jpeg")
        self.send(404, b"{}")


def main():
    mock = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=mock.serve_forever, daemon=True).start()
    mport = mock.server_address[1]
    os.environ.update({"GEMINI_API_KEY": "server-key", "PPT2SCORM_DAILY_LIMIT": "50",
                       "PPT2SCORM_GEMINI_BASE_URL": f"http://127.0.0.1:{mport}/v1beta/openai/",
                       "PPT2SCORM_PEXELS_URL": f"http://127.0.0.1:{mport}/v1", "PPT2SCORM_ALLOW_PRIVATE_FETCH": "tests-only",
                       "PPT2SCORM_CSP_EXTRA_IMG": f"http://127.0.0.1:{mport}"})   # mock Pexels thumbnails
    for k in ("ANTHROPIC_API_KEY", "ZAI_API_KEY", "DASHSCOPE_API_KEY", "OPENROUTER_API_KEY", "PEXELS_API_KEY"):
        os.environ.pop(k, None)

    import uvicorn
    from playwright.sync_api import sync_playwright
    from web.app import app
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.05)
    base = f"http://127.0.0.1:{srv.servers[0].sockets[0].getsockname()[1]}"
    r = {}

    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append("CSP: " + m.text) if "Content Security Policy" in m.text else None)
        page.goto(base)
        page.wait_for_function("() => document.querySelectorAll('#provider option').length > 0")

        # 1. AI conversion with stock photos (key pasted on the upload page)
        page.set_input_files("#file", str(DECK))
        page.check("input[name=photos][value=pexels]")
        assert page.locator("#pexels-field").is_visible()
        page.click("#convert")
        page.wait_for_selector("#frame-note:has-text('Pexels')", timeout=3000)
        page.fill("#pexels_key", "pexels-test-key")
        page.click("#convert")
        page.wait_for_selector("#frame[data-state=done]", timeout=60000)
        jid = page.locator("#dl-zip").get_attribute("href").split("/")[3]
        course = page.request.get(f"{base}/api/jobs/{jid}/course").json()["course"]
        imgs = {i: s["image"] for i, s in enumerate(course["screens"]) if s.get("image")}
        assert set(imgs) == {2, 10}, imgs
        assert imgs[2]["credit"] == "Photo by Asha 1.jpg on Pexels" and imgs[2]["credit_url"].startswith("https://www.pexels.com/")
        assert imgs[10]["src"] != imgs[2]["src"], "the same photo was used twice"
        assert SEEN["queries"] == ["technician safety gloves", "woman at washing machine"], SEEN["queries"]
        r["photos"] = f"{len(imgs)} photos added with credits; queries {SEEN['queries']}"

        # the zip ships icons, the rich player, the photos and no photo_query
        z = zipfile.ZipFile(io.BytesIO(page.request.get(base + f"/api/jobs/{jid}/download").body()))
        names = z.namelist()
        for need in ("icons.js", "rich.js", "ICONS_LICENSE.txt", imgs[2]["src"], imgs[10]["src"]):
            assert need in names, need
        cj = z.read("course.js").decode()
        assert "photo_query" not in cj and "Photo by Asha" in cj
        r["zip"] = "icons, rich.js, licence and 2 photos; photo_query stripped"

        # 2. every layout renders in the player
        pp = b.new_page(viewport={"width": 1280, "height": 860})
        pp.on("pageerror", lambda e: errors.append(str(e)))
        pp.on("console", lambda m: errors.append("CSP: " + m.text) if "Content Security Policy" in m.text else None)
        expect = {1: ".ig-stats .ig-stat", 2: ".sec figure img", 3: ".ig-process .ig-step", 4: ".ig-compare .ig-col.is-good",
                  5: ".ig-check .ig-tick", 8: ".ig-grid .ig-card svg", 9: ".sort-chip", 12: ".ig-pyramid li",
                  13: ".ig-timeline li", 14: ".ig-cycle .ig-node"}
        for i, sel in expect.items():
            pp.goto(f"{base}/preview/{jid}/index.html?t={i}#screen={i}")
            pp.wait_for_selector(sel, timeout=5000)
            n = pp.locator(sel).count()
            assert n >= 1, (i, sel)
            if SHOTS:
                pp.wait_for_timeout(900)
                pp.screenshot(path=str(SHOTS / f"rich_{i:02d}_{course['screens'][i].get('layout') or course['screens'][i]['type']}.png"))
        r["layouts"] = "stats, section photo, process, comparison, checklist, grid, sort, pyramid, timeline, cycle"

        # stats count up to the real value
        pp.goto(f"{base}/preview/{jid}/index.html?t=s#screen=1")
        pp.wait_for_function("() => document.querySelector('.ig-value').textContent.trim() === '50%'", timeout=5000)
        # credit link on the section photo
        pp.goto(f"{base}/preview/{jid}/index.html?t=c#screen=2")
        cr = pp.locator(".credit a")
        assert "Asha" in cr.text_content() and cr.get_attribute("href").startswith("https://www.pexels.com/")

        # checklist gates Next until every item is ticked
        pp.goto(f"{base}/preview/{jid}/index.html?t=k#screen=5")
        pp.wait_for_selector(".ig-tick")
        assert pp.locator("#btn-next").is_disabled()
        ticks = pp.locator(".ig-tick")
        for k in range(ticks.count()):
            ticks.nth(k).click()
        assert ticks.nth(0).get_attribute("aria-checked") == "true"
        pp.wait_for_function("() => !document.getElementById('btn-next').disabled", timeout=3000)
        r["checklist"] = "Next unlocks after all 4 ticks"

        # cycle shows details for the chosen stage
        pp.goto(f"{base}/preview/{jid}/index.html?t=y#screen=14")
        pp.locator(".ig-node").nth(2).click()
        assert "No drain complaints" in pp.text_content(".ig-cycle-detail")

        # sort: a wrong answer is sent back, the right answer completes the screen
        pp.goto(f"{base}/preview/{jid}/index.html?t=o#screen=9")
        pp.wait_for_selector(".sort-chip")
        terms = course["screens"][9]["terms"]
        assert pp.locator("#btn-next").is_disabled()

        def place(text, bucket):
            pp.locator(".sort-pool .sort-chip", has_text=text).click()
            pp.locator(".sort-zone h3").nth(bucket).click()

        for t in terms:   # put the first one in the wrong group
            place(t["text"], 1 - t["bucket"] if t is terms[0] else t["bucket"])
        if SHOTS: pp.screenshot(path=str(SHOTS / "rich_sort_placed.png"))
        pp.click(".actions button")
        pp.wait_for_selector(".feedback.bad")
        pp.wait_for_selector(f".sort-pool .sort-chip:has-text('{terms[0]['text']}')", timeout=4000)
        place(terms[0]["text"], terms[0]["bucket"])
        pp.click(".actions button")
        pp.wait_for_selector(".feedback.ok")
        pp.wait_for_function("() => !document.getElementById('btn-next').disabled", timeout=3000)
        if SHOTS: pp.screenshot(path=str(SHOTS / "rich_sort_done.png"))
        r["sort"] = "wrong item returned to the list, then all correct"

        # mobile layout of the process infographic
        mp = b.new_page(viewport={"width": 390, "height": 800})
        mp.goto(f"{base}/preview/{jid}/index.html?t=m#screen=3")
        mp.wait_for_selector(".ig-step")
        assert mp.evaluate("document.documentElement.scrollWidth <= 391"), "horizontal scroll on mobile"
        if SHOTS: mp.wait_for_timeout(900); mp.screenshot(path=str(SHOTS / "rich_mobile_process.png"), full_page=True)
        mp.close()

        # 3. editor: edit an infographic, change its layout, edit a sort, search Pexels
        page.click("#edit")
        page.wait_for_selector("#editor:not(.is-hidden) .ed-item")
        assert "Infographic: Stats" in page.locator(".ed-item").nth(1).text_content()
        page.locator(".ed-item .ed-pick").nth(3).click()   # process
        page.wait_for_selector("#ed-f-layout")
        assert page.input_value("#ed-f-layout") == "process"
        page.locator(".ed-cell-title input").nth(0).fill("Unplug it")
        ico = page.locator(".ed-cell-icon input").nth(0)
        ico.fill("not-an-icon")
        page.wait_for_function("() => document.querySelector('.ed-row.has-error, .ed-err:not(:empty)')")
        assert page.locator("#ed-save").is_disabled()
        ico.fill("zap")
        assert page.locator(".ed-cell-icon .ed-ico-chip svg").count() >= 1
        # switching to stats requires a number on every part
        page.select_option("#ed-f-layout", "stats")
        page.wait_for_selector(".ed-cell-value input")
        assert page.locator("#ed-save").is_disabled()
        assert "required parts" in page.text_content("#ed-form")
        page.select_option("#ed-f-layout", "process")
        page.wait_for_function("() => !document.getElementById('ed-save').disabled")
        if SHOTS: page.screenshot(path=str(SHOTS / "rich_editor_infographic.png"))

        # sort: rename a group and move an item to the other group
        page.locator(".ed-item .ed-pick").nth(9).click()
        page.wait_for_selector(".ed-cell-bucket select")
        page.locator("[data-k=buckets] .ed-row input").nth(1).fill("Check the drain hose first")
        page.locator(".ed-cell-bucket select").nth(0).select_option("1")
        if SHOTS: page.screenshot(path=str(SHOTS / "rich_editor_sort.png"))

        # new infographic screen from the template
        page.select_option("#ed-newtype", "infographic")
        page.click(".ed-add button")
        page.wait_for_selector("#ed-f-layout")
        page.select_option("#ed-f-layout", "comparison")
        rows = page.locator("[data-k=parts] .ed-row")
        for k in range(3):
            rows.nth(k).locator(".ed-cell-title input").fill(f"Column {k + 1}")
        page.wait_for_function("() => document.getElementById('ed-form').textContent.includes('exactly 2')")
        page.locator("[data-k=parts] .ed-del").last.click()
        page.fill("#ed-f-title", "Before and after")
        for k, (t, txt, tone) in enumerate((("Before", "Filter blocked\nWater in drum", "bad"), ("After", "Filter clean\nDrains in seconds", "good"))):
            rows.nth(k).locator(".ed-cell-title input").fill(t)
            rows.nth(k).locator(".ed-cell-text textarea").fill(txt)
            rows.nth(k).locator(".ed-cell-tone select").select_option(tone)
        page.wait_for_function("() => !document.getElementById('ed-save').disabled")

        # Pexels search in the image picker of the scenario
        page.locator(".ed-item .ed-pick").nth(11).click()   # the new screen went in after the sort, at 10
        page.wait_for_selector("[data-k=image] .ed-img")
        assert "Photo by Asha" in page.text_content("[data-k=image]")
        page.click("text=Change image")
        page.fill("#ed-stock-q", "repair van")
        page.click("#ed-stock-go")
        page.wait_for_selector(".ed-stock-results .ed-thumb")
        if SHOTS: page.screenshot(path=str(SHOTS / "rich_editor_pexels.png"))
        page.locator(".ed-stock-results .ed-thumb").nth(2).click()
        page.wait_for_function("() => !document.querySelector('.ed-stock') && document.querySelector('[data-k=image]').textContent.includes('Asha 3.jpg')", timeout=15000)
        page.click("#ed-save")
        page.wait_for_function("() => document.getElementById('ed-status').textContent.startsWith('Saved') || document.getElementById('ed-status').textContent.startsWith('All changes')", timeout=30000)

        saved = page.request.get(f"{base}/api/jobs/{jid}/course").json()["course"]
        sc = saved["screens"]
        assert sc[3]["parts"][0]["title"] == "Unplug it" and sc[3]["parts"][0]["icon"] == "zap"
        assert sc[3]["layout"] == "process" and "value" not in sc[3]["parts"][0]
        assert sc[9]["buckets"][1] == "Check the drain hose first" and sc[9]["terms"][0]["bucket"] == 1
        assert isinstance(sc[9]["terms"][1]["bucket"], int)
        assert sc[11]["image"].get("credit") == "Photo by Asha 3.jpg on Pexels", (sc[11]["image"], errors)
        assert sc[11]["image"]["credit"] == "Photo by Asha 3.jpg on Pexels" and sc[11]["image"]["credit_url"]
        new = sc[10]
        assert new["type"] == "infographic" and new["layout"] == "comparison" and [x["tone"] for x in new["parts"]] == ["bad", "good"]
        r["editor"] = "infographic part + icon edited, layout rules enforced, sort regrouped, comparison added, Pexels photo swapped"

        # the rebuilt package plays the edits
        pp.goto(f"{base}/preview/{jid}/index.html?t=z#screen=10")
        pp.wait_for_selector(".ig-compare .ig-col.is-good")
        assert "Drains in seconds" in pp.text_content(".ig-compare")
        if SHOTS: pp.wait_for_timeout(800); pp.screenshot(path=str(SHOTS / "rich_new_comparison.png"))
        b.close()

    assert not errors, errors
    print(json.dumps(r, indent=1))
    print("ALL RICH CONTENT CHECKS PASSED")


if __name__ == "__main__":
    main()
