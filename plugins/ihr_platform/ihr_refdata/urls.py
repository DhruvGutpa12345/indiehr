"""The hospital admin's reference pack decisions (P1-T15b1), under /api/ihr/packs/."""

from django.urls import path

from ihr_refdata import api

urlpatterns = [
    path("updates/", api.UpdatesView.as_view(), name="ihr-packs-updates"),
    path("updates/<int:pk>/", api.UpdateView.as_view(), name="ihr-packs-update"),
    path("history/", api.HistoryView.as_view(), name="ihr-packs-history"),
]
