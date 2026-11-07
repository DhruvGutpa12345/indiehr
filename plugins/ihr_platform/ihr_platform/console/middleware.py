"""Platform hosts (the public schema: health checks and the operator console, P1-T5b)."""

from django.db import connection

CSP = (
    "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; "
    "frame-ancestors 'none'; base-uri 'none'"
)


class PlatformHostMiddleware:
    """On platform hosts there are no CARE sessions: a stray ``sessionid`` cookie would make
    CARE's audit middleware query a session table that only hospitals have. Console pages
    also get a strict security policy and are never cached."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        platform = getattr(connection, "schema_name", None) == "public"
        if platform:
            request.COOKIES.pop("sessionid", None)
        response = self.get_response(request)
        if platform and request.path.startswith("/ops/"):
            response["Content-Security-Policy"] = CSP
            response["X-Frame-Options"] = "DENY"
            # same-origin, not no-referrer: with no-referrer browsers send "Origin: null" on
            # form posts, which Django's CSRF check rightly refuses
            response["Referrer-Policy"] = "same-origin"
            response["Cache-Control"] = "no-store"
            response["X-Content-Type-Options"] = "nosniff"
        return response
