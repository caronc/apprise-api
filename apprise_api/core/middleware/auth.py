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

from api.utils import CONFIG_KEY_HEADER, CONFIG_KEY_REGEX, is_authenticated, is_json_response
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse, JsonResponse
from django.utils.translation import gettext_lazy as _

# Keyed routes defer authentication to the view, which accepts either global
# credentials or credentials belonging to that key.
_KEY_IN_URL = re.compile(r"^/(cfg|add|del|get|notify|json/urls|auth|status)/(?P<key>{})/?$".format(CONFIG_KEY_REGEX))

# These bare routes may obtain their key from X-Apprise-Config-ID.
# The bare /cfg admin listing has no per-key access and always uses global auth.
_HEADER_ELIGIBLE_PATH = re.compile(r"^/(add|del|get|notify|json/urls|auth|status)/?$")


class GlobalAuthMiddleware:
    """Apply optional global Basic Auth to every endpoint.

    Keyed requests continue to their view so either global or per-key
    credentials can authorize them. Other requests are denied here when
    global credentials do not match.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.APPRISE_BASIC_AUTH_TOKEN is None:
            request.globally_authenticated = False
            return self.get_response(request)

        request.globally_authenticated = is_authenticated(request)
        if request.globally_authenticated:
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
            HttpResponse(msg, status=status, content_type="text/plain")
            if not is_json_response(request)
            else JsonResponse(
                {"error": str(msg)},
                encoder=DjangoJSONEncoder,
                safe=False,
                status=status,
            )
        )
        response["WWW-Authenticate"] = 'Basic realm="{}"'.format(settings.APPRISE_BASIC_AUTH_REALM)
        return response
