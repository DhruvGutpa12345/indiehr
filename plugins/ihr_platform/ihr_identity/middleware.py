"""Response-side helpers for staff sessions (ADR-009)."""

from django.conf import settings
from django.utils.cache import patch_vary_headers


class StaffSessionMiddleware:
    """Clears a dead session cookie (an authentication class has no response to do it), and
    keeps API answers out of shared and back-button caches: unlike an Authorization header,
    a cookie does not stop a shared cache from storing a response."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if sub := getattr(request, "ihr_revoke_sub", None):
            from ihr_identity.authentication import revoke

            revoke(
                sub
            )  # after the view's transaction, so a refusal cannot roll it back
        if getattr(request, "ihr_clear_session", False):
            response.delete_cookie(settings.IHR_SESSION_COOKIE, path="/")
        if request.path.startswith("/api/"):
            if not response.has_header("Cache-Control"):
                response["Cache-Control"] = "no-store"
            patch_vary_headers(response, ["Cookie"])
        return response
