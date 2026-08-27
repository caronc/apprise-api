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

from api.auth import Authentication
from api.responses import error_response
from api.utils import (
    CONFIG_KEY_HEADER,
    CONFIG_KEY_PATTERN,
    is_html_response,
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
    """Return the route name, Config ID, and view class Django resolved."""
    try:
        match = resolve(request.path_info)
    except Resolver404:
        return None, None, None
    return match.url_name, match.kwargs.get("key"), getattr(match.func, "view_class", None)


def _authentication_response(request):
    """Build the standard global Basic Auth challenge."""
    return error_response(
        request,
        _("Access Denied"),
        401,
        template="401.html",
        headers={"WWW-Authenticate": 'Basic realm="{}"'.format(settings.APPRISE_BASIC_AUTH_REALM)},
    )


def _request_config_key(request, route_name, route_key):
    """Return the valid Config ID this API request will use, if any."""
    if route_name not in _KEYED_ROUTES and route_name not in _HEADER_ROUTES:
        return None

    # A header intentionally overrides a key in the URL. If that header is
    # invalid, leave authentication to the view so it can return HTTP 400.
    header_key = request.headers.get(CONFIG_KEY_HEADER, "").strip()
    candidate = header_key if header_key else route_key
    return candidate if isinstance(candidate, str) and CONFIG_KEY_PATTERN.match(candidate) else None


def _view_authenticates_method(view_class, method):
    """Return whether the resolved class implements this HTTP method."""
    if view_class is None or not isinstance(method, str):
        return False

    method = method.lower()
    # Django supplies OPTIONS itself. It reports allowed methods without
    # calling the view's authentication code, so do not add work here.
    if method == "options":
        return False
    # Django sends HEAD through get() when a view does not define head().
    if method == "head" and not hasattr(view_class, "head"):
        method = "get"
    return hasattr(view_class, method)


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
        request.apprise_auth_permission = Authentication.ROLE_DISABLED
        request.apprise_auth_username = None

        if not settings.APPRISE_AUTH_REQUIRED:
            request.globally_authenticated = False
            return self.get_response(request)

        # The login form and static assets are always reachable without a cookie.
        route_name, route_key, route_view = _request_route(request)
        if route_name and route_name.startswith("http_"):
            return self.get_response(request)

        # Static assets contain no protected data and must load before login.
        if request.path_info.startswith("/s/"):
            return self.get_response(request)

        # The form itself must be reachable before a browser can sign in.
        if route_name == "login":
            return self.get_response(request)

        html_request = is_html_response(request)
        web_request = html_request or request.headers.get(Authentication.WEB_HEADER) == "1"
        if web_request:
            # Logout must remain reachable without a valid cookie.
            if route_name == "logout":
                return self.get_response(request)

            requested_key = route_key if route_name in _KEYED_ROUTES else None
            if requested_key is None and route_name in _HEADER_ROUTES:
                header_key = request.headers.get(CONFIG_KEY_HEADER, "").strip()
                requested_key = header_key or None

            if Authentication.restore_web(
                request,
                requested_key,
                allow_shared_without_key=bool(route_name in _SHARED_WEB_ROUTES or route_name in _CURRENT_CONFIG_ROUTES),
            ):
                response = self.get_response(request)
                # Renew active sessions unless the view changed or cleared it.
                if Authentication.WEB_COOKIE not in response.cookies:
                    Authentication.set_web_cookie(
                        response,
                        request,
                        request.apprise_auth_permission,
                        request.apprise_auth_username,
                        getattr(request, "apprise_web_auth_key", None),
                    )
                return response

            if html_request:
                query = urlencode({"next": request.get_full_path(), "key": requested_key or ""})
                return redirect("{}?{}".format(reverse("login"), query))

            # An expired GUI cookie should not trigger the browser's Basic prompt.
            return error_response(request, _("Access Denied"), 401)

        # Keyed requests may carry administrator or per-key credentials.
        has_key_in_url = route_name in _KEYED_ROUTES
        has_key_in_header = bool(route_name in _HEADER_ROUTES and request.headers.get(CONFIG_KEY_HEADER, "").strip())
        keyed_request = has_key_in_url or has_key_in_header
        request.globally_authenticated = Authentication.is_authenticated(request)
        if request.globally_authenticated:
            request.apprise_auth_permission = Authentication.ROLE_ADMIN
            request.apprise_auth_username = Authentication.basic_credentials(request)[0]
            return self.get_response(request)

        # The logout response asks the browser to discard cached credentials.
        if route_name == "logout":
            return self.get_response(request)

        # Keyed views still enforce access and build their own error response.
        if keyed_request:
            config_key = _request_config_key(request, route_name, route_key)
            if config_key is not None and _view_authenticates_method(route_view, request.method):
                # Save the decision on the request. The view still owns its
                # existing error response, but it will not verify twice.
                Authentication.key_ok(
                    request,
                    config_key,
                    allow_public=route_name in {"notify", "s_notify"},
                )
            return self.get_response(request)

        # Nginx may throttle repeated failures before they reach Django.
        return _authentication_response(request)
