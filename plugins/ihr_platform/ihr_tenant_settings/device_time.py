"""The "this computer is not on India Standard Time" warning bar (P1-T7b, owner 2026-11-08).

The web app shows and takes every date and time in the device's own zone, so a computer set
to another zone shows wrong times and saves wrong ones. Staff computers must be on IST; this
script, appended to ``bootstrap.js`` (same origin, so the CSP allows it; inline style
attributes are allowed by ``style-src 'unsafe-inline'``), shows a bar in English and Hindi
until the device is fixed. It never blocks work, and is re-checked every minute and whenever
the tab comes back into view.

Which devices count as IST: the offset now must be +05:30 AND the zone the browser names
must be Asia/Kolkata (or its old name Asia/Calcutta). A zone that merely shares the offset
today (Asia/Colombo) is still warned about: its rules differ for other dates (Colombo was
+06:00 in 1996-2006), the app works out past and future times with the device's rules, and
the owner asked for the device to be set to IST. If the browser cannot name its zone, the
offset alone decides.

The bar is fixed at the top and reserves its height: the page is pushed down
(``body`` padding) and so are the app's full-height fixed panels (the side bar and side
sheets, Tailwind ``fixed inset-y-0``), so it covers none of the app's controls.

The script must never contain a less-than sign: bootstrap.js is checked for that (it can then
never close a script tag), so comparisons are written the other way round.
"""

WARNING_SCRIPT = r"""(function () {
  "use strict";
  var IST_ZONES = ["Asia/Kolkata", "Asia/Calcutta"];
  var IST_OFFSET = -330; /* Date#getTimezoneOffset: minutes west of UTC */
  var BAR_ID = "ihr-tz-warning";
  var STYLE_ID = "ihr-tz-warning-style";
  function zoneName() {
    try {
      var zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
      return zone ? String(zone) : "";
    } catch (e) {
      return "";
    }
  }
  function two(n) {
    return (n > 9 ? "" : "0") + n;
  }
  function offsetLabel(offset) {
    var east = -offset;
    var sign = east >= 0 ? "+" : "-";
    var minutes = Math.abs(east);
    return "UTC" + sign + two(Math.floor(minutes / 60)) + ":" + two(minutes % 60);
  }
  function deviceIsIst(zone, offset) {
    if (offset !== IST_OFFSET) return false;
    return !zone || IST_ZONES.indexOf(zone) !== -1;
  }
  function reserve(height) {
    var style = document.getElementById(STYLE_ID);
    if (!style) {
      style = document.createElement("style");
      style.id = STYLE_ID;
      (document.head || document.documentElement).appendChild(style);
    }
    var h = Math.max(0, Math.ceil(height)) + "px";
    style.textContent =
      "body{padding-top:" + h + " !important}" +
      "html{scroll-padding-top:" + h + "}" +
      ".fixed.inset-y-0{top:" + h + " !important;height:auto !important}";
  }
  function show(zone, offset) {
    var label = (zone ? zone + " (" : "(") + offsetLabel(offset) + ")";
    var bar = document.getElementById(BAR_ID);
    if (!bar) {
      bar = document.createElement("div");
      bar.id = BAR_ID;
      bar.setAttribute("role", "alert");
      bar.setAttribute(
        "style",
        "position:fixed;top:0;left:0;right:0;z-index:2147483647;" +
          "box-sizing:border-box;margin:0;padding:6px 16px;" +
          "background:#991b1b;color:#ffffff;border-bottom:2px solid #450a0a;" +
          "font:600 14px/1.4 system-ui,-apple-system,'Segoe UI',sans-serif;" +
          "text-align:center"
      );
      var english = document.createElement("div");
      english.setAttribute("lang", "en");
      var hindi = document.createElement("div");
      hindi.setAttribute("lang", "hi");
      bar.appendChild(english);
      bar.appendChild(hindi);
      document.body.insertBefore(bar, document.body.firstChild);
    }
    /* textContent only: the zone name comes from the device and is never parsed as markup */
    bar.children[0].textContent =
      "This computer's time zone is " + label +
      ", so times shown here can be wrong. " +
      "Set the computer's time zone to India Standard Time (IST).";
    bar.children[1].textContent =
      "इस कंप्यूटर का टाइम ज़ोन " + label +
      " है, इसलिए यहाँ दिखने वाले समय गलत हो सकते हैं। " +
      "कंप्यूटर का टाइम ज़ोन India Standard Time (IST) पर सेट करें।";
    reserve(bar.offsetHeight);
  }
  function hide() {
    var bar = document.getElementById(BAR_ID);
    var style = document.getElementById(STYLE_ID);
    if (bar) bar.parentNode.removeChild(bar);
    if (style) style.parentNode.removeChild(style);
  }
  function check() {
    try {
      if (!document.body) return;
      var zone = zoneName();
      var offset = new Date().getTimezoneOffset();
      if (deviceIsIst(zone, offset)) hide();
      else show(zone, offset);
    } catch (e) {
      /* a warning must never break the page */
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", check);
  } else {
    check();
  }
  setInterval(check, 60000);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) check();
  });
  window.addEventListener("resize", function () {
    var bar = document.getElementById(BAR_ID);
    if (bar) reserve(bar.offsetHeight);
  });
})();
"""
