"""Step 2b - Redesign extracted slides into an engaging course using an LLM.

Providers: Claude (Anthropic SDK) or any OpenAI-compatible API (Gemini, OpenRouter, Z.ai GLM, Qwen).
Without a key, `heuristic_course` builds a basic (less engaging) course so the pipeline still runs.
"""
import json
import os
import random
import re

from .icons import icon_for
from .schema import SCREEN_FIELDS, CourseError, validate

# name: (API key env var, base_url for OpenAI-compatible APIs or None for Anthropic, default model)
PROVIDERS = {
    "gemini": ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-3.8-flash"),
    "claude": ("ANTHROPIC_API_KEY", None, "claude-sonnet-5-5"),
    "openrouter": ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1", None),  # pass --model, e.g. a ':free' model ID
    "glm": ("ZAI_API_KEY", "https://api.z.ai/api/paas/v4/", "glm-4.7-flash"),
    "qwen": ("DASHSCOPE_API_KEY", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
}


# Lighter models to try when the default one is overloaded (each has its own free quota on Gemini).
FALLBACK_MODELS = {"gemini": ["gemini-3.5-flash-lite"]}
TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504, 529}
RETRY_WAITS = [3, 8]  # seconds between attempts on the same model


def _is_transient(err):
    """Overloaded, rate limited or a network blip: worth retrying. Bad keys or unknown models are not."""
    status = getattr(err, "status_code", None) or getattr(getattr(err, "response", None), "status_code", None)
    if status in TRANSIENT_STATUS:
        return True
    name = err.__class__.__name__
    return name in ("APIConnectionError", "APITimeoutError", "InternalServerError", "RateLimitError", "OverloadedError")


def detect_provider():
    """First provider whose API key is set in the environment, or None."""
    for name, (env, _, _) in PROVIDERS.items():
        if os.environ.get(env):
            return name
    return None


def _make_caller(provider, model, api_key=None, system=None):
    env, base_url, default_model = PROVIDERS[provider]
    # Optional override, e.g. PPT2SCORM_GEMINI_BASE_URL, for proxies or tests
    if base_url is not None:
        base_url = os.environ.get(f"PPT2SCORM_{provider.upper()}_BASE_URL", base_url)
    key = api_key or os.environ.get(env)
    if not key:
        raise CourseError(f"{env} is not set (needed for provider '{provider}')")
    model = model or default_model
    if not model:
        raise CourseError(f"provider '{provider}' needs --model")
    # The SDKs' own retries are switched off so this module controls waiting and model fallback.
    if base_url is None:
        import anthropic
        client = anthropic.Anthropic(api_key=key, max_retries=0)

        def call_once(messages, m):
            r = client.messages.create(model=m, max_tokens=16000, system=system or SYSTEM_PROMPT, messages=messages)
            return "".join(b.text for b in r.content if b.type == "text")
    else:
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url=base_url, max_retries=0)

        def call_once(messages, m):
            r = client.chat.completions.create(model=m, max_tokens=16000,
                                               messages=[{"role": "system", "content": system or SYSTEM_PROMPT}] + messages)
            return r.choices[0].message.content or ""

    import time
    chain = [model] + [f for f in FALLBACK_MODELS.get(provider, []) if f != model]
    state = {"model": model}

    def call(messages):
        """Retry busy/rate-limited calls, then move to a lighter model. Remembers the model that worked."""
        first_busy = None
        for m in chain[chain.index(state["model"]):]:
            for attempt in range(len(RETRY_WAITS) + 1):
                try:
                    text = call_once(messages, m)
                    state["model"] = m
                    return text
                except Exception as e:  # noqa: BLE001 - classified below
                    if not _is_transient(e):
                        if first_busy is not None:   # a fallback model failed for another reason (e.g. renamed):
                            raise first_busy          # report the original "busy" problem, which is the real one
                        raise
                    first_busy = first_busy or e
                    if attempt < len(RETRY_WAITS):
                        time.sleep(RETRY_WAITS[attempt])
        raise first_busy

    call.state = state
    return call, model

from .icons import NAMES as _ICON_NAMES

