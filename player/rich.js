/* Rich screens for the course player: section dividers, eight infographic layouts and the sort activity.
   window.RichScreens(api) returns renderers; api = { el, markDone, state, shuffled, sections }.
   Icons come from window.ICONS (Lucide, ISC licence). */
(function (global) {
  "use strict";
  var SVGNS = "http://www.w3.org/2000/svg";

  function icon(name, cls) {
    var body = (global.ICONS || {})[name];
    if (!body) return null;
    var s = document.createElementNS(SVGNS, "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("fill", "none"); s.setAttribute("stroke", "currentColor");
    s.setAttribute("stroke-width", "2"); s.setAttribute("stroke-linecap", "round"); s.setAttribute("stroke-linejoin", "round");
    s.setAttribute("aria-hidden", "true"); s.setAttribute("focusable", "false");
    s.setAttribute("class", "ico" + (cls ? " " + cls : ""));
    s.innerHTML = body;
    return s;
  }

  global.RichIcon = icon;

  global.RichScreens = function (api) {
    var el = api.el;
    var reduce = global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches;
    function stagger(node, i) { node.style.setProperty("--i", i); node.classList.add("ig-in"); return node; }
    function lines(t) { return (t || "").split(/\n|;\s*/).map(function (x) { return x.trim(); }).filter(Boolean); }

    /* ---------- section divider ---------- */
    function section(s) {
      api.markDone();
      var all = api.sections(), pos = all.indexOf(api.state.i);
      var big = s.icon ? el("div", { class: "sec-ico" }, [icon(s.icon)]) : null;
      return el("section", { class: "screen sec" }, [
        el("div", { class: "sec-text" }, [
          el("p", { class: "eyebrow", text: all.length > 1 ? "Part " + (pos + 1) + " of " + all.length : "Next up" }),
          el("h1", { text: s.title }),
          s.subtitle ? el("p", { class: "lead", text: s.subtitle }) : null]),
        big]);
    }

    /* ---------- infographics ---------- */
    var L = {};

    L.process = function (parts) {
      return el("ol", { class: "ig ig-process", style: "--n:" + parts.length }, parts.map(function (p, i) {
        return stagger(el("li", { class: "ig-step" }, [
          el("div", { class: "ig-badge" }, [p.icon ? icon(p.icon) : el("span", { class: "ig-num", text: String(i + 1) })]),
          el("div", { class: "ig-body" }, [el("span", { class: "ig-k", text: "Step " + (i + 1) }), el("h3", { text: p.title }), p.text ? el("p", { text: p.text }) : null])]), i);
      }));
    };

    L.timeline = function (parts) {
      return el("ol", { class: "ig ig-timeline" }, parts.map(function (p, i) {
        return stagger(el("li", null, [
          el("span", { class: "ig-when", text: p.value || "" }),
          el("span", { class: "ig-dot", "aria-hidden": "true" }, [p.icon ? icon(p.icon) : null]),
          el("div", { class: "ig-body" }, [el("h3", { text: p.title }), p.text ? el("p", { text: p.text }) : null])]), i);
      }));
    };

    function countUp(node, value) {
      var m = /^([^\d-]*)(-?[\d,]*\.?\d+)(.*)$/.exec(value || "");
      node.textContent = value;
      if (!m || reduce) return;
      var target = parseFloat(m[2].replace(/,/g, "")), dec = (m[2].split(".")[1] || "").length, t0 = null, dur = 900;
      if (!isFinite(target)) return;
      function fmt(v) { var s = v.toFixed(dec); return m[2].indexOf(",") >= 0 ? Number(s).toLocaleString("en-IN", { minimumFractionDigits: dec }) : s; }
      function tick(ts) {
        if (!node.isConnected) return;
        t0 = t0 || ts; var k = Math.min(1, (ts - t0) / dur), e = 1 - Math.pow(1 - k, 3);
        node.textContent = m[1] + fmt(target * e) + m[3];
        if (k < 1) requestAnimationFrame(tick);
      }
      requestAnimationFrame(tick);
    }

    L.stats = function (parts) {
      return el("div", { class: "ig ig-stats", style: "--n:" + parts.length }, parts.map(function (p, i) {
        var v = el("span", { class: "ig-value" });
        countUp(v, p.value);
        return stagger(el("div", { class: "ig-stat" }, [p.icon ? icon(p.icon, "ig-stat-ico") : null, v,
          el("h3", { text: p.title }), p.text ? el("p", { text: p.text }) : null]), i);
      }));
    };

    L.comparison = function (parts) {
      return el("div", { class: "ig ig-compare" }, parts.slice(0, 2).map(function (p, i) {
        var tone = p.tone || "";
        var mark = tone === "bad" ? "x" : "check";
        var head = icon(p.icon || (tone === "good" ? "circle-check" : tone === "bad" ? "circle-x" : ""));
        return stagger(el("div", { class: "ig-col" + (tone ? " is-" + tone : "") }, [
          el("div", { class: "ig-col-head" }, [head, el("h3", { text: p.title })]),
          el("ul", null, lines(p.text).map(function (t) {
            return el("li", null, [icon(mark, "ig-mark"), el("span", { text: t })]);
          }))]), i);
      }));
    };

    L.cycle = function (parts) {
      var n = parts.length, wrap = el("div", { class: "ig ig-cycle", style: "--n:" + n });
      var ring = el("div", { class: "ig-ring" });
      var svg = document.createElementNS(SVGNS, "svg");
      svg.setAttribute("viewBox", "0 0 100 100"); svg.setAttribute("class", "ig-ring-svg"); svg.setAttribute("aria-hidden", "true");
      var defs = '<defs><marker id="cy-arrow" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0 10 5 0 10z" fill="currentColor"/></marker></defs>';
      var arcs = "";
      for (var k = 0; k < n; k++) {
        var gap = Math.min(0.62, Math.PI / n * 0.55), a0 = (k / n) * 2 * Math.PI - Math.PI / 2 + gap, a1 = ((k + 1) / n) * 2 * Math.PI - Math.PI / 2 - gap;
        arcs += '<path d="M' + (50 + 38 * Math.cos(a0)).toFixed(2) + " " + (50 + 38 * Math.sin(a0)).toFixed(2) + " A38 38 0 0 1 " +
          (50 + 38 * Math.cos(a1)).toFixed(2) + " " + (50 + 38 * Math.sin(a1)).toFixed(2) + '" marker-end="url(#cy-arrow)"/>';
      }
      svg.innerHTML = defs + '<g fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round">' + arcs + "</g>";
      ring.appendChild(svg);
      var detail = el("div", { class: "ig-cycle-detail", "aria-live": "polite" });
      var btns = [];
      function show(i) {
        btns.forEach(function (b, j) { b.setAttribute("aria-pressed", String(j === i)); });
        detail.innerHTML = "";
        detail.appendChild(el("span", { class: "ig-k", text: (i + 1) + " of " + n }));
        detail.appendChild(el("h3", { text: parts[i].title }));
        if (parts[i].text) detail.appendChild(el("p", { text: parts[i].text }));
      }
      parts.forEach(function (p, i) {
        var a = (i / n) * 2 * Math.PI - Math.PI / 2;
        var b = el("button", { type: "button", class: "ig-node", "aria-pressed": "false",
          style: "left:" + (50 + 38 * Math.cos(a)).toFixed(2) + "%;top:" + (50 + 38 * Math.sin(a)).toFixed(2) + "%" },
          [el("span", { class: "ig-node-ico" }, [p.icon ? icon(p.icon) : el("span", { class: "ig-num", text: String(i + 1) })]), el("span", { class: "ig-node-t", text: p.title })]);
        b.addEventListener("click", function () { show(i); });
        btns.push(stagger(b, i));
        ring.appendChild(b);
      });
      ring.appendChild(el("div", { class: "ig-ring-center" }, [icon("refresh-cw")]));
      wrap.appendChild(ring);
      wrap.appendChild(el("div", { class: "ig-cycle-side" }, [el("p", { class: "ig-hint", text: "Select a stage to read about it." }), detail]));
      show(0);
      return wrap;
    };

    L.pyramid = function (parts) {
      var n = parts.length;
      return el("ol", { class: "ig ig-pyramid" }, parts.map(function (p, i) {
        var w = 38 + (62 * i) / Math.max(1, n - 1);
        return stagger(el("li", { style: "--w:" + w.toFixed(1) + "%;--lvl:" + (i / Math.max(1, n - 1)).toFixed(2) }, [
          el("div", { class: "ig-band" }, [p.icon ? icon(p.icon) : null, el("span", { text: p.title })]),
          el("div", { class: "ig-band-text" }, [el("h3", { text: p.title }), p.text ? el("p", { text: p.text }) : null])]), i);
      }));
    };

    L.grid = function (parts) {
      return el("div", { class: "ig ig-grid", style: "--n:" + parts.length }, parts.map(function (p, i) {
        return stagger(el("div", { class: "ig-card" }, [el("span", { class: "ig-chip" }, [icon(p.icon || "sparkles")]),
          el("h3", { text: p.title }), p.text ? el("p", { text: p.text }) : null]), i);
      }));
    };

    L.checklist = function (parts) {
      var ticked = {}, fb = el("div", { "aria-live": "polite" });
      var list = el("ul", { class: "ig ig-check" }, parts.map(function (p, i) {
        var b = el("button", { type: "button", role: "checkbox", "aria-checked": "false", class: "ig-tick" }, [
          el("span", { class: "ig-box", "aria-hidden": "true" }, [icon("check")]),
          p.icon ? icon(p.icon, "ig-tick-ico") : null,
          el("span", { class: "ig-body" }, [el("strong", { text: p.title }), p.text ? el("span", { class: "ig-sub", text: p.text }) : null])]);
        b.addEventListener("click", function () {
          var on = b.getAttribute("aria-checked") !== "true";
          b.setAttribute("aria-checked", String(on));
          if (on) ticked[i] = true; else delete ticked[i];
          if (Object.keys(ticked).length === parts.length && !api.state.done[api.state.i]) {
            fb.className = "feedback ok"; fb.textContent = "All set. You've got a checklist for the job."; api.markDone();
          }
        });
        return stagger(el("li", null, [b]), i);
      }));
      return el("div", null, [list, fb]);
    };

    function infographic(s) {
      var lay = L[s.layout] ? s.layout : "grid";
      if (lay !== "checklist") api.markDone();
      var eyebrow = { process: "How it works", timeline: "Timeline", stats: "By the numbers", comparison: "Compare",
        cycle: "The cycle", pyramid: "What matters most", grid: "Key ideas", checklist: "Checklist" }[lay];
      return el("section", { class: "screen ig-screen ig-" + lay + "-screen" }, [
        el("p", { class: "eyebrow", text: eyebrow }), el("h2", { text: s.title }),
        s.intro ? el("p", { class: "lead", text: s.intro }) : null,
        L[lay](s.parts || [], s)]);
    }

    /* ---------- sort activity ---------- */
    function sort(s) {
      var placed = {}, sel = null, fb = el("div", { "aria-live": "polite" });
      var terms = api.shuffled(s.terms.map(function (t, k) { return { t: t.text, b: t.bucket, k: k }; }), api.state.i + 11);
      var pool = el("div", { class: "sort-pool", "aria-label": "Items to sort" });
      var check = el("button", { class: "btn primary", type: "button", text: "Check my answers", disabled: "disabled" });
      var chips = {};
      function chip(o) {
        var c = el("button", { type: "button", class: "sort-chip", draggable: "true", "aria-pressed": "false", text: o.t });
        c.dataset.k = o.k;
        c.addEventListener("click", function (e) {
          e.stopPropagation();
          if (c.parentNode !== pool) { pool.appendChild(c); delete placed[o.k]; refresh(); return; }  // tap a placed item to take it back
          if (sel) sel.setAttribute("aria-pressed", "false");
          sel = sel === c ? null : c;
          if (sel) sel.setAttribute("aria-pressed", "true");
          zones.forEach(function (z) { z.classList.toggle("is-ready", !!sel); });
        });
        c.addEventListener("dragstart", function (e) { e.dataTransfer.setData("text/plain", o.k); c.classList.add("is-drag"); });
        c.addEventListener("dragend", function () { c.classList.remove("is-drag"); });
        chips[o.k] = c;
        return c;
      }
      terms.forEach(function (o) { pool.appendChild(chip(o)); });
      function place(k, bi) {
        var c = chips[k]; if (!c) return;
        zones[bi].querySelector(".sort-items").appendChild(c);
        c.setAttribute("aria-pressed", "false"); c.classList.remove("is-wrong", "is-right");
        placed[k] = bi; sel = null;
        zones.forEach(function (z) { z.classList.remove("is-ready"); });
        refresh();
      }
      var zones = s.buckets.map(function (name, bi) {
        var z = el("div", { class: "sort-zone", role: "button", tabindex: "0", "aria-label": "Put the selected item in " + name }, [
          el("h3", { text: name }), el("div", { class: "sort-items" })]);
        z.addEventListener("click", function () { if (sel) place(sel.dataset.k, bi); });
        z.addEventListener("keydown", function (e) { if ((e.key === "Enter" || e.key === " ") && sel) { e.preventDefault(); place(sel.dataset.k, bi); } });
        z.addEventListener("dragover", function (e) { e.preventDefault(); z.classList.add("is-over"); });
        z.addEventListener("dragleave", function () { z.classList.remove("is-over"); });
        z.addEventListener("drop", function (e) { e.preventDefault(); z.classList.remove("is-over"); place(e.dataTransfer.getData("text/plain"), bi); });
        return z;
      });
      function refresh() {
        var left = pool.children.length;
        check.disabled = left > 0;
        pool.classList.toggle("is-empty", left === 0);
        if (left) { fb.className = ""; fb.textContent = left + (left === 1 ? " item" : " items") + " left to sort."; }
        else if (!api.state.done[api.state.i]) { fb.className = ""; fb.textContent = "All sorted. Check your answers."; }
      }
      check.addEventListener("click", function () {
        var wrong = [];
        s.terms.forEach(function (t, k) {
          var c = chips[k], ok = placed[k] === t.bucket;
          c.classList.toggle("is-right", ok); c.classList.toggle("is-wrong", !ok);
          if (!ok) wrong.push(k);
        });
        if (!wrong.length) {
          fb.className = "feedback ok"; fb.innerHTML = ""; fb.appendChild(el("strong", { text: "Spot on." }));
          fb.appendChild(el("span", { text: s.feedback || "Every item is in the right group." }));
          Object.keys(chips).forEach(function (k) { chips[k].disabled = true; chips[k].draggable = false; });
          check.style.display = "none"; api.markDone();
        } else {
          fb.className = "feedback bad"; fb.innerHTML = "";
          fb.appendChild(el("strong", { text: wrong.length + (wrong.length === 1 ? " item is" : " items are") + " in the wrong group." }));
          fb.appendChild(el("span", { text: "They're marked in red and go back to the list. Try again." }));
          setTimeout(function () { wrong.forEach(function (k) { chips[k].classList.remove("is-wrong"); pool.appendChild(chips[k]); delete placed[k]; }); refresh(); }, 1400);
          check.disabled = true;
        }
      });
      refresh();
      return el("section", { class: "screen" }, [
        el("p", { class: "eyebrow", text: "Sort it out" }), el("h2", { text: s.title }),
        el("p", { class: "instruction", text: (s.instruction || "Sort each item into the right group.") + " Select an item, then the group it belongs to, or drag it." }),
        pool, el("div", { class: "sort-zones", style: "--n:" + s.buckets.length }, zones),
        el("div", { class: "actions" }, [check]), fb]);
    }

    return { section: section, infographic: infographic, sort: sort };
  };
})(window);
