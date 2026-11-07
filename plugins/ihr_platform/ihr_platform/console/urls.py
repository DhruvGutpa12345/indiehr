from django.urls import path

from ihr_platform.console import views

urlpatterns = [
    path("", views.home, name="ops-home"),
    path("style.css", views.style, name="ops-style"),
    path("login/", views.login, name="ops-login"),
    path("callback/", views.callback, name="ops-callback"),
    path("logout/", views.logout, name="ops-logout"),
    path("signed-out/", views.signed_out, name="ops-signed-out"),
    path("hospitals/new/", views.new_hospital, name="ops-new-hospital"),
    path("operations/<uuid:op_id>/", views.operation, name="ops-operation"),
    path("operations/<uuid:op_id>/retry/", views.retry, name="ops-retry"),
    path("operations/<uuid:op_id>/rollback/", views.rollback, name="ops-rollback"),
    path(
        "operations/<uuid:op_id>/resend-setup/",
        views.resend_setup,
        name="ops-resend-setup",
    ),
]
