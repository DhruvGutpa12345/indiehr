from django.urls import path

from ihr_audit import views

urlpatterns = [
    path("entries/", views.EntriesView.as_view(), name="ihr-audit-entries"),
    path("entries/<int:pk>/", views.EntryView.as_view(), name="ihr-audit-entry"),
    path("export/", views.ExportView.as_view(), name="ihr-audit-export"),
    path("status/", views.StatusView.as_view(), name="ihr-audit-status"),
    path("verifier.py", views.VerifierView.as_view(), name="ihr-audit-verifier"),
]
