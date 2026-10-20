"""Step 1 - Extract content from a .pptx: titles, bullet text, speaker notes, tables, images.
Optionally renders every slide to PNG via headless LibreOffice (useful for diagram-heavy slides).
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE


def _shape_texts(shape):
    out = []
    if shape.has_text_frame:
        for p in shape.text_frame.paragraphs:
            t = "".join(r.text for r in p.runs).strip()
            if t:
                out.append({"text": t, "level": p.level})
    if getattr(shape, "has_table", False) and shape.has_table:
        rows = [[c.text.strip() for c in r.cells] for r in shape.table.rows]
        out.append({"table": rows})
    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        for s in shape.shapes:
            out.extend(_shape_texts(s))
    return out


def extract(pptx_path, media_dir):
    """Returns a dict: {title, slides:[{index,title,blocks,notes,images}]}"""
    media_dir = Path(media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)
    prs = Presentation(pptx_path)
    slides = []
    for i, slide in enumerate(prs.slides, start=1):
        title = slide.shapes.title.text.strip() if slide.shapes.title is not None else ""
        blocks, images = [], []
        for shape in slide.shapes:
            if slide.shapes.title is not None and shape.shape_id == slide.shapes.title.shape_id:
                continue
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                img = shape.image
                name = f"s{i}_{shape.shape_id}.{img.ext}"
                (media_dir / name).write_bytes(img.blob)
                images.append(name)
            blocks.extend(_shape_texts(shape))
        notes = ""
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
        slides.append({"index": i, "title": title, "blocks": blocks, "notes": notes, "images": images})
    deck_title = slides[0]["title"] if slides and slides[0]["title"] else Path(pptx_path).stem
    return {"title": deck_title, "slides": slides}


def render_slides(pptx_path, media_dir):
    """Render each slide to PNG (slide-1.png ...) using LibreOffice -> PDF -> PNG. Returns list of names."""
    if not shutil.which("soffice"):
        return []
    media_dir = Path(media_dir)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", tmp, str(pptx_path)],
                       check=True, capture_output=True, timeout=180)
        pdf = next(Path(tmp).glob("*.pdf"))
        if not shutil.which("pdftoppm"):
            return []
        subprocess.run(["pdftoppm", "-png", "-r", "60", str(pdf), str(media_dir / "slide")], check=True)
    names = sorted(p.name for p in media_dir.glob("slide-*.png"))
    # normalise names to slide-1.png, slide-2.png
    out = []
    for p in names:
        n = int(p.split("-")[1].split(".")[0])
        new = f"slide-{n}.png"
        os.replace(media_dir / p, media_dir / new)
        out.append(new)
    return out


def as_prompt_text(deck):
    """Flatten extracted deck into readable text for the LLM."""
    lines = [f"DECK TITLE: {deck['title']}", ""]
    for s in deck["slides"]:
        lines.append(f"--- SLIDE {s['index']}: {s['title']}")
        for b in s["blocks"]:
            if "text" in b:
                lines.append("  " * b["level"] + "- " + b["text"])
            else:
                lines.append("TABLE: " + " | ".join(" / ".join(r) for r in b["table"]))
        if s["images"]:
            lines.append(f"[images: {', '.join(s['images'])}]")
        if s["notes"]:
            lines.append(f"SPEAKER NOTES: {s['notes']}")
        lines.append("")
    return "\n".join(lines)
