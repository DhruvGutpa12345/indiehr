from django.urls import path

from ihr_identity import join

urlpatterns = [
    path("", join.start, name="ihr-join"),
    path("confirm/", join.confirm, name="ihr-join-confirm"),
]
