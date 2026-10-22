/* Branding field: standard look (with a quick main colour), the deck's own branding, or a saved brand kit. */
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var sel = $("brandkit");
  function remember(v) { try { localStorage.setItem("ppt2scorm.brandkit", v); } catch (e) {} }
  function recall() { try { return localStorage.getItem("ppt2scorm.brandkit"); } catch (e) { return null; } }
  function render(selectId) {
    var keep = selectId || sel.value || recall() || "standard";
    sel.querySelectorAll("option.kit, optgroup").forEach(function (o) { o.remove(); });
    var kits = BrandKits.data.kits;
    if (kits.length) {
      var g = document.createElement("optgroup"); g.label = "Your brand kits";
      kits.forEach(function (k) { var o = document.createElement("option"); o.value = k.id; o.textContent = k.name; o.className = "kit"; g.appendChild(o); });
      sel.appendChild(g);
    }
    sel.value = [].some.call(sel.options, function (o) { return o.value === keep; }) ? keep : "standard";
    sync();
  }
  function sync() {
    var v = sel.value, kit = BrandKits.get(v), saving = BrandKits.data.saving;
    $("quick-colour").hidden = v !== "standard";
    $("bk-edit").hidden = !kit || !saving;
    $("bk-new").hidden = $("bk-import-label").hidden = !saving;
    $("brand-help").textContent = v === "standard" ? "Pick a main colour, or create a brand kit with your logo, colours and fonts." :
      v === "deck" ? "Uses the deck's theme colours, theme fonts and the logo repeated on its slides. You can save it as a kit afterwards." :
      kit ? kit.name + ": " + (kit.logo_url ? "logo, " : "") + "colours and fonts." : "";
    remember(v);
  }
  sel.addEventListener("change", sync);
  function ai() {
    var p = $("provider").value, key = $("api_key").value.trim();
    var info = (window.__cfg && window.__cfg.providers || []).filter(function (x) { return x.id === p; })[0];
    return key || (info && info.server_key) ? { provider: p, api_key: key } : {};
  }
  function deckFile() { return window.__chosenFile ? window.__chosenFile() : null; }
  $("bk-new").addEventListener("click", function () {
    BrandKits.open({ deckFile: deckFile, ai: ai }, function (saved) { render(saved ? saved.id : null); });
  });
  $("bk-edit").addEventListener("click", function () {
    BrandKits.open({ kit: BrandKits.get(sel.value), deckFile: deckFile, ai: ai }, function (saved) { render(saved ? saved.id : "standard"); });
  });
  $("bk-import").addEventListener("change", function () {
    var f = this.files[0]; this.value = ""; if (!f) return;
    $("brand-help").textContent = "Importing " + f.name + "…";
    BrandKits.importFile(f).then(function (saved) { render(saved.id); $("brand-help").textContent = "Imported " + saved.name + "."; })
      .catch(function (e) { $("brand-help").textContent = e.message; });
  });
  document.addEventListener("brandkits:changed", function () { render(); });
  BrandKits.load().catch(function () { sync(); });
  window.__brandChoice = function () { return sel.value; };
})();
