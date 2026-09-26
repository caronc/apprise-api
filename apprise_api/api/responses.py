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
"""Build consistent API, browser, and plain-text error responses."""

import json
import logging

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from .utils import is_html_response, is_json_response

logger = logging.getLogger("django")


def error_response(
    request,
    message,
    status,
    *,
    field=None,
    template=None,
    context=None,
    headers=None,
):
    """Return one error in the response format requested by the client."""
    # JSON clients always receive a predictable object with an ``error`` key.
    if is_json_response(request):
        payload = {"error": message}
        if field is not None:
            payload["field"] = field
        response = JsonResponse(
            payload,
            encoder=DjangoJSONEncoder,
            safe=False,
            status=status,
        )
    # Browser pages may opt into a friendly error template.
    elif template and is_html_response(request):
        response = render(request, template, context=context, status=status)
    else:
        response = HttpResponse(message, status=status, content_type="text/plain")

    # Authentication challenges and retry hints are attached after rendering.
    for name, value in (headers or {}).items():
        response[name] = value
    return response


def parse_json_body(request, operation, key=None):
    """Decode a JSON request body and return any client-safe error response."""
    try:
        return json.loads(request.body.decode("utf-8")), None

    except RequestDataTooBig:
        logger.warning(
            "%s - %s - JSON payload exceeded %dMB%s",
            operation,
            request.META.get("REMOTE_ADDR", ""),
            settings.APPRISE_UPLOAD_MAX_MEMORY_SIZE / 1048576,
            " using KEY: {}".format(key) if key else "",
        )
        return None, error_response(
            request,
            _("JSON Payload provided is too large"),
            431,
        )

    except (AttributeError, RecursionError, ValueError):
        logger.warning(
            "%s - %s - Invalid JSON payload%s",
            operation,
            request.META.get("REMOTE_ADDR", ""),
            " using KEY: {}".format(key) if key else "",
        )
        return None, error_response(
            request,
            _("Invalid JSON Payload provided"),
            400,
        )


def parse_json_object_body(request, operation, key=None):
    """Decode a JSON request body and require an object at its root."""
    content, parse_error = parse_json_body(request, operation, key)
    if parse_error is not None:
        return None, parse_error

    # Endpoint payloads use named fields, so arrays and scalar roots are invalid.
    if not isinstance(content, dict):
        return None, error_response(
            request,
            _("The JSON payload must be an object"),
            400,
        )

    return content, None
