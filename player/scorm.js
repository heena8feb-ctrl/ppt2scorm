/* Minimal SCORM 1.2 runtime wrapper.
   Finds the LMS API (window.API) in parent frames / opener. If none is found the course
   runs in "preview mode" and all calls are no-ops, so the same files work outside an LMS. */
(function (global) {
  var api = null, started = 0, finished = false;

  function findAPI(win) {
    var tries = 0;
    while (win && !win.API && win.parent && win.parent !== win && tries < 10) { win = win.parent; tries++; }
    return win && win.API ? win.API : null;
  }

  function locate() {
    var a = findAPI(window);
    if (!a && window.opener) a = findAPI(window.opener);
    if (!a && window.top && window.top.opener) a = findAPI(window.top.opener);
    return a;
  }

  function pad(n, w) { n = String(n); while (n.length < w) n = "0" + n; return n; }

  function sessionTime() { // SCORM 1.2 CMITimespan: HHHH:MM:SS.SS
    var s = (Date.now() - started) / 1000;
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = (s % 60).toFixed(2);
    return pad(h, 4) + ":" + pad(m, 2) + ":" + pad(sec, 5);
  }

  var SCORM = {
    connected: false,
    init: function () {
      api = locate();
      started = Date.now();
      if (api) {
        this.connected = String(api.LMSInitialize("")) === "true";
      }
      return this.connected;
    },
    get: function (k) { return this.connected ? String(api.LMSGetValue(k) || "") : ""; },
    set: function (k, v) { return this.connected ? String(api.LMSSetValue(k, String(v))) === "true" : false; },
    commit: function () { return this.connected ? api.LMSCommit("") : false; },
    finish: function (exitMode) {
      if (!this.connected || finished) return;
      this.set("cmi.core.session_time", sessionTime());
      this.set("cmi.core.exit", exitMode || "");
      this.commit();
      api.LMSFinish("");
      finished = true;
    }
  };
  global.SCORM = SCORM;
})(window);
