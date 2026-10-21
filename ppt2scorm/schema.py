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
}


class CourseError(ValueError):
    pass


IMAGE_TYPES = {"intro", "content", "flipcards", "reveal", "scenario", "mcq"}
IMAGE_SRC = re.compile(r"^media/[A-Za-z0-9_.-]{1,120}$")
AUDIO_SRC = re.compile(r"^media/narr_[0-9a-f]{12}\.(mp3|wav)$")
VOICEOVER_MODES = {"off", "audio", "device"}
MAX_NARRATION = 1500


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
        used.add(src)
    return warnings


# Field shapes, used to check hand-edited courses (the web editor) before they reach the player.
TEXT_FIELDS = {"title", "subtitle", "body", "callout", "instruction", "situation", "question", "explanation"}
TEXT_LISTS = {"objectives", "bullets", "points", "options"}
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
