"""Step 2a - The course JSON contract between the AI layer and the player.

Course = {
  "title": str, "description": str, "est_minutes": int, "mastery_score": int (0-100),
  "screens": [Screen, ...]
}
Screen types (field "type"):
  intro      title, subtitle, objectives[str]
  content    title, body(str), bullets[str]?, callout(str)?
  flipcards  title, instruction, cards[{front, back}]
  reveal     title, instruction, items[{label, detail}]          (click-to-expand accordion)
  match      title, instruction, pairs[{left, right}]            (click-to-match)
  scenario   title, situation, choices[{text, correct(bool), feedback}]
  mcq        question, options[str], correct(int), explanation, scored(bool)
  summary    title, points[str]
  section    title, subtitle?, icon?                                  (module divider)
  infographic title, layout, parts[{title, text?, icon?, value?, tone?}], intro?
             layout: process | timeline | stats | comparison | cycle | pyramid | grid | checklist
  sort       title, instruction?, buckets[str] (2-3), terms[{text, bucket(int)}]  (sort items into categories)
Optional on intro, content, flipcards, reveal, scenario and mcq screens:
  image      {"src": "media/<file>", "alt": "what the image shows"}
Optional on every screen:
  narration  str: the voice-over script            audio  {"src": "media/narr_<hash>.mp3", "duration": seconds}
Optional on the course:
  voiceover  {"mode": "off" | "audio" | "device", "voice": "<Gemini voice name>"}
Scored MCQs make up the final assessment; score = % correct, pass if >= mastery_score.
"""
import re

SCREEN_FIELDS = {
    "intro": ["title", "objectives"],
    "content": ["title", "body"],
    "flipcards": ["title", "cards"],
    "reveal": ["title", "items"],
    "match": ["title", "pairs"],
    "scenario": ["title", "situation", "choices"],
    "mcq": ["question", "options", "correct", "explanation"],
    "summary": ["title", "points"],
    "section": ["title"],
    "infographic": ["title", "layout", "parts"],
    "sort": ["title", "buckets", "terms"],
}
LAYOUTS = ("process", "timeline", "stats", "comparison", "cycle", "pyramid", "grid", "checklist")


class CourseError(ValueError):
    pass


IMAGE_TYPES = {"intro", "content", "flipcards", "reveal", "scenario", "mcq", "section"}
ICON_SCREENS = {"content", "section", "flipcards", "reveal", "scenario", "summary", "intro"}
CREDIT_URL = re.compile(r"^https://[^\s\"<>]{1,400}$")
IMAGE_SRC = re.compile(r"^media/[A-Za-z0-9_.-]{1,120}$")
AUDIO_SRC = re.compile(r"^media/narr_[0-9a-f]{12}\.(mp3|wav)$")
VOICEOVER_MODES = {"off", "audio", "device"}
MAX_NARRATION = 1500


def _check_rich(i, s, strict):
    """Section / infographic / sort rules, plus icons. Lenient mode repairs small slips; strict mode reports them."""
    from .icons import valid as icon_ok
    t = s.get("type")
    if "icon" in s and not icon_ok(s["icon"]):
        if strict:
            raise CourseError(f"screen {i}: pick an icon from the list")
        s.pop("icon")
    if t == "infographic":
        if s.get("layout") not in LAYOUTS:
            if strict:
                raise CourseError(f"screen {i}: choose an infographic layout")
            s["layout"] = "grid"
        parts = s.get("parts")
        if not isinstance(parts, list) or not 2 <= len(parts) <= 8:
            raise CourseError(f"screen {i}: an infographic needs 2 to 8 parts")
        for n, part in enumerate(parts, 1):
            if not isinstance(part, dict) or not isinstance(part.get("title"), str) or not part["title"].strip():
                raise CourseError(f"screen {i}, part {n}: give it a title")
            for k in ("text", "value", "icon", "tone"):
                if k in part and not isinstance(part[k], str):
                    raise CourseError(f"screen {i}, part {n}: '{k}' must be text")
            if part.get("icon") and not icon_ok(part["icon"]):
                if strict:
                    raise CourseError(f"screen {i}, part {n}: pick an icon from the list")
                part.pop("icon")
            if part.get("tone") not in (None, "", "good", "bad"):
                if strict:
                    raise CourseError(f"screen {i}, part {n}: tone must be good or bad")
                part.pop("tone")
        if s["layout"] == "comparison" and len(parts) != 2:
            raise CourseError(f"screen {i}: a comparison has exactly 2 columns")
        if s["layout"] == "stats" and not all((p.get("value") or "").strip() for p in parts):
            raise CourseError(f"screen {i}: every stat needs a value, such as 50% or 3x")
    elif t == "sort":
        b = s.get("buckets")
        if not isinstance(b, list) or not 2 <= len(b) <= 3 or not all(isinstance(x, str) and x.strip() for x in b):
            raise CourseError(f"screen {i}: a sort activity needs 2 or 3 named categories")
        terms = s.get("terms")
        if not isinstance(terms, list) or len(terms) < 3:
            raise CourseError(f"screen {i}: a sort activity needs at least 3 items to sort")
        for n, term in enumerate(terms, 1):
            if not isinstance(term, dict) or not isinstance(term.get("text"), str) or not term["text"].strip():
                raise CourseError(f"screen {i}, item {n}: the item needs text")
            if not (isinstance(term.get("bucket"), int) and not isinstance(term.get("bucket"), bool) and 0 <= term["bucket"] < len(b)):
                raise CourseError(f"screen {i}, item {n}: choose its correct category")
        if len(terms) > 12:
            raise CourseError(f"screen {i}: keep a sort activity to 12 items")