SYSTEM_PROMPT = """You are the lead of a world-class L&D studio that builds award-winning e-learning for top
enterprises. You turn a slide deck into a short course that people actually want to finish: visual,
story-driven, practical and memorable. You never invent facts the deck doesn't support; you may restructure,
rephrase, dramatise as realistic scenarios, and turn content into interactions and infographics.

HOW GREAT COURSES ARE BUILT
1. Hook first. Open with an `intro` (title, a one-line promise of what the learner will be able to DO,
   3-4 measurable objectives starting with action verbs). Then, before any theory, a short `scenario`
   or striking `infographic` (stats layout) that shows why this matters to THEM, using the deck's facts.
2. Chunk into 2-4 modules. Start each with a `section` screen (title, one-line subtitle, icon).
3. Show, don't tell. Every screen must be visual or interactive. A `content` screen is allowed only for a
   single key idea, must have an icon and ideally an image or photo, and at most 2 in a row is never OK.
4. Pick the right visual for the content (this is the most important rule):
   - steps or a procedure -> infographic "process" (3-6 parts, each with an icon)
   - history, phases or a sequence over time -> infographic "timeline" (value = the date/phase label)
   - numbers, percentages, costs, durations -> infographic "stats" (value = the number, e.g. "50%", "3x", "72 hrs")
   - do vs don't, right vs wrong, before vs after -> infographic "comparison" (exactly 2 parts; text = one
     point per line; tone "good" for the right column, "bad" for the wrong one)
   - a repeating loop (plan-do-check-act, lifecycle) -> infographic "cycle" (3-6 parts)
   - levels, priorities, a hierarchy -> infographic "pyramid" (top = most important/smallest, 3-5 parts)
   - a set of related concepts, principles or types -> infographic "grid" (3-6 parts, each with an icon)
   - things to remember to do on the job -> infographic "checklist" (learners tick each item)
   - terms and meanings -> `match`; recall/self-test -> `flipcards`; deeper detail -> `reveal`
   - classifying (safe vs risky, personal data vs not) -> `sort` (2-3 buckets, 6-10 short items)
   - judgement in a realistic situation -> `scenario` with named people, a place and a consequence
5. Variety. Never use the same screen type or infographic layout twice in a row. A 15-25 screen course
   should use at least 3 different infographic layouts and at least 3 different interaction types.
6. Spaced practice. End each module with one practice `mcq` ("scored": false). Finish the course with a
   `summary` (the 4-6 things to remember), then a final assessment of 4-6 `mcq` with "scored": true.
7. Real-world, respectful tone: short sentences, active voice, "you", concrete workplace examples.
   Body text <= 50 words. Infographic part text <= 18 words. Titles are specific ("Spot a phishing email
   in 5 seconds"), never generic ("Overview").
8. Questions test application, not recall: 3-4 plausible options, one correct, an explanation that teaches,
   the correct position varied; no "all of the above".
9. Use speaker notes: they usually hold the real teaching point and the answers to scenarios.

ICONS
Give `section`, `content`, `scenario`, `flipcards`, `reveal` and `summary` screens an "icon", and every
infographic part an "icon" (except stats and timeline, where it's optional). Use ONLY names from this list:
""" + " ".join(_ICON_NAMES) + """

IMAGES AND PHOTOS
- Slides may list IMAGE lines (photos, diagrams, charts, or whole slides rendered as images). Attach the
  ones that help learning with "image": {"src": "<the exact media/... path>", "alt": "<what it shows>"}.
  Only use listed paths; use each at most once. A chart or diagram belongs on the screen that teaches it.
- For about a third of the screens (intro, sections, content, scenarios), add "photo_query": a 3-6 word
  stock-photo search that shows the situation concretely, e.g. "technician replacing water filter kitchen",
  "office worker reading suspicious email". People at work, real settings, no text in the photo. Don't add
  a photo_query to a screen that already has an "image", nor to infographic, sort or match screens.
- Alt text describes what the image shows in one sentence. Don't start with "Image of".

NARRATION
- Give EVERY screen a "narration": what a warm, expert trainer would say, 1-3 short sentences (<= 45 words),
  written to be spoken. Add context or the reason why; guide the learner ("Tick each item as you go.").
- On scenario, sort and mcq screens, set up the task but never hint at the answer or read the options.
- Plain words, no abbreviations a voice would stumble on, no emoji, no stage directions.

Output ONLY a JSON object, no markdown fences, matching this schema:
{
 "title": str, "description": str (1 sentence), "est_minutes": int, "mastery_score": 80,
 "screens": [
  {"type":"intro","title":str,"subtitle":str,"objectives":[str]},
  {"type":"section","title":str,"subtitle":str,"icon":str},
  {"type":"content","title":str,"body":str,"bullets":[str],"callout":str,"icon":str},
  {"type":"infographic","title":str,"intro":str,"layout":"process|timeline|stats|comparison|cycle|pyramid|grid|checklist",
   "parts":[{"title":str,"text":str,"icon":str,"value":str,"tone":"good|bad"}]},
  {"type":"flipcards","title":str,"instruction":str,"cards":[{"front":str,"back":str}]},
  {"type":"reveal","title":str,"instruction":str,"items":[{"label":str,"detail":str}]},
  {"type":"match","title":str,"instruction":str,"pairs":[{"left":str,"right":str}]},
  {"type":"sort","title":str,"instruction":str,"buckets":[str],"terms":[{"text":str,"bucket":int}]},
  {"type":"scenario","title":str,"situation":str,"choices":[{"text":str,"correct":bool,"feedback":str}]},
  {"type":"summary","title":str,"points":[str]},
  {"type":"mcq","question":str,"options":[str],"correct":int,"explanation":str,"scored":true}
 ]
}
("narration" goes on every screen. "icon", "image" and "photo_query" are optional where described above.
In infographic parts, "value" is for stats and timeline, "tone" only for comparison; omit unused keys.)"""


