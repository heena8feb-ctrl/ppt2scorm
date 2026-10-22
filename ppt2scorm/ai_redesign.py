"""Step 2b - Redesign extracted slides into an engaging course using an LLM.

Providers: Claude (Anthropic SDK) or any OpenAI-compatible API (Gemini, OpenRouter, Z.ai GLM, Qwen).
Without a key, `heuristic_course` builds a basic (less engaging) course so the pipeline still runs.
"""
import json
import os
import random
import re

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

SYSTEM_PROMPT = """You are a senior instructional designer. You turn slide decks into short,
engaging e-learning courses. You never invent facts that are not supported by the deck;
you may rephrase, restructure, and turn content into interactions.

Design rules:
- Start with an `intro` screen (3-4 measurable learning objectives).
- Keep every screen short: body <= 60 words, bullets <= 5, each bullet <= 15 words.
- Do NOT reproduce slides one-to-one. Turn lists of definitions/examples into `flipcards` or `reveal`,
  term-to-meaning lists into `match`, and any situation/case/example into a `scenario`.
- Use speaker notes: they usually hold the real teaching point and the right answer to scenarios.
- Roughly every 2-3 screens, include an interaction (flipcards, reveal, match, scenario).
- End with a `summary` screen, then a final assessment of 4-6 `mcq` screens with "scored": true.
  Each mcq: 3-4 plausible options, exactly one correct, an explanation that teaches.
- Spread the correct answer position across options; avoid "all of the above".
- Plain, friendly language for a corporate audience.

Images:
- Slides may list IMAGE lines (photos, diagrams, charts, or whole slides rendered as images).
  Attach images that help learning with "image": {"src": "<the exact media/... path>", "alt": "<what it shows>"}.
- Only use paths listed in the deck; never invent one. Use each image at most once. Images can go on
  intro, content, flipcards, reveal, scenario and mcq screens.
- A chart or diagram usually belongs on the content or reveal screen that teaches it. A photo of equipment
  can make a scenario or a question concrete ("Look at the photo: what should the technician check first?").
- Alt text describes what the image shows for someone who can't see it (e.g. "Bar chart: blocked filters
  cause 50% of drain complaints"), in one sentence. Don't start with "Image of".
- If an image is decorative or unclear, leave it out.

Narration:
- Give EVERY screen a "narration" field: what a friendly trainer would say on that screen, 1 to 3 short
  sentences (at most 45 words), written to be spoken aloud.
- Don't just read the screen out. Add the context or reason from the speaker notes, and guide the learner
  ("Flip each card to check your guess.").
- On scenario and mcq screens, set up the situation or read the question, but never hint at the answer
  or read the options.
- Plain words, no abbreviations a voice would stumble on, no emoji, no stage directions.

Output ONLY a JSON object, no markdown fences, matching this schema:
{
 "title": str, "description": str (1 sentence), "est_minutes": int, "mastery_score": 80,
 "screens": [
  {"type":"intro","title":str,"subtitle":str,"objectives":[str]},
  {"type":"content","title":str,"body":str,"bullets":[str],"callout":str},
  {"type":"flipcards","title":str,"instruction":str,"cards":[{"front":str,"back":str}]},
  {"type":"reveal","title":str,"instruction":str,"items":[{"label":str,"detail":str}]},
  {"type":"match","title":str,"instruction":str,"pairs":[{"left":str,"right":str}]},
  {"type":"scenario","title":str,"situation":str,"choices":[{"text":str,"correct":bool,"feedback":str}]},
  {"type":"summary","title":str,"points":[str]},
  {"type":"mcq","question":str,"options":[str],"correct":int,"explanation":str,"scored":true}
 ]
}
("narration": str goes on every screen. bullets and callout are optional on content screens; "image" is optional on intro, content,
flipcards, reveal, scenario and mcq screens.)"""


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
        pool += [(title, b) for b in bullets]
        if len(bullets) >= 4:
            screen = {"type": "reveal", "title": title, "instruction": "Select each item to learn more.",
                      "items": [{"label": b.split(":")[0][:60], "detail": b} for b in bullets]}
        else:
            what = {"slide": "diagram"}.get(images[0]["kind"], "picture") if images else ""
            screen = {"type": "content", "title": title,
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
