"""ppt2scorm - turn a PowerPoint deck into an interactive SCORM 1.2 course.

Usage:
  python -m ppt2scorm.cli deck.pptx --provider gemini      # AI redesign with Gemini (needs GEMINI_API_KEY)
  python -m ppt2scorm.cli deck.pptx                        # AI redesign with whichever provider key is set
  python -m ppt2scorm.cli deck.pptx --course-json c.json   # use a reviewed/edited course JSON
  python -m ppt2scorm.cli deck.pptx --draft-only           # stop after AI step so a human can review the JSON
  python -m ppt2scorm.cli deck.pptx --no-ai                # basic conversion without AI
Options: --out course.zip  --brand "#e4002b"  --provider claude|gemini|openrouter|glm|qwen  --model <id>  --preview-dir ./preview
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

from .ai_redesign import PROVIDERS
from .pipeline import build_package, design_course, extract, screen_summary
from .schema import validate


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ppt2scorm", description="Convert a .pptx into an interactive SCORM 1.2 package")
    ap.add_argument("pptx")
    ap.add_argument("--out", help="output .zip (default: <deck>_scorm.zip)")
    ap.add_argument("--course-json", help="skip AI and package this course JSON")
    ap.add_argument("--draft-only", action="store_true", help="write the AI course JSON for review and stop")
    ap.add_argument("--no-ai", action="store_true", help="basic conversion without any LLM")
    ap.add_argument("--brand", help="brand colour, e.g. #e4002b")
    ap.add_argument("--provider", choices=list(PROVIDERS), help="LLM provider (default: first one with an API key set)")
    ap.add_argument("--model", help="model ID (default depends on provider)")
    ap.add_argument("--instructions", default="", help="extra guidance for the AI, e.g. audience or tone")
    ap.add_argument("--preview-dir", help="also keep the unzipped package here for previewing in a browser")
    a = ap.parse_args(argv)

    deck_path = Path(a.pptx)
    out = Path(a.out or deck_path.with_name(deck_path.stem + "_scorm.zip"))
    media = Path(tempfile.mkdtemp(prefix="ppt2scorm_media_"))

    print(f"1/3 Extracting {deck_path.name} ...")
    deck = extract(deck_path, media)
    print(f"    {len(deck['slides'])} slides, {sum(len(s['images']) for s in deck['slides'])} images")

    print("2/3 Designing the course ...")
    if a.course_json:
        course = json.loads(Path(a.course_json).read_text(encoding="utf-8"))
        warnings = validate(course)
        print(f"    using reviewed course JSON: {a.course_json}")
    else:
        course, warnings, how = design_course(deck, use_ai=not a.no_ai, provider=a.provider, model=a.model,
                                              instructions=a.instructions, log=lambda m: print("    " + m))
        print(f"    {how} produced {len(course['screens'])} screens")
    for w in warnings:
        print("    warning:", w)

    if a.draft_only:
        draft = deck_path.with_name(deck_path.stem + ".course.json")
        draft.write_text(json.dumps(course, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"    Draft saved to {draft}. Review/edit it, then rerun with:")
        print(f"    python -m ppt2scorm.cli {a.pptx} --course-json {draft}")
        return 0

    print("3/3 Packaging SCORM 1.2 ...")
    build_package(course, out, media_dir=media, brand=a.brand, keep_dir=a.preview_dir)
    kinds = screen_summary(course)
    print(f"    {out}  ({out.stat().st_size // 1024} KB) - screens: " + ", ".join(f"{v} {k}" for k, v in kinds.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
