"""The "synthetic data only" bar (P1-T32 E5, ADR-020 §3).

On a synthetic instance (not ``production-*``; ``ihr_platform.environment.synthetic()``) the
public ``bootstrap.js`` carries this script after the time-zone warning (``device_time``). It
draws a permanent bar in English and Hindi at the top of every web-app page, the sign-in page
included, so nobody enters a real patient on a throwaway or staging server.

It is its own bar, not a shared host with the time-zone one (design §7.3): the shipped IST
bar stays as the owner confirmed it, and only offsets itself by ``--ihr-synthetic-h``, the
height this script publishes on ``<html>`` (absent on production, where the offset is 0).

* ``role="note"``, never hides, no close button; one z-index below the release gate's
  full-screen overlay (``2147483647``), which covers the page anyway.
* It pushes the page down with ``html{padding-top}``, not ``body``: the time-zone bar owns
  ``body{padding-top}``, so the two stack in any order. The app's full-height fixed panels
  (Tailwind ``fixed inset-y-0``) start below it.
* The texts are set with ``textContent``; the script has no ``<`` (``bootstrap.js`` is checked
  for that, so it can never close a script tag), no ``innerHTML`` and no ``eval``, and
  everything runs inside a ``try`` so a banner can never break the page.
"""

import json

from ihr_platform import environment

_TEMPLATE = r"""(function () {
  "use strict";
  var BAR_ID = "ihr-synthetic";
  var STYLE_ID = "ihr-synthetic-style";
  var VAR_NAME = "--ihr-synthetic-h";
  var TEXT_EN = __TEXT_EN__;
  var TEXT_HI = __TEXT_HI__;
  function reserve(height) {
    var h = Math.max(0, Math.ceil(height)) + "px";
    document.documentElement.style.setProperty(VAR_NAME, h);
    var style = document.getElementById(STYLE_ID);
    if (!style) {
      style = document.createElement("style");
      style.id = STYLE_ID;
      (document.head || document.documentElement).appendChild(style);
    }
    style.textContent =
      "html{padding-top:var(--ihr-synthetic-h)}" +
      "html{scroll-padding-top:var(--ihr-synthetic-h)}" +
      ".fixed.inset-y-0{top:var(--ihr-synthetic-h) !important;height:auto !important}";
  }
  function ensure() {
    try {
      if (!document.body) return;
      var bar = document.getElementById(BAR_ID);
      if (!bar) {
        bar = document.createElement("div");
        bar.id = BAR_ID;
        bar.setAttribute("role", "note");
        bar.setAttribute(
          "style",
          "position:fixed;top:0;left:0;right:0;z-index:2147483646;" +
            "box-sizing:border-box;margin:0;padding:4px 16px;" +
            "background:#92400e;color:#ffffff;border-bottom:2px solid #451a03;" +
            "font:600 13px/1.35 system-ui,-apple-system,'Segoe UI',sans-serif;" +
            "text-align:center"
        );
        var english = document.createElement("div");
        english.setAttribute("lang", "en");
        english.textContent = TEXT_EN;
        var hindi = document.createElement("div");
        hindi.setAttribute("lang", "hi");
        hindi.textContent = TEXT_HI;
        bar.appendChild(english);
        bar.appendChild(hindi);
        document.body.insertBefore(bar, document.body.firstChild);
      }
      reserve(bar.offsetHeight);
    } catch (e) {
      /* a banner must never break the page */
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", ensure);
  } else {
    ensure();
  }
  window.addEventListener("load", ensure);
  window.addEventListener("resize", ensure);
  setInterval(ensure, 60000);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) ensure();
  });
})();
"""

SYNTHETIC_SCRIPT = _TEMPLATE.replace(
    "__TEXT_EN__", json.dumps(environment.BANNER_EN, ensure_ascii=False)
).replace("__TEXT_HI__", json.dumps(environment.BANNER_HI, ensure_ascii=False))
