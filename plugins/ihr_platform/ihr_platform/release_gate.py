"""The release gate (P1-T18a): a hospital whose schema is not this build's, or that is held
for a release window, is not served at all.

Runs right after django-tenants' TenantMainMiddleware, before anything that reads or writes the
hospital's schema (the audit trail, sessions, CARE's views). Platform hosts (the public schema:
health, console, back-channel logout) are never gated. The answers (design item 4):

- ``/api/ihr/bootstrap.js``: **200**, a stub built only from public data (the hospital's
  registered name and the announced ``MaintenanceWindow``). The web app loads it before its own
  code, and a browser does not run a script that comes back with an error status. It draws an
  "upgrading" notice over the page in English and Hindi and reloads every minute.
- a request that asks for HTML (a sign-in link, a bookmark): a short 503 page, both languages;
- everything else: 503 JSON ``{"detail": "upgrading", "code": "ihr_upgrading"}``.

All of them carry ``Retry-After`` and ``Cache-Control: no-store``. The same checks sit in
staff and service authentication (defence in depth, ADR-002 Am. 1 #7).
"""

import json

from django.conf import settings
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django_tenants.utils import get_public_schema_name

from ihr_platform import release

BOOTSTRAP_JS = "/api/ihr/bootstrap.js"
RETRY_AFTER = 60  # seconds
IST = timezone.get_fixed_timezone(330)

# English and Hindi (the Hindi awaits the owner's review: docs/i18n/review/hi-release-gate.csv)
TEXT = {
    "title_en": "This hospital's system is being upgraded",
    "title_hi": "इस अस्पताल का सिस्टम अपग्रेड हो रहा है",
    "body_en": "It will be back shortly. This page reloads by itself every minute.",
    "body_hi": "यह जल्द ही फिर से उपलब्ध होगा। यह पेज हर मिनट अपने आप फिर से लोड होता है।",
    "until_en": "Planned to end by {time} IST on {date}.",
    "until_hi": "{date} को {time} IST तक पूरा होने की योजना है।",
}


class ReleaseGateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant = getattr(connection, "tenant", None)
        if (
            tenant is None
            or tenant.schema_name == get_public_schema_name()
            or release.schema_ready(tenant)
        ):
            return self.get_response(request)
        return upgrading_response(request, tenant)


def _window_text():
    """{"en": ..., "hi": ...} for the window that is open now, or {} if none is."""
    item = release.window()
    if item is None or item.start > timezone.now():
        return {}
    end = item.end.astimezone(IST)
    values = {"time": end.strftime("%H:%M"), "date": end.strftime("%d-%m-%Y")}
    return {
        "en": TEXT["until_en"].format(**values),
        "hi": TEXT["until_hi"].format(**values),
        "ends_at": end.isoformat(),
    }


def _no_store(response, seconds=RETRY_AFTER):
    response["Cache-Control"] = "no-store"
    response["Retry-After"] = str(seconds)
    response["X-Content-Type-Options"] = "nosniff"
    return response


def upgrading_response(request, tenant):
    if request.path == BOOTSTRAP_JS:
        response = HttpResponse(
            bootstrap_stub(tenant), content_type="text/javascript; charset=utf-8"
        )
        return _no_store(response)
    accept = request.headers.get("Accept", "")
    if "text/html" in accept or not request.path.startswith("/api/"):
        return _no_store(HttpResponse(_page(), status=503))
    return _no_store(
        JsonResponse({"detail": "upgrading", "code": "ihr_upgrading"}, status=503)
    )


def _page():
    from django.utils.html import escape

    window = _window_text()
    until = (
        f'<p lang="en">{escape(window["en"])}</p><p lang="hi">{escape(window["hi"])}</p>'
        if window
        else ""
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="{RETRY_AFTER}">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(TEXT['title_en'])}</title></head>"
        '<body style="font:16px/1.5 system-ui,sans-serif;margin:2em auto;max-width:36em;'
        'padding:0 1em">'
        f'<h1 lang="en">{escape(TEXT["title_en"])}</h1>'
        f'<h1 lang="hi">{escape(TEXT["title_hi"])}</h1>'
        f'<p lang="en">{escape(TEXT["body_en"])}</p>'
        f'<p lang="hi">{escape(TEXT["body_hi"])}</p>'
        f"{until}</body></html>"
    )


def _js(value):
    # JSON is valid JavaScript; "<" escaped so the value can never close a script tag.
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")


def bootstrap_stub(tenant):
    """bootstrap.js for a gated hospital, from public data only (never its schema)."""
    branding = {
        "platform": "IndieHR",
        "name": tenant.name,
        "short_name": tenant.name[:40],
        "primary_colour": "",
        "logo_url": "",
    }
    auth = {
        "idle_seconds": settings.IHR_SESSION_IDLE_SECONDS,
        "warn_seconds": min(120, settings.IHR_SESSION_IDLE_SECONDS // 2),
    }
    upgrading = {"text": {k: v for k, v in TEXT.items() if "until" not in k}}
    upgrading["window"] = _window_text()
    upgrading["retry_seconds"] = RETRY_AFTER
    return (
        f"window.__IHR_BRANDING__ = {_js(branding)};\n"
        f"window.__IHR_AUTH__ = {_js(auth)};\n"
        f"window.__IHR_UPGRADING__ = {_js(upgrading)};\n"
        "document.title = window.__IHR_BRANDING__.short_name;\n" + OVERLAY_SCRIPT
    )


# Never a less-than sign in here (bootstrap.js rule, device_time.py): comparisons are written
# the other way round. textContent only: nothing is parsed as markup.
OVERLAY_SCRIPT = r"""(function () {
  "use strict";
  var info = window.__IHR_UPGRADING__;
  var ID = "ihr-upgrading";
  function line(tag, lang, text, size) {
    var el = document.createElement(tag);
    el.setAttribute("lang", lang);
    el.setAttribute("style", "margin:0 0 8px;font-size:" + size);
    el.textContent = text;
    return el;
  }
  function show() {
    try {
      if (!document.body || document.getElementById(ID)) return;
      var t = info.text;
      var box = document.createElement("div");
      box.id = ID;
      box.setAttribute("role", "alert");
      box.setAttribute(
        "style",
        "position:fixed;top:0;left:0;right:0;bottom:0;z-index:2147483647;" +
          "box-sizing:border-box;padding:15vh 24px 24px;overflow:auto;" +
          "background:#ffffff;color:#111827;text-align:center;" +
          "font:16px/1.5 system-ui,-apple-system,'Segoe UI',sans-serif"
      );
      box.appendChild(line("h1", "en", t.title_en, "24px"));
      box.appendChild(line("h1", "hi", t.title_hi, "24px"));
      box.appendChild(line("p", "en", t.body_en, "16px"));
      box.appendChild(line("p", "hi", t.body_hi, "16px"));
      if (info.window && info.window.en) {
        box.appendChild(line("p", "en", info.window.en, "16px"));
        box.appendChild(line("p", "hi", info.window.hi, "16px"));
      }
      document.body.appendChild(box);
    } catch (e) {
      /* a notice must never break the page */
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", show);
  } else {
    show();
  }
  setInterval(show, 1000);
  setTimeout(function () {
    window.location.reload();
  }, info.retry_seconds * 1000);
})();
"""
