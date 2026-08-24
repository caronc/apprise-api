# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
# All rights reserved.
#
# This code is licensed under the MIT License.
"""Build consistent API, browser, and plain-text error responses."""

from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render

from .utils import is_html_response, is_json_response


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
    elif template and is_html_response(request):
        response = render(request, template, context=context, status=status)
    else:
        response = HttpResponse(message, status=status, content_type="text/plain")

    for name, value in (headers or {}).items():
        response[name] = value
    return response
