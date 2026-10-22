/* Course editor: review and fix the AI draft, then rebuild the SCORM package.
   Usage: CourseEditor.open(jobId, onClose)  -- onClose(changed) is called when the person clicks Done. */
(function () {
  "use strict";

  /* What each screen type looks like in the editor. Field kinds:
     text, textarea, list (lines of text), records (rows with named cells), options (mcq answers), check */
  var TYPES = {
    intro: { label: "Welcome", fields: [
      { k: "title", label: "Course title on the welcome screen", kind: "text", req: true },
      { k: "subtitle", label: "Subtitle", kind: "text" },
      { k: "objectives", label: "Learning objectives", kind: "list", req: true, add: "Add an objective" },
      { k: "image", label: "Image", kind: "image" }] },
    content: { label: "Content", fields: [
      { k: "title", label: "Heading", kind: "text", req: true },
      { k: "icon", label: "Icon", kind: "icon" },
      { k: "body", label: "Main text", kind: "textarea", req: true, help: "Keep it short: about 60 words reads well on one screen." },
      { k: "bullets", label: "Bullet points", kind: "list", add: "Add a bullet point" },
      { k: "callout", label: "Key message", kind: "text", help: "Optional. Shown in a highlighted box." },
      { k: "image", label: "Image", kind: "image" }] },
    flipcards: { label: "Flip cards", fields: [
      { k: "title", label: "Heading", kind: "text", req: true },
      { k: "icon", label: "Icon", kind: "icon" },
      { k: "instruction", label: "Instruction", kind: "text" },
      { k: "cards", label: "Cards", kind: "records", req: true, min: 1, two: true, add: "Add a card",
        cells: [{ k: "front", label: "Front" }, { k: "back", label: "Back", area: true }] },
      { k: "image", label: "Image", kind: "image" }] },
    reveal: { label: "Click to reveal", fields: [
      { k: "title", label: "Heading", kind: "text", req: true },
      { k: "icon", label: "Icon", kind: "icon" },
      { k: "instruction", label: "Instruction", kind: "text" },
      { k: "items", label: "Items", kind: "records", req: true, min: 1, add: "Add an item",
        cells: [{ k: "label", label: "Label" }, { k: "detail", label: "Shown when opened", area: true }] },
      { k: "image", label: "Image", kind: "image" }] },
    match: { label: "Matching", fields: [
      { k: "title", label: "Heading", kind: "text", req: true },
      { k: "instruction", label: "Instruction", kind: "text" },
      { k: "pairs", label: "Pairs", kind: "records", req: true, min: 2, two: true, add: "Add a pair",
        help: "Learners see the right-hand column shuffled.",
        cells: [{ k: "left", label: "Item" }, { k: "right", label: "Matches" }] }] },
    scenario: { label: "Scenario", fields: [
      { k: "title", label: "Heading", kind: "text", req: true },
      { k: "icon", label: "Icon", kind: "icon" },
      { k: "situation", label: "Situation", kind: "textarea", req: true },
      { k: "choices", label: "Choices", kind: "records", req: true, min: 2, correct: "radio", add: "Add a choice",
        help: "Mark the best response. Learners keep trying until they find it.",
        cells: [{ k: "text", label: "Choice" }, { k: "feedback", label: "Feedback after choosing it", area: true }] },
      { k: "image", label: "Image", kind: "image" }] },
    summary: { label: "Recap", fields: [
      { k: "title", label: "Heading", kind: "text", req: true },
      { k: "icon", label: "Icon", kind: "icon" },
      { k: "points", label: "Key points", kind: "list", req: true, add: "Add a point" }] },
    section: { label: "Module divider", fields: [
      { k: "title", label: "Module title", kind: "text", req: true },
      { k: "subtitle", label: "One-line promise", kind: "text", help: "What the learner gets from this module." },
      { k: "icon", label: "Icon", kind: "icon", help: "Shown large on the divider (a photo replaces it)." },
      { k: "image", label: "Photo", kind: "image" }] },
    infographic: { label: "Infographic", fields: [
      { k: "title", label: "Heading", kind: "text", req: true },
      { k: "intro", label: "Intro line", kind: "text" },
      { k: "layout", label: "Layout", kind: "select", req: true, options: [["process", "Process: numbered steps"], ["timeline", "Timeline: phases or dates"],
        ["stats", "Stats: big numbers"], ["comparison", "Comparison: do vs don't (2 columns)"], ["cycle", "Cycle: a repeating loop"],
        ["pyramid", "Pyramid: most important at the top"], ["grid", "Grid: key ideas with icons"], ["checklist", "Checklist: learners tick each item"]] },
      { k: "parts", label: "Parts", kind: "records", req: true, min: 2, max: 8, add: "Add a part",
        help: "2 to 8 parts. Short is better: a title of a few words and one line of text.",
        cells: [
          { k: "value", label: "Number or label", opt: true, show: function (x) { return x.layout === "stats" || x.layout === "timeline"; },
            reqIf: function (x) { return x.layout === "stats"; }, placeholder: "e.g. 50% or Day 1" },
          { k: "title", label: "Title" },
          { k: "text", label: "Text", area: true, opt: true, labelFor: function (x) { return x.layout === "comparison" ? "Points (one per line)" : "Text"; } },
          { k: "icon", label: "Icon", kind: "icon", opt: true },
          { k: "tone", label: "Column style", kind: "select", opt: true, show: function (x) { return x.layout === "comparison"; },
            options: [["", "Neutral"], ["good", "Right way (green)"], ["bad", "Wrong way (red)"]] }] }] },
    sort: { label: "Sort activity", fields: [
      { k: "title", label: "Heading", kind: "text", req: true },
      { k: "instruction", label: "Instruction", kind: "text" },
      { k: "buckets", label: "Groups (2 or 3)", kind: "list", req: true, add: "Add a group" },
      { k: "terms", label: "Items to sort", kind: "records", req: true, min: 3, max: 12, add: "Add an item", two: true,
        cells: [{ k: "text", label: "Item" },
          { k: "bucket", label: "Belongs in", kind: "select", num: true,
            options: function (x) { return (x.buckets || []).map(function (b, i) { return [i, b || "Group " + (i + 1)]; }); } }] }] },
    mcq: { label: "Quiz question", fields: [
      { k: "question", label: "Question", kind: "textarea", req: true },
      { k: "image", label: "Image", kind: "image" },
      { k: "options", label: "Answers", kind: "options", req: true, add: "Add an answer", help: "Select the correct answer." },
      { k: "explanation", label: "Explanation shown after answering", kind: "textarea", req: true },
      { k: "scored", label: "Counts toward the score", kind: "check" }] }
  };
  var NEW = {
    intro: function () { return { type: "intro", title: "", subtitle: "", objectives: [""] }; },
    content: function () { return { type: "content", title: "", body: "", bullets: [] }; },
    flipcards: function () { return { type: "flipcards", title: "", instruction: "Flip each card to check.", cards: [{ front: "", back: "" }, { front: "", back: "" }] }; },
    reveal: function () { return { type: "reveal", title: "", instruction: "Select each item to learn more.", items: [{ label: "", detail: "" }, { label: "", detail: "" }] }; },
    match: function () { return { type: "match", title: "", instruction: "Match each item on the left to one on the right.", pairs: [{ left: "", right: "" }, { left: "", right: "" }] }; },
    scenario: function () { return { type: "scenario", title: "", situation: "", choices: [{ text: "", correct: true, feedback: "" }, { text: "", correct: false, feedback: "" }] }; },
    summary: function () { return { type: "summary", title: "Key takeaways", points: [""] }; },
    mcq: function () { return { type: "mcq", question: "", options: ["", ""], correct: 0, explanation: "", scored: true }; },
    section: function () { return { type: "section", title: "", subtitle: "", icon: "flag" }; },
    infographic: function () { return { type: "infographic", title: "", layout: "process", parts: [{ title: "", icon: "circle-check" }, { title: "", icon: "circle-check" }, { title: "", icon: "circle-check" }] }; },
    sort: function () { return { type: "sort", title: "", instruction: "Sort each item into the right group.", buckets: ["", ""], terms: [{ text: "", bucket: 0 }, { text: "", bucket: 1 }, { text: "", bucket: 0 }] }; }
  };
  function iconOk(n) { return !n || !!(window.ICONS && window.ICONS[n]); }
  function filledVal(v) { return typeof v === "number" || (typeof v === "string" && v.trim() !== ""); }
  function shownCells(f, scr) { return f.cells.filter(function (c) { return !c.show || c.show(scr); }); }

  var S = { media: [], picking: false, cfg: null, key: "", hostKey: null, needsAudio: 0, recording: false, ttsServerKey: false, voPoll: null, id: null, course: null, brand: "#1f5eff", sel: "course", dirty: false, changed: false, version: 1, onClose: null, previewTimer: null };
  var $ = function (id) { return document.getElementById(id); };
  var savedNarr = new WeakMap();   // screen -> narration text as last saved (to spot audio that will need re-recording)
  var MAX_NARR = 1500;

  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") n.textContent = attrs[k];
      else if (k === "class") n.className = attrs[k];
      else if (k.indexOf("on") === 0) n.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] !== false && attrs[k] != null) n.setAttribute(k, attrs[k] === true ? "" : attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c != null) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return n;
  }
  function blank(v) { return typeof v !== "string" || !v.trim(); }

  /* ---------- validation (mirrors ppt2scorm/schema.py so problems show before saving) ---------- */
  function problems() {
    var out = [], c = S.course;
    if (blank(c.title)) out.push({ s: "course", f: "title", m: "The course needs a title." });
    if (!(c.mastery_score >= 0 && c.mastery_score <= 100)) out.push({ s: "course", f: "mastery_score", m: "Use a number from 0 to 100." });
    if (!(c.est_minutes >= 1 && c.est_minutes <= 600)) out.push({ s: "course", f: "est_minutes", m: "Use a number from 1 to 600." });
    if (!c.screens.length) out.push({ s: "course", f: "screens", m: "Add at least one screen." });
    c.screens.forEach(function (scr, i) {
      if (typeof scr.narration === "string" && scr.narration.length > MAX_NARR) out.push({ s: i, f: "narration", m: "Keep the script under " + MAX_NARR + " characters." });
      TYPES[scr.type].fields.forEach(function (f) {
        var v = scr[f.k], add = function (m, row) { out.push({ s: i, f: f.k, r: row, m: m }); };
        if (f.kind === "image") { if (v && v.src && blank(v.alt)) add("Describe the image for people who can't see it."); }
        else if (f.kind === "icon") { if (!iconOk(v)) add("Pick an icon from the list, or clear the box."); }
        else if (f.kind === "select") { if (f.req && !filledVal(v)) add("Choose one."); }
        else if (f.kind === "text" || f.kind === "textarea") { if (f.req && blank(v)) add("Fill this in."); }
        else if (f.kind === "list" || f.kind === "options") {
          var filled = (v || []).filter(function (x) { return !blank(x); });
          if (f.req && !filled.length) add("Add at least one line.");
          if (f.kind === "options") {
            if (filled.length < 2) add("Add at least two answers.");
            if (blank((v || [])[scr.correct])) add("Select the correct answer, and make sure it has text.");
          }
        } else if (f.kind === "records") {
          var cells = shownCells(f, scr), textCells = cells.filter(function (c2) { return c2.kind !== "select" && c2.kind !== "icon"; });
          var rows = (v || []).filter(function (r) { return textCells.some(function (c2) { return filledVal(r[c2.k]); }); });
          if (rows.length < (f.min || 1)) add(f.min > 1 ? "Add at least " + f.min + " filled-in rows." : "Add at least one row.");
          if (f.max && rows.length > f.max) add("Use at most " + f.max + ".");
          (v || []).forEach(function (r, n) {
            var some = textCells.some(function (c2) { return filledVal(r[c2.k]); });
            var need = f.correct ? ["text"] : cells.filter(function (c2) { return !c2.opt || (c2.reqIf && c2.reqIf(scr)); }).map(function (c2) { return c2.k; });
            if (some && need.some(function (k) { return !filledVal(r[k]); })) add(f.correct ? "This choice needs text." : "Fill in the required parts.", n);
            if (some && cells.some(function (c2) { return c2.kind === "icon" && !iconOk(r[c2.k]); })) add("Pick an icon from the list, or clear it.", n);
          });
          if (f.correct && !(v || []).some(function (r) { return r.correct && !blank(r.text); })) add("Mark the best response.");
        }
      });
      if (scr.type === "infographic" && scr.layout === "comparison" && (scr.parts || []).length !== 2) out.push({ s: i, f: "parts", m: "A comparison has exactly 2 columns." });
      if (scr.type === "sort") {
        var nb = (scr.buckets || []).filter(function (b) { return !blank(b); }).length;
        if (nb < 2 || nb > 3) out.push({ s: i, f: "buckets", m: "Use 2 or 3 groups." });
      }
    });
    return out;
  }

  /* drop blank lines/rows and empty optional text before saving */
  function cleaned() {
    var c = JSON.parse(JSON.stringify(S.course));
    c.title = (c.title || "").trim();
    c.screens = c.screens.map(function (scr) {
      if (typeof scr.narration === "string") { scr.narration = scr.narration.trim(); if (!scr.narration) delete scr.narration; }
      TYPES[scr.type].fields.forEach(function (f) {
        var v = scr[f.k];
        if (f.kind === "image") {
          if (v && v.src) {
            var img = { src: v.src, alt: (v.alt || "").trim() };
            if (v.credit) { img.credit = v.credit; if (v.credit_url) img.credit_url = v.credit_url; }
            scr.image = img;
          } else delete scr.image;
        } else if (f.kind === "icon") {
          if (blank(v)) delete scr[f.k]; else scr[f.k] = v.trim();
        } else if (f.kind === "select") {
          if (!filledVal(v)) delete scr[f.k];
        } else if (f.kind === "text" || f.kind === "textarea") {
          if (typeof v === "string") scr[f.k] = v.trim();
          if (!f.req && blank(scr[f.k])) delete scr[f.k];
        } else if (f.kind === "list") {
          scr[f.k] = (v || []).map(function (x) { return x.trim(); }).filter(Boolean);
          if (!f.req && !scr[f.k].length) delete scr[f.k];
        } else if (f.kind === "options") {
          var correctText = (v || [])[scr.correct];
          var kept = (v || []).map(function (x) { return x.trim(); }).filter(Boolean);
          scr.options = kept;
          scr.correct = Math.max(0, kept.indexOf((correctText || "").trim()));
        } else if (f.kind === "records") {
          var cs = shownCells(f, scr), tc = cs.filter(function (c2) { return c2.kind !== "select" && c2.kind !== "icon"; });
          scr[f.k] = (v || []).filter(function (r) { return tc.some(function (c2) { return filledVal(r[c2.k]); }); })
            .map(function (r) {
              var o = {};
              cs.forEach(function (c2) {
                var val = r[c2.k];
                if (c2.num) { o[c2.k] = Number(val) || 0; return; }
                val = typeof val === "string" ? val.trim() : "";
                if (val || !c2.opt) o[c2.k] = val;
              });
              if (f.correct) o.correct = !!r.correct;
              return o;
            });
        }
      });
      return scr;
    });
    return c;
  }

  /* ---------- outline ---------- */
  function nameOf(scr) { return (scr.title || scr.question || "").trim() || "Untitled"; }
  function typeLabel(scr) {
    if (scr.type === "mcq" && scr.scored === false) return "Practice question";
    if (scr.type === "infographic" && scr.layout) return "Infographic: " + scr.layout.charAt(0).toUpperCase() + scr.layout.slice(1);
    return TYPES[scr.type] ? TYPES[scr.type].label : scr.type;
  }

  function renderOutline() {
    var errs = problems(), bad = {};
    errs.forEach(function (e) { bad[e.s] = true; });
    $("ed-course").setAttribute("aria-current", S.sel === "course" ? "true" : "false");
    $("ed-course").textContent = "Course settings" + (bad.course ? " (needs attention)" : "");
    var list = $("ed-list");
    list.innerHTML = "";
    S.course.screens.forEach(function (scr, i) {
      var li = el("li", { class: "ed-item" + (bad[i] ? " has-error" : ""), "aria-current": S.sel === i ? "true" : "false" }, [
        el("button", { type: "button", class: "ed-pick", onclick: function () { select(i); } }, [
          el("span", { class: "ed-type", text: typeLabel(scr) }), el("span", { class: "ed-name", text: nameOf(scr) })]),
        el("span", { class: "ed-tools" }, [
          el("button", { type: "button", title: "Move up", "aria-label": "Move screen " + (i + 1) + " up", disabled: i === 0, onclick: function () { move(i, -1); }, text: "↑" }),
          el("button", { type: "button", title: "Move down", "aria-label": "Move screen " + (i + 1) + " down", disabled: i === S.course.screens.length - 1, onclick: function () { move(i, 1); }, text: "↓" }),
          el("button", { type: "button", title: "Delete", "aria-label": "Delete screen " + (i + 1), onclick: function () { remove(i); }, text: "✕" })])
      ]);
      list.appendChild(li);
    });
    var n = errs.length;
    if (!S.saving) status(S.dirty ? "Unsaved changes" + (n ? ". " + n + (n === 1 ? " thing needs" : " things need") + " fixing before you can save." : ".") :
      (S.changed ? "All changes saved. Downloads include your edits." : "Showing the AI draft. Changes are saved when you click Save and preview."), S.dirty ? "dirty" : "");
    $("ed-save").disabled = !S.dirty || n > 0 || S.recording;
    var rb = $("ed-audio");
    rb.classList.toggle("is-hidden", !(voMode() === "audio" && (S.needsAudio || S.recording)));
    rb.textContent = S.recording ? "Recording audio…" : "Record audio (" + S.needsAudio + ")";
  }

  function status(text, kind) {
    var s = $("ed-status");
    s.textContent = text;
    s.className = "ed-status" + (kind === "dirty" ? " is-dirty" : kind === "error" ? " is-error" : "");
  }

  function touch() { S.dirty = true; renderOutline(); showErrors(); }

  function move(i, d) {
    var a = S.course.screens, t = a[i]; a[i] = a[i + d]; a[i + d] = t;
    if (S.sel === i) S.sel = i + d; else if (S.sel === i + d) S.sel = i;
    touch(); renderForm();
  }
  function remove(i) {
    if (!window.confirm("Delete screen " + (i + 1) + " (" + nameOf(S.course.screens[i]) + ")?")) return;
    S.course.screens.splice(i, 1);
    if (S.sel === i) S.sel = S.course.screens.length ? Math.min(i, S.course.screens.length - 1) : "course";
    else if (typeof S.sel === "number" && S.sel > i) S.sel--;
    touch(); renderForm();
  }
  function addScreen() {
    var t = $("ed-newtype").value, at = typeof S.sel === "number" ? S.sel + 1 : S.course.screens.length;
    S.course.screens.splice(at, 0, NEW[t]());
    S.sel = at; touch(); renderForm();
    var first = $("ed-form").querySelector("input, textarea"); if (first) first.focus();
  }
  function select(i) { S.sel = i; S.picking = false; renderOutline(); renderForm(); schedulePreview(); }

  /* ---------- form ---------- */
  function mediaUrl(src) { return "/api/jobs/" + S.id + "/media/" + encodeURIComponent(src.split("/").pop()); }
  function mediaLabel(m) {
    if (m.kind === "upload") return "Uploaded: " + (m.slide_title || "image");
    if (m.kind === "photo") return m.credit || "Stock photo";
    return "Slide " + m.slide + (m.kind === "slide" ? ", " + (m.reason || "diagram") : ", picture") + (m.slide_title ? ": " + m.slide_title : "");
  }

  function imageField(scr) {
    var wrap = el("div", { class: "ed-field", "data-k": "image" });
    var redraw = function () { var fresh = imageField(scr); wrap.replaceWith(fresh); showErrors(); return fresh; };
    wrap.appendChild(el("span", { class: "ed-label", text: "Image (optional)" }));
    var img = scr.image;
    if (img && img.src) {
      var alt = el("textarea", { id: "ed-f-image-alt", rows: 2, maxlength: 300 });
      alt.value = img.alt || "";
      alt.addEventListener("input", function () { img.alt = alt.value; touch(); });
      wrap.appendChild(el("div", { class: "ed-img" }, [
        el("img", { src: mediaUrl(img.src), alt: "" }),
        el("div", { class: "ed-img-side" }, [
          el("label", { for: "ed-f-image-alt", text: "Description for people who can't see it" }), alt,
          img.credit ? el("p", { class: "help", text: img.credit }) : null,
          el("div", { class: "ed-img-actions" }, [
            el("button", { type: "button", class: "ed-addrow", text: "Change image", onclick: function () { S.picking = true; redraw(); } }),
            el("button", { type: "button", class: "ed-del", text: "Remove image", onclick: function () { delete scr.image; S.picking = false; touch(); redraw(); } })])])]));
    } else if (!S.picking) {
      wrap.appendChild(el("button", { type: "button", class: "ed-addrow", text: "+ Add an image", onclick: function () { S.picking = true; redraw(); } }));
    }
    if (S.picking) wrap.appendChild(picker(scr, redraw));
    wrap.appendChild(el("span", { class: "ed-err", id: "ed-err-image", role: "status" }));
    return wrap;
  }

  function picker(scr, redraw) {
    var box = el("div", { class: "ed-picker" });
    var note = el("p", { class: "help", role: "status" });
    var grid = el("div", { class: "ed-gallery" }, S.media.map(function (m) {
      return el("button", { type: "button", class: "ed-thumb" + (scr.image && scr.image.src === m.src ? " is-on" : ""),
        "aria-label": "Use image: " + mediaLabel(m), onclick: function () {
          var keep = scr.image && scr.image.src === m.src ? scr.image.alt : "";
          scr.image = { src: m.src, alt: keep || m.alt || (m.kind === "slide" ? "" : "") };
          S.picking = false; touch(); var f = redraw(); var a = f.querySelector("textarea"); if (a) a.focus();
        } }, [el("img", { src: mediaUrl(m.src), alt: "", loading: "lazy" }), el("span", { text: mediaLabel(m) })]);
    }));
    var up = el("input", { type: "file", accept: "image/png,image/jpeg,image/gif,image/webp", class: "ed-upload-input", id: "ed-upload" });
    up.addEventListener("change", function () {
      var file = up.files[0]; if (!file) return;
      note.textContent = "Uploading " + file.name + "…";
      var fd = new FormData(); fd.append("file", file);
      fetch("/api/jobs/" + S.id + "/media", { method: "POST", body: fd })
        .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.detail || "Upload failed."); return j; }); })
        .then(function (m) {
          S.media = S.media.filter(function (x) { return x.src !== m.src; }).concat([m]);
          scr.image = { src: m.src, alt: "" }; S.picking = false; touch();
          var f = redraw(); var a = f.querySelector("textarea"); if (a) a.focus();
        })
        .catch(function (e) { note.textContent = e.message; });
    });
    box.appendChild(el("p", { class: "ed-picker-head", text: S.media.length ? "Choose an image from your deck, or upload one." : "This deck has no usable images. Upload one." }));
    if (S.media.length) box.appendChild(grid);
    box.appendChild(el("div", { class: "ed-img-actions" }, [
      el("label", { class: "ed-addrow ed-upload", for: "ed-upload", text: "Upload an image" }), up,
      el("button", { type: "button", class: "ed-del", text: "Cancel", onclick: function () { S.picking = false; redraw(); } })]));
    box.appendChild(el("p", { class: "help", text: "PNG, JPG, GIF or WebP, up to 8 MB. Large images are resized automatically." }));
    box.appendChild(note);
    box.appendChild(stockPanel(scr, redraw));
    return box;
  }

  function pexelsKey() {
    return S.pexKey || (S.hostPexels ? S.hostPexels() : "");
  }

  function stockPanel(scr, redraw) {
    var wrap = el("div", { class: "ed-stock" });
    wrap.appendChild(el("p", { class: "ed-picker-head", text: "Or search free stock photos" }));
    var q = el("input", { type: "search", id: "ed-stock-q", placeholder: "e.g. technician repairing washing machine", "aria-label": "Search stock photos" });
    q.value = S.stockQ || scr.photo_query || scr.title || "";
    var keyRow = null, keyIn = null;
    if (!(S.cfg && S.cfg.photos_server_key) && !pexelsKey()) {
      keyIn = el("input", { type: "password", id: "ed-stock-key", autocomplete: "off", placeholder: "Pexels API key", "aria-label": "Pexels API key" });
      keyRow = el("div", { class: "ed-stock-key" }, [
        el("label", { for: "ed-stock-key", text: "Pexels API key (free at pexels.com/api; kept only in this tab)" }), keyIn]);
    }
    var results = el("div", { class: "ed-gallery ed-stock-results" });
    var msg = el("p", { class: "help", role: "status" });
    function run() {
      if (keyIn && keyIn.value.trim()) S.pexKey = keyIn.value.trim();
      var query = q.value.trim(); S.stockQ = query;
      if (!query) { msg.textContent = "Type what the photo should show."; return; }
      msg.textContent = "Searching…"; results.innerHTML = "";
      api("POST", "/api/jobs/" + S.id + "/photos/search", { query: query, api_key: pexelsKey() }).then(function (j) {
        msg.textContent = j.photos.length ? "Photos from Pexels. The photographer is credited on the screen." : "No photos found. Try simpler words.";
        j.photos.forEach(function (ph) {
          results.appendChild(el("button", { type: "button", class: "ed-thumb", "aria-label": "Use photo: " + (ph.alt || query) + " by " + ph.photographer,
            onclick: function () {
              msg.textContent = "Adding the photo…";
              api("POST", "/api/jobs/" + S.id + "/photos/add", { photo: ph, api_key: pexelsKey() }).then(function (m) {
                S.media = S.media.filter(function (x) { return x.src !== m.src; }).concat([m]);
                scr.image = { src: m.src, alt: m.alt || ph.alt || "", credit: m.credit, credit_url: m.credit_url };
                S.picking = false; touch();
                var f = redraw(); var a = f.querySelector("textarea"); if (a) a.focus();
              }).catch(function (e) { msg.textContent = e.message; });
            } }, [el("img", { src: ph.thumb, alt: "", loading: "lazy", referrerpolicy: "no-referrer" }), el("span", { text: ph.photographer })]));
        });
      }).catch(function (e) { msg.textContent = e.message; });
    }
    q.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); run(); } });
    wrap.appendChild(el("div", { class: "ed-stock-row" }, [q, el("button", { type: "button", class: "btn", id: "ed-stock-go", text: "Search", onclick: run })]));
    if (keyRow) wrap.appendChild(keyRow);
    wrap.appendChild(msg);
    wrap.appendChild(results);
    return wrap;
  }

  function iconInput(value, onChange, id, label) {
    if (!$("ed-icons")) {
      var dl = el("datalist", { id: "ed-icons" }, Object.keys(window.ICONS || {}).map(function (n) { return el("option", { value: n }); }));
      document.body.appendChild(dl);
    }
    var chip = el("span", { class: "ed-ico-chip", "aria-hidden": "true" });
    var inp = el("input", { type: "text", id: id, list: "ed-icons", spellcheck: "false", autocomplete: "off", placeholder: "Type to search, e.g. shield", "aria-label": label });
    inp.value = value || "";
    function show() {
      var n = inp.value.trim(), body = window.ICONS && window.ICONS[n];
      chip.innerHTML = body ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + body + "</svg>" : "";
      chip.classList.toggle("is-empty", !body);
    }
    inp.addEventListener("input", function () { show(); onChange(inp.value.trim()); });
    show();
    return el("span", { class: "ed-ico" }, [chip, inp]);
  }

  function selectInput(options, value, onChange, id, label) {
    var sel = el("select", { id: id, "aria-label": label }, options.map(function (o) { return el("option", { value: String(o[0]), text: o[1] }); }));
    sel.value = value == null ? "" : String(value);
    sel.addEventListener("change", function () { onChange(sel.value); });
    return sel;
  }

  function field(f, scr) {
    if (f.kind === "image") return imageField(scr);
    if (f.kind === "icon" || f.kind === "select") {
      var w = el("div", { class: "ed-field", "data-k": f.k }), fid = "ed-f-" + f.k;
      w.appendChild(el("label", { class: "ed-label", for: fid, text: f.label + (f.req ? "" : " (optional)") }));
      w.appendChild(f.kind === "icon" ? iconInput(scr[f.k], function (v) { scr[f.k] = v; touch(); }, fid, f.label)
        : selectInput(f.options, scr[f.k], function (v) { scr[f.k] = v; touch(); renderForm(); }, fid, f.label));
      if (f.help) w.appendChild(el("p", { class: "help", text: f.help }));
      w.appendChild(el("span", { class: "ed-err", id: "ed-err-" + f.k, role: "status" }));
      return w;
    }
    var id = "ed-f-" + f.k, wrap = el("div", { class: "ed-field", "data-k": f.k }), v = scr[f.k];
    var onText = function (e) { scr[f.k] = e.target.value; touch(); if (f.k === "title" || f.k === "question") renderOutline(); };
    if (f.kind === "check") {
      var cb = el("input", { type: "checkbox", id: id });
      cb.checked = v !== false;
      cb.addEventListener("change", function () { scr[f.k] = cb.checked; touch(); });
      wrap.appendChild(el("label", { class: "ed-check", for: id }, [cb, f.label]));
      wrap.appendChild(el("p", { class: "help", text: "Untick to make this a practice question that doesn't affect the pass mark." }));
      return wrap;
    }
    wrap.appendChild(el(f.kind === "text" || f.kind === "textarea" ? "label" : "span", { class: "ed-label", for: id, text: f.label + (f.req ? "" : " (optional)") }));
    if (f.kind === "text" || f.kind === "textarea") {
      var inp = el(f.kind === "text" ? "input" : "textarea", f.kind === "text" ? { type: "text", id: id } : { id: id, rows: 3 });
      inp.value = v || ""; inp.addEventListener("input", onText);
      wrap.appendChild(inp);
    } else {
      wrap.appendChild(rows(f, scr));
    }
    if (f.help) wrap.appendChild(el("p", { class: "help", text: f.help }));
    wrap.appendChild(el("span", { class: "ed-err", id: "ed-err-" + f.k, role: "status" }));
    return wrap;
  }

  function rows(f, scr) {
    if (!Array.isArray(scr[f.k])) scr[f.k] = [];
    var arr = scr[f.k], box = el("div", { class: "ed-rows" });
    var rerender = function () { var fresh = rows(f, scr); box.replaceWith(fresh); };
    arr.forEach(function (item, n) {
      var cells = el("div", { class: "ed-cells" }), row;
      if (f.kind === "list" || f.kind === "options") {
        var inp = el("input", { type: "text", "aria-label": f.label + " " + (n + 1) });
        inp.value = item;
        inp.addEventListener("input", function () { arr[n] = inp.value; touch(); });
        if (f.kind === "options") {
          var radio = el("input", { type: "radio", name: "ed-correct", "aria-label": "Answer " + (n + 1) + " is correct" });
          radio.checked = scr.correct === n;
          radio.addEventListener("change", function () { scr.correct = n; touch(); rerender(); });
          cells.appendChild(el("label", { class: "ed-correct" }, [radio, inp]));
          inp.style.flex = "1";
        } else cells.appendChild(inp);
        row = el("div", { class: "ed-row" + (f.kind === "options" && scr.correct === n ? " is-correct" : ""), "data-row": n });
      } else {
        shownCells(f, scr).forEach(function (c) {
          var lab = (c.labelFor ? c.labelFor(scr) : c.label) + (c.opt && !(c.reqIf && c.reqIf(scr)) ? " (optional)" : "");
          var aria = lab + " " + (n + 1), ctl;
          if (c.kind === "icon") ctl = iconInput(item[c.k], function (v) { item[c.k] = v; touch(); }, null, aria);
          else if (c.kind === "select") ctl = selectInput(typeof c.options === "function" ? c.options(scr) : c.options, item[c.k],
            function (v) { item[c.k] = c.num ? Number(v) : v; touch(); }, null, aria);
          else {
            ctl = el(c.area ? "textarea" : "input", c.area ? { rows: 2 } : { type: "text", placeholder: c.placeholder || "" });
            ctl.value = item[c.k] == null ? "" : String(item[c.k]);
            (function (inp2) { inp2.addEventListener("input", function () { item[c.k] = inp2.value; touch(); }); })(ctl);
          }
          cells.appendChild(el("label", { class: "ed-cell ed-cell-" + c.k }, [lab, ctl]));
        });
        if (f.correct) {
          var r2 = el("input", { type: "radio", name: "ed-best" });
          r2.checked = !!item.correct;
          r2.addEventListener("change", function () { arr.forEach(function (x, j) { x.correct = j === n; }); touch(); rerender(); });
          cells.appendChild(el("label", { class: "ed-correct" }, [r2, "Best response"]));
        }
        row = el("div", { class: "ed-row record" + (f.two ? " two" : "") + (f.correct && item.correct ? " is-correct" : ""), "data-row": n });
      }
      row.appendChild(el("span", { class: "ed-n", text: String(n + 1) }));
      row.appendChild(cells);
      row.appendChild(el("button", { type: "button", class: "ed-del", "aria-label": "Remove " + f.label.toLowerCase() + " " + (n + 1), text: "Remove",
        onclick: function () {
          arr.splice(n, 1);
          if (f.kind === "options") { if (scr.correct === n) scr.correct = 0; else if (scr.correct > n) scr.correct--; }
          touch(); rerender();
        } }));
      box.appendChild(row);
    });
    box.appendChild(el("button", { type: "button", class: "ed-addrow", text: "+ " + f.add, onclick: function () {
      if (f.max && arr.length >= f.max) { status("This screen can have at most " + f.max + ".", "error"); return; }
      arr.push(f.kind === "list" || f.kind === "options" ? "" : f.cells.reduce(function (o, c) { o[c.k] = c.num ? 0 : ""; return o; }, f.correct ? { correct: false } : {}));
      var holder = box.parentNode;
      touch(); rerender();
      var ins = holder && holder.querySelectorAll(".ed-row input[type=text], .ed-row textarea");
      if (ins && ins.length) ins[ins.length - 1].focus();
    } }));
    return box;
  }

  function fmtTime(sec) { sec = Math.round(sec || 0); return Math.floor(sec / 60) + ":" + ("0" + (sec % 60)).slice(-2); }
  function voMode() { return (S.course.voiceover && S.course.voiceover.mode) || "off"; }

  function narrationField(scr) {
    var wrap = el("div", { class: "ed-field ed-narr", "data-k": "narration" });
    var ta = el("textarea", { id: "ed-f-narration", rows: 3, maxlength: MAX_NARR + 200 });
    ta.value = scr.narration || "";
    var note = el("p", { class: "help ed-narr-status" });
    var refresh = function () {
      var mode = voMode(), changed = (scr.narration || "").trim() !== (savedNarr.get(scr) || "").trim();
      note.innerHTML = "";
      if (mode === "off") note.textContent = "Voice-over is off for this course. Turn it on in Course settings.";
      else if (mode === "device") note.textContent = "Read aloud by the learner's device.";
      else if (scr.audio && !changed) {
        note.appendChild(document.createTextNode("Recorded (" + fmtTime(scr.audio.duration) + "). "));
        note.appendChild(el("audio", { controls: "", preload: "none", src: mediaUrl(scr.audio.src), "aria-label": "Recorded narration" }));
      } else note.textContent = (changed && scr.audio ? "Changed. " : "No audio yet. ") +
        (canRecord() ? "When you save, it's recorded in " + voiceName() + "." : "Save, then record audio in Course settings.");
    };
    ta.addEventListener("input", function () { scr.narration = ta.value; touch(); refresh(); });
    wrap.appendChild(el("label", { class: "ed-label", for: "ed-f-narration", text: "Narration script (optional)" }));
    wrap.appendChild(ta);
    wrap.appendChild(el("p", { class: "help", text: "What the voice says on this screen. One to three short sentences works best; never give away a quiz answer." }));
    wrap.appendChild(note);
    wrap.appendChild(el("span", { class: "ed-err", id: "ed-err-narration", role: "status" }));
    refresh();
    return wrap;
  }

  function voiceSection() {
    var c = S.course, vo = c.voiceover = c.voiceover || { mode: "off" };
    var box = el("div", { class: "ed-vo", id: "ed-vo" });
    box.appendChild(el("h4", { text: "Voice-over" }));
    var modeSel = el("select", { id: "ed-c-vomode" }, [
      el("option", { value: "off", text: "Off" }), el("option", { value: "audio", text: "AI voice (recorded with Google Gemini)" }),
      el("option", { value: "device", text: "Device voice (read aloud by the learner's computer)" })]);
    modeSel.value = vo.mode || "off";
    modeSel.addEventListener("change", function () { vo.mode = modeSel.value; touch(); renderForm(); });
    box.appendChild(el("div", { class: "ed-field" }, [el("label", { class: "ed-label", for: "ed-c-vomode", text: "Narration" }), modeSel]));
    if (vo.mode === "audio") {
      var voices = (S.cfg && S.cfg.voices) || [];
      var vSel = el("select", { id: "ed-c-voice" }, voices.map(function (v) { return el("option", { value: v.id, text: v.label }); }));
      vSel.value = vo.voice || (S.cfg && S.cfg.default_voice) || "";
      vSel.addEventListener("change", function () { vo.voice = vSel.value; touch(); renderForm(); });
      box.appendChild(el("div", { class: "ed-field" }, [el("label", { class: "ed-label", for: "ed-c-voice", text: "Voice" }), vSel,
        el("p", { class: "help", text: "Changing the voice means every screen is recorded again." })]));
      var scripts = c.screens.filter(function (x) { return (x.narration || "").trim(); }).length;
      var rec = el("div", { class: "ed-record" });
      var msg = S.recording ? "Recording audio…" : S.dirty ? "Save your changes, then record audio." :
        S.needsAudio ? S.needsAudio + (S.needsAudio === 1 ? " screen needs" : " screens need") + " audio." :
        scripts ? "All " + scripts + " narrated screens have audio." : "No screen has a narration script yet.";
      rec.appendChild(el("p", { class: "ed-record-msg", id: "ed-record-msg", role: "status", text: msg }));
      var key = null;
      if (!S.ttsServerKey) {
        key = el("input", { type: "password", id: "ed-tts-key", autocomplete: "off", spellcheck: "false", placeholder: "Paste your Gemini API key" });
        rec.appendChild(el("label", { class: "ed-label", for: "ed-tts-key", text: "Gemini API key" }));
        rec.appendChild(key);
        rec.appendChild(el("p", { class: "help", text: "Used for this recording only and never stored." }));
      }
      rec.appendChild(el("button", { type: "button", class: "btn primary", id: "ed-record", text: "Record audio",
        disabled: S.recording || S.dirty || !S.needsAudio ? "" : false, onclick: function () { record(key ? key.value.trim() : ""); } }));
      if (key && S.key) key.value = S.key;
      box.appendChild(rec);
    }
    return box;
  }

  function voiceName() { return (S.course.voiceover && S.course.voiceover.voice) || (S.cfg && S.cfg.default_voice) || "the chosen voice"; }
  // A Gemini key the browser already has this session: one typed here, or the one pasted on the upload page.
  // It stays in this page's memory only and is sent with each recording request, never stored on the server.
  function knownKey() { return S.key || (S.hostKey ? S.hostKey() : ""); }
  function canRecord() { return S.ttsServerKey || !!knownKey(); }

  function record(key) {
    key = key || knownKey();
    if (!S.ttsServerKey && !key) {
      var m = $("ed-record-msg"); if (m) m.textContent = "Paste a Gemini API key first.";
      var k = $("ed-tts-key"); if (k) k.focus();
      return Promise.resolve(false);
    }
    if (key) S.key = key;
    return api("POST", "/api/jobs/" + S.id + "/narration", { api_key: S.ttsServerKey && !key ? "" : key })
      .then(function () { S.recording = true; renderForm(); renderOutline(); pollRecording(); return true; })
      .catch(function (e) { var m = $("ed-record-msg"); status(e.message, "error"); if (m) m.textContent = e.message; return false; });
  }

  function pollRecording() {
    clearTimeout(S.voPoll);
    api("GET", "/api/jobs/" + S.id).then(function (j) {
      if (j.narr_status === "running") {
        var m = $("ed-record-msg");
        if (m && j.narr_total) m.textContent = "Recording screen " + Math.min(j.narr_done + 1, j.narr_total) + " of " + j.narr_total + "…";
        status("Recording audio. You can keep reviewing; saving is paused until it finishes.");
        S.voPoll = setTimeout(pollRecording, 1500);
        return;
      }
      S.recording = false;
      var warn = (j.narr_warnings || []).join(" ");
      load().then(function () {
        S.changed = true;
        status(warn || ("Audio recorded in " + voiceName() + " for " + (j.narr_result || "all") + " screens. The preview and downloads include it."), warn ? "error" : "");
      });
    }).catch(function () { S.voPoll = setTimeout(pollRecording, 3000); });
  }

  function brandSection() {
    var box = el("div", { class: "ed-vo ed-brand", id: "ed-brand" });
    box.appendChild(el("h4", { text: "Brand" }));
    var kits = (window.BrandKits && BrandKits.data.kits) || [];
    var now = S.brandKit ? "This course: " + S.brandKit.name + (S.brandDetected ? " (from the deck)" : "") : "Standard look";
    box.appendChild(el("p", { class: "help ed-brand-now", text: now }));
    var sel = el("select", { id: "ed-c-kit" }, [el("option", { value: "", text: "Switch to…" }), el("option", { value: "standard", text: "Standard look" })]
      .concat(kits.map(function (k) { return el("option", { value: "kit:" + k.id, text: k.name }); })));
    var apply = el("button", { type: "button", class: "btn", id: "ed-kit-apply", text: "Apply", disabled: "" });
    sel.addEventListener("change", function () { apply.disabled = !sel.value || S.dirty; });
    apply.addEventListener("click", function () {
      status("Applying the brand…");
      api("POST", "/api/jobs/" + S.id + "/brand", { use: sel.value }).then(function () { S.changed = true; return load(); })
        .then(function () { select("course"); status("Brand applied. The preview and downloads use it now."); })
        .catch(function (e) { status(e.message, "error"); });
    });
    box.appendChild(el("div", { class: "ed-field" }, [el("label", { class: "ed-label", for: "ed-c-kit", text: "Brand kit" }),
      el("div", { class: "brand-row" }, [sel, apply]),
      el("p", { class: "help", text: S.dirty ? "Save your changes before switching brand." : "Kits are made on the upload page (Branding, then New brand kit)." })]));
    if (S.brandKit && S.brandSaving) {
      box.appendChild(el("button", { type: "button", class: "bk-link", id: "ed-kit-save", text: "Save this course's brand as a kit", onclick: function () {
        var name = window.prompt("Name for the new brand kit", S.brandKit.name);
        if (!name) return;
        api("POST", "/api/jobs/" + S.id + "/brand", { save_as: name }).then(function (k) {
          return window.BrandKits ? BrandKits.load().then(function () { return k; }) : k;
        }).then(function (k) { status("Saved the brand kit \u201c" + k.name + "\u201d. It's now in the Branding list."); renderForm(); })
          .catch(function (e) { status(e.message, "error"); });
      } }));
    }
    return box;
  }

  function courseForm() {
    var c = S.course, f = el("div");
    f.appendChild(el("h3", { text: "Course settings" }));
    f.appendChild(el("p", { class: "ed-kind", text: "Shown in the LMS and on the welcome screen." }));
    var t = el("input", { type: "text", id: "ed-c-title" }); t.value = c.title || "";
    t.addEventListener("input", function () { c.title = t.value; touch(); });
    f.appendChild(el("div", { class: "ed-field", "data-k": "title" }, [el("label", { class: "ed-label", for: "ed-c-title", text: "Course title" }), t, el("span", { class: "ed-err", id: "ed-err-title" })]));
    var d = el("textarea", { id: "ed-c-desc", rows: 2 }); d.value = c.description || "";
    d.addEventListener("input", function () { c.description = d.value; touch(); });
    f.appendChild(el("div", { class: "ed-field" }, [el("label", { class: "ed-label", for: "ed-c-desc", text: "Description (optional)" }), d]));
    var num = function (k, label, help) {
      var i = el("input", { type: "number", id: "ed-c-" + k, inputmode: "numeric" }); i.value = c[k];
      i.addEventListener("input", function () { c[k] = i.value === "" ? NaN : Math.round(Number(i.value)); touch(); });
      return el("div", { class: "ed-field", "data-k": k }, [el("label", { class: "ed-label", for: "ed-c-" + k, text: label }), i,
        el("p", { class: "help", text: help }), el("span", { class: "ed-err", id: "ed-err-" + k })]);
    };
    f.appendChild(el("div", { class: "ed-two" }, [num("mastery_score", "Pass mark (%)", "Learners below this can retry the quiz."),
      num("est_minutes", "Time to complete (minutes)", "Shown on the welcome screen.")]));
    f.appendChild(brandSection());
    var pick = el("input", { type: "color", "aria-label": "Pick main colour" }), hex = el("input", { type: "text", id: "ed-c-brand", maxlength: 7, spellcheck: "false" });
    pick.value = hex.value = S.brand;
    pick.addEventListener("input", function () { hex.value = pick.value; S.brand = pick.value; touch(); });
    hex.addEventListener("input", function () { if (/^#[0-9a-f]{6}$/i.test(hex.value)) { pick.value = hex.value; S.brand = hex.value; touch(); } });
    f.appendChild(el("div", { class: "ed-field" }, [el("label", { class: "ed-label", for: "ed-c-brand", text: "Main colour (this course only)" }),
      el("div", { class: "colour" }, [pick, hex]),
      el("p", { class: "help", text: "Overrides the brand kit's main colour for this course. Saved with the course." })]));
    f.appendChild(voiceSection());
    return f;
  }

  function renderForm() {
    var box = $("ed-form");
    box.innerHTML = "";
    if (S.sel === "course") box.appendChild(courseForm());
    else {
      var scr = S.course.screens[S.sel];
      box.appendChild(el("h3", { text: "Screen " + (S.sel + 1) + ": " + typeLabel(scr) }));
      box.appendChild(el("p", { class: "ed-kind", text: { intro: "The first thing learners see.", content: "A short reading screen.",
        flipcards: "Learners flip every card to continue.", reveal: "Learners open every item to continue.", match: "Learners match every pair to continue.",
        scenario: "A situation with one best response.", summary: "A recap before the quiz.",
        mcq: "One question with a single correct answer." }[scr.type] }));
      TYPES[scr.type].fields.forEach(function (f) { box.appendChild(field(f, scr)); });
      box.appendChild(narrationField(scr));
    }
    showErrors();
  }

  function showErrors() {
    var mine = problems().filter(function (e) { return e.s === S.sel; });
    document.querySelectorAll("#ed-form .ed-err").forEach(function (s) { s.textContent = ""; });
    document.querySelectorAll("#ed-form .invalid").forEach(function (n) { n.classList.remove("invalid"); });
    mine.forEach(function (e) {
      var span = $("ed-err-" + e.f), wrap = document.querySelector('#ed-form .ed-field[data-k="' + e.f + '"]');
      if (span && !span.textContent) span.textContent = e.m;
      if (e.r != null && wrap) { var r = wrap.querySelector('[data-row="' + e.r + '"]'); if (r) r.classList.add("invalid"); }
      else if (wrap && (e.f !== "options" && e.f !== "choices" && e.f !== "cards" && e.f !== "items" && e.f !== "pairs")) wrap.classList.add("invalid");
    });
  }

  /* ---------- preview, save, revert ---------- */
  function previewSrc() { return "/preview/" + S.id + "/index.html?v=" + S.version + "-" + Date.now() + "#screen=" + (typeof S.sel === "number" ? S.sel : 0); }
  function schedulePreview() { clearTimeout(S.previewTimer); S.previewTimer = setTimeout(function () { $("ed-preview").src = previewSrc(); }, 250); }

  function api(method, url, body) {
    return fetch(url, { method: method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.detail || "The server couldn't save the course."); return j; }); });
  }

  function save() {
    if (!S.dirty || problems().length || S.recording) return;
    S.saving = true; $("ed-save").disabled = true; status("Saving and rebuilding the package…");
    var course = cleaned();
    api("PUT", "/api/jobs/" + S.id + "/course", { course: course, brand: S.brand })
      .then(function (j) {
        S.saving = false; S.changed = true;
        if (typeof S.sel === "number" && S.sel >= course.screens.length) S.sel = course.screens.length - 1;
        return load().then(function () {
          var n = S.needsAudio, screens = n + (n === 1 ? " screen" : " screens");
          if (voMode() === "audio" && n && canRecord()) {
            return record().then(function (ok) {
              if (ok) status("Saved. Recording new audio for " + screens + " in " + voiceName() + "…");
            });
          }
          status("Saved. The preview and downloads now include your changes." +
            (voMode() === "audio" && n ? " " + screens + " need new audio: add a Gemini key under Course settings, then click Record audio." : ""));
        });
      })
      .catch(function (e) {
        S.saving = false; renderOutline();
        var m = /screen (\d+)/i.exec(e.message);
        if (m) select(parseInt(m[1], 10) - 1);
        status(e.message, "error");
      });
  }

  function revert() {
    if (!window.confirm("Throw away all your edits and go back to the AI draft?")) return;
    status("Reverting…");
    api("POST", "/api/jobs/" + S.id + "/revert").then(function (j) { S.version = j.version; S.changed = true; return load(); })
      .then(function () { status("Back to the AI draft."); }).catch(function (e) { status(e.message, "error"); });
  }

  function load() {
    if (!S.cfg) api("GET", "/api/config").then(function (c) { S.cfg = c; if (S.sel === "course") renderForm(); }).catch(function () {});
    api("GET", "/api/jobs/" + S.id + "/media").then(function (j) { S.media = j.images || []; }).catch(function () { S.media = []; });
    return api("GET", "/api/jobs/" + S.id + "/course").then(function (j) {
      S.course = j.course; S.brand = j.brand; S.version = j.version; S.dirty = false;
      S.needsAudio = j.needs_audio || 0; S.ttsServerKey = !!j.tts_server_key;
      S.brandKit = j.brand_kit; S.brandDetected = !!j.brand_detected; S.brandSaving = j.brand_saving !== false;
      if (j.recording && !S.recording) { S.recording = true; pollRecording(); }
      S.course.screens.forEach(function (x) { savedNarr.set(x, x.narration || ""); });
      if (typeof S.course.mastery_score !== "number") S.course.mastery_score = 80;
      if (typeof S.course.est_minutes !== "number") S.course.est_minutes = Math.max(5, S.course.screens.length);
      $("ed-revert").disabled = !j.edited;
      if (typeof S.sel === "number" && S.sel >= S.course.screens.length) S.sel = "course";
      renderOutline(); renderForm(); $("ed-preview").src = previewSrc();
    });
  }

  function close() {
    if (S.dirty && !window.confirm("You have unsaved changes. Leave the editor and lose them?")) return;
    S.dirty = false;
    clearTimeout(S.voPoll);
    document.body.classList.remove("editing");
    $("editor").classList.add("is-hidden");
    $("ed-preview").src = "about:blank";
    if (S.onClose) S.onClose(S.changed);
  }

  function build() {
    if ($("editor")) return;
    var opts = Object.keys(TYPES).map(function (t) { return el("option", { value: t, text: TYPES[t].label }); });
    var section = el("section", { id: "editor", class: "editor is-hidden", "aria-label": "Course editor" }, [
      el("div", { class: "ed-bar" }, [
        el("h2", { text: "Edit course" }),
        el("p", { class: "ed-status", id: "ed-status", role: "status", "aria-live": "polite" }),
        el("button", { type: "button", class: "btn is-hidden", id: "ed-audio", onclick: function () {
          select("course"); var r = $("ed-vo"); if (r) { r.scrollIntoView({ behavior: "smooth", block: "center" }); var b = $("ed-tts-key") || $("ed-record"); if (b) b.focus(); } } }),
        el("button", { type: "button", class: "btn", id: "ed-revert", text: "Revert to AI draft", onclick: revert }),
        el("button", { type: "button", class: "btn primary", id: "ed-save", text: "Save and preview", onclick: save }),
        el("button", { type: "button", class: "btn", id: "ed-done", text: "Done", onclick: close })]),
      el("div", { class: "ed-grid" }, [
        el("nav", { class: "ed-outline", "aria-label": "Screens" }, [
          el("button", { type: "button", class: "ed-course-btn", id: "ed-course", onclick: function () { select("course"); } }),
          el("ol", { class: "ed-list", id: "ed-list" }),
          el("div", { class: "ed-add" }, [
            el("select", { id: "ed-newtype", "aria-label": "Type of screen to add" }, opts),
            el("button", { type: "button", class: "btn", text: "Add screen", onclick: addScreen })])]),
        el("div", { class: "ed-form", id: "ed-form" }),
        el("div", { class: "ed-preview" }, [
          el("iframe", { id: "ed-preview", title: "Preview of the saved course" }),
          el("p", { text: "Preview of the last saved version. It opens on the screen you're editing." })])])]);
    document.querySelector("main").appendChild(section);
    $("ed-newtype").value = "mcq";
    document.addEventListener("keydown", function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s" && !$("editor").classList.contains("is-hidden")) { e.preventDefault(); save(); }
    });
    window.addEventListener("beforeunload", function (e) { if (S.dirty) { e.preventDefault(); e.returnValue = ""; } });
  }

  window.CourseEditor = {
    open: function (jobId, onClose, opts) {
      build();
      S.id = jobId; S.onClose = onClose; S.sel = 0; S.changed = false;
      S.hostKey = opts && opts.geminiKey || null;
      S.hostPexels = opts && opts.pexelsKey || null;
      document.body.classList.add("editing");
      $("editor").classList.remove("is-hidden");
      window.scrollTo(0, 0);
      status("Loading the course…");
      return load().catch(function (e) { status(e.message, "error"); });
    }
  };
})();
