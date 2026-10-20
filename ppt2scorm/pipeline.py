"""The conversion pipeline, shared by the command line (cli.py) and the web app (web/app.py)."""
from .ai_redesign import PROVIDERS, ai_course, detect_provider, heuristic_course
from .extract import as_prompt_text, available_images, extract
from .schema import resolve_images
from .package import build_package


def design_course(deck, use_ai=True, provider=None, model=None, api_key=None, instructions="", log=print):
    """Turns an extracted deck into a validated course dict.
    Returns (course, warnings, how) where `how` says which engine produced it."""
    provider = provider or (None if api_key else detect_provider())
    if use_ai and provider:
        model = model or PROVIDERS[provider][2]
        log(f"asking {provider} ({model}) to redesign the deck")
        course, warnings = ai_course(as_prompt_text(deck), model=model, extra_instructions=instructions,
                                     provider=provider, api_key=api_key)
        return course, _finish(course, deck, warnings), f"{provider} ({model})"
    if use_ai:
        log("no LLM API key available - using basic (non-AI) conversion")
    course, warnings = heuristic_course(deck)
    return course, _finish(course, deck, warnings), "basic (no AI)"


def _finish(course, deck, warnings):
    """Drop image references to files that don't exist, and pass on extraction warnings."""
    return list(deck.get("warnings", [])) + list(warnings) + resolve_images(course, available_images(deck))


def screen_summary(course):
    kinds = {}
    for s in course["screens"]:
        kinds[s["type"]] = kinds.get(s["type"], 0) + 1
    return kinds


__all__ = ["extract", "design_course", "build_package", "screen_summary"]
