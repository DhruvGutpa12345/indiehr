"""Records grants a request found expired are ended after its view (P1-T11b4 review C3).

Inside the audit middleware (the actor and context are still bound), after the view: CARE's
ATOMIC_REQUESTS wraps only the view, so each end here commits in its own short transaction whether or not the view was refused, and no
request holds a grant or patient row lock while its view runs (records.on_request takes none).
"""

from ihr_access import records


class RecordsExpiryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = records.start_due()
        try:
            return self.get_response(request)
        finally:
            records.end_due(token)
