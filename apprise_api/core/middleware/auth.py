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
import re
from urllib.parse import urlencode

from api.utils import (
    AUTH_MODE_DISABLED,
    AUTH_MODE_MASTER,
    CONFIG_KEY_HEADER,
    CONFIG_KEY_REGEX,
    WEB_AUTH_HEADER,
    basic_auth_credentials,
    is_authenticated,
    is_html_response,
    is_json_response,
    restore_web_auth,
)
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

# Keyed routes defer authentication to the view, which accepts either global
# credentials or credentials belonging to that key.
_KEY_IN_URL = re.compile(r"^/(cfg|add|del|get|notify|json/urls|auth|status)/(?P<key>{})/?$".format(CONFIG_KEY_REGEX))

# These bare routes may obtain their key from X-Apprise-Config-ID.
# The bare /cfg admin listing has no per-key access and always uses global auth.
_HEADER_ELIGIBLE_PATH = re.compile(r"^/(add|del|get|notify|json/urls|auth|status)/?$")

# Login and logout remain reachable without a browser session.
_LOGOUT_PATH = re.compile(r"^/logout/?$")
_LOGIN_PATH = re.compile(r"^/login/?$")

# Error pages contain no private data and must remain reachable when nginx
# internally redirects an authentication failure.
_ERROR_PATH = re.compile(r"^/_/(?:401|404|421|429|50x)/?$")

# Shared browser logins may use these general pages. Other access stays tied
# to the configuration key found in the URL or X-Apprise-Config-ID.
_SHARED_WEB_PATH = re.compile(r"^/(?:|cfg|details|status)/?$")


class GlobalAuthMiddleware:
    """Protect API calls with Basic Auth and browser pages with a cookie.

    HTML pages redirect to a login form. Regular API requests continue to
    validate Basic credentials on every call.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Templates can safely read these values on every request.
        request.apprise_auth_permission = AUTH_MODE_DISABLED
        request.apprise_auth_username = None

        if not settings.APPRISE_AUTH_REQUIRED:
            request.globally_authenticated = False
            return self.get_response(request)

        if _ERROR_PATH.match(request.path_info):
            return self.get_response(request)

        # Static assets contain no protected data and must load before login.
        if request.path_info.startswith("/s/"):
            return self.get_response(request)

        # The form itself must be reachable before a browser can sign in.
        if _LOGIN_PATH.match(request.path_info):
            return self.get_response(request)

        html_request = is_html_response(request)
        web_request = html_request or request.headers.get(WEB_AUTH_HEADER) == "1"
        if web_request:
            # Logout must remain reachable without a valid cookie.
            if _LOGOUT_PATH.match(request.path_info):
                return self.get_response(request)

            key_match = _KEY_IN_URL.match(request.path_info)
            requested_key = key_match.group("key") if key_match else None
            if requested_key is None and _HEADER_ELIGIBLE_PATH.match(request.path_info):
                header_key = request.headers.get(CONFIG_KEY_HEADER, "").strip()
                requested_key = header_key if re.fullmatch(CONFIG_KEY_REGEX, header_key) else None

            if restore_web_auth(
                request,
                requested_key,
                allow_shared_without_key=bool(_SHARED_WEB_PATH.match(request.path_info)),
            ):
                return self.get_response(request)

            if html_request:
                query = urlencode({"next": request.get_full_path(), "key": requested_key or ""})
                return redirect("{}?{}".format(reverse("login"), query))

            # An expired GUI cookie should not trigger the browser's Basic prompt.
            msg = _("Access Denied")
            return (
                JsonResponse(
                    {"error": str(msg)},
                    encoder=DjangoJSONEncoder,
                    safe=False,
                    status=401,
                )
                if is_json_response(request)
                else HttpResponse(msg, status=401, content_type="text/plain")
            )

        request.globally_authenticated = is_authenticated(request)
        if request.globally_authenticated:
            request.apprise_auth_permission = AUTH_MODE_MASTER
            request.apprise_auth_username = basic_auth_credentials(request)[0]
            return self.get_response(request)

        # The logout response asks the browser to discard cached credentials.
        if _LOGOUT_PATH.match(request.path_info):
            return self.get_response(request)

        # path_info excludes APPRISE_BASE_URL and matches the route patterns.
        has_key_in_url = bool(_KEY_IN_URL.match(request.path_info))
        has_key_in_header = bool(
            _HEADER_ELIGIBLE_PATH.match(request.path_info) and request.headers.get(CONFIG_KEY_HEADER, "").strip()
        )
        if has_key_in_url or has_key_in_header:
            return self.get_response(request)

        # A Basic challenge lets clients retry missing or invalid credentials.
        msg = _("Access Denied")
        status = 401
        response = (
            JsonResponse(
                {"error": str(msg)},
                encoder=DjangoJSONEncoder,
                safe=False,
                status=status,
            )
            if is_json_response(request)
            else render(request, "401.html", status=status)
            if is_html_response(request)
            else HttpResponse(msg, status=status, content_type="text/plain")
        )
        response["WWW-Authenticate"] = 'Basic realm="{}"'.format(settings.APPRISE_BASIC_AUTH_REALM)
        return response
