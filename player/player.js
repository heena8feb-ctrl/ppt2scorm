/* Course player: renders window.COURSE (see ppt2scorm/schema.py) and reports to the LMS via SCORM. */
(function () {
  "use strict";
  var C = window.COURSE;
  var stage = document.getElementById("stage");
  var btnNext = document.getElementById("btn-next");
  var btnBack = document.getElementById("btn-back");
  var hint = document.getElementById("nav-hint");

  var scored = [];                       // indices of scored mcq screens
  C.screens.forEach(function (s, i) { if (s.type === "mcq" && s.scored !== false) scored.push(i); });
  var hasAssessment = scored.length > 0;
  var RESULTS = C.screens.length;        // virtual final screen index
  var total = C.screens.length + 1;

  var state = { i: 0, done: [], answers: {}, completed: false };

  /* ---------- helpers ---------- */
  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      if (k === "class") n.className = attrs[k];
      else if (k === "text") n.textContent = attrs[k];
      else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), attrs[k]);
      else n.setAttribute(k, attrs[k]);
    }
    (kids || []).forEach(function (c) { if (c != null) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return n;
  }
  function shuffled(arr, seed) {
    var a = arr.slice(), s = seed || 1;
    for (var i = a.length - 1; i > 0; i--) { s = (s * 9301 + 49297) % 233280; var j = Math.floor((s / 233280) * (i + 1)); var t = a[i]; a[i] = a[j]; a[j] = t; }
    return a;
  }
  function markDone() { state.done[state.i] = true; updateNav(); save(); }
  function letter(n) { return String.fromCharCode(65 + n); }

  /* ---------- persistence (SCORM suspend_data, kept compact for the 4096-char 1.2 limit) ---------- */
  function save() {
    var d = ""; for (var i = 0; i < C.screens.length; i++) d += state.done[i] ? "1" : "0";
    SCORM.set("cmi.suspend_data", JSON.stringify({ i: state.i, d: d, a: state.answers }));
    SCORM.set("cmi.core.lesson_location", String(state.i));
    SCORM.commit();
  }
  function restore() {
    try {
      var raw = SCORM.get("cmi.suspend_data");
      if (!raw) return;
      var o = JSON.parse(raw);
      state.i = Math.min(o.i || 0, total - 1);
      (o.d || "").split("").forEach(function (c, i) { state.done[i] = c === "1"; });
      state.answers = o.a || {};
    } catch (e) { /* corrupt data: start fresh */ }
  }

  /* ---------- screen renderers ---------- */
  var R = {};

  R.intro = function (s) {
    markDone();
    return el("section", { class: "screen intro" }, [
      el("p", { class: "eyebrow", text: "Welcome" }),
      el("h1", { text: s.title }),
      s.subtitle ? el("p", { class: "lead", text: s.subtitle }) : null,
      el("div", { class: "objectives" }, [
        el("h3", { text: "By the end you will be able to" }),
        el("ul", null, s.objectives.map(function (o) { return el("li", { text: o }); }))
      ]),
      el("div", { class: "meta" }, [
        el("span", { text: "⏱ About " + (C.est_minutes || 10) + " min" }),
        hasAssessment ? el("span", { text: "✓ Pass mark " + C.mastery_score + "%" }) : null
      ])
    ]);
  };

  R.content = function (s) {
    markDone();
    return el("section", { class: "screen" }, [
      el("h2", { text: s.title }),
      el("p", { class: "lead", text: s.body }),
      s.bullets && s.bullets.length ? el("ul", { class: "ticks" }, s.bullets.map(function (b) { return el("li", { text: b }); })) : null,
      s.callout ? el("div", { class: "callout", text: s.callout }) : null
    ]);
  };

  R.flipcards = function (s) {
    var flipped = {};
    var grid = el("div", { class: "cards" }, s.cards.map(function (c, k) {
      var b = el("button", { class: "flip", type: "button", "aria-pressed": "false", "aria-label": c.front + ". Flip card." }, [
        el("div", { class: "flip-inner" }, [el("div", { class: "face front", text: c.front }), el("div", { class: "face back", text: c.back })])
      ]);
      b.addEventListener("click", function () {
        var on = b.getAttribute("aria-pressed") !== "true";
        b.setAttribute("aria-pressed", String(on));
        b.setAttribute("aria-label", (on ? c.back : c.front) + ". Flip card.");
        flipped[k] = true;
        if (Object.keys(flipped).length === s.cards.length && !state.done[state.i]) markDone();
      });
      return b;
    }));
    return el("section", { class: "screen" }, [
      el("p", { class: "eyebrow", text: "Flip the cards" }), el("h2", { text: s.title }),
      el("p", { class: "instruction", text: s.instruction || "Select each card to flip it." }), grid
    ]);
  };

  R.reveal = function (s) {
    var seen = {};
    var list = el("div", { class: "reveal-list" }, s.items.map(function (it, k) {
      var id = "rv-" + state.i + "-" + k;
      var item = el("div", { class: "reveal-item" });
      var btn = el("button", { type: "button", "aria-expanded": "false", "aria-controls": id }, [
        el("span", { text: it.label }), el("span", { class: "chev", "aria-hidden": "true", text: "›" })
      ]);
      btn.addEventListener("click", function () {
        var open = !item.classList.contains("open");
        item.classList.toggle("open", open); btn.setAttribute("aria-expanded", String(open));
        item.classList.add("seen"); seen[k] = true;
        if (Object.keys(seen).length === s.items.length && !state.done[state.i]) markDone();
      });
      item.appendChild(btn); item.appendChild(el("div", { class: "detail", id: id, text: it.detail }));
      return item;
    }));
    return el("section", { class: "screen" }, [
      el("p", { class: "eyebrow", text: "Explore" }), el("h2", { text: s.title }),
      el("p", { class: "instruction", text: s.instruction || "Select each item to learn more." }), list
    ]);
  };

  R.match = function (s) {
    var sel = null, matched = 0;
    var fb = el("div", { "aria-live": "polite" });
    function pick(btn) {
      if (btn.disabled) return;
      if (!sel || sel.dataset.side === btn.dataset.side) {
        if (sel) sel.classList.remove("selected");
        sel = btn; btn.classList.add("selected"); return;
      }
      var a = sel, b = btn; sel.classList.remove("selected"); sel = null;
      if (a.dataset.key === b.dataset.key) {
        matched++;
        [a, b].forEach(function (x) { x.classList.remove("wrong", "selected"); x.classList.add("done"); x.disabled = true; x.insertBefore(el("span", { class: "pair-num", text: String(matched) }), x.firstChild); });
        fb.textContent = "Matched " + matched + " of " + s.pairs.length + ".";
        if (matched === s.pairs.length) { fb.className = "feedback ok"; fb.textContent = "All matched. Nicely done!"; markDone(); }
      } else {
        [a, b].forEach(function (x) { x.classList.remove("wrong"); void x.offsetWidth; x.classList.add("wrong"); });
        fb.className = ""; fb.textContent = "Not a match. Try again.";
      }
    }
    function chip(text, key, side) {
      var b = el("button", { class: "chip", type: "button", text: text }); b.dataset.key = key; b.dataset.side = side;
      b.addEventListener("click", function () { pick(b); }); return b;
    }
    var left = el("div", { class: "match-col" }, s.pairs.map(function (p, k) { return chip(p.left, k, "L"); }));
    var right = el("div", { class: "match-col" }, shuffled(s.pairs.map(function (p, k) { return { t: p.right, k: k }; }), state.i + 3)
      .map(function (o) { return chip(o.t, o.k, "R"); }));
    return el("section", { class: "screen" }, [
      el("p", { class: "eyebrow", text: "Match up" }), el("h2", { text: s.title }),
      el("p", { class: "instruction", text: (s.instruction || "Match each item on the left to one on the right.") + " Select one from each column." }),
      el("div", { class: "match" }, [left, right]), fb
    ]);
  };

  R.scenario = function (s) {
    var fb = el("div", { "aria-live": "polite" });
    var btns = s.choices.map(function (c, k) {
      var b = el("button", { class: "choice", type: "button" }, [el("span", { class: "key", text: letter(k) }), el("span", { text: c.text })]);
      b.addEventListener("click", function () {
        b.classList.add(c.correct ? "correct" : "incorrect");
        fb.className = "feedback " + (c.correct ? "ok" : "bad");
        fb.innerHTML = ""; fb.appendChild(el("strong", { text: c.correct ? "Good call." : "Not the best choice." }));
        fb.appendChild(el("span", { text: c.feedback || "" }));
        if (c.correct) { btns.forEach(function (x) { x.disabled = true; }); markDone(); }
        else { b.disabled = true; fb.lastChild.textContent += " Try another option."; }
      });
      return b;
    });
    return el("section", { class: "screen" }, [
      el("p", { class: "eyebrow", text: "Scenario" }), el("h2", { text: s.title }),
      el("div", { class: "situation", text: s.situation }), el("div", { class: "choices" }, btns), fb
    ]);
  };

  R.summary = function (s) {
    markDone();
    return el("section", { class: "screen" }, [
      el("p", { class: "eyebrow", text: "Recap" }), el("h2", { text: s.title }),
      el("ul", { class: "ticks" }, s.points.map(function (p) { return el("li", { text: p }); })),
      hasAssessment ? el("div", { class: "callout", text: "Next: a short " + scored.length + "-question check. You need " + C.mastery_score + "% to pass." }) : null
    ]);
  };

  R.mcq = function (s) {
    var idx = state.i, chosen = null;
    var prev = state.answers[idx];
    var fb = el("div", { "aria-live": "polite" });
    var submit = el("button", { class: "btn primary", type: "button", text: "Check answer", disabled: "disabled" });
    var btns = s.options.map(function (o, k) {
      var b = el("button", { class: "choice", type: "button", role: "radio", "aria-checked": "false" },
        [el("span", { class: "key", text: letter(k) }), el("span", { text: o })]);
      b.addEventListener("click", function () {
        chosen = k; btns.forEach(function (x, j) { x.classList.toggle("selected", j === k); x.setAttribute("aria-checked", String(j === k)); });
        submit.disabled = false;
      });
      return b;
    });
    function lock(k) {
      var right = k === s.correct;
      btns.forEach(function (x, j) {
        x.disabled = true; x.classList.remove("selected");
        if (j === s.correct) x.classList.add("correct"); else if (j === k) x.classList.add("incorrect");
      });
      submit.style.display = "none";
      fb.className = "feedback " + (right ? "ok" : "bad"); fb.innerHTML = "";
      fb.appendChild(el("strong", { text: right ? "Correct!" : "Not quite." }));
      fb.appendChild(el("span", { text: s.explanation }));
    }
    submit.addEventListener("click", function () {
      if (chosen == null) return;
      state.answers[idx] = chosen; lock(chosen); markDone();
    });
    if (prev != null) lock(prev);
    var pos = scored.indexOf(idx);
    return el("section", { class: "screen" }, [
      el("p", { class: "eyebrow", text: pos >= 0 ? "Knowledge check" : "Quick question" }),
      pos >= 0 ? el("div", { class: "qcount", text: "Question " + (pos + 1) + " of " + scored.length }) : null,
      el("h2", { text: s.question }),
      el("div", { class: "choices", role: "radiogroup" }, btns),
      el("div", { class: "actions" }, [submit]), fb
    ]);
  };

  function score() {
    var right = 0;
    scored.forEach(function (i) { if (state.answers[i] === C.screens[i].correct) right++; });
    return { right: right, pct: Math.round((right / scored.length) * 100) };
  }

  function renderResults() {
    state.done[RESULTS] = true;
    if (!hasAssessment) {
      complete("completed");
      return el("section", { class: "screen results" }, [
        el("p", { class: "eyebrow", text: "All done" }), el("h1", { text: "Course complete" }),
        el("p", { class: "lead", text: "You've finished " + C.title + ". You can close this window." })
      ]);
    }
    var unanswered = scored.filter(function (i) { return state.answers[i] == null; });
    if (unanswered.length) {
      return el("section", { class: "screen results" }, [
        el("h2", { text: "Almost there" }),
        el("p", { class: "lead", text: "Answer all " + scored.length + " questions to see your result." }),
        el("button", { class: "btn primary", type: "button", text: "Go to question", onclick: function () { go(unanswered[0]); } })
      ]);
    }
    var sc = score(), pass = sc.pct >= C.mastery_score;
    complete(pass ? "passed" : "failed", sc.pct);
    var r = 64, circ = 2 * Math.PI * r, color = pass ? "var(--ok)" : "var(--bad)";
    var ring = el("div", { class: "ring" });
    ring.innerHTML = '<svg width="150" height="150" viewBox="0 0 150 150" aria-hidden="true">' +
      '<circle cx="75" cy="75" r="' + r + '" fill="none" stroke="var(--line)" stroke-width="12"/>' +
      '<circle cx="75" cy="75" r="' + r + '" fill="none" stroke="' + color + '" stroke-width="12" stroke-linecap="round" ' +
      'stroke-dasharray="' + circ + '" stroke-dashoffset="' + circ * (1 - sc.pct / 100) + '"/></svg>' +
      '<div class="val">' + sc.pct + '%</div>';
    var kids = [
      el("p", { class: "eyebrow", text: "Your result" }), ring,
      el("span", { class: "badge " + (pass ? "pass" : "fail"), text: pass ? "Passed" : "Not passed yet" }),
      el("p", { class: "lead", style: "margin-top:16px", text: "You answered " + sc.right + " of " + scored.length + " correctly. " +
        (pass ? "Well done! Your completion has been recorded." : "You need " + C.mastery_score + "% to pass. Review and try again.") })
    ];
    if (!pass) kids.push(el("button", { class: "btn primary", type: "button", text: "Retry the questions", onclick: retry }));
    return el("section", { class: "screen results" }, kids);
  }

  function retry() {
    scored.forEach(function (i) { delete state.answers[i]; state.done[i] = false; });
    go(scored[0]);
  }

  function complete(status, pct) {
    if (pct != null) {
      SCORM.set("cmi.core.score.min", "0"); SCORM.set("cmi.core.score.max", "100"); SCORM.set("cmi.core.score.raw", String(pct));
    }
    SCORM.set("cmi.core.lesson_status", status);
    state.completed = status === "passed" || status === "completed";
    save();
  }

  /* ---------- navigation ---------- */
  function updateNav() {
    var gated = state.i < C.screens.length && !state.done[state.i];
    btnBack.disabled = state.i === 0;
    btnNext.disabled = gated || state.i === RESULTS;
    btnNext.style.visibility = state.i === RESULTS ? "hidden" : "visible";
    var t = C.screens[state.i] ? C.screens[state.i].type : "";
    hint.textContent = !gated ? "" : ({ flipcards: "Flip all cards to continue", reveal: "Open every item to continue",
      match: "Match all pairs to continue", scenario: "Find the best response to continue", mcq: "Answer to continue" })[t] || "";
    var pct = Math.round((state.i / (total - 1)) * 100);
    document.getElementById("progress-fill").style.width = pct + "%";
    document.getElementById("progress-label").textContent = pct + "%";
  }

  function go(i) {
    state.i = Math.max(0, Math.min(i, total - 1));
    stage.innerHTML = "";
    var s = C.screens[state.i];
    stage.appendChild(state.i === RESULTS ? renderResults() : R[s.type](s));
    updateNav(); save();
    stage.focus({ preventScroll: true }); window.scrollTo(0, 0);
  }

  btnNext.addEventListener("click", function () { go(state.i + 1); });
  btnBack.addEventListener("click", function () { go(state.i - 1); });

  /* ---------- boot ---------- */
  document.title = C.title;
  document.getElementById("course-title").textContent = C.title;
  if (C.theme && C.theme.brand) document.documentElement.style.setProperty("--brand", C.theme.brand);

  SCORM.init();
  var st = SCORM.get("cmi.core.lesson_status");
  if (!st || st === "not attempted") SCORM.set("cmi.core.lesson_status", "incomplete");
  state.completed = st === "passed" || st === "completed";
  restore();
  // Preview only (no LMS): "#screen=N" opens screen N, so an editor can show the screen just changed.
  var jump = !SCORM.connected && /(?:^#|&)screen=(\d+)/.exec(location.hash);
  if (jump) state.i = Math.min(parseInt(jump[1], 10), total - 1);
  function leave() { SCORM.finish(state.completed ? "" : "suspend"); }
  window.addEventListener("pagehide", leave);
  window.addEventListener("beforeunload", leave);
  go(state.i);
})();
