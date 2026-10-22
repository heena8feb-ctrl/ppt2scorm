"""Stock photos from Pexels (free API key: https://www.pexels.com/api/). The AI writes a short "photo_query" for
screens that benefit from a real-world photo; this module finds a matching photo, downloads it into the course
and records the credit the Pexels guidelines ask for ("Photo by <name> on Pexels", linking to the photo page).
Photos are bundled in the SCORM package, so courses work offline."""
import json
import os
import re
import urllib.parse

from .brand import BrandError
from .website import fetch

API = os.environ.get("PPT2SCORM_PEXELS_URL", "https://api.pexels.com/v1")
PHOTO_TYPES = {"intro", "content", "section", "scenario", "flipcards", "reveal", "mcq"}
MAX_PHOTOS = 10


class PhotoError(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status_code = status


def _allowed_image_host(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    if os.environ.get("PPT2SCORM_ALLOW_PRIVATE_FETCH") == "tests-only" and host == "127.0.0.1":
        return True   # tests only
    return host == "images.pexels.com"


def search(query, key, per_page=8):
    query = re.sub(r"\s+", " ", (query or "").strip())[:100]
    if not query:
        return []
    url = f"{API}/search?" + urllib.parse.urlencode({"query": query, "per_page": per_page, "orientation": "landscape"})
    try:
        _, _, data = fetch(url, 1_000_000, "application/json", headers={"Authorization": key})
    except BrandError as e:
        status = getattr(e, "status_code", None)
        if status in (401, 403):
            raise PhotoError("Pexels rejected the API key. Check it and try again.", status) from None
        if status == 429:
            raise PhotoError("Pexels' hourly limit was reached. Try again later.", status) from None
        raise PhotoError(f"Couldn't search Pexels ({e}).", status) from None
    out = []
    for p in json.loads(data.decode("utf-8", "replace")).get("photos", []):
        src = p.get("src") or {}
        big, thumb = src.get("large") or src.get("landscape") or src.get("original"), src.get("medium") or src.get("small")
        if not big or not _allowed_image_host(big):
            continue
        out.append({"id": int(p.get("id", 0)), "large": big, "thumb": thumb if thumb and _allowed_image_host(thumb) else big,
                    "alt": (p.get("alt") or "").strip()[:200], "photographer": (p.get("photographer") or "a Pexels photographer")[:80],
                    "page": p.get("url") if str(p.get("url", "")).startswith("https://") else "https://www.pexels.com"})
    return out


def download(photo, media_dir):
    """Save a search result into the course. Returns a media entry with the credit."""
    from .extract import _save_image
    url = photo.get("large", "")
    if not _allowed_image_host(url):
        raise PhotoError("That photo isn't from Pexels.")
    try:
        _, _, data = fetch(url, 8_000_000, "image/*")
    except BrandError as e:
        raise PhotoError(f"Couldn't download the photo ({e}).") from None
    res = _save_image(data, "jpg", media_dir, f"px_{int(photo.get('id', 0))}")
    if not res:
        raise PhotoError("The photo couldn't be read.")
    name, w, h = res
    page = photo.get("page") if str(photo.get("page", "")).startswith("https://") else "https://www.pexels.com"
    return {"src": f"media/{name}", "w": w, "h": h, "alt": photo.get("alt", ""), "kind": "photo",
            "credit": f"Photo by {photo.get('photographer', 'a Pexels photographer')} on Pexels", "credit_url": page}


def add_photos(course, media_dir, key, progress=None):
    """Attach a photo to each screen with a photo_query and no image. Returns (media entries added, warnings)."""
    todo = [s for s in course["screens"] if s.get("photo_query") and not s.get("image") and s.get("type") in PHOTO_TYPES][:MAX_PHOTOS]
    added, used, warnings = [], set(), []
    for k, s in enumerate(todo):
        if progress:
            progress(k, len(todo))
        try:
            results = [r for r in search(s["photo_query"], key) if r["id"] not in used]
            if not results:
                continue
            entry = download(results[0], media_dir)
        except PhotoError as e:
            warnings.append(str(e) + (" Photos were skipped." if e.status_code in (401, 403, 429) else ""))
            if e.status_code in (401, 403, 429):
                break
            continue
        used.add(results[0]["id"])
        s["image"] = {"src": entry["src"], "alt": entry["alt"] or s.get("title") or s.get("question", ""),
                      "credit": entry["credit"], "credit_url": entry["credit_url"]}
        added.append(entry)
    if progress:
        progress(len(todo), len(todo))
    return added, warnings
