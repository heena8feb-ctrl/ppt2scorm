"""End-to-end test of the course editor in the web app (Basic mode, so no API key is needed).
Run from the project root:  python tests/test_editor.py [screenshot_dir]"""
import io
import json
import sys
import threading
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else None
DECK = ROOT / "samples/data_privacy_essentials.pptx"


def main():
    import uvicorn
    from playwright.sync_api import sync_playwright
    from web.app import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8766, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.1)
    base = "http://127.0.0.1:8766"
    r = {}

    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1480, "height": 940})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append("CSP: " + m.text) if "Content Security Policy" in m.text else None)
        page.on("dialog", lambda d: d.accept())
        page.goto(base)

        # convert in Basic mode
        page.set_input_files("#file", str(DECK))
        page.check("input[name=mode][value=basic]", force=True)
        page.click("#convert")
        page.wait_for_selector("#actions-done:not(.is-hidden)", timeout=30000)
        job = page.evaluate("document.getElementById('dl-zip').getAttribute('href').split('/')[3]")
        api = lambda: page.evaluate("id => fetch('/api/jobs/' + id + '/course').then(r => r.json())", job)
        draft = api()["course"]
        n_screens = len(draft["screens"])

        # open the editor
        page.click("#edit")
        page.wait_for_selector("#editor:not(.is-hidden) .ed-item")
        assert page.locator(".ed-item").count() == n_screens
        assert page.is_disabled("#ed-save"), "save should start disabled"
        assert page.is_disabled("#ed-revert"), "revert should start disabled"

        # edit the first quiz question: new wording, different correct answer
        qi = next(i for i, s in enumerate(draft["screens"]) if s["type"] == "mcq")
        page.locator(".ed-pick").nth(qi).click()
        page.fill("#ed-f-question", "Which slide explained the five golden rules?")
        new_correct = (draft["screens"][qi]["correct"] + 1) % len(draft["screens"][qi]["options"])
        page.locator("#ed-form input[name=ed-correct]").nth(new_correct).check()
        assert "Unsaved" in page.text_content("#ed-status")

        # validation: an empty question blocks saving and is flagged in the outline
        page.fill("#ed-f-question", "")
        assert page.is_disabled("#ed-save")
        assert "Fill this in" in page.text_content("#ed-err-question")
        assert page.locator(".ed-item.has-error").count() == 1
        if SHOTS: page.screenshot(path=str(SHOTS / "ed_2_validation.png"))
        page.fill("#ed-f-question", "Which slide explained the five golden rules?")
        assert page.is_enabled("#ed-save")

        # add a scenario right after the question, fill it in, mark the 2nd choice as best
        page.select_option("#ed-newtype", "scenario")
        page.click("text=Add screen")
        assert page.locator(".ed-item").count() == n_screens + 1
        page.fill("#ed-f-title", "Scenario: a stranger at the server room door")
        page.fill("#ed-f-situation", "Someone in a vendor uniform asks you to hold the door to the server room. What do you do?")
        rows = page.locator(".ed-field[data-k=choices] .ed-row")
        rows.nth(0).locator("input[type=text]").fill("Hold the door. They look official.")
        rows.nth(0).locator("textarea").fill("Uniforms are easy to fake.")
        rows.nth(1).locator("input[type=text]").fill("Politely ask them to badge in or check with reception.")
        rows.nth(1).locator("textarea").fill("Right. Everyone badges in, every time.")
        page.locator(".ed-field[data-k=choices] input[name=ed-best]").nth(1).check()

        # reorder: move the new scenario up one; delete the last screen
        page.locator(".ed-item[aria-current=true] button[title='Move up']").click()
        page.locator(".ed-item").last.locator("button[title=Delete]").click()

        # course settings: pass mark and brand colour
        page.click("#ed-course")
        page.fill("#ed-c-mastery_score", "70")
        page.fill("#ed-c-brand", "#aa3300")
        if SHOTS: page.screenshot(path=str(SHOTS / "ed_1_course.png"))

        # save with Ctrl+S
        page.keyboard.press("Control+s")
        page.wait_for_function("() => document.getElementById('ed-status').textContent.startsWith('Saved')", timeout=15000)
        saved = api()
        c = saved["course"]
        scen_i = qi  # moved up from qi+1
        assert c["screens"][scen_i]["type"] == "scenario", [s["type"] for s in c["screens"]]
        assert [ch["correct"] for ch in c["screens"][scen_i]["choices"]] == [False, True]
        q = c["screens"][qi + 1]
        assert q["question"] == "Which slide explained the five golden rules?" and q["correct"] == new_correct, q
        assert len(c["screens"]) == n_screens, "added one, deleted one"
        assert c["mastery_score"] == 70 and saved["brand"] == "#aa3300" and saved["edited"] is True
        assert page.is_enabled("#ed-revert")
        r["saved"] = f"{len(c['screens'])} screens, scenario at {scen_i + 1}, question at {qi + 2}, pass mark 70"

        # preview opens on the screen being edited and shows the new content
        page.locator(".ed-pick").nth(scen_i).click()
        frame = page.frame_locator("#ed-preview")
        frame.locator("text=a stranger at the server room door").wait_for(timeout=10000)
        if SHOTS: page.wait_for_timeout(600); page.screenshot(path=str(SHOTS / "ed_3_saved_preview.png"))

        # the downloaded package contains the edits
        data = page.evaluate("""id => fetch('/api/jobs/' + id + '/download').then(r => r.arrayBuffer())
                                .then(b => Array.from(new Uint8Array(b)))""", job)
        z = zipfile.ZipFile(io.BytesIO(bytes(data)))
        manifest, course_js = z.read("imsmanifest.xml").decode(), z.read("course.js").decode()
        assert "<adlcp:masteryscore>70</adlcp:masteryscore>" in manifest
        assert "server room door" in course_js and '"brand": "#aa3300"' in course_js
        r["download"] = "zip has edits, pass mark 70 in manifest, brand applied"

        # server rejects a broken course even if the browser checks are bypassed
        bad = page.evaluate("""id => fetch('/api/jobs/' + id + '/course', {method: 'PUT', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({course: {title: 'x', screens: [{type: 'mcq', question: 'q', options: ['a', 'b'], explanation: 'e'}]}})})
              .then(r => r.json().then(j => [r.status, j.detail]))""", job)
        assert bad[0] == 422 and "correct" in bad[1], bad
        bad2 = page.evaluate("""id => fetch('/api/jobs/' + id + '/course', {method: 'PUT', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({course: {title: 'x', screens: [{type: 'content', title: 'a', body: 5}]}, brand: 'red;}'})})
              .then(r => r.json().then(j => [r.status, j.detail]))""", job)
        assert bad2[0] == 422, bad2
        r["server_validation"] = bad[1]

        # revert to the AI draft
        page.click("#ed-revert")
        page.wait_for_function("() => document.getElementById('ed-status').textContent.startsWith('Back to')", timeout=15000)
        back = api()
        assert back["course"]["screens"] == draft["screens"] and back["edited"] is False
        assert page.locator(".ed-item").count() == n_screens
        r["revert"] = "ok"

        # unsaved changes prompt on Done (auto-accepted), then the main view shows the updated summary
        page.click("#ed-course")
        page.fill("#ed-c-title", "Data Privacy Essentials (edited)")
        page.click("#ed-done")
        page.wait_for_selector("#editor.is-hidden", state="attached")
        page.wait_for_selector("#actions-done:not(.is-hidden)")
        r["after_done"] = page.text_content("#summary")[:80]

        # mobile layout of the editor
        page.set_viewport_size({"width": 390, "height": 844})
        page.click("#edit")
        page.wait_for_selector("#editor:not(.is-hidden) .ed-item")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "horizontal scroll on mobile"
        if SHOTS: page.screenshot(path=str(SHOTS / "ed_4_mobile.png"))

        assert not errors, errors
        b.close()
    server.should_exit = True
    print(json.dumps(r, indent=1))
    print("ALL EDITOR CHECKS PASSED")


if __name__ == "__main__":
    main()
