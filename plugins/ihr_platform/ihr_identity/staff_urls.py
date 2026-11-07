from django.urls import path

from ihr_identity.staff_access import StaffAccessView

urlpatterns = [
    path("<str:username>/access/", StaffAccessView.as_view(), name="ihr-staff-access"),
    path(
        "<str:username>/<slug:action>/", StaffAccessView.as_view(), name="ihr-staff-act"
    ),
]
