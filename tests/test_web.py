"""End-to-end test of the web app with a mock Gemini endpoint (no real API key needed).
Run from the project root:  python tests/test_web.py [screenshot_dir]"""
import http.server
import io
import json
import os
import sys
import threading
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else None
DECK = ROOT / "samples/data_privacy_essentials.pptx"
COURSE = (ROOT / "samples/data_privacy_essentials.course.json").read_text()


class MockGemini(http.server.BaseHTTPRequestHandler):
    """OpenAI-compatible /chat/completions that returns the sample course after a short delay."""
    def log_message(self, *a): pass

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        if self.headers.get("Authorization") == "Bearer bad-key":
            body = json.dumps({"error": {"message": "API key not valid", "code": 401}}).encode()
            self.send_response(401)
        else:
            time.sleep(2.5)
            body = json.dumps({"id": "1", "object": "chat.completion", "created": 0, "model": "m",
                               "choices": [{"index": 0, "finish_reason": "stop",
                                            "message": {"role": "assistant", "content": COURSE}}]}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1]


def main():
    mock = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockGemini)
    os.environ.update({"GEMINI_API_KEY": "server-key", "PPT2SCORM_DAILY_LIMIT": "3",
                       "PPT2SCORM_GEMINI_BASE_URL": f"http://127.0.0.1:{start(mock)}/v1beta/openai/"})
    for k in ("ANTHROPIC_API_KEY", "ZAI_API_KEY", "DASHSCOPE_API_KEY", "OPENROUTER_API_KEY"):
        os.environ.pop(k, None)

    import uvicorn
    from web.app import app
    cfg = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    srv = uvicorn.Server(cfg)
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.05)
    base = f"http://127.0.0.1:{srv.servers[0].sockets[0].getsockname()[1]}"
    results = {}

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1180, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base)
        page.wait_for_function("document.querySelectorAll('#provider option').length > 0")
        assert page.locator("#convert").is_disabled()
        if SHOTS: page.screenshot(path=str(SHOTS / "web_1_empty.png"), full_page=True)

        # 1. AI conversion through the browser
        page.set_input_files("#file", str(DECK))
        assert page.locator("#frame").get_attribute("data-state") == "file"
        page.fill("#instructions", "Audience: all employees.")
        if SHOTS: page.screenshot(path=str(SHOTS / "web_2_file.png"), full_page=True)
        page.click("#convert")
        page.wait_for_selector("#steps li.now[data-step=design]")
        if SHOTS: page.wait_for_timeout(1300); page.screenshot(path=str(SHOTS / "web_3_busy.png"))
        page.wait_for_selector("#frame[data-state=done]", timeout=30000)
        summary = page.locator("#summary").text_content()
        assert "14 screens" in summary and "gemini" in summary, summary
        page.frame_locator("#preview").locator(".intro").wait_for()
        if SHOTS: page.wait_for_timeout(600); page.screenshot(path=str(SHOTS / "web_4_done.png"), full_page=True)
        results["ai_summary"] = summary

        # the download is a valid SCORM zip
        href = page.locator("#dl-zip").get_attribute("href")
        r = page.request.get(base + href)
        assert r.ok and "SCORM12.zip" in r.headers["content-disposition"]
        z = zipfile.ZipFile(io.BytesIO(r.body()))
        assert "imsmanifest.xml" in z.namelist() and "index.html" in z.namelist()
        results["zip_files"] = sorted(z.namelist())
        assert page.request.get(base + page.locator("#dl-json").get_attribute("href")).json()["title"]

        # 2. Basic mode, then "convert another"
        page.click("#again")
        assert page.locator("#frame").get_attribute("data-state") == "empty"
        page.set_input_files("#file", str(DECK))
        page.check("input[name=mode][value=basic]")
        assert page.locator(".ai-only").first.is_hidden()
        page.click("#convert")
        page.wait_for_selector("#frame[data-state=done]", timeout=30000)
        results["basic_summary"] = page.locator("#summary").text_content()
        assert "basic (no AI)" in results["basic_summary"]

        # 3. Wrong API key shows a clear error in the frame
        page.click("#again")
        page.set_input_files("#file", str(DECK))
        page.check("input[name=mode][value=ai]")
        page.click("summary")
        page.fill("#api_key", "bad-key")
        page.click("#convert")
        page.wait_for_selector("#frame[data-state=error]", timeout=30000)
        results["bad_key_error"] = page.locator("#error-text").text_content()
        assert "rejected the API key" in results["bad_key_error"]
        if SHOTS: page.screenshot(path=str(SHOTS / "web_5_error.png"))
        page.click("#error-back")
        assert page.locator("#frame").get_attribute("data-state") == "file"

        # 4. Provider without a server key asks for the user's key (client-side check)
        page.fill("#api_key", "")
        page.select_option("#provider", "claude")
        page.click("#convert")
        results["needs_key_note"] = page.locator("#frame-note").text_content()
        assert "Paste an API key" in results["needs_key_note"]

        # 5. Server-side validation
        req = page.request
        bad = req.post(base + "/api/convert", multipart={"file": {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"hi"}})
        assert bad.status == 400, bad.status
        nokey = req.post(base + "/api/convert", multipart={"file": {"name": "d.pptx", "mimeType": "application/octet-stream",
                                                                    "buffer": DECK.read_bytes()}, "provider": "claude", "mode": "ai"})
        assert nokey.status == 400 and "No server key" in nokey.json()["detail"]
        badbrand = req.post(base + "/api/convert", multipart={"file": {"name": "d.pptx", "mimeType": "application/octet-stream",
                                                                       "buffer": DECK.read_bytes()}, "brand": "red", "mode": "basic"})
        assert badbrand.status == 400
        assert req.get(base + "/api/jobs/" + "0" * 32).status == 404
        assert req.get(base + "/preview/../../etc/passwd").status == 404
        # 6. Daily limit on the server key (limit 3): one AI run above used it; basic mode and the bad key did not
        statuses = []
        for _ in range(3):
            statuses.append(req.post(base + "/api/convert", multipart={
                "file": {"name": "d.pptx", "mimeType": "application/octet-stream", "buffer": DECK.read_bytes()},
                "mode": "ai", "provider": "gemini"}).status)
        results["daily_limit_statuses"] = statuses
        assert statuses[-1] == 429, statuses
        # corrupt pptx -> friendly error
        jid = req.post(base + "/api/convert", multipart={"file": {"name": "broken.pptx", "mimeType": "application/octet-stream",
                                                                  "buffer": b"not a pptx"}, "mode": "basic"}).json()["job_id"]
        for _ in range(50):
            j = req.get(base + f"/api/jobs/{jid}").json()
            if j["status"] != "running": break
            time.sleep(0.2)
        results["corrupt_error"] = j.get("error")
        assert j["status"] == "error" and "PowerPoint" in j["error"], j

        # 7. Mobile layout
        m = browser.new_page(viewport={"width": 390, "height": 844})
        m.goto(base)
        m.wait_for_function("document.querySelectorAll('#provider option').length > 0")
        if SHOTS: m.screenshot(path=str(SHOTS / "web_6_mobile.png"), full_page=True)
        assert m.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "horizontal scroll on mobile"
        assert not errors, errors
        browser.close()

    srv.should_exit = True
    mock.shutdown()
    print(json.dumps(results, indent=1))
    print("ALL WEB CHECKS PASSED")


if __name__ == "__main__":
    main()
