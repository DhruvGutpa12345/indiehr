from django.urls import path

from ihr_tenant_settings import views

urlpatterns = [
    path("bootstrap/", views.bootstrap, name="ihr-bootstrap"),
    path("bootstrap.js", views.bootstrap_js, name="ihr-bootstrap-js"),
    path("logo/", views.logo, name="ihr-logo"),
    path("profile/", views.ProfileView.as_view(), name="ihr-profile"),
    path("pincode/<str:pincode>/", views.PinCodeView.as_view(), name="ihr-pincode"),
]
