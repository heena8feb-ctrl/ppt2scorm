"""Step 3 - Package the course as a SCORM 1.2 zip (imsmanifest.xml at the root)."""
import json
import re
import shutil
import uuid
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

PLAYER_DIR = Path(__file__).resolve().parent.parent / "player"

MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest identifier="{ident}" version="1.0"
  xmlns="http://www.imsproject.org/xsd/imscp_rootv1p1p2"
  xmlns:adlcp="http://www.adlnet.org/xsd/adlcp_rootv1p2"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xsi:schemaLocation="http://www.imsproject.org/xsd/imscp_rootv1p1p2 imscp_rootv1p1p2.xsd
                      http://www.imsglobal.org/xsd/imsmd_rootv1p2p1 imsmd_rootv1p2p1.xsd
                      http://www.adlnet.org/xsd/adlcp_rootv1p2 adlcp_rootv1p2.xsd">
  <metadata>
    <schema>ADL SCORM</schema>
    <schemaversion>1.2</schemaversion>
  </metadata>
  <organizations default="ORG-1">
    <organization identifier="ORG-1">
      <title>{title}</title>
      <item identifier="ITEM-1" identifierref="RES-1" isvisible="true">
        <title>{title}</title>
        <adlcp:masteryscore>{mastery}</adlcp:masteryscore>
      </item>
    </organization>
  </organizations>
  <resources>
    <resource identifier="RES-1" type="webcontent" adlcp:scormtype="sco" href="index.html">
{files}
    </resource>
  </resources>
</manifest>
"""


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "course"


def build_package(course, out_zip, media_dir=None, brand=None, keep_dir=None):
    """Writes out_zip. If keep_dir is given, the unzipped package is also left there (handy for previewing)."""
    out_zip = Path(out_zip)
    build = Path(keep_dir) if keep_dir else out_zip.with_suffix("")
    if build.exists():
        shutil.rmtree(build)
    build.mkdir(parents=True)

    for name in ("index.html", "player.css", "player.js", "voice.js", "scorm.js"):
        shutil.copy(PLAYER_DIR / name, build / name)
    if brand:
        course.setdefault("theme", {})["brand"] = brand
    # copy only the images and narration audio the course uses; drop references to files that are missing
    use_audio = (course.get("voiceover") or {}).get("mode") == "audio"
    for scr in course["screens"]:
        for key in ("image", "audio"):
            ref = scr.get(key)
            if not ref or (key == "audio" and not use_audio):
                continue  # recordings are kept on the course but only packaged when the AI voice is switched on
            src = Path(media_dir, Path(ref["src"]).name) if media_dir else None
            if src is None or not src.is_file():
                scr.pop(key)
                continue
            (build / "media").mkdir(exist_ok=True)
            shutil.copy(src, build / "media" / src.name)
    # Embed the course as JS (not fetched JSON) so it works from file:// and inside any LMS frame.
    shipped = course if use_audio else dict(course, screens=[{k: v for k, v in sc.items() if k != "audio"} for sc in course["screens"]])
    payload = json.dumps(shipped, ensure_ascii=False, indent=1).replace("</", "<\\/")
    (build / "course.js").write_text("window.COURSE = " + payload + ";\n", encoding="utf-8")

    files = sorted(p.relative_to(build).as_posix() for p in build.rglob("*") if p.is_file())
    file_xml = "\n".join(f'      <file href="{escape(f)}"/>' for f in files)
    mastery = course.get("mastery_score", 80) if any(
        s["type"] == "mcq" and s.get("scored", True) for s in course["screens"]) else ""
    manifest = MANIFEST.format(ident=f"{_slug(course['title'])}-{uuid.uuid4().hex[:8]}",
                               title=escape(course["title"]), mastery=mastery, files=file_xml)
    if mastery == "":
        manifest = manifest.replace("        <adlcp:masteryscore></adlcp:masteryscore>\n", "")
    (build / "imsmanifest.xml").write_text(manifest, encoding="utf-8")

    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(build.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(build).as_posix())
    if not keep_dir:
        shutil.rmtree(build)
    return out_zip