def _check_audio(i, s):
    if "narration" in s and (not isinstance(s["narration"], str) or len(s["narration"]) > MAX_NARRATION):
        raise CourseError(f"screen {i}: narration must be text under {MAX_NARRATION} characters")
    a = s.get("audio")
    if a is None:
        return
    if not isinstance(a, dict) or not isinstance(a.get("src"), str) or not AUDIO_SRC.match(a["src"]):
        raise CourseError(f"screen {i}: the narration audio file isn't valid")
    if not isinstance(a.get("duration", 0), (int, float)) or isinstance(a.get("duration"), bool):
        raise CourseError(f"screen {i}: the narration length isn't valid")


def _check_voiceover(course):
    vo = course.get("voiceover")
    if vo is None:
        return
    if not isinstance(vo, dict) or vo.get("mode", "off") not in VOICEOVER_MODES or not isinstance(vo.get("voice", ""), str):
        raise CourseError("the voice-over setting isn't valid")


def _tidy_image(i, s, warnings):
    """Lenient check for AI output: fix a missing media/ prefix, drop anything else that's malformed."""
    img = s.get("image")
    if img is None:
        return
    if isinstance(img, str):
        img = s["image"] = {"src": img, "alt": ""}
    src = img.get("src") if isinstance(img, dict) else None
    if isinstance(src, str) and "/" not in src.strip():
        img["src"] = "media/" + src.strip()
    try:
        _check_image(i, s)
    except CourseError as e:
        s.pop("image", None)
        warnings.append(f"{e} (image removed)")


def _check_image(i, s):
    img = s.get("image")
    if img is None:
        return
    if s.get("type") not in IMAGE_TYPES:
        raise CourseError(f"screen {i}: {s.get('type')} screens can't have an image")
    if not isinstance(img, dict) or not isinstance(img.get("src"), str) or not IMAGE_SRC.match(img["src"]) \
            or ".." in img["src"]:
        raise CourseError(f"screen {i}: the image must point to a file in media/")
    if not isinstance(img.get("alt", ""), str) or len(img.get("alt", "")) > 300:
        raise CourseError(f"screen {i}: image description must be text under 300 characters")
    if not isinstance(img.get("credit", ""), str) or len(img.get("credit", "")) > 160:
        raise CourseError(f"screen {i}: the photo credit isn't valid")
    if img.get("credit_url") is not None and not (isinstance(img["credit_url"], str) and CREDIT_URL.match(img["credit_url"])):
        raise CourseError(f"screen {i}: the photo credit link isn't valid")


def resolve_images(course, available):
    """Keep only image references that point at real files; fix common path slips. Returns warnings.
    available: set or dict of 'media/<file>' paths that exist."""
    warnings, used = [], set()
    for i, s in enumerate(course.get("screens", []), 1):
        img = s.get("image")
        if img is None:
            continue
        src = img.get("src", "") if isinstance(img, dict) else img if isinstance(img, str) else ""
        src = src.strip().lstrip("./")
        if src and not src.startswith("media/"):
            src = "media/" + src.split("/")[-1]
        if src not in available or s.get("type") not in IMAGE_TYPES:
            s.pop("image")
            if src:
                warnings.append(f"screen {i}: removed an image reference that doesn't exist ({src[:60]})")
            continue
        alt = (img.get("alt") if isinstance(img, dict) else "") or ""
        if not alt and isinstance(available, dict):
            alt = available[src].get("alt", "")
        s["image"] = {"src": src, "alt": (alt or s.get("title") or s.get("question") or "")[:300]}
        src_meta = available.get(src, {}) if isinstance(available, dict) else {}
        credit = (img.get("credit") if isinstance(img, dict) else None) or src_meta.get("credit")
        if credit:
            s["image"]["credit"] = str(credit)[:160]
            url = (img.get("credit_url") if isinstance(img, dict) else None) or src_meta.get("credit_url")
            if isinstance(url, str) and CREDIT_URL.match(url):
                s["image"]["credit_url"] = url
        used.add(src)
    return warnings


