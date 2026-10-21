"""Retry and model fallback when the AI provider is busy, using a mock OpenAI-compatible server.
Run from the project root:  python tests/test_retry.py"""
import http.server
import json
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
COURSE = (ROOT / "samples/data_privacy_essentials.course.json").read_text()
PLAN = {}     # model -> list of HTTP statuses to return in order (last one repeats); 200 = success
CALLS = []


class Mock(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        m = body["model"]
        CALLS.append(m)
        seq = PLAN.get(m, [404])
        status = seq.pop(0) if len(seq) > 1 else seq[0]
        if status == 200:
            out = {"id": "1", "object": "chat.completion", "created": 0, "model": m,
                   "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": COURSE}}]}
        else:
            msg = {503: "This model is currently experiencing high demand.", 401: "API key not valid",
                   404: f"models/{m} is not found"}.get(status, "error")
            out = [{"error": {"code": status, "message": msg, "status": "UNAVAILABLE" if status == 503 else "ERR"}}]
        data = json.dumps(out).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ["PPT2SCORM_GEMINI_BASE_URL"] = f"http://127.0.0.1:{srv.server_address[1]}/v1beta/openai/"
    from ppt2scorm import ai_redesign as ai
    from web.app import _friendly
    ai.RETRY_WAITS = [0, 0]
    main_m, light = ai.PROVIDERS["gemini"][2], ai.FALLBACK_MODELS["gemini"][0]
    run = lambda: ai.ai_course("deck", provider="gemini", api_key="k")
    r = {}

    def case(name, plan):
        PLAN.clear(); PLAN.update(plan); CALLS.clear()
        try:
            course, warnings = run()
            return course, warnings, None
        except Exception as e:  # noqa: BLE001
            return None, None, e

    c, w, e = case("recovers", {main_m: [503, 503, 200]})
    assert c and not e and CALLS == [main_m] * 3 and not any("busy" in x for x in w), (CALLS, w, e)
    r["recovers_on_retry"] = f"{len(CALLS)} calls, same model"

    c, w, e = case("fallback", {main_m: [503], light: [200]})
    assert c and CALLS == [main_m] * 3 + [light] and "busy" in w[0] and light in w[0], (CALLS, w, e)
    r["falls_back"] = w[0]

    c, w, e = case("bad key", {main_m: [401]})
    assert e is not None and CALLS == [main_m], CALLS
    r["bad_key_not_retried"] = _friendly(e)

    c, w, e = case("fallback renamed", {main_m: [503], light: [404]})
    assert e is not None and getattr(e, "status_code", None) == 503, e
    r["renamed_fallback_reports_busy"] = _friendly(e)

    c, w, e = case("all busy", {main_m: [503], light: [503]})
    assert e is not None and len(CALLS) == 6 and "busy" in _friendly(e), (CALLS, e)
    r["all_busy"] = f"{len(CALLS)} calls, then: " + _friendly(e)[:60] + "…"

    srv.shutdown()
    print(json.dumps(r, indent=1))
    print("ALL RETRY CHECKS PASSED")


if __name__ == "__main__":
    main()
