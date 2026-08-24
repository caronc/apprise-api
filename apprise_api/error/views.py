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


from api.responses import error_response
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.views import View

# Nginx uses this page for rejected request bursts.
AUTH_RETRY_AFTER_SECONDS = 60


class Error401View(View):
    """Render the authentication-required response."""

    template_name = "401.html"

    def get(self, request):
        """Return an HTML or JSON response with a Basic Auth challenge."""
        return error_response(
            request,
            _("Access Denied"),
            401,
            template=self.template_name,
            headers={"WWW-Authenticate": 'Basic realm="{}"'.format(settings.APPRISE_BASIC_AUTH_REALM)},
        )


class Error404View(View):
    """
    Render a 404 page for errors

    Proxy must pass:
      - HTTP_X_ERROR_CODE
      - HTTP_X_ORIGINAL_URI
      - HTTP_X_ORIGINAL_METHOD
    """

    template_name = "404.html"

    def get(self, request):

        original_uri = request.META.get("HTTP_X_ORIGINAL_URI", request.path)
        original_method = request.META.get("HTTP_X_ORIGINAL_METHOD", request.method)
        remote_ip = request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR")

        context = {
            "original_uri": original_uri,
            "original_method": original_method,
            "remote_ip": remote_ip,
        }

        return error_response(
            request,
            _("Page not found"),
            404,
            template=self.template_name,
            context=context,
        )


class Error421View(View):
    """
    Render a 421 page for errors

    Proxy must pass:
      - HTTP_X_ERROR_CODE
      - HTTP_X_ORIGINAL_URI
      - HTTP_X_ORIGINAL_METHOD
    """

    template_name = "421.html"

    def get(self, request):

        original_uri = request.META.get("HTTP_X_ORIGINAL_URI", request.path)
        original_method = request.META.get("HTTP_X_ORIGINAL_METHOD", request.method)
        remote_ip = request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR")

        context = {
            "original_uri": original_uri,
            "original_method": original_method,
            "remote_ip": remote_ip,
        }

        return error_response(
            request,
            _("Page not found"),
            421,
            template=self.template_name,
            context=context,
        )


class Error429View(View):
    """Render the response used after too many authentication attempts."""

    template_name = "429.html"

    def get(self, request):
        """Return an HTML or JSON response with the retry delay."""
        return error_response(
            request,
            _("Too Many Requests"),
            429,
            template=self.template_name,
            context={"retry_after": AUTH_RETRY_AFTER_SECONDS},
            headers={"Retry-After": str(AUTH_RETRY_AFTER_SECONDS)},
        )


class Error50xView(View):
    """
    50x Error Code Response

    Proxy must pass:
      - HTTP_X_ERROR_CODE
      - HTTP_X_ORIGINAL_URI
      - HTTP_X_ORIGINAL_METHOD
    """

    template_name = "50x.html"

    def get(self, request):

        original_uri = request.META.get("HTTP_X_ORIGINAL_URI", request.path)
        original_method = request.META.get("HTTP_X_ORIGINAL_METHOD", request.method)
        remote_ip = request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR")

        context = {
            "original_uri": original_uri,
            "original_method": original_method,
            "remote_ip": remote_ip,
        }

        return error_response(
            request,
            _("System error"),
            500,
            template=self.template_name,
            context=context,
        )
