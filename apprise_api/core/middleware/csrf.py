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
"""Provide a small CSRF safeguard without requiring CSRF tokens.

Django's CsrfViewMiddleware requires browser cookies and tokens for POST and
DELETE requests. That would change the documented ability to call this API
directly from curl or another application. This middleware instead rejects
cross-site browser requests with a different Origin header. Calls without an
Origin header continue normally.
"""

from urllib.parse import urlsplit

from api.utils import is_json_response
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse, JsonResponse
from django.utils.translation import gettext_lazy as _

# Safe methods do not change state and need no Origin check.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _same_origin(request, origin_header):
    """True when `origin_header` names this same server.

    ``APPRISE_TRUSTED_ORIGINS`` compares scheme, host, and port. Without it,
    only host and port are compared because bundled nginx does not forward
    the browser's original scheme. HTTPS deployments should set the list.
    """
    origin = urlsplit(origin_header)

    if settings.APPRISE_TRUSTED_ORIGINS:
        full_origin = "{}://{}".format(origin.scheme, origin.netloc).lower()
        return full_origin in settings.APPRISE_TRUSTED_ORIGINS

    return origin.netloc.lower() == request.get_host().lower()


class OriginValidationMiddleware:
    """Reject unsafe browser requests from another origin.

    CLI, mobile, and backend clients normally omit ``Origin`` and remain
    unaffected. Matching browser origins are also allowed.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method not in _SAFE_METHODS:
            origin = request.headers.get("origin", "")
            if origin and not _same_origin(request, origin):
                msg = _("Cross-site request rejected")
                status = 403
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
                return response

        return self.get_response(request)
