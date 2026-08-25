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
#
from urllib.parse import urlencode

from api.responses import error_response
from api.utils import (
    AUTH_ROLE_ADMIN,
    AUTH_ROLE_DISABLED,
    CONFIG_KEY_HEADER,
    WEB_AUTH_HEADER,
    basic_auth_credentials,
    is_authenticated,
    is_html_response,
    restore_web_auth,
)
from django.conf import settings
from django.shortcuts import redirect
from django.urls import Resolver404, resolve, reverse
from django.utils.translation import gettext_lazy as _

# Route names come from Django's URL table, so adding an endpoint does not
# require another path regular expression in this middleware.
_KEYED_ROUTES = frozenset({"config", "add", "del", "get", "move", "notify", "json_urls", "auth", "health_key"})
_HEADER_ROUTES = frozenset(
    {
        "add_by_header",
        "del_by_header",
        "get_by_header",
        "move_by_header",
        "s_notify",
        "json_urls_by_header",
        "auth_by_header",
        "health",
        "details",
    }
)
_SHARED_WEB_ROUTES = frozenset({"welcome", "config_list", "details"})
_CURRENT_CONFIG_ROUTES = frozenset({"config_current", "auth_current", "health_current"})


def _request_route(request):
    """Return ``(name, key)`` from Django's authoritative URL resolver."""
    try:
        match = resolve(request.path_info)
    except Resolver404:
        return None, None
    return match.url_name, match.kwargs.get("key")


def _authentication_response(request):
    """Build the standard global Basic Auth challenge."""
    return error_response(
        request,
        _("Access Denied"),
        401,
        template="401.html",
        headers={"WWW-Authenticate": 'Basic realm="{}"'.format(settings.APPRISE_BASIC_AUTH_REALM)},
    )


class GlobalAuthMiddleware:
    """Protect API calls with Basic Auth and browser pages with a cookie.

    HTML pages redirect to a login form. Regular API requests continue to
    validate Basic credentials on every call.
    """

    def __init__(self, get_response):
        """Store the next middleware or view in the request chain."""
        self.get_response = get_response

    def __call__(self, request):
        """Authenticate the request or return the appropriate login response."""
        # Templates can safely read these values on every request.
        request.apprise_auth_permission = AUTH_ROLE_DISABLED
        request.apprise_auth_username = None

        if not settings.APPRISE_AUTH_REQUIRED:
            request.globally_authenticated = False
            return self.get_response(request)

        route_name, route_key = _request_route(request)
        if route_name and route_name.startswith("http_"):
            return self.get_response(request)

        # Static assets contain no protected data and must load before login.
        if request.path_info.startswith("/s/"):
            return self.get_response(request)

        # The form itself must be reachable before a browser can sign in.
        if route_name == "login":
            return self.get_response(request)

        html_request = is_html_response(request)
        web_request = html_request or request.headers.get(WEB_AUTH_HEADER) == "1"
        if web_request:
            # Logout must remain reachable without a valid cookie.
            if route_name == "logout":
                return self.get_response(request)

            requested_key = route_key if route_name in _KEYED_ROUTES else None
            if requested_key is None and route_name in _HEADER_ROUTES:
                header_key = request.headers.get(CONFIG_KEY_HEADER, "").strip()
                requested_key = header_key or None

            if restore_web_auth(
                request,
                requested_key,
                allow_shared_without_key=bool(route_name in _SHARED_WEB_ROUTES or route_name in _CURRENT_CONFIG_ROUTES),
            ):
                return self.get_response(request)

            if html_request:
                query = urlencode({"next": request.get_full_path(), "key": requested_key or ""})
                return redirect("{}?{}".format(reverse("login"), query))

            # An expired GUI cookie should not trigger the browser's Basic prompt.
            return error_response(request, _("Access Denied"), 401)

        # Keyed requests may carry administrator or per-key credentials.
        has_key_in_url = route_name in _KEYED_ROUTES
        has_key_in_header = bool(route_name in _HEADER_ROUTES and request.headers.get(CONFIG_KEY_HEADER, "").strip())
        keyed_request = has_key_in_url or has_key_in_header
        request.globally_authenticated = is_authenticated(request)
        if request.globally_authenticated:
            request.apprise_auth_permission = AUTH_ROLE_ADMIN
            request.apprise_auth_username = basic_auth_credentials(request)[0]
            return self.get_response(request)

        # The logout response asks the browser to discard cached credentials.
        if route_name == "logout":
            return self.get_response(request)

        # Keyed views perform their own per-key authentication.
        if keyed_request:
            return self.get_response(request)

        # Nginx may throttle repeated failures before they reach Django.
        return _authentication_response(request)