def _parse_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def ai_course(deck_text, model=None, extra_instructions="", max_retries=2, provider="claude", api_key=None):
    """Calls the LLM, validates the JSON, and asks the model to fix it if validation fails.
    api_key overrides the provider's environment variable (used by the web app's bring-your-own-key)."""
    call, model = _make_caller(provider, model, api_key)
    user = f"Convert this deck into a course.\n{extra_instructions}\n\n<deck>\n{deck_text}\n</deck>"
    messages = [{"role": "user", "content": user}]
    last_err = None
    for _ in range(max_retries + 1):
        text = call(messages)
        try:
            course = _parse_json(text)
            warnings = validate(course)
            used = getattr(call, "state", {}).get("model", model)
            if used != model:
                warnings.insert(0, f"{model} was busy, so {used} wrote this course. Review it closely.")
            return course, warnings
        except (ValueError, CourseError) as e:  # json errors are ValueErrors
            last_err = e
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": f"That JSON failed validation: {e}. Return the corrected full JSON only."}]
    raise CourseError(f"AI output invalid after retries: {last_err}")


NUMBER = re.compile(r"(\d[\d,.]*\s?(%|x|×|hrs?|hours?|mins?|minutes?|days?|weeks?|months?|years?|crore|lakh|k|m)?)", re.I)
STEP_WORDS = re.compile(r"(?i)\b(steps?|process|procedure|how to|stages?|workflow|method|rules?)\b")
VERB_START = re.compile(r"(?i)^(check|verify|unplug|switch|turn|open|close|remove|clean|refit|test|report|collect|use|"
                        r"share|store|delete|ask|call|send|keep|follow|wear|read|review|confirm|identify|record|shut|"
                        r"release|swap|flush|install|replace|inspect|pause|stop|avoid|never|always|do)\b")


def _basic_visual(title, bullets):
    """Basic mode: turn a short list into an infographic when its shape is obvious."""
    if not 3 <= len(bullets) <= 6 or any(len(b.split()) > 22 for b in bullets):
        return None
    numbered = [NUMBER.search(b) for b in bullets]
    if sum(1 for m in numbered if m) >= max(2, len(bullets) - 1) and not STEP_WORDS.search(title):
        parts = []
        for b, m in zip(bullets, numbered):
            if not m:
                return None
            value = m.group(1).strip()
            label = (b[:m.start()] + b[m.end():]).strip(" :-–,.") or b
            parts.append({"value": value, "title": label[:90]})
        return {"type": "infographic", "layout": "stats", "title": title, "parts": parts}
    def split(b):
        head, _, tail = b.partition(":")
        return (head.strip(), tail.strip()) if tail and len(head.split()) <= 6 else (b, "")
    parts = []
    for b in bullets:
        t, txt = split(b)
        part = {"title": t[:90], "icon": icon_for(b)}
        if txt:
            part["text"] = txt[:160]
        parts.append(part)
    if STEP_WORDS.search(title) or sum(1 for b in bullets if VERB_START.match(b)) >= len(bullets) - 1:
        return {"type": "infographic", "layout": "process", "title": title, "parts": parts}
    if all(len(b.split()) <= 14 for b in bullets):
        return {"type": "infographic", "layout": "grid", "title": title, "parts": parts}
    return None


