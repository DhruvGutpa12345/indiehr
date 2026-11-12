"""A budget of terminology calls per web request (P1-T10a1): CARE makes one call per code system
of a value set, so a crafted value set could otherwise make thousands in one request."""

from ihr_terminology import engine


class TerminologyBudgetMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = engine.start_request()
        try:
            return self.get_response(request)
        finally:
            engine.end_request(token)
