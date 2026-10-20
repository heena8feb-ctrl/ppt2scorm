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
Scored MCQs make up the final assessment; score = % correct, pass if >= mastery_score.
"""

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
    if not any(s["type"] == "mcq" and s.get("scored", True) for s in course["screens"]):
        warnings.append("no scored questions: course will complete on viewing all screens (no score sent)")
    return warnings
