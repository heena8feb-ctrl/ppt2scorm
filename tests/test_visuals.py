"""End-to-end test of slide visuals: extraction, AI and Basic image attachment, player display, editor picker/upload.
Run from the project root:  python tests/test_visuals.py [screenshot_dir]
Chart and diagram rendering is checked only when LibreOffice is installed."""
import io
import json
import os
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else None
os.environ.setdefault("GEMINI_API_KEY", "server-test-key")  # lets the web app run AI mode with the mocked provider


def build_deck(tmp):
    sys.path.insert(0, str(ROOT / "samples"))
    from make_visual_deck import main as make
    path = Path(tmp) / "visuals_test.pptx"
    make(str(path))
    return path


def check_extraction(deck_path, tmp):
    from ppt2scorm.extract import extract, find_soffice
    d = extract(deck_path, Path(tmp) / "media")
    by_slide = {s["index"]: s["images"] for s in d["slides"]}
    srcs = [im["src"] for s in d["slides"] for im in s["images"]]
    assert by_slide[1] == [], "logo on every slide must be skipped"
    assert len(by_slide[2]) == 1 and by_slide[2][0]["src"].endswith(".jpg"), "big photo kept as JPEG, icon skipped"
    assert max(by_slide[2][0]["w"], by_slide[2][0]["h"]) <= 1600, "photo resized"
    assert len(by_slide[3]) == 1, "picture inside a group found"
    assert by_slide[6][0]["src"] == by_slide[3][0]["src"], "same picture stored once"
    assert all(im["alt"] == "" for im in by_slide[2] + by_slide[3]), "file-name alt text ignored"
    if find_soffice():
        assert by_slide[4] and by_slide[4][0]["kind"] == "slide" and "chart" in by_slide[4][0]["reason"]
        assert by_slide[5] and by_slide[5][0]["kind"] == "slide" and "shapes" in by_slide[5][0]["reason"]
        assert not d["warnings"], d["warnings"]
    else:
        assert any("LibreOffice" in w for w in d["warnings"]), d["warnings"]
    return {"images": len(set(srcs)), "rendered": find_soffice() is not None}


