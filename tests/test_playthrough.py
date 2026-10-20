"""End-to-end test: plays the packaged course inside a mock LMS and checks the SCORM calls.
Run from the project root after building out/preview:  python tests/test_playthrough.py [screenshot_dir]"""
import functools
import http.server
import json
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else None
course = json.loads((ROOT / "samples/data_privacy_essentials.course.json").read_text())


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass


def serve():
    h = functools.partial(Quiet, directory=str(ROOT))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def play(f, page, wrong_mcqs, shot_prefix=""):
    """Plays every screen. wrong_mcqs = set of mcq ordinal numbers (0-based) to answer wrongly."""
    mcq_n = 0
    for i, s in enumerate(course["screens"]):
        f.locator(".screen").first.wait_for()
        t = s["type"]
        assert (f.locator("#btn-next").is_disabled()) == (t not in ("intro", "content", "summary")), f"gating wrong on {i} {t}"
        if t == "flipcards":
            for b in f.locator(".flip").all(): b.click()
        elif t == "reveal":
            for b in f.locator(".reveal-item button").all(): b.click()
        elif t == "match":
            f.locator(".chip[data-side=L][data-key='0']").click(); f.locator(".chip[data-side=R][data-key='1']").click()  # a wrong try
            for k in range(len(s["pairs"])):
                f.locator(f".chip[data-side=L][data-key='{k}']").click(); f.locator(f".chip[data-side=R][data-key='{k}']").click()
        elif t == "scenario":
            wrong = next(c for c in s["choices"] if not c["correct"])
            f.get_by_role("button", name=wrong["text"]).click()
            assert not f.locator("#btn-next").is_enabled(), "scenario should still gate after wrong answer"
            right = next(c for c in s["choices"] if c["correct"])
            f.get_by_role("button", name=right["text"]).click()
        elif t == "mcq":
            pick = s["correct"] if mcq_n not in wrong_mcqs else (s["correct"] + 1) % len(s["options"])
            f.locator(".choice").nth(pick).click(); f.get_by_role("button", name="Check answer").click()
            mcq_n += 1
        if SHOTS and shot_prefix and t not in getattr(play, "shot", set()):
            play.shot = getattr(play, "shot", set()) | {t}
            page.wait_for_timeout(600)
            page.screenshot(path=str(SHOTS / f"{shot_prefix}{i:02d}_{t}.png"))
        assert f.locator("#btn-next").is_enabled(), f"next not enabled after completing screen {i} {t}"
        f.locator("#btn-next").click()
    f.locator(".results").wait_for()


def main():
    srv = serve()
    base = f"http://127.0.0.1:{srv.server_address[1]}/tests/mock_lms.html"
    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        # Run 1: 2 wrong out of 5 -> 60% -> failed, then retry all correct -> passed
        ctx = browser.new_context(viewport={"width": 1180, "height": 820})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base)
        f = page.frame_locator("#sco")
        play(f, page, wrong_mcqs={0, 3}, shot_prefix="desk_")
        cmi = page.evaluate("cmi()")
        results["after_fail"] = {k: cmi.get(k) for k in ("cmi.core.lesson_status", "cmi.core.score.raw")}
        assert cmi["cmi.core.lesson_status"] == "failed" and cmi["cmi.core.score.raw"] == "60", cmi
        if SHOTS: page.screenshot(path=str(SHOTS / "desk_99_results_fail.png"))
        f.get_by_role("button", name="Retry the questions").click()
        for s in [s for s in course["screens"] if s["type"] == "mcq"]:
            f.locator(".choice").nth(s["correct"]).click(); f.get_by_role("button", name="Check answer").click()
            f.locator("#btn-next").click()
        f.locator(".results").wait_for()
        if SHOTS: page.wait_for_timeout(500); page.screenshot(path=str(SHOTS / "desk_99_results_pass.png"))
        cmi = page.evaluate("cmi()")
        results["after_retry"] = {k: cmi.get(k) for k in ("cmi.core.lesson_status", "cmi.core.score.raw")}
        assert cmi["cmi.core.lesson_status"] == "passed" and cmi["cmi.core.score.raw"] == "100", cmi
        log = page.evaluate("scormLog")
        assert log[0][0] == "LMSInitialize"
        assert len(cmi["cmi.suspend_data"]) < 4096
        results["suspend_data_chars"] = len(cmi["cmi.suspend_data"])
        page.goto("about:blank")  # unload -> LMSFinish
        assert not errors, errors

        # Run 2: resume - leave midway, reopen, should land on same screen
        ctx2 = browser.new_context(viewport={"width": 390, "height": 844})
        page = ctx2.new_page()
        page.goto(base)
        f = page.frame_locator("#sco")
        for _ in range(2):
            f.locator("#btn-next").click()
        f.locator(".flip").first.wait_for()
        if SHOTS: page.screenshot(path=str(SHOTS / "mobile_flipcards.png"))
        page.reload()
        f = page.frame_locator("#sco")
        f.locator(".screen").wait_for()
        resumed_title = f.locator("h2").first.text_content()
        cmi = page.evaluate("cmi()")
        results["resume"] = {"lesson_location": cmi.get("cmi.core.lesson_location"), "resumed_on": resumed_title,
                             "exit": cmi.get("cmi.core.exit"), "status": cmi.get("cmi.core.lesson_status")}
        assert resumed_title == course["screens"][2]["title"], resumed_title
        # Preview mode (no LMS) must also work
        page.goto(base.replace("tests/mock_lms.html", "out/preview/index.html"))
        page.locator(".intro").wait_for()
        results["standalone_preview"] = "ok"
        browser.close()
    srv.shutdown()
    print(json.dumps(results, indent=1))
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
