"""The built-in icon set (Lucide, ISC licence), shared by the player, the editor, the AI prompt and Basic mode."""
import json
import re
from pathlib import Path

_FILE = Path(__file__).resolve().parent.parent / "player" / "icons.js"
_text = _FILE.read_text(encoding="utf-8")
ICONS = json.loads(_text[_text.index("{"):_text.rindex("}") + 1])
NAMES = sorted(ICONS)

# words in slide text -> a fitting icon (used by Basic mode; the AI chooses icons itself)
KEYWORDS = [
    (r"secur|protect|safe(ty)?\b|privacy|guard", "shield-check"), (r"password|lock|access|encrypt", "lock"),
    (r"phish|scam|fraud|suspicious|warning|risk|danger|hazard", "triangle-alert"), (r"breach|incident|emergenc|alarm", "siren"),
    (r"report|notify|escalat", "megaphone"), (r"email|mail|inbox", "mail"), (r"phone|call|otp|mobile|sms", "phone"),
    (r"customer|client|consumer", "users"), (r"employee|staff|team|people|colleague", "users"), (r"manager|leader", "user-check"),
    (r"data|database|record", "database"), (r"personal|identity|aadhaar|id\b", "fingerprint"), (r"delete|remove|dispos|trash", "trash-2"),
    (r"store|storage|keep|retain|archive", "boxes"), (r"share|send|transfer", "share-2"), (r"collect|gather", "clipboard-list"),
    (r"law|legal|act\b|regulat|complian|polic", "scale"), (r"penalt|fine\b|cost|money|price|budget", "banknote"),
    (r"time|hour|minute|deadline|urgent|quick", "clock"), (r"date|calendar|schedule|month", "calendar"),
    (r"check|verify|confirm|inspect|test", "clipboard-check"), (r"rule|step|process|procedure", "list-checks"),
    (r"learn|train|course|educat", "graduation-cap"), (r"idea|tip|insight", "lightbulb"), (r"goal|target|objective|aim", "target"),
    (r"grow|increase|improv|perform", "trending-up"), (r"water|filter|leak|drain|liquid", "droplet"), (r"heat|fire|burn|hot\b", "flame"),
    (r"cold|freez|cool", "snowflake"), (r"electric|power|plug|voltage|energy", "zap"), (r"tool|repair|fix|service|maintain", "wrench"),
    (r"machine|washing|laundry", "washing-machine"), (r"fridge|refrigerator", "refrigerator"), (r"kitchen|cook|food|meal", "utensils"),
    (r"deliver|ship|truck|logistic", "truck"), (r"product|package|parcel|order", "package"), (r"sale|sell|buy|purchase|shop", "shopping-cart"),
    (r"quality|award|excellen|best", "award"), (r"feedback|talk|conversation|discuss|communicat", "message-circle"),
    (r"health|wellbeing|medical", "heart-pulse"), (r"environment|green|sustainab|recycl", "recycle"), (r"world|global|country", "globe"),
    (r"computer|laptop|software|system|digital|online", "laptop"), (r"cloud|internet|wifi|network", "cloud"),
    (r"document|file|form|paper", "file-text"), (r"search|find|look", "search"), (r"settings|configur|setup", "settings"),
    (r"success|done|complete|pass", "circle-check"), (r"never|don't|do not|avoid|stop|no\b", "ban"),
]


def icon_for(text, fallback="sparkles"):
    t = (text or "").lower()
    for pattern, name in KEYWORDS:
        if re.search(pattern, t):
            return name
    return fallback


def valid(name):
    return isinstance(name, str) and name in ICONS
