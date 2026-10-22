(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var frame = $("frame"), fileInput = $("file"), form = $("form");
  var cfg = { providers: [], max_mb: 50, daily_limit: 10 };
  var file = null, poll = null;

  function setState(s) {
    frame.dataset.state = s;
    frame.className = "frame" + ({ file: " is-file", busy: " is-busy", done: " is-done" }[s] || "");
    $("settings").classList.toggle("is-hidden", s === "busy" || s === "done");
    $("actions-start").classList.toggle("is-hidden", s === "busy" || s === "done");
    $("actions-done").classList.toggle("is-hidden", s !== "done");
    $("summary").classList.toggle("is-hidden", s !== "done");
    $("warn").classList.toggle("is-hidden", s !== "done" || !$("warn").textContent);
    $("convert").disabled = !file;
  }

  function fmtSize(b) { return b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB"; }

  function chooseFile(f) {
    if (!f) return;
    if (!/\.pptx$/i.test(f.name)) { showError("Please choose a PowerPoint .pptx file. Older .ppt files must be saved as .pptx first."); return; }
    if (f.size > cfg.max_mb * 1048576) { showError("That file is " + fmtSize(f.size) + ". The limit is " + cfg.max_mb + " MB."); return; }
    file = f;
    $("file-name").textContent = f.name;
    $("file-size").textContent = fmtSize(f.size);
    setState("file");
    $("frame-note").textContent = "";
  }

  function showError(msg) {
    clearInterval(poll);
    $("error-text").textContent = msg;
    setState("error");
  }

  // file picking: click, keyboard, drag and drop
  $("drop").addEventListener("click", function () { fileInput.click(); });
  $("drop").addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
  $("change-file").addEventListener("click", function () { fileInput.click(); });
  fileInput.addEventListener("change", function () { chooseFile(fileInput.files[0]); fileInput.value = ""; });
  ["dragenter", "dragover"].forEach(function (ev) {
    frame.addEventListener(ev, function (e) {
      if (frame.dataset.state === "busy" || frame.dataset.state === "done") return;
      e.preventDefault(); frame.classList.add("is-over");
    });
  });
  ["dragleave", "drop"].forEach(function (ev) { frame.addEventListener(ev, function () { frame.classList.remove("is-over"); }); });
  frame.addEventListener("drop", function (e) {
    if (frame.dataset.state === "busy" || frame.dataset.state === "done") return;
    e.preventDefault(); chooseFile(e.dataTransfer.files[0]);
  });
  $("error-back").addEventListener("click", function () { setState(file ? "file" : "empty"); });

  // settings
  function mode() { return form.querySelector("input[name=mode]:checked").value; }
  function providerInfo() { return cfg.providers.filter(function (p) { return p.id === $("provider").value; })[0]; }
  function narration() { return form.querySelector("input[name=narration]:checked").value; }
  function refreshSettings() {
    var ai = mode() === "ai", nar = narration();
    document.querySelectorAll(".ai-only").forEach(function (el) { el.classList.toggle("is-hidden", !ai && nar !== "audio"); });
    $("instructions").closest(".field").classList.toggle("is-hidden", !ai);
    $("voice-field").classList.toggle("is-hidden", nar !== "audio");
    var ph = form.querySelector("input[name=photos]:checked").value;
    $("pexels-field").classList.toggle("is-hidden", ph !== "pexels" || cfg.photos_server_key);
    $("photo-help").textContent = ph === "pexels" ? "The AI picks moments that deserve a photo; matching free photos from Pexels are added with credits."
      : "Infographics and icons are always included. Add real-world photos too?";
    $("narr-help").textContent = { off: "No voice-over.",
      audio: "Records a natural voice-over for every screen with Google Gemini. Adds a minute or two." +
        (cfg.tts_server_key ? "" : " Needs a Gemini API key."),
      device: "No recording: the learner's computer reads each screen aloud. Quality depends on their device." }[nar];
    $("mode-help").textContent = ai ? "Redesigns the content into interactions and writes the quiz."
      : "No AI: one screen per slide and a simple recall quiz. Nothing leaves this server.";
    var p = providerInfo();
    if (p) {
      $("provider-help").textContent = p.server_key
        ? "Free to try here, up to " + cfg.daily_limit + " AI conversions a day. Add your own key for more."
        : "No shared key for this provider. Paste your own API key.";
      if (ai && !p.server_key && !$("api_key").value.trim()) $("frame-note").textContent = "";
    }
  }
  form.addEventListener("change", refreshSettings);
  $("brand-pick").addEventListener("input", function () { $("brand-text").value = this.value; });
  $("brand-text").addEventListener("input", function () { if (/^#[0-9a-f]{6}$/i.test(this.value)) $("brand-pick").value = this.value; });

  window.__chosenFile = function () { return file; };
  fetch("/api/config").then(function (r) { return r.json(); }).then(function (c) {
    cfg = c; window.__cfg = c;
    if (c.signed_in) $("signout").classList.remove("is-hidden");
    $("max-note").textContent = "Up to " + c.max_mb + " MB";
    c.providers.forEach(function (p) {
      var o = document.createElement("option"); o.value = p.id;
      o.textContent = p.label + (p.server_key ? "" : " (needs your key)");
      $("provider").appendChild(o);
    });
    $("provider").value = c.default_provider;
    (c.voices || []).forEach(function (v) {
      var o = document.createElement("option"); o.value = v.id; o.textContent = v.label; $("voice").appendChild(o);
    });
    if (c.default_voice) $("voice").value = c.default_voice;
    var p = providerInfo();
    if (p && !p.server_key) document.querySelector("details.adv").open = true;
    refreshSettings();
  });

  // convert
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (!file) return;
    var brand = $("brand-text").value.trim();
    if (brand && !/^#[0-9a-f]{6}$/i.test(brand)) { $("frame-note").textContent = "Brand colour must look like #1f5eff."; $("brand-text").focus(); return; }
    var p = providerInfo();
    if (mode() === "ai" && p && !p.server_key && !$("api_key").value.trim()) {
      document.querySelector("details.adv").open = true;
      $("frame-note").textContent = "Paste an API key for " + p.label + ", or choose Basic.";
      $("api_key").focus(); return;
    }
    if (narration() === "audio" && !cfg.tts_server_key && !($("provider").value === "gemini" && $("api_key").value.trim())) {
      document.querySelector("details.adv").open = true;
      $("frame-note").textContent = "AI voice needs a Google Gemini key: choose Google Gemini as the provider and paste your key, or choose Device voice.";
      $("provider").focus(); return;
    }
    var ph = form.querySelector("input[name=photos]:checked").value;
    if (ph === "pexels" && !cfg.photos_server_key && !$("pexels_key").value.trim()) {
      $("frame-note").textContent = "Paste a free Pexels API key for stock photos, or choose None.";
      $("pexels_key").focus(); return;
    }
    var fd = new FormData();
    fd.append("photos", ph);
    fd.append("pexels_key", $("pexels_key").value.trim());
    fd.append("narration", narration());
    fd.append("voice", $("voice").value);
    fd.append("file", file);
    fd.append("mode", mode());
    fd.append("provider", $("provider").value || "gemini");
    fd.append("api_key", $("api_key").value.trim());
    fd.append("brand", window.__brandChoice && window.__brandChoice() !== "standard" ? "" : brand);
    fd.append("brand_kit", window.__brandChoice ? window.__brandChoice() : "standard");
    fd.append("instructions", $("instructions").value);
    markSteps("extract", {});
    $("sub-extract").textContent = ""; $("sub-design").textContent = ""; $("sub-narrate").textContent = "";
    $("step-narrate").classList.toggle("is-hidden", narration() !== "audio");
    $("step-photos").classList.toggle("is-hidden", form.querySelector("input[name=photos]:checked").value !== "pexels");
    $("sub-photos").textContent = "";
    $("frame-note").textContent = "Uploading " + file.name + "…";
    setState("busy");
    fetch("/api/convert", { method: "POST", body: fd })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.detail || "Upload failed."); return j; }); })
      .then(function (j) { $("frame-note").textContent = ""; track(j.job_id); })
      .catch(function (err) { $("frame-note").textContent = ""; showError(err.message || "Upload failed. Check your connection and try again."); });
  });

  function markSteps(current, j) {
    var order = ["extract", "design", "photos", "narrate", "package"], idx = order.indexOf(current);
    if (current === "done") idx = 5;
    document.querySelectorAll("#steps li").forEach(function (li) {
      var i = order.indexOf(li.dataset.step);
      li.classList.toggle("ok", i < idx); li.classList.toggle("now", i === idx);
      if (i === idx) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
    });
    if (j.slides) $("sub-extract").textContent = j.slides + " slides";
    if (current === "photos" && j.photo_total) $("sub-photos").textContent = "Screen " + Math.min(j.photo_done + 1, j.photo_total) + " of " + j.photo_total;
    if (current === "narrate" && j.narr_total) $("sub-narrate").textContent = "Screen " + Math.min(j.narr_done + 1, j.narr_total) + " of " + j.narr_total;
    if (current === "design") {
      var s = j.design_seconds || 0;
      $("sub-design").textContent = (j.mode === "ai" ? "Writing interactions and quiz with " + j.provider : "Building screens and quiz") +
        (s > 4 ? " (" + s + "s)" : "") + (j.mode === "ai" && s > 25 ? ". Large decks can take a minute or two." : "");
    }
  }

  function track(id) {
    var fails = 0;
    clearInterval(poll);
    poll = setInterval(function () {
      fetch("/api/jobs/" + id).then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.detail); return j; }); })
        .then(function (j) {
          fails = 0;
          if (j.status === "error") { showError(j.error || "Something went wrong while converting."); return; }
          markSteps(j.step, j);
          if (j.status === "done") { clearInterval(poll); finish(id, j); }
        })
        .catch(function (err) { if (++fails > 4) showError(err.message || "Lost contact with the server. Try again."); });
    }, 1200);
  }

  var LABELS = { intro: ["intro", "intros"], content: ["content screen", "content screens"], flipcards: ["flip-card set", "flip-card sets"],
    reveal: ["click-to-reveal list", "click-to-reveal lists"], match: ["matching exercise", "matching exercises"],
    scenario: ["scenario", "scenarios"], summary: ["recap", "recaps"], mcq: ["quiz question", "quiz questions"],
    infographic: ["infographic", "infographics"], section: ["module", "modules"], sort: ["sorting activity", "sorting activities"] };

  function finish(id, j) {
    currentJob = id;
    $("preview").src = "/preview/" + id + "/index.html";
    $("dl-zip").href = "/api/jobs/" + id + "/download";
    $("dl-json").href = "/api/jobs/" + id + "/course.json";
    var parts = Object.keys(j.summary || {}).filter(function (k) { return k !== "intro" && k !== "summary"; })
      .map(function (k) { var n = j.summary[k]; return n + " " + (LABELS[k] ? LABELS[k][n === 1 ? 0 : 1] : k); });
    $("summary").innerHTML = "";
    var strong = document.createElement("strong"); strong.textContent = j.title;
    $("summary").appendChild(strong);
    $("summary").appendChild(document.createTextNode(": " + j.screens + " screens built by " + j.how + ", including " + parts.join(", ") +
      ". The package is " + j.size_kb + " KB. Click through the preview above, then download it."));
    $("warn").textContent = (j.warnings || []).length ? "Note: " + j.warnings.join("; ") : "";
    if (j.brand_name) $("summary").appendChild(document.createTextNode(" Branding: " + j.brand_name +
      (j.brand_detected ? " (detected from the deck; save it as a kit under Review and edit, Course settings)." : ".")));
    setState("done");
  }

  var currentJob = null;
  $("edit").addEventListener("click", function () {
    CourseEditor.open(currentJob, function (changed) {
      if (!changed) { $("edit").focus(); return; }
      fetch("/api/jobs/" + currentJob).then(function (r) { return r.json(); }).then(function (j) {
        finish(currentJob, j);
        $("preview").src = "/preview/" + currentJob + "/index.html?v=" + j.version;
        $("summary").appendChild(document.createTextNode(" Includes your edits."));
        $("edit").focus();
      });
    }, { geminiKey: function () { return $("provider").value === "gemini" ? $("api_key").value.trim() : ""; },
         pexelsKey: function () { return $("pexels_key").value.trim(); } });
  });

  $("again").addEventListener("click", function () {
    file = null; $("preview").src = "about:blank"; $("warn").textContent = "";
    setState("empty"); $("drop").focus();
  });
})();
