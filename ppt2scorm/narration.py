"""Audio narration: turn each screen's narration script into a voice-over with Gemini text-to-speech.

- Scripts live on each screen as "narration" (written by the AI, or built from speaker notes in Basic mode).
- Audio files are named from a hash of voice + script, so unchanged screens are never re-recorded and
  edited screens are detected as needing new audio.
- MP3 is made with lameenc (pip install lameenc) or ffmpeg; without either, WAV is used (about 6x larger).
- Narration never breaks a conversion: if the provider is busy or a quota runs out, the screens recorded
  so far are kept and the rest can be recorded later from the editor.
"""
import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

TTS_URL = os.environ.get("PPT2SCORM_TTS_URL", "https://generativelanguage.googleapis.com/v1beta/interactions")
TTS_MODELS = ["gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"]  # second is the fallback when the first is busy
# A short list of Gemini voices that suit training, with Google's own one-word descriptions.
VOICES = {"Achird": "Friendly", "Sulafat": "Warm", "Kore": "Firm", "Charon": "Informative", "Iapetus": "Clear",
          "Schedar": "Even", "Sadaltager": "Knowledgeable", "Aoede": "Breezy", "Puck": "Upbeat", "Vindemiatrix": "Gentle"}
DEFAULT_VOICE = "Achird"
STYLE = "a friendly, clear corporate trainer speaking at a relaxed pace"
MAX_SCRIPT = 1500
TTS_WAITS = [4, 15, 30]   # rate limits on free text-to-speech are tight: back off longer than for text models
AUDIO_EXTS = (".mp3", ".wav")


class TTSError(Exception):
    def __init__(self, status, message):
        super().__init__(f"Error code: {status} - {message}")
        self.status_code = status


# ---------- scripts ----------
def _sentence(x):
    x = (x or "").strip()
    return x if not x or x[-1] in ".?!:" else x + "."


def default_script(screen, notes=None):
    """A narration script for a screen that has none: the speaker notes if known, else the screen's own words."""
    if notes:
        return notes.strip()[:MAX_SCRIPT]
    t = screen.get("type")
    parts = []
    if t == "intro":
        parts = [f"Welcome to {screen.get('title', 'this course')}.", "By the end, you'll be able to:"] + \
                [_sentence(o) for o in screen.get("objectives", [])[:4]]
    elif t == "content":
        parts = [_sentence(screen.get("body", ""))] + [_sentence(b) for b in screen.get("bullets", [])[:3]]
    elif t in ("flipcards", "reveal", "match"):
        parts = [_sentence(screen.get("title", "")), _sentence(screen.get("instruction", ""))]
    elif t == "scenario":
        parts = [screen.get("situation", ""), "Choose the best response."]
    elif t == "summary":
        parts = ["Let's recap."] + [_sentence(p) for p in screen.get("points", [])[:5]]
    elif t == "mcq":
        parts = [screen.get("question", "")]
    elif t == "section":
        parts = [_sentence(screen.get("title", "")), _sentence(screen.get("subtitle", ""))]
    elif t == "infographic":
        items = screen.get("parts", [])
        if screen.get("layout") == "stats":
            parts = [_sentence(screen.get("title", ""))] + [_sentence(f"{p.get('value', '')} {p.get('title', '')}") for p in items[:4]]
        elif screen.get("layout") == "checklist":
            parts = [_sentence(screen.get("title", "")), "Tick each item as you go."]
        else:
            parts = [_sentence(screen.get("title", "")), _sentence(screen.get("intro", ""))] + [_sentence(p.get("title", "")) for p in items[:6]]
    elif t == "sort":
        parts = [_sentence(screen.get("title", "")), _sentence(screen.get("instruction", "") or "Sort each item into the right group.")]
    return " ".join(p.strip() for p in parts if p and p.strip())[:MAX_SCRIPT]


def fill_scripts(course, notes_by_screen=None):
    """Give every screen without a script one. notes_by_screen: {screen index: speaker notes}."""
    notes_by_screen = notes_by_screen or {}
    for i, s in enumerate(course["screens"]):
        if not (isinstance(s.get("narration"), str) and s["narration"].strip()):
            text = default_script(s, notes_by_screen.get(i))
            if text:
                s["narration"] = text


def audio_name(voice, text):
    return "narr_" + hashlib.sha1(f"{voice}\n{text.strip()}".encode("utf-8")).hexdigest()[:12]


def prune_stale(course):
    """Drop audio that no longer matches the screen's script or the course voice. Returns how many screens need audio."""
    vo = course.get("voiceover") or {}
    voice, need = vo.get("voice", DEFAULT_VOICE), 0
    for s in course["screens"]:
        text = (s.get("narration") or "").strip()
        a = s.get("audio")
        if a and (not text or Path(a.get("src", "")).stem != audio_name(voice, text)):
            s.pop("audio")
        if vo.get("mode") == "audio" and text and not s.get("audio"):
            need += 1
    return need


