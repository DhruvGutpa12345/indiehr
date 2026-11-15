from django.urls import path

from ihr_access import endpoints

urlpatterns = [
    path(
        "records-access/",
        endpoints.RecordsAccessView.as_view(),
        name="ihr-records-access",
    ),
    path(
        "records-access/<int:pk>/",
        endpoints.RecordsAccessEndView.as_view(),
        name="ihr-records-access-end",
    ),
    path("owner/totals/", endpoints.OwnerTotalsView.as_view(), name="ihr-owner-totals"),
    path("my-access/", endpoints.MyAccessView.as_view(), name="ihr-my-access"),
]
