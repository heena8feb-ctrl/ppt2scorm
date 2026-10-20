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
      { k: "objectives", label: "Learning objectives", kind: "list", req: true, add: "Add an objective" }] },
    content: { label: "Content", fields: [
      { k: "title", label: "Heading", kind: "text", req: true },
      { k: "body", label: "Main text", kind: "textarea", req: true, help: "Keep it short: about 60 words reads well on one screen." },
      { k: "bullets", label: "Bullet points", kind: "list", add: "Add a bullet point" },
      { k: "callout", label: "Key message", kind: "text", help: "Optional. Shown in a highlighted box." }] },
    flipcards: { label: "Flip cards", fields: [
      { k: "title", label: "Heading", kind: "text", req: true },
      { k: "instruction", label: "Instruction", kind: "text" },
      { k: "cards", label: "Cards", kind: "records", req: true, min: 1, two: true, add: "Add a card",
        cells: [{ k: "front", label: "Front" }, { k: "back", label: "Back", area: true }] }] },
    reveal: { label: "Click to reveal", fields: [
      { k: "title", label: "Heading", kind: "text", req: true },
      { k: "instruction", label: "Instruction", kind: "text" },
      { k: "items", label: "Items", kind: "records", req: true, min: 1, add: "Add an item",
        cells: [{ k: "label", label: "Label" }, { k: "detail", label: "Shown when opened", area: true }] }] },
    match: { label: "Matching", fields: [
      { k: "title", label: "Heading", kind: "text", req: true },
      { k: "instruction", label: "Instruction", kind: "text" },
      { k: "pairs", label: "Pairs", kind: "records", req: true, min: 2, two: true, add: "Add a pair",
        help: "Learners see the right-hand column shuffled.",
        cells: [{ k: "left", label: "Item" }, { k: "right", label: "Matches" }] }] },
    scenario: { label: "Scenario", fields: [
      { k: "title", label: "Heading", kind: "text", req: true },
      { k: "situation", label: "Situation", kind: "textarea", req: true },
      { k: "choices", label: "Choices", kind: "records", req: true, min: 2, correct: "radio", add: "Add a choice",
        help: "Mark the best response. Learners keep trying until they find it.",
        cells: [{ k: "text", label: "Choice" }, { k: "feedback", label: "Feedback after choosing it", area: true }] }] },
    summary: { label: "Recap", fields: [
      { k: "title", label: "Heading", kind: "text", req: true },
      { k: "points", label: "Key points", kind: "list", req: true, add: "Add a point" }] },
    mcq: { label: "Quiz question", fields: [
      { k: "question", label: "Question", kind: "textarea", req: true },
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
    mcq: function () { return { type: "mcq", question: "", options: ["", ""], correct: 0, explanation: "", scored: true }; }
  };

  var S = { id: null, course: null, brand: "#1f5eff", sel: "course", dirty: false, changed: false, version: 1, onClose: null, previewTimer: null };
  var $ = function (id) { return document.getElementById(id); };

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
      TYPES[scr.type].fields.forEach(function (f) {
        var v = scr[f.k], add = function (m, row) { out.push({ s: i, f: f.k, r: row, m: m }); };
        if (f.kind === "text" || f.kind === "textarea") { if (f.req && blank(v)) add("Fill this in."); }
        else if (f.kind === "list" || f.kind === "options") {
          var filled = (v || []).filter(function (x) { return !blank(x); });
          if (f.req && !filled.length) add("Add at least one line.");
          if (f.kind === "options") {
            if (filled.length < 2) add("Add at least two answers.");
            if (blank((v || [])[scr.correct])) add("Select the correct answer, and make sure it has text.");
          }
        } else if (f.kind === "records") {
          var rows = (v || []).filter(function (r) { return f.cells.some(function (c2) { return !blank(r[c2.k]); }); });
          if (rows.length < (f.min || 1)) add(f.min > 1 ? "Add at least " + f.min + " filled-in rows." : "Add at least one row.");
          (v || []).forEach(function (r, n) {
            var some = f.cells.some(function (c2) { return !blank(r[c2.k]); });
            var need = f.correct ? ["text"] : f.cells.map(function (c2) { return c2.k; });
            if (some && need.some(function (k) { return blank(r[k]); })) add(f.correct ? "This choice needs text." : "Fill in both parts.", n);
          });
          if (f.correct && !(v || []).some(function (r) { return r.correct && !blank(r.text); })) add("Mark the best response.");
        }
      });
    });
    return out;
  }

  /* drop blank lines/rows and empty optional text before saving */
  function cleaned() {
    var c = JSON.parse(JSON.stringify(S.course));
    c.title = (c.title || "").trim();
    c.screens = c.screens.map(function (scr) {
      TYPES[scr.type].fields.forEach(function (f) {
        var v = scr[f.k];
        if (f.kind === "text" || f.kind === "textarea") {
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
          scr[f.k] = (v || []).filter(function (r) { return f.cells.some(function (c2) { return !blank(r[c2.k]); }); })
            .map(function (r) { var o = {}; f.cells.forEach(function (c2) { o[c2.k] = (r[c2.k] || "").trim(); }); if (f.correct) o.correct = !!r.correct; return o; });
        }
      });
      return scr;
    });
    return c;
  }

  /* ---------- outline ---------- */
  function nameOf(scr) { return (scr.title || scr.question || "").trim() || "Untitled"; }
  function typeLabel(scr) { return scr.type === "mcq" && scr.scored === false ? "Practice question" : TYPES[scr.type].label; }

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
    $("ed-save").disabled = !S.dirty || n > 0;
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
  function select(i) { S.sel = i; renderOutline(); renderForm(); schedulePreview(); }

  /* ---------- form ---------- */
  function field(f, scr) {
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
        f.cells.forEach(function (c) {
          var inp2 = el(c.area ? "textarea" : "input", c.area ? { rows: 2 } : { type: "text" });
          inp2.value = item[c.k] || "";
          inp2.addEventListener("input", function () { item[c.k] = inp2.value; touch(); });
          cells.appendChild(el("label", null, [c.label, inp2]));
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
      arr.push(f.kind === "list" || f.kind === "options" ? "" : f.cells.reduce(function (o, c) { o[c.k] = ""; return o; }, f.correct ? { correct: false } : {}));
      var holder = box.parentNode;
      touch(); rerender();
      var ins = holder && holder.querySelectorAll(".ed-row input[type=text], .ed-row textarea");
      if (ins && ins.length) ins[ins.length - 1].focus();
    } }));
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
    var pick = el("input", { type: "color", "aria-label": "Pick brand colour" }), hex = el("input", { type: "text", id: "ed-c-brand", maxlength: 7, spellcheck: "false" });
    pick.value = hex.value = S.brand;
    pick.addEventListener("input", function () { hex.value = pick.value; S.brand = pick.value; touch(); });
    hex.addEventListener("input", function () { if (/^#[0-9a-f]{6}$/i.test(hex.value)) { pick.value = hex.value; S.brand = hex.value; touch(); } });
    f.appendChild(el("div", { class: "ed-field" }, [el("label", { class: "ed-label", for: "ed-c-brand", text: "Brand colour" }), el("div", { class: "colour" }, [pick, hex])]));
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
    if (!S.dirty || problems().length) return;
    S.saving = true; $("ed-save").disabled = true; status("Saving and rebuilding the package…");
    var course = cleaned();
    api("PUT", "/api/jobs/" + S.id + "/course", { course: course, brand: S.brand })
      .then(function (j) {
        S.saving = false; S.course = course; S.version = j.version; S.dirty = false; S.changed = true;
        $("ed-revert").disabled = false;
        if (typeof S.sel === "number" && S.sel >= course.screens.length) S.sel = course.screens.length - 1;
        renderOutline(); renderForm(); $("ed-preview").src = previewSrc();
        status("Saved. The preview and downloads now include your changes.");
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
    return api("GET", "/api/jobs/" + S.id + "/course").then(function (j) {
      S.course = j.course; S.brand = j.brand; S.version = j.version; S.dirty = false;
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
    open: function (jobId, onClose) {
      build();
      S.id = jobId; S.onClose = onClose; S.sel = 0; S.changed = false;
      document.body.classList.add("editing");
      $("editor").classList.remove("is-hidden");
      window.scrollTo(0, 0);
      status("Loading the course…");
      return load().catch(function (e) { status(e.message, "error"); });
    }
  };
})();
