"""No URLs at all: what a hospital's own address serves on the back-channel service (P1-T23).

The back-channel logout service (``IHR_SERVICE=backchannel``) answers on the platform hosts only
(urls_backchannel.py); a hospital address that ever reached it gets a plain 404 for every path.
One catch-all pattern says so: an empty list would make Django (with DEBUG on) show its welcome
page with a 200.
"""

from django.urls import re_path

from ihr_platform.urls_public import not_found, server_error

handler404 = not_found
handler500 = server_error

urlpatterns = [re_path(r"^.*$", not_found)]
