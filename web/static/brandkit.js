/* Brand kit dialog: create, edit, detect (website or deck), import and export brand kits.
   BrandKits.load() -> Promise of {kits, stacks, saving}
   BrandKits.open({ kit?: summary to edit, draft?: draft summary, deckFile?: () => File, ai?: () => {provider, api_key} }, onSaved)
   BrandKits.importFile(file) -> Promise of saved summary */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var data = { kits: [], stacks: [], saving: true, def: null };
  var st = null;        // dialog state
  var onSaved = null, opts = {};
  var fontSeq = 0;

  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v === false || v == null) return;
      if (k === "text") n.textContent = v; else if (k === "class") n.className = v;
      else if (k.indexOf("on") === 0) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v === true ? "" : v);
    });
    (kids || []).forEach(function (c) { if (c != null) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return n;
  }
  function api(method, url, body, isForm) {
    return fetch(url, { method: method, body: isForm ? body : body ? JSON.stringify(body) : undefined,
      headers: body && !isForm ? { "Content-Type": "application/json" } : {} })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.detail || "That didn't work. Try again."); return j; }); });
  }

  /* ---------- colour maths (mirrors ppt2scorm/brand.py) ---------- */
  function rgb(h) { h = h.replace("#", ""); return [0, 2, 4].map(function (i) { return parseInt(h.substr(i, 2), 16) / 255; }); }
  function hex(c) { return "#" + c.map(function (x) { return ("0" + Math.round(Math.max(0, Math.min(1, x)) * 255).toString(16)).slice(-2); }).join(""); }
  function lum(h) { return rgb(h).map(function (c) { return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); })
    .reduce(function (a, c, i) { return a + c * [0.2126, 0.7152, 0.0722][i]; }, 0); }
  function contrast(a, b) { var x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }
  function mix(a, b, t) { var x = rgb(a), y = rgb(b); return hex(x.map(function (v, i) { return v + (y[i] - v) * t; })); }
  function readableOn(bg) { return contrast(bg, "#ffffff") >= contrast(bg, "#111827") ? "#ffffff" : "#111827"; }
  function shade(fg, bg, target) {
    var c = fg, toward = lum(bg) > 0.4 ? "#000000" : "#ffffff";
    for (var i = 0; i < 30 && contrast(c, bg) < target; i++) c = mix(c, toward, 0.06);
    return c;
  }
  function derived(k) {
    var c = k.colors, dark = lum(c.background) < 0.25, card = dark ? mix(c.background, "#ffffff", 0.08) : "#ffffff";
    var ink = contrast(c.text, card) < 4.5 ? readableOn(card) : c.text;
    return { brand: c.primary, brandInk: readableOn(c.primary), accentText: shade(c.accent, card, 4.5), ink: ink,
      muted: mix(ink, card, 0.38), bg: c.background, card: card, line: mix(ink, card, 0.86) };
  }
  function warnings(k) {
    var c = k.colors, d = derived(k), out = [];
    if (contrast(c.text, d.card) < 4.5) out.push("The text colour is hard to read on the page, so courses will use " + (d.ink === "#ffffff" ? "white" : "near-black") + " text.");
    if (contrast(c.primary, d.brandInk) < 3) out.push("Buttons in the main colour will have low contrast. A darker or lighter main colour reads better.");
    if (contrast(c.accent, d.card) < 3) out.push("The accent colour is faint on white, so small labels use a darker shade of it.");
    return out;
  }
  function stackCss(f) {
    var s = data.stacks.filter(function (x) { return x.id === (f.stack || f.fallback || "system"); })[0];
    var base = s ? s.css : "system-ui, sans-serif";
    return f.family ? '"' + f.family + '", ' + base : base;
  }

  /* ---------- dialog ---------- */
  function build() {
    if ($("bk")) return;
    var colourRow = function (key, label, help) {
      return el("div", { class: "bk-colour" }, [
        el("label", { for: "bk-c-" + key, text: label }),
        el("div", { class: "bk-colour-in" }, [
          el("input", { type: "color", id: "bk-p-" + key, "aria-label": label + " picker" }),
          el("input", { type: "text", id: "bk-c-" + key, maxlength: 7, spellcheck: "false" })]),
        help ? el("small", { text: help }) : null]);
    };
    var fontRow = function (role, label) {
      return el("div", { class: "bk-font", id: "bk-font-" + role }, [
        el("label", { for: "bk-f-" + role, text: label }),
        el("select", { id: "bk-f-" + role }),
        el("div", { class: "bk-custom", id: "bk-custom-" + role }, [
          el("label", { for: "bk-fam-" + role, class: "bk-sub", text: "Font name" }),
          el("input", { type: "text", id: "bk-fam-" + role, maxlength: 60, placeholder: "For example: Whirlpool Sans" }),
          el("ul", { class: "bk-files", id: "bk-files-" + role }),
          el("label", { class: "bk-filebtn", for: "bk-up-" + role, text: "Add font files" }),
          el("input", { type: "file", id: "bk-up-" + role, class: "bk-hidden", multiple: true, accept: ".woff2,.woff,.ttf,.otf" }),
          el("small", { text: "WOFF2, WOFF, TTF or OTF, up to 3 MB each. Add regular and bold. Files are bundled in each course, so make sure your licence allows that." })])]);
    };
    var dlg = el("dialog", { id: "bk", class: "bk", "aria-labelledby": "bk-title" }, [
      el("form", { method: "dialog", id: "bk-form", novalidate: true }, [
        el("div", { class: "bk-head" }, [el("h2", { id: "bk-title", text: "Brand kit" }),
          el("button", { type: "button", class: "bk-x", "aria-label": "Close", text: "×", onclick: close })]),
        el("div", { class: "bk-body" }, [
          el("div", { class: "bk-fields" }, [
            el("section", { class: "bk-auto", id: "bk-auto" }, [
              el("h3", { text: "Fill it in automatically" }),
              el("label", { for: "bk-url", text: "Company website" }),
              el("div", { class: "bk-url" }, [
                el("input", { type: "url", id: "bk-url", placeholder: "www.yourcompany.com", inputmode: "url", autocomplete: "url" }),
                el("button", { type: "button", class: "btn", id: "bk-read", text: "Read website", onclick: fromWebsite })]),
              el("p", { class: "bk-or" }, [el("button", { type: "button", class: "bk-link", id: "bk-deck", text: "Use the branding in my deck", onclick: fromDeck })]),
              el("div", { id: "bk-auto-msg", class: "bk-auto-msg", role: "status", "aria-live": "polite" })]),
            el("div", { class: "bk-field" }, [el("label", { for: "bk-name", text: "Kit name" }), el("input", { type: "text", id: "bk-name", maxlength: 60 })]),
            el("section", { class: "bk-group" }, [el("h3", { text: "Logo" }),
              el("div", { class: "bk-logo" }, [
                el("div", { class: "bk-logo-box" }, [el("img", { id: "bk-logo-img", alt: "" }), el("span", { id: "bk-logo-none", text: "No logo" })]),
                el("div", { class: "bk-logo-side" }, [
                  el("div", { class: "bk-row" }, [
                    el("label", { class: "bk-filebtn", for: "bk-logo", text: "Upload logo" }),
                    el("input", { type: "file", id: "bk-logo", class: "bk-hidden", accept: "image/png,image/jpeg,image/webp" }),
                    el("button", { type: "button", class: "bk-link", id: "bk-logo-rm", text: "Remove", onclick: removeLogo })]),
                  el("small", { text: "PNG, JPG or WebP. A transparent PNG looks best. SVG isn't accepted." }),
                  el("label", { for: "bk-logo-alt", class: "bk-sub", text: "Logo description (read by screen readers)" }),
                  el("input", { type: "text", id: "bk-logo-alt", maxlength: 120 })])])]),
            el("section", { class: "bk-group" }, [el("h3", { text: "Colours" }),
              el("div", { class: "bk-colours" }, [colourRow("primary", "Main", "Buttons, progress, welcome screen"),
                colourRow("accent", "Accent", "Labels and highlights"), colourRow("text", "Text"), colourRow("background", "Page background")])]),
            el("section", { class: "bk-group" }, [el("h3", { text: "Fonts" }), fontRow("heading", "Headings"), fontRow("body", "Body text")]),
            el("section", { class: "bk-group" }, [el("h3", { id: "bk-corners-label", text: "Corners" }),
              el("div", { class: "seg", role: "radiogroup", "aria-labelledby": "bk-corners-label" }, ["rounded", "soft", "square"].map(function (c) {
                return el("label", null, [el("input", { type: "radio", name: "bk-corners", value: c }), c[0].toUpperCase() + c.slice(1)]);
              }))])]),
          el("div", { class: "bk-side" }, [
            el("p", { class: "bk-sub", text: "Preview" }),
            el("div", { class: "bk-preview", id: "bk-preview", "aria-hidden": "true" }, [
              el("div", { class: "pv-top" }, [el("img", { id: "pv-logo", alt: "" }), el("span", { class: "pv-dot", id: "pv-dot" }), el("b", { class: "pv-title", text: "Product Safety Basics" }),
                el("span", { class: "pv-bar" }, [el("i")])]),
              el("div", { class: "pv-card" }, [el("p", { class: "pv-eye", text: "Scenario" }), el("p", { class: "pv-h", text: "A customer reports a burning smell" }),
                el("p", { class: "pv-t", text: "The washing machine is still running. What should the technician do first?" }),
                el("div", { class: "pv-choice is-on" }, [el("span", { text: "A" }), "Switch off and unplug the machine"]),
                el("div", { class: "pv-choice" }, [el("span", { text: "B" }), "Open the door to check the drum"]),
                el("div", { class: "pv-btns" }, [el("span", { class: "pv-btn ghost", text: "Back" }), el("span", { class: "pv-btn", text: "Next" })])])]),
            el("ul", { class: "bk-warn", id: "bk-warn" })])]),
        el("p", { class: "bk-error", id: "bk-error", role: "alert", hidden: true }),
        el("div", { class: "bk-foot" }, [
          el("button", { type: "button", class: "bk-link danger", id: "bk-delete", text: "Delete kit", onclick: del }),
          el("a", { class: "bk-link", id: "bk-export", href: "#", download: "", text: "Export kit file" }),
          el("span", { class: "bk-spacer" }),
          el("button", { type: "button", class: "btn", text: "Cancel", onclick: close }),
          el("button", { type: "submit", class: "btn primary", id: "bk-save", text: "Save brand kit" })])])]);
    document.body.appendChild(dlg);

    ["primary", "accent", "text", "background"].forEach(function (k) {
      $("bk-p-" + k).addEventListener("input", function () { $("bk-c-" + k).value = this.value; st.kit.colors[k] = this.value; paint(); });
      $("bk-c-" + k).addEventListener("input", function () { if (/^#[0-9a-f]{6}$/i.test(this.value)) { $("bk-p-" + k).value = this.value; st.kit.colors[k] = this.value.toLowerCase(); paint(); } });
    });
    ["heading", "body"].forEach(function (role) {
      $("bk-f-" + role).addEventListener("change", function () {
        var v = this.value, f = st.kit.fonts[role];
        st.kit.fonts[role] = v === "custom" ? { family: f.family || "", files: f.files || [], fallback: f.fallback || "system" } : { stack: v };
        if (v !== "custom") st.newFonts[role] = [];
        showFonts(); paint();
      });
      $("bk-fam-" + role).addEventListener("input", function () { st.kit.fonts[role].family = this.value; paint(); });
      $("bk-up-" + role).addEventListener("change", function () { addFonts(role, this.files); this.value = ""; });
    });
    $("bk-logo").addEventListener("change", function () {
      var f = this.files[0]; if (!f) return;
      st.logoFile = f; st.removeLogo = false;
      var url = URL.createObjectURL(f); setLogo(url); this.value = "";
    });
    $("bk-name").addEventListener("input", function () { st.kit.name = this.value; });
    $("bk-logo-alt").addEventListener("input", function () { st.kit.logo_alt = this.value; });
    dlg.addEventListener("change", function (e) { if (e.target.name === "bk-corners") { st.kit.corners = e.target.value; paint(); } });
    $("bk-form").addEventListener("submit", function (e) { e.preventDefault(); save(); });
    $("bk-url").addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); fromWebsite(); } });
    dlg.addEventListener("cancel", function (e) { if (st && st.busy) e.preventDefault(); });
  }

  function setLogo(url) {
    $("bk-logo-img").hidden = !url; $("bk-logo-none").hidden = !!url; $("bk-logo-rm").hidden = !url;
    if (url) $("bk-logo-img").src = url; else $("bk-logo-img").removeAttribute("src");
    $("pv-logo").hidden = !url; $("pv-dot").hidden = !!url;
    if (url) $("pv-logo").src = url;
  }
  function removeLogo() { st.logoFile = null; st.removeLogo = true; setLogo(null); }

  function loadFace(family, src) {
    try {
      var face = new FontFace(family, src);
      face.load().then(function (f) { document.fonts.add(f); paint(); }).catch(function () {});
    } catch (e) {}
  }

  function addFonts(role, files) {
    var f = st.kit.fonts[role];
    if (!f.family) { msg("Type the font's name first, then add its files."); $("bk-fam-" + role).focus(); return; }
    Array.prototype.forEach.call(files, function (file) {
      if (!/\.(woff2|woff|ttf|otf)$/i.test(file.name)) { msg(file.name + " isn't a font file. Use WOFF2, WOFF, TTF or OTF."); return; }
      if (file.size > 3 * 1024 * 1024) { msg(file.name + " is over 3 MB."); return; }
      st.newFonts[role].push(file);
      file.arrayBuffer().then(function (buf) { loadFace(f.family, buf); });
    });
    showFonts(); paint();
  }

  function showFonts() {
    ["heading", "body"].forEach(function (role) {
      var f = st.kit.fonts[role], custom = !!f.family || f.stack === undefined;
      $("bk-f-" + role).value = custom ? "custom" : f.stack;
      $("bk-custom-" + role).hidden = !custom;
      if (!custom) return;
      $("bk-fam-" + role).value = f.family || "";
      var ul = $("bk-files-" + role); ul.innerHTML = "";
      (f.files || []).forEach(function (x, i) {
        ul.appendChild(el("li", null, [x.file.replace(/^.*?-([0-9a-f]{8})/, f.family + " ") + " (" + (x.weight === 700 ? "bold" : x.weight === 400 ? "regular" : x.weight) + (x.style === "italic" ? " italic" : "") + ")",
          el("button", { type: "button", class: "bk-link", text: "Remove", onclick: function () { f.files.splice(i, 1); showFonts(); paint(); } })]));
      });
      st.newFonts[role].forEach(function (file, i) {
        ul.appendChild(el("li", null, [file.name + " (new)",
          el("button", { type: "button", class: "bk-link", text: "Remove", onclick: function () { st.newFonts[role].splice(i, 1); showFonts(); paint(); } })]));
      });
      if (!(f.files || []).length && !st.newFonts[role].length) ul.appendChild(el("li", { class: "bk-nofiles", text: "No files yet: the font shows only on computers that have it installed." }));
    });
  }

  function paint() {
    var k = st.kit, d = derived(k), pv = $("bk-preview"), rs = { rounded: 1, soft: 0.5, square: 0 }[k.corners];
    var vars = { "--pv-brand": d.brand, "--pv-brand-ink": d.brandInk, "--pv-accent": d.accentText, "--pv-ink": d.ink, "--pv-muted": d.muted,
      "--pv-bg": d.bg, "--pv-card": d.card, "--pv-line": d.line, "--pv-rs": rs,
      "--pv-head": stackCss(k.fonts.heading), "--pv-body": stackCss(k.fonts.body) };
    Object.keys(vars).forEach(function (v) { pv.style.setProperty(v, vars[v]); });
    var w = $("bk-warn"); w.innerHTML = "";
    warnings(k).forEach(function (t) { w.appendChild(el("li", { text: t })); });
  }

  function msg(t, kind) { var m = $("bk-auto-msg"); m.innerHTML = ""; m.className = "bk-auto-msg" + (kind ? " is-" + kind : ""); if (t) m.appendChild(el("p", { text: t })); }
  function error(t) { $("bk-error").textContent = t || ""; $("bk-error").hidden = !t; }

  function fill(kit, assetBase, logoUrl) {
    st.kit = JSON.parse(JSON.stringify(kit));
    st.kit.fonts.heading = st.kit.fonts.heading || { stack: "system" };
    st.kit.fonts.body = st.kit.fonts.body || { stack: "system" };
    st.newFonts = { heading: [], body: [] };
    $("bk-name").value = st.kit.name || "";
    $("bk-logo-alt").value = st.kit.logo_alt || "";
    ["primary", "accent", "text", "background"].forEach(function (c) { $("bk-c-" + c).value = $("bk-p-" + c).value = st.kit.colors[c]; });
    document.querySelectorAll("input[name=bk-corners]").forEach(function (r) { r.checked = r.value === (st.kit.corners || "rounded"); });
    setLogo(logoUrl || null);
    ["heading", "body"].forEach(function (role) {
      var f = st.kit.fonts[role];
      (f.files || []).forEach(function (x) { if (assetBase) loadFace(f.family, "url(" + assetBase + x.file + ")"); });
    });
    showFonts(); paint();
  }

  function fromWebsite() {
    var url = $("bk-url").value.trim();
    if (!url) { msg("Type the company's website address, for example www.yourcompany.com."); $("bk-url").focus(); return; }
    var ai = opts.ai ? opts.ai() : {};
    st.busy = true; $("bk-read").disabled = true; $("bk-deck").disabled = true;
    msg("Reading " + url + " and its stylesheets" + (ai.provider ? ", then asking the AI to pick the brand…" : "…"));
    api("POST", "/api/brand-drafts/website", { url: url, provider: ai.provider || null, api_key: ai.api_key || "" }).then(applyDraft)
      .catch(function (e) { msg(e.message, "error"); })
      .then(function () { st.busy = false; $("bk-read").disabled = false; $("bk-deck").disabled = !(opts.deckFile && opts.deckFile()); });
  }
  function fromDeck() {
    var f = opts.deckFile && opts.deckFile(); if (!f) return;
    var fd = new FormData(); fd.append("file", f);
    st.busy = true; msg("Reading the theme and logo in " + f.name + "…");
    api("POST", "/api/brand-drafts/deck", fd, true).then(applyDraft).catch(function (e) { msg(e.message, "error"); })
      .then(function () { st.busy = false; });
  }
  function applyDraft(d) {
    st.source = "draft:" + d.draft_id; st.logoFile = null; st.removeLogo = false;
    fill({ name: d.name, colors: d.colors, fonts: d.fonts, corners: d.corners, logo_alt: d.logo_alt }, d.font_base, d.logo_url);
    var m = $("bk-auto-msg"); m.innerHTML = ""; m.className = "bk-auto-msg is-ok";
    m.appendChild(el("p", null, [el("strong", { text: "Filled in from " + d.how + ". " }), "Check everything below, then save."]));
    if (d.notes && d.notes.length) m.appendChild(el("ul", null, d.notes.map(function (n) { return el("li", { text: n }); })));
  }

  function save() {
    error("");
    var k = st.kit;
    if (!(k.name || "").trim()) { error("Give the kit a name."); $("bk-name").focus(); return; }
    var fd = new FormData(), kit = JSON.parse(JSON.stringify(k));
    ["heading", "body"].forEach(function (role) {
      var f = kit.fonts[role];
      if (f.stack === undefined && !(f.family || "").trim()) { f = kit.fonts[role] = { stack: "system" }; }
      if (f.family) { f.family = f.family.trim(); f.files = f.files || []; }
      st.newFonts[role].forEach(function (file) { fd.append(role + "_files", file, file.name); });
    });
    if (!st.editing) delete kit.id;
    fd.append("kit", JSON.stringify(kit));
    fd.append("source", st.source || "");
    if (st.editing) fd.append("replace", "1");
    if (st.logoFile) fd.append("logo", st.logoFile, st.logoFile.name);
    if (st.removeLogo) fd.append("remove_logo", "1");
    $("bk-save").disabled = true; $("bk-save").textContent = "Saving…";
    api("POST", "/api/brands", fd, true).then(function (saved) {
      return BrandKits.load().then(function () { close(); if (onSaved) onSaved(saved); });
    }).catch(function (e) { error(e.message); })
      .then(function () { $("bk-save").disabled = false; $("bk-save").textContent = "Save brand kit"; });
  }

  function del() {
    if (!st.editing || !window.confirm("Delete the brand kit “" + st.kit.name + "”? Courses already made with it keep their look.")) return;
    api("DELETE", "/api/brands/" + st.editing).then(function () { return BrandKits.load(); })
      .then(function () { close(); if (onSaved) onSaved(null); }).catch(function (e) { error(e.message); });
  }

  function close() { var d = $("bk"); if (d && d.open) d.close(); }

  window.BrandKits = {
    data: data,
    load: function () {
      return api("GET", "/api/brands").then(function (j) {
        data.kits = j.kits; data.stacks = j.stacks; data.saving = j.saving; data.def = j.default;
        document.dispatchEvent(new CustomEvent("brandkits:changed"));
        return data;
      });
    },
    get: function (id) { return data.kits.filter(function (k) { return k.id === id; })[0]; },
    open: function (o, cb) {
      opts = o || {}; onSaved = cb; build();
      ["heading", "body"].forEach(function (role) {
        var sel = $("bk-f-" + role); sel.innerHTML = "";
        data.stacks.forEach(function (s) { sel.appendChild(el("option", { value: s.id, text: s.label })); });
        sel.appendChild(el("option", { value: "custom", text: "Brand font (upload files)" }));
      });
      st = { kit: null, source: "", editing: null, logoFile: null, removeLogo: false, newFonts: { heading: [], body: [] }, busy: false };
      var k = opts.kit;
      $("bk-title").textContent = k ? "Edit brand kit" : "New brand kit";
      $("bk-auto").hidden = !!k;
      $("bk-deck").disabled = !(opts.deckFile && opts.deckFile());
      $("bk-deck").textContent = opts.deckFile && opts.deckFile() ? "Or use the branding in " + opts.deckFile().name : "Or use the branding in my deck (choose a deck first)";
      $("bk-delete").hidden = !k; $("bk-export").hidden = !k;
      msg(""); error(""); $("bk-url").value = "";
      if (k) {
        st.editing = k.id; st.source = "kit:" + k.id;
        $("bk-export").href = "/api/brands/" + k.id + "/export";
        fill(k, k.font_base, k.logo_url);
      } else {
        fill(JSON.parse(JSON.stringify(data.def || { name: "", colors: { primary: "#1f5eff", accent: "#1f5eff", text: "#17202e", background: "#f3f5f9" },
          fonts: { heading: { stack: "system" }, body: { stack: "system" } }, corners: "rounded", logo_alt: "" })));
        $("bk-name").value = st.kit.name = "";
      }
      $("bk").showModal();
      (k ? $("bk-name") : $("bk-url")).focus();
    },
    importFile: function (file) {
      var fd = new FormData(); fd.append("file", file);
      return api("POST", "/api/brands/import", fd, true).then(function (saved) { return BrandKits.load().then(function () { return saved; }); });
    }
  };
})();
