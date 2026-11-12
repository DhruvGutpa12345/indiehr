from django.urls import path

from ihr_identity.staff_access import StaffAccessView

# Methods per route (P1-T16a): reading is GET on access/, every action is a POST. Anything else
# gets 405 from the view, never a handler called without the argument it needs.
urlpatterns = [
    path(
        "<str:username>/access/",
        StaffAccessView.as_view(http_method_names=["get", "head", "options"]),
        name="ihr-staff-access",
    ),
    path(
        "<str:username>/<slug:action>/",
        StaffAccessView.as_view(http_method_names=["post", "options"]),
        name="ihr-staff-act",
    ),
]
