/* Voice-over for the course player.
   Plays each screen's recorded narration (screen.audio) when the course uses an AI voice, or reads
   screen.narration aloud with the device's built-in voice when the course is set to device voice. Controls: play/pause, replay, captions and auto-play.
   Usage: Voice.init(course); Voice.show(screen or null) on every screen change. */
(function (global) {
  "use strict";
  var C, mode, bar, capBox, playBtn, replayBtn, ccBtn, autoBtn, fill, timeEl;
  var audio = null, kind = null, text = "", playing = false, speechPos = 0, activated = false;
  var prefs = { auto: true, cc: false };
  var canSpeak = "speechSynthesis" in global && "SpeechSynthesisUtterance" in global;

  function load() { try { var p = JSON.parse(localStorage.getItem("ppt2scorm.voice") || "{}"); prefs.auto = p.auto !== false; prefs.cc = !!p.cc; } catch (e) {} }
  function store() { try { localStorage.setItem("ppt2scorm.voice", JSON.stringify(prefs)); } catch (e) {} }

  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { if (k === "text") n.textContent = attrs[k]; else if (k === "class") n.className = attrs[k]; else n.setAttribute(k, attrs[k]); });
    (kids || []).forEach(function (c) { n.appendChild(c); });
    return n;
  }
  function fmt(s) { s = Math.max(0, Math.round(s || 0)); return Math.floor(s / 60) + ":" + ("0" + (s % 60)).slice(-2); }

  function build() {
    playBtn = el("button", { type: "button", class: "vo-play", "aria-pressed": "false" }, [el("span", { class: "vo-ico", "aria-hidden": "true" }), el("span", { class: "vo-label", text: "Play narration" })]);
    replayBtn = el("button", { type: "button", class: "vo-btn vo-replay", "aria-label": "Replay narration", title: "Replay" });
    fill = el("div", { class: "vo-fill" });
    timeEl = el("span", { class: "vo-time", "aria-hidden": "true" });
    ccBtn = el("button", { type: "button", class: "vo-btn vo-toggle", "aria-pressed": String(prefs.cc), text: "Captions" });
    autoBtn = el("button", { type: "button", class: "vo-btn vo-toggle", "aria-pressed": String(prefs.auto), text: "Auto-play" });
    bar = el("div", { class: "vo", role: "group", "aria-label": "Narration" }, [playBtn, replayBtn, el("div", { class: "vo-track" }, [fill]), timeEl, ccBtn, autoBtn]);
    capBox = el("div", { class: "vo-captions", "aria-live": "off" });
    bar.hidden = capBox.hidden = true;
    var stage = document.getElementById("stage");
    stage.parentNode.insertBefore(bar, stage);
    stage.parentNode.insertBefore(capBox, stage);

    playBtn.addEventListener("click", function () { activated = true; playing ? pause() : play(); });
    replayBtn.addEventListener("click", function () { activated = true; restart(); });
    ccBtn.addEventListener("click", function () { prefs.cc = !prefs.cc; store(); ccBtn.setAttribute("aria-pressed", String(prefs.cc)); capBox.hidden = !prefs.cc || !text; });
    autoBtn.addEventListener("click", function () { prefs.auto = !prefs.auto; store(); autoBtn.setAttribute("aria-pressed", String(prefs.auto)); });
    // Browsers only allow sound after the learner has clicked something; Next/Back clicks count.
    document.addEventListener("click", function () { activated = true; }, true);
    document.addEventListener("keydown", function () { activated = true; }, true);
  }

  function setUnavailable(on) {
    bar.classList.toggle("no-audio", on);
    playBtn.disabled = replayBtn.disabled = on;
    if (on) playBtn.querySelector(".vo-label").textContent = "No audio for this screen";
    else if (!playing) playBtn.querySelector(".vo-label").textContent = "Play narration";
  }

  function setPlaying(on) {
    playing = on;
    playBtn.setAttribute("aria-pressed", String(on));
    playBtn.querySelector(".vo-label").textContent = on ? "Pause" : "Play narration";
    bar.classList.toggle("is-playing", on);
  }
  function progress(p, cur, dur) {
    fill.style.width = Math.min(100, Math.max(0, p * 100)) + "%";
    timeEl.textContent = dur ? fmt(cur) + " / " + fmt(dur) : "";
  }

  /* ---- recorded audio ---- */
  function useFile(src, duration) {
    kind = "file";
    audio = new Audio();
    audio.preload = "auto";
    audio.src = src;
    progress(0, 0, duration);
    audio.addEventListener("timeupdate", function () { progress(audio.currentTime / (audio.duration || duration || 1), audio.currentTime, audio.duration || duration); });
    audio.addEventListener("ended", function () { setPlaying(false); progress(1, audio.duration, audio.duration); });
    audio.addEventListener("pause", function () { if (!audio.ended) setPlaying(false); });
    audio.addEventListener("play", function () { setPlaying(true); });
    audio.addEventListener("error", function () { // file missing or blocked: show captions only, no other voice
      if (kind === "file") { audio = null; kind = null; setPlaying(false); setUnavailable(true); }
    });
  }

  /* ---- device voice ---- */
  function speak(from) {
    global.speechSynthesis.cancel();
    var u = new SpeechSynthesisUtterance(text.slice(from));
    u.lang = document.documentElement.lang || "en";
    u.rate = 1;
    u.onboundary = function (e) { speechPos = from + (e.charIndex || 0); progress(speechPos / text.length); };
    u.onend = function () { if (kind === "speech" && playing) { setPlaying(false); progress(1); speechPos = 0; } };
    u.onerror = function () { setPlaying(false); };
    global.speechSynthesis.speak(u);
    setPlaying(true);
  }

  function play() {
    if (kind === "file" && audio) { var p = audio.play(); if (p && p.catch) p.catch(function () { setPlaying(false); }); }
    else if (kind === "speech") { speak(speechPos >= text.length - 1 ? 0 : speechPos); }
  }
  function pause() {
    if (kind === "file" && audio) { audio.pause(); setPlaying(false); }
    else if (kind === "speech") { global.speechSynthesis.cancel(); setPlaying(false); }  // cancel + resume-from-position is more reliable than pause()
  }
  function restart() {
    if (kind === "file" && audio) { audio.currentTime = 0; play(); }
    else if (kind === "speech") { speechPos = 0; speak(0); }
  }
  function stop() {
    if (audio) { audio.pause(); audio.removeAttribute("src"); audio.load(); audio = null; }
    if (canSpeak) global.speechSynthesis.cancel();
    kind = null; speechPos = 0; setPlaying(false); progress(0);
  }

  var Voice = {
    init: function (course) {
      C = course;
      mode = (C.voiceover && C.voiceover.mode) || "off";
      if (mode === "off") return;
      load(); build();
      global.addEventListener("pagehide", stop);
    },
    show: function (s) {
      if (mode === "off" || !bar) return;
      stop();
      text = s && typeof s.narration === "string" ? s.narration.trim() : "";
      var file = mode === "audio" && s && s.audio && s.audio.src;
      if (!text && !file) { bar.hidden = capBox.hidden = true; return; }
      // Recorded courses never fall back to the device voice: a second, different voice mid-course is jarring.
      if (file) useFile(s.audio.src, s.audio.duration);
      else if (mode === "device" && canSpeak && text) { kind = "speech"; progress(0); }
      else kind = null;
      bar.hidden = false;
      setUnavailable(!kind);
      capBox.textContent = text;
      capBox.hidden = !prefs.cc || !text;
      if (kind && prefs.auto && activated) play();
    },
    stop: function () { if (bar) stop(); }
  };
  global.Voice = Voice;
})(window);
