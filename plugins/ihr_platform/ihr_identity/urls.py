from django.urls import path

from ihr_identity import views

urlpatterns = [
    path("login/", views.login, name="ihr-auth-login"),
    path("callback/", views.callback, name="ihr-auth-callback"),
    path("logout/", views.logout, name="ihr-auth-logout"),
    path("session/", views.SessionView.as_view(), name="ihr-auth-session"),
]
