"""End-to-end test of narration with a mock Gemini text-to-speech server (no real API key needed).
Run from the project root:  python tests/test_narration.py [screenshot_dir]"""
import base64
import io
import json
import math
import os
import struct
import sys
import tempfile
import threading
import time
import wave
import zipfile
import http.server
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else None
DECK = ROOT / "samples/data_privacy_essentials.pptx"
os.environ["GEMINI_API_KEY"] = "server-test-key"   # the server has a key, so AI voice works without pasting one
TTS = {"calls": [], "fail": None}


def wav(text):
    rate, n = 24000, int(24000 * min(4.0, 0.8 + len(text) / 60))
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(4000 * math.sin(i / 9))) for i in range(n)))
    return b.getvalue()


class MockTTS(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        text = body["input"][0]["content"][0]["text"]
        TTS["calls"].append((body["model"], body["generation_config"]["speech_config"][0]["voice"], text,
                             self.headers.get("x-goog-api-key")))
        if TTS["fail"]:
            out, code = json.dumps({"error": {"code": TTS["fail"], "message": "API key not valid"}}).encode(), TTS["fail"]
        else:
            out = json.dumps({"id": "i", "steps": [{"type": "model_output", "content": [
                {"type": "audio", "mime_type": "audio/wav", "data": base64.b64encode(wav(text)).decode()}]}]}).encode()
            code = 200
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def main():
    tts = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockTTS)
    threading.Thread(target=tts.serve_forever, daemon=True).start()
    from ppt2scorm import narration
    narration.TTS_URL = f"http://127.0.0.1:{tts.server_address[1]}/v1beta/interactions"
    narration.TTS_WAITS = [0, 0, 0]

    import uvicorn
    from playwright.sync_api import sync_playwright
    from web.app import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8768, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.1)
    base = "http://127.0.0.1:8768"
    r = {}

    with sync_playwright() as p:
        b = p.chromium.launch(args=["--autoplay-policy=user-gesture-required"])
        page = b.new_page(viewport={"width": 1480, "height": 940})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append("CSP: " + m.text) if "Content Security Policy" in m.text else None)
        page.on("dialog", lambda d: d.accept())
        page.goto(base)
        page.wait_for_selector("#voice option", state="attached")

        def convert(narr, voice=None):
            page.set_input_files("#file", str(DECK))
            page.check("input[name=mode][value=basic]", force=True)
            page.check(f"input[name=narration][value={narr}]", force=True)
            if voice:
                page.select_option("#voice", voice)
            page.click("#convert")
            if narr == "audio":
                page.wait_for_selector("#step-narrate.now, #actions-done:not(.is-hidden)", timeout=60000)
            page.wait_for_selector("#actions-done:not(.is-hidden), .frame[data-state=error]", timeout=90000)
            if page.get_attribute("#frame", "data-state") == "error":
                raise AssertionError("conversion failed: " + page.text_content("#error-text"))
            return page.evaluate("document.getElementById('dl-zip').getAttribute('href').split('/')[3]")

        get = lambda path: page.evaluate("p => fetch(p).then(r => r.json())", path)

        def zip_of(job):
            data = page.evaluate("""id => fetch('/api/jobs/' + id + '/download').then(r => r.arrayBuffer())
                                    .then(b => Array.from(new Uint8Array(b)))""", job)
            return zipfile.ZipFile(io.BytesIO(bytes(data)))

        # --- 1. Basic + AI voice: every screen narrated, recorded once, packaged ---
        assert page.is_visible("#voice-field") is False
        page.check("input[name=narration][value=audio]", force=True)
        assert page.is_visible("#voice-field")
        if SHOTS: page.screenshot(path=str(SHOTS / "nar_1_settings.png"))
        job = convert("audio", "Sulafat")
        course = get(f"/api/jobs/{job}/course")["course"]
        screens = course["screens"]
        assert course["voiceover"] == {"mode": "audio", "voice": "Sulafat"}, course["voiceover"]
        assert all(s.get("narration") for s in screens), "every screen gets a script"
        assert all(s.get("audio", {}).get("src", "").endswith(".mp3") for s in screens), "MP3 for every screen"
        assert {c[1] for c in TTS["calls"]} == {"Sulafat"} and {c[3] for c in TTS["calls"]} == {"server-test-key"}
        z = zip_of(job)
        mp3 = [n for n in z.namelist() if n.startswith("media/narr_")]
        assert len(mp3) == len({s["audio"]["src"] for s in screens}) and all(f'"{n}"' in z.read("imsmanifest.xml").decode().replace("href=", "")
                                                                            for n in mp3[:1])
        assert "voice.js" in z.namelist()
        notes = next(s for s in screens if s["type"] == "content" or s["type"] == "reveal")
        r["convert"] = f"{len(screens)} screens narrated, {len(TTS['calls'])} TTS calls, {len(mp3)} MP3s in the zip ({sum(z.getinfo(n).file_size for n in mp3)//1024} KB)"

        # --- 2. Player: no sound before a click, auto-plays after Next, pause, captions ---
        frame = page.frame_locator("#preview")
        frame.locator(".vo").wait_for()
        assert frame.locator(".vo-play").get_attribute("aria-pressed") == "false"
        frame.locator("#btn-next").click()
        frame.locator(".vo.is-playing").wait_for(timeout=5000)
        frame.locator(".vo-toggle", has_text="Captions").click()
        cap = frame.locator(".vo-captions").text_content()
        assert cap == screens[1]["narration"], (cap, screens[1]["narration"])
        if SHOTS: page.wait_for_timeout(700); page.screenshot(path=str(SHOTS / "nar_2_player.png"))
        frame.locator(".vo-play").click()
        frame.locator(".vo-play[aria-pressed=false]").wait_for(timeout=2000)
        frame.locator(".vo-toggle", has_text="Auto-play").click()
        for btn in frame.locator(".reveal-item button, .flip").all():   # finish the activity so Next unlocks
            btn.click()
        frame.locator("#btn-next").click()
        page.wait_for_timeout(600)
        assert frame.locator(".vo-play").get_attribute("aria-pressed") == "false", "auto-play off is respected"
        r["player"] = "silent until a click, auto-plays after Next, pause, captions match script, auto-play off respected"

        # --- 3. Editor: edit one script -> saved, then re-recorded automatically in the voice chosen at upload ---
        page.click("#edit")
        page.wait_for_selector("#editor:not(.is-hidden) .ed-item")
        page.locator(".ed-pick").nth(1).click()
        page.fill("#ed-f-narration", "Here's why privacy matters to every one of us.")
        assert "recorded in Sulafat" in page.text_content(".ed-narr-status"), page.text_content(".ed-narr-status")
        before = len(TTS["calls"])
        page.click("#ed-save")
        page.wait_for_function("() => document.getElementById('ed-status').textContent.startsWith('Audio recorded')", timeout=30000)
        assert "in Sulafat" in page.text_content("#ed-status"), page.text_content("#ed-status")
        new_calls = TTS["calls"][before:]
        assert len(new_calls) == 1 and new_calls[0][1] == "Sulafat" and new_calls[0][2].startswith("Here's why"), new_calls
        c2 = get(f"/api/jobs/{job}/course")
        assert c2["needs_audio"] == 0 and c2["course"]["screens"][1]["audio"]["src"] != screens[1]["audio"]["src"]
        page.locator(".ed-pick").nth(1).click()
        assert page.locator(".ed-narr-status audio").count() == 1
        if SHOTS: page.screenshot(path=str(SHOTS / "nar_3_editor.png"))
        r["re_record"] = "edited script re-recorded automatically on save, in Sulafat (the voice chosen at upload); others reused"

        # --- 4. Change voice -> everything re-recorded in the new voice; device mode packages no MP3s ---
        page.click("#ed-course")
        page.select_option("#ed-c-voice", "Kore")
        before = len(TTS["calls"])
        page.click("#ed-save")
        page.wait_for_function("() => document.getElementById('ed-status').textContent.startsWith('Audio recorded')", timeout=60000)
        voices = {c[1] for c in TTS["calls"][before:]}
        assert voices == {"Kore"} and len(TTS["calls"][before:]) == len(screens), (voices, len(TTS["calls"][before:]))
        assert get(f"/api/jobs/{job}/course")["needs_audio"] == 0
        page.select_option("#ed-c-vomode", "device")
        page.click("#ed-save")
        page.wait_for_function("() => document.getElementById('ed-status').textContent.startsWith('Saved')", timeout=15000)
        z = zip_of(job)
        assert not [n for n in z.namelist() if n.startswith("media/narr_")], "device mode ships no recordings"
        assert '"mode": "device"' in z.read("course.js").decode()
        r["voice_and_device"] = "voice change re-records every screen in Kore; device mode ships no MP3s"
        page.click("#ed-done")

        # --- 4b. No server key: the key pasted on the upload page is reused for re-recording (never stored) ---
        os.environ.pop("GEMINI_API_KEY")
        page.click("#again")
        page.check("input[name=mode][value=ai]", force=True)
        page.evaluate("document.querySelector('details.adv').open = true")
        page.select_option("#provider", "gemini")
        page.fill("#api_key", "users-own-key")
        page.check("input[name=mode][value=basic]", force=True)
        jobk = convert("audio", "Charon")
        page.click("#edit")
        page.wait_for_selector("#editor:not(.is-hidden) .ed-item")
        page.locator(".ed-pick").nth(2).click()
        page.fill("#ed-f-narration", "A fresh line for screen three.")
        before = len(TTS["calls"])
        page.click("#ed-save")
        page.wait_for_function("() => document.getElementById('ed-status').textContent.startsWith('Audio recorded')", timeout=30000)
        last = TTS["calls"][before:]
        assert len(last) == 1 and last[0][1] == "Charon" and last[0][3] == "users-own-key", last
        page.click("#ed-done")
        os.environ["GEMINI_API_KEY"] = "server-test-key"
        r["own_key"] = "re-recorded with the key pasted at upload, in Charon"

        # --- 4c. Player never swaps in a different voice for a screen without audio ---
        import copy, json as _j
        from ppt2scorm.package import build_package
        jc = get(f"/api/jobs/{jobk}/course")["course"]
        cc = copy.deepcopy(jc)
        cc["screens"][1].pop("audio")
        tmpd = Path(tempfile.mkdtemp())
        from web.app import JOBS_DIR
        build_package(cc, tmpd / "c.zip", media_dir=JOBS_DIR / jobk / "media", keep_dir=tmpd / "pkg")
        pp = b.new_page()
        pp.add_init_script("window.__spoken = 0; const o = window.speechSynthesis && speechSynthesis.speak.bind(speechSynthesis);"
                           "if (o) speechSynthesis.speak = (u) => { window.__spoken++; };")
        pp.goto((tmpd / "pkg" / "index.html").as_uri())
        pp.wait_for_selector(".vo")
        pp.click("#btn-next")
        pp.wait_for_timeout(500)
        assert pp.text_content(".vo-play .vo-label") == "No audio for this screen" and pp.is_disabled(".vo-play")
        assert pp.evaluate("window.__spoken") == 0, "device voice must not stand in for a missing recording"
        pp.close()
        r["no_voice_mixing"] = "screen without audio shows 'No audio for this screen'; no device voice"

        # --- 5. Key rejected by the voice service: course still made, falls back to device voice ---
        TTS["fail"] = 401
        page.click("#again")
        job2 = convert("audio")
        st = get(f"/api/jobs/{job2}")
        assert st["voiceover"] == "device" and any("rejected" in w for w in st["warnings"]), st["warnings"]
        TTS["fail"] = None
        r["key_rejected"] = st["warnings"][-1][:90]

        # --- 6. Device voice option ---
        page.click("#again")
        job3 = convert("device")
        assert get(f"/api/jobs/{job3}/course")["course"]["voiceover"]["mode"] == "device"
        page.frame_locator("#preview").locator(".vo").wait_for()

        assert not errors, errors
        b.close()
    server.should_exit = True
    tts.shutdown()
    print(json.dumps(r, indent=1))
    print("ALL NARRATION CHECKS PASSED")


if __name__ == "__main__":
    main()