def heuristic_course(deck, seed=7):
    """No-AI fallback: one screen per slide, long lists become reveal screens,
    plus a simple recall quiz built from slide bullets."""
    rnd = random.Random(seed)
    slides = deck["slides"]
    screens = []
    first = slides[0]
    screens.append({"type": "intro", "title": deck["title"],
                    "subtitle": " · ".join(b["text"] for b in first["blocks"] if "text" in b)[:120],
                    "objectives": [s["title"] for s in slides[1:5] if s["title"]] or ["Understand the key ideas"]})
    pool = []  # (slide title, bullet)
    for s in slides[1:]:
        title = s["title"] or f"Slide {s['index']}"
        bullets = [b["text"] for b in s["blocks"] if "text" in b]
        for b in s["blocks"]:  # tables: header row + one bullet per data row
            if "table" in b and len(b["table"]) > 1:
                head = b["table"][0]
                bullets += ["; ".join(f"{h}: {v}" if h else v for h, v in zip(head, row) if v) for row in b["table"][1:]]
        images = s.get("images") or []
        if not bullets and not s["notes"] and not images:
            continue  # nothing usable on this slide (e.g. a thank-you slide)
        if re.search(r"(?i)^(agenda|contents|outline|today'?s (plan|agenda)|thank you|thanks|questions\??|q ?& ?a)\b", title.strip()):
            continue
        pool += [(title, b) for b in bullets]
        visual = _basic_visual(title, bullets)
        prev = screens[-1] if screens else {}
        if visual and prev.get("type") == "infographic" and prev.get("layout") == visual["layout"]:
            # keep variety: a second grid in a row becomes flip cards (or a reveal list)
            if visual["layout"] == "grid" and all(p.get("text") for p in visual["parts"]):
                visual = {"type": "flipcards", "title": title, "instruction": "Think about each one, then flip the card to check.",
                          "cards": [{"front": p["title"], "back": p["text"]} for p in visual["parts"]], "icon": icon_for(title)}
            else:
                visual = None
        if visual and not images:
            screen = visual
        elif len(bullets) >= 4:
            screen = {"type": "reveal", "title": title, "instruction": "Select each item to learn more.",
                      "items": [{"label": b.split(":")[0][:60], "detail": b} for b in bullets], "icon": icon_for(title)}
        else:
            what = {"slide": "diagram"}.get(images[0]["kind"], "picture") if images else ""
            screen = {"type": "content", "title": title, "icon": icon_for(title + " " + " ".join(bullets)),
                      "body": s["notes"] or " ".join(bullets) or f"Take a close look at this {what}.", "bullets": bullets}
        if images:
            screen["image"] = {"src": images[0]["src"], "alt": images[0].get("alt") or title}
        if s["notes"]:
            screen["narration"] = s["notes"][:1500]
        screens.append(screen)
    screens.append({"type": "summary", "title": "Key takeaways",
                    "points": [s["title"] for s in slides[1:] if s["title"]][:6]})
    titles = list({t for t, _ in pool})
    for t, b in rnd.sample(pool, min(4, len(pool))):
        others = [x for x in titles if x != t]
        if not others:
            continue  # a one-topic deck can't make a "which topic" question
        opts = rnd.sample(others, min(2, len(others))) + [t]
        rnd.shuffle(opts)
        screens.append({"type": "mcq", "question": f'Which topic covered this point: "{b}"?',
                        "options": opts, "correct": opts.index(t),
                        "explanation": f"This was covered in '{t}'.", "scored": True})
    course = {"title": deck["title"], "description": f"Interactive course generated from {deck['title']}.",
              "est_minutes": max(5, len(screens)), "mastery_score": 75, "screens": screens}
    return course, validate(course)