# Field shapes, used to check hand-edited courses (the web editor) before they reach the player.
TEXT_FIELDS = {"title", "subtitle", "body", "callout", "instruction", "situation", "question", "explanation", "intro", "layout"}
TEXT_LISTS = {"objectives", "bullets", "points", "options", "buckets"}
RECORD_LISTS = {"cards": ("front", "back"), "items": ("label", "detail"), "pairs": ("left", "right"),
                "choices": ("text", "feedback")}
MAX_TEXT = 4000


def check_types(course):
    """Strict shape check for courses edited by people. Raises CourseError naming the screen and field."""
    if not isinstance(course, dict) or not isinstance(course.get("screens"), list):
        raise CourseError("the course must be an object with a list of screens")
    if not isinstance(course.get("title", ""), str) or not course.get("title", "").strip():
        raise CourseError("the course needs a title")
    for k, lo, hi in (("mastery_score", 0, 100), ("est_minutes", 1, 600)):
        if k in course and (not isinstance(course[k], int) or isinstance(course[k], bool) or not lo <= course[k] <= hi):
            raise CourseError(f"{k.replace('_', ' ')} must be a whole number from {lo} to {hi}")
    _check_voiceover(course)
    if len(course["screens"]) > 200:
        raise CourseError("a course can have at most 200 screens")
    for i, s in enumerate(course["screens"], 1):
        if not isinstance(s, dict):
            raise CourseError(f"screen {i}: not a valid screen")
        for k, v in s.items():
            where = f"screen {i}, {k}"
            if k in TEXT_FIELDS and not isinstance(v, str):
                raise CourseError(f"{where}: must be text")
            if k in TEXT_FIELDS and len(v) > MAX_TEXT:
                raise CourseError(f"{where}: too long (max {MAX_TEXT} characters)")
            if k in TEXT_LISTS and (not isinstance(v, list) or not all(isinstance(x, str) for x in v)):
                raise CourseError(f"{where}: must be a list of text")
            if k in RECORD_LISTS:
                if not isinstance(v, list) or not all(isinstance(x, dict) for x in v):
                    raise CourseError(f"{where}: must be a list of items")
                for n, rec in enumerate(v, 1):
                    for f in RECORD_LISTS[k]:
                        if not isinstance(rec.get(f, ""), str):
                            raise CourseError(f"{where} {n}: '{f}' must be text")
                    if k != "choices" and any(not str(rec.get(f, "")).strip() for f in RECORD_LISTS[k]):
                        raise CourseError(f"{where} {n}: fill in both sides")
                    if k == "choices" and not str(rec.get("text", "")).strip():
                        raise CourseError(f"{where} {n}: the choice needs text")
            if k in TEXT_LISTS and any(not x.strip() for x in v):
                raise CourseError(f"{where}: remove or fill in the empty line")
        _check_image(i, s)
        _check_audio(i, s)
        _check_rich(i, s, True)
        if s.get("type") == "mcq" and not (isinstance(s.get("correct"), int) and not isinstance(s.get("correct"), bool)):
            raise CourseError(f"screen {i}: pick the correct answer")
    return validate(course)


def validate(course):
    """Raises CourseError with a readable message; returns list of warnings."""
    warnings = []
    for k in ("title", "screens"):
        if k not in course:
            raise CourseError(f"course missing '{k}'")
    if not course["screens"]:
        raise CourseError("course has no screens")
    course.setdefault("description", "")
    course.setdefault("est_minutes", max(5, len(course["screens"])))
    course.setdefault("mastery_score", 80)
    for i, s in enumerate(course["screens"], 1):
        t = s.get("type")
        if t not in SCREEN_FIELDS:
            raise CourseError(f"screen {i}: unknown type '{t}'")
        for f in SCREEN_FIELDS[t]:
            if f not in s or s[f] in (None, "", []):
                raise CourseError(f"screen {i} ({t}): missing '{f}'")
        if t == "mcq":
            if not (0 <= int(s["correct"]) < len(s["options"])):
                raise CourseError(f"screen {i}: 'correct' index out of range")
            if len(s["options"]) < 2:
                raise CourseError(f"screen {i}: mcq needs at least 2 options")
            s.setdefault("scored", True)
        if t == "scenario" and not any(c.get("correct") for c in s["choices"]):
            raise CourseError(f"screen {i}: scenario needs one correct choice")
        if t == "match" and len(s["pairs"]) < 2:
            raise CourseError(f"screen {i}: match needs at least 2 pairs")
        _tidy_image(i, s, warnings)
        _check_rich(i, s, False)
        if "narration" in s and not isinstance(s["narration"], str):
            s.pop("narration")
        elif isinstance(s.get("narration"), str):
            s["narration"] = s["narration"].strip()[:MAX_NARRATION]
        try:
            _check_audio(i, s)
        except CourseError:
            s.pop("audio", None)
    if not any(s["type"] == "mcq" and s.get("scored", True) for s in course["screens"]):
        warnings.append("no scored questions: course will complete on viewing all screens (no score sent)")
    return warnings