# ---------- text-to-speech ----------
def _post(payload, api_key, timeout=90):
    req = urllib.request.Request(TTS_URL, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            j = json.loads(body)
            j = j[0] if isinstance(j, list) else j
            msg = j.get("error", {}).get("message", body)
        except ValueError:
            msg = body
        raise TTSError(e.code, msg[:300]) from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise TTSError(503, f"network error: {e}") from None


def _find_audio(resp):
    """The last base64 audio block in the response (steps[].content[] with type 'audio')."""
    found = []

    def walk(x):
        if isinstance(x, dict):
            if x.get("type") == "audio" and isinstance(x.get("data"), str):
                found.append(x["data"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(resp.get("steps", resp))
    if not found:
        raise TTSError(502, "the response had no audio")
    return base64.b64decode(found[-1])


def tts(text, voice, api_key, model):
    """Returns (pcm bytes, sample rate) for one script."""
    payload = {
        "model": model,
        "input": [{"type": "user_input", "content": [{"type": "text", "text": text,
                                                       "annotations": [{"type": "speech_metadata", "style": STYLE}]}]}],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": voice}]},
    }
    data = _find_audio(_post(payload, api_key))
    if data[:4] == b"RIFF":
        with wave.open(io.BytesIO(data)) as w:
            if w.getsampwidth() != 2:
                raise TTSError(502, "unexpected audio format")
            if w.getnchannels() != 1:
                raise TTSError(502, "unexpected stereo audio")
            return w.readframes(w.getnframes()), w.getframerate()
    return data, 24000  # headerless 16-bit mono PCM


# ---------- encoding ----------
def encode(pcm, rate, out_base):
    """Writes out_base.mp3 (or .wav) from 16-bit mono PCM. Returns (path, seconds)."""
    seconds = round(len(pcm) / 2 / rate, 1)
    try:
        import lameenc
        enc = lameenc.Encoder()
        enc.set_bit_rate(48)
        enc.set_in_sample_rate(rate)
        enc.set_channels(1)
        enc.set_quality(2)
        path = Path(f"{out_base}.mp3")
        path.write_bytes(enc.encode(pcm) + enc.flush())
        return path, seconds
    except ImportError:
        pass
    if shutil.which("ffmpeg"):
        path = Path(f"{out_base}.mp3")
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0",
                            "-b:a", "48k", str(path)], input=pcm, capture_output=True, timeout=60)
        if r.returncode == 0:
            return path, seconds
    path = Path(f"{out_base}.wav")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return path, seconds


def _existing(media_dir, base):
    for ext in AUDIO_EXTS:
        p = Path(media_dir, base + ext)
        if p.is_file():
            return p
    return None


def _duration(path):
    if path.suffix == ".wav":
        with wave.open(str(path)) as w:
            return round(w.getnframes() / w.getframerate(), 1)
    return round(path.stat().st_size * 8 / 48000, 1)  # 48 kbps constant bit rate


# ---------- whole course ----------
def narrate_course(course, media_dir, api_key, voice=None, progress=None, sleep=time.sleep):
    """Records audio for every screen that has a script but no matching audio.
    Returns (recorded, total_needed, warnings). Never raises for provider problems."""
    from .ai_redesign import _is_transient

    vo = course.setdefault("voiceover", {})
    vo["mode"] = "audio"
    vo["voice"] = voice = voice if voice in VOICES else vo.get("voice") if vo.get("voice") in VOICES else DEFAULT_VOICE
    prune_stale(course)
    todo = [s for s in course["screens"] if (s.get("narration") or "").strip() and not s.get("audio")]
    warnings, done, models = [], 0, list(TTS_MODELS)
    for k, s in enumerate(todo):
        if progress:
            progress(k, len(todo))
        text = s["narration"].strip()[:MAX_SCRIPT]
        base = audio_name(voice, text)
        cached = _existing(media_dir, base)
        if cached:
            s["audio"] = {"src": f"media/{cached.name}", "duration": _duration(cached)}
            done += 1
            continue
        err = busy = None
        while models and not s.get("audio"):
            for attempt in range(len(TTS_WAITS) + 1):
                try:
                    pcm, rate = tts(text, voice, api_key, models[0])
                    path, secs = encode(pcm, rate, Path(media_dir, base))
                    s["audio"] = {"src": f"media/{path.name}", "duration": secs}
                    done += 1
                    break
                except Exception as e:  # noqa: BLE001 - classified below
                    err = e
                    if not _is_transient(e):
                        break
                    busy = busy or e
                    if attempt < len(TTS_WAITS):
                        sleep(TTS_WAITS[attempt])
            if s.get("audio") or not _is_transient(err):
                break
            models.pop(0)  # this model is busy or out of quota: use the lighter one from now on
        if busy is not None and not s.get("audio") and not models:
            err = busy
        elif busy is not None and not s.get("audio") and getattr(err, "status_code", None) == 404:
            err = busy     # the fallback model was renamed: the real problem is that the main one is busy
        if not s.get("audio"):
            if err is not None and _is_transient(err):
                warnings.append(f"Narration stopped after {done} of {len(todo)} screens: the voice service is busy "
                                "or today's free limit was reached. Open the editor later and click Record audio.")
            elif getattr(err, "status_code", None) in (400, 401, 403):
                warnings.append("Narration was skipped: the API key was rejected by Google's voice service. "
                                "AI voice needs a Gemini key.")
            else:
                warnings.append(f"Narration stopped after {done} of {len(todo)} screens ({str(err)[:120]}).")
            break
    if progress:
        progress(len(todo), len(todo))
    return done, len(todo), warnings