def main():
    import uvicorn
    from playwright.sync_api import sync_playwright
    from ppt2scorm import pipeline

    r = {}
    tmp = tempfile.mkdtemp()
    deck_path = build_deck(tmp)
    r["extraction"] = check_extraction(deck_path, tmp)

    # mocked AI: uses one real image, one path without the media/ prefix, and one invented path
    seen_prompt = {}

    def fake_ai(deck_text, **kw):
        seen_prompt["text"] = deck_text
        lines = [l for l in deck_text.splitlines() if l.startswith("IMAGE: ")]
        real = [l.split()[1] for l in lines]
        chart = next((p for p, l in zip(real, lines) if "chart" in l), real[0])
        course = {"title": "Drain Filter Service", "description": "d", "est_minutes": 8, "mastery_score": 80, "screens": [
            {"type": "intro", "title": "Drain Filter Service", "objectives": ["Find the filter", "Clean it safely"]},
            {"type": "content", "title": "Why it matters", "body": "Half of drain complaints are a blocked filter.",
             "image": {"src": chart, "alt": "Bar chart: blocked filters cause half of complaints"}},
            {"type": "scenario", "title": "On site", "situation": "Water is left in the drum.", "choices": [
                {"text": "Check the filter first", "correct": True, "feedback": "Yes."},
                {"text": "Replace the pump", "correct": False, "feedback": "Too early."}],
             "image": {"src": real[0].replace("media/", ""), "alt": "The machine"}},
            {"type": "mcq", "question": "What usually blocks the filter?", "options": ["Lint and coins", "Soap"], "correct": 0,
             "explanation": "Lint, coins and pins.", "scored": True, "image": {"src": "media/made_up.png", "alt": "x"}}]}
        from ppt2scorm.schema import validate
        return course, validate(course)

    pipeline.ai_course = fake_ai
    from web.app import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8767, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.1)
    base = "http://127.0.0.1:8767"

    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1480, "height": 940})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.goto(base)

        def convert(mode):
            page.set_input_files("#file", str(deck_path))
            page.check(f"input[name=mode][value={mode}]", force=True)
            page.click("#convert")
            page.wait_for_selector("#actions-done:not(.is-hidden), .frame[data-state=error]", timeout=90000)
            if page.get_attribute("#frame", "data-state") == "error":
                raise AssertionError(f"{mode} conversion failed: " + page.text_content("#error-text"))
            return page.evaluate("document.getElementById('dl-zip').getAttribute('href').split('/')[3]")

        def zip_names(job):
            data = page.evaluate("""id => fetch('/api/jobs/' + id + '/download').then(r => r.arrayBuffer())
                                    .then(b => Array.from(new Uint8Array(b)))""", job)
            z = zipfile.ZipFile(io.BytesIO(bytes(data)))
            return z, sorted(n for n in z.namelist() if n.startswith("media/"))

        api = lambda path, job: page.evaluate("([p, id]) => fetch('/api/jobs/' + id + p).then(r => r.json())", [path, job])

        # --- Basic mode: every slide visual attached, nothing decorative in the zip ---
        job = convert("basic")
        course = api("/course", job)["course"]
        attached = sorted({s["image"]["src"] for s in course["screens"] if s.get("image")})
        _, media = zip_names(job)
        assert media == attached, (media, attached)
        r["basic"] = f"{len(attached)} images attached, zip media = exactly those"
        page.click("#again")

        # --- AI mode (mocked): prompt lists images; bad references are dropped with a warning ---
        job = convert("ai")
        assert "IMAGE: media/" in seen_prompt["text"]
        course = api("/course", job)["course"]
        imgs = {s["type"]: s.get("image") for s in course["screens"]}
        assert imgs["content"]["src"].startswith("media/") and imgs["scenario"]["src"].startswith("media/"), imgs
        assert imgs["mcq"] is None, "invented image path must be removed"
        status = page.evaluate("id => fetch('/api/jobs/' + id).then(r => r.json())", job)
        assert any("made_up.png" in w for w in status["warnings"]), status["warnings"]
        r["ai"] = "prompt lists images; prefix fixed; invented path removed with a warning"

        # --- player shows the image and zooms ---
        frame = page.frame_locator("#preview")
        frame.locator("#btn-next").click()
        frame.locator(".fig img").wait_for()
        assert frame.locator(".fig img").evaluate("i => i.complete && i.naturalWidth > 0")
        frame.locator(".zoom").click()
        frame.locator("dialog.zoombox[open]").wait_for()
        frame.locator(".zoom-close").click()
        r["player"] = "image loads, zoom opens and closes"

        # --- editor: add an image from the deck, require a description, upload one, remove one ---
        page.click("#edit")
        page.wait_for_selector("#editor:not(.is-hidden) .ed-item")
        qi = [s["type"] for s in course["screens"]].index("mcq")
        page.locator(".ed-pick").nth(qi).click()
        page.click("text=+ Add an image")
        n_media = len(api("/media", job)["images"])
        assert page.locator(".ed-thumb").count() == n_media
        if SHOTS: page.screenshot(path=str(SHOTS / "vis_1_picker.png"))
        page.locator(".ed-thumb").first.click()
        page.fill("#ed-f-image-alt", "")
        assert page.is_disabled("#ed-save") and "Describe the image" in page.text_content("#ed-err-image")
        page.fill("#ed-f-image-alt", "Close-up of the drain filter")

        # upload a new image onto the content screen (replacing the chart)
        from PIL import Image
        up = Path(tmp) / "my photo.png"
        Image.new("RGB", (800, 500), (200, 120, 40)).save(up)
        page.locator(".ed-pick").nth(1).click()
        page.click("text=Change image")
        page.set_input_files("#ed-upload", str(up))
        page.wait_for_selector(".ed-img img[src*='up_']")
        page.fill("#ed-f-image-alt", "An orange test photo")
        # remove the scenario's image
        page.locator(".ed-pick").nth([s["type"] for s in course["screens"]].index("scenario")).click()
        page.click("text=Remove image")
        page.keyboard.press("Control+s")
        page.wait_for_function("document.getElementById('ed-status').textContent.startsWith('Saved')", timeout=15000)
        c2 = api("/course", job)["course"]
        by = {s["type"]: s.get("image") for s in c2["screens"]}
        assert by["mcq"]["alt"] == "Close-up of the drain filter" and "up_" in by["content"]["src"] and by["scenario"] is None, by
        _, media = zip_names(job)
        assert media == sorted({by["mcq"]["src"], by["content"]["src"]}), media
        page.locator(".ed-pick").nth(1).click()
        page.frame_locator("#ed-preview").locator(".fig img").wait_for()
        if SHOTS: page.wait_for_timeout(600); page.screenshot(path=str(SHOTS / "vis_2_editor_saved.png"))
        r["editor"] = "picker, required description, upload, remove; zip has only used images"

        # --- server-side protection ---
        put = lambda body: page.evaluate("""([id, body]) => fetch('/api/jobs/' + id + '/course', {method: 'PUT',
              headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}).then(r => r.status)""", [job, body])
        bad = json.loads(json.dumps(c2))
        bad["screens"][1]["image"] = {"src": "media/../../etc/passwd", "alt": "x"}
        assert put({"course": bad}) == 422
        gone = json.loads(json.dumps(c2))
        gone["screens"][1]["image"] = {"src": "media/not_here.png", "alt": "x"}
        assert put({"course": gone}) == 200 and "image" not in api("/course", job)["course"]["screens"][1]
        fake = Path(tmp) / "notes.png"
        fake.write_text("not an image")
        st = page.evaluate("""async ([id]) => { const fd = new FormData();
              fd.append('file', new File(['not an image'], 'notes.png', {type: 'image/png'}));
              return (await fetch('/api/jobs/' + id + '/media', {method: 'POST', body: fd})).status; }""", [job])
        assert st == 400, st
        r["server"] = "traversal rejected, missing file dropped, fake image upload rejected"

        assert not errors, errors
        b.close()
    server.should_exit = True
    print(json.dumps(r, indent=1))
    print("ALL VISUALS CHECKS PASSED")


if __name__ == "__main__":
    main()
