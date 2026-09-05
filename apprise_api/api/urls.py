#
# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
# All rights reserved.
#
# This code is licensed under the MIT License.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files(the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and / or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions :
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
from django.urls import re_path

from . import views
from .utils import CONFIG_KEY_REGEX

_KEY = r"(?P<key>{})".format(CONFIG_KEY_REGEX)

urlpatterns = [
    re_path(r"^$", views.WelcomeView.as_view(), name="welcome"),
    re_path(r"^login/?$", views.LoginView.as_view(), name="login"),
    re_path(r"^logout/?$", views.LogoutView.as_view(), name="logout"),
    re_path(r"^status/?$", views.HealthCheckView.as_view(), name="health"),
    re_path(r"^status/@/?$", views.CurrentHealthCheckView.as_view(), name="health_current"),
    re_path(
        r"^status/{}/?$".format(_KEY),
        views.KeyedHealthCheckView.as_view(),
        name="health_key",
    ),
    re_path(r"^details/?$", views.DetailsView.as_view(), name="details"),
    # Browser sessions can keep their current ID out of the address bar. The
    # '@' cannot collide with a valid configuration ID.
    re_path(r"^cfg/@/?$", views.CurrentConfigView.as_view(), name="config_current"),
    re_path(
        r"^cfg/{}/?$".format(_KEY),
        views.ConfigView.as_view(),
        name="config",
    ),
    re_path(r"^cfg/?$", views.ConfigListView.as_view(), name="config_list"),
    re_path(r"^add/{}/?$".format(_KEY), views.AddView.as_view(), name="add"),
    # Bare routes read the key from X-Apprise-Config-ID, keeping it out of
    # proxy and web server access logs.
    re_path(r"^add/?$", views.AddView.as_view(), name="add_by_header"),
    re_path(r"^del/{}/?$".format(_KEY), views.DelView.as_view(), name="del"),
    re_path(r"^del/?$", views.DelView.as_view(), name="del_by_header"),
    re_path(r"^get/{}/?$".format(_KEY), views.GetView.as_view(), name="get"),
    re_path(r"^get/?$", views.GetView.as_view(), name="get_by_header"),
    re_path(r"^move/{}/?$".format(_KEY), views.MoveView.as_view(), name="move"),
    re_path(r"^move/?$", views.MoveView.as_view(), name="move_by_header"),
    re_path(r"^auth/@/?$", views.CurrentAuthView.as_view(), name="auth_current"),
    re_path(
        r"^auth/{}/?$".format(_KEY),
        views.AuthView.as_view(),
        name="auth",
    ),
    re_path(r"^auth/?$", views.AuthView.as_view(), name="auth_by_header"),
    re_path(r"^qr/@/?$", views.CurrentMobileQrView.as_view(), name="qr_current"),
    re_path(
        r"^qr/{}/?$".format(_KEY),
        views.MobileQrView.as_view(),
        name="qr",
    ),
    re_path(r"^qr/?$", views.MobileQrView.as_view(), name="qr_by_header"),
    re_path(
        r"^notify/{}/?$".format(_KEY),
        views.StatefulNotifyView.as_view(),
        name="notify",
    ),
    # Without explicit URLs the header selects a saved configuration. With
    # explicit URLs it scopes configuration-user authentication instead.
    re_path(r"^notify/?$", views.StatelessNotifyView.as_view(), name="s_notify"),
    re_path(
        r"^json/urls/{}/?$".format(_KEY),
        views.JsonUrlView.as_view(),
        name="json_urls",
    ),
    re_path(r"^json/urls/?$", views.JsonUrlView.as_view(), name="json_urls_by_header"),
]
