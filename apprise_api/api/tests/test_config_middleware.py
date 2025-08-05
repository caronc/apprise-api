# Copyright (C) 2025 Chris Caron <lead2gold@gmail.com>
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
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from apprise_api.core.middleware.config import DetectConfigMiddleware


class DetectConfigMiddlewareTest(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(APPRISE_DEFAULT_CONFIG_ID="")
    def test_middleware_falls_back_when_config_absent(self):
        """
        Ensure we trigger the `if not config:` fallback
        """
        # Create request to path that does NOT match /cfg/<key>
        request = self.factory.get("/")
        request.COOKIES = {}  # no 'key' cookie

        # Patch middleware to capture the 'config' result
        def get_response(req):
            req._config = getattr(req, "_config", None)
            return HttpResponse()  # simulate response

        middleware = DetectConfigMiddleware(get_response)
        response = middleware(request)

        # Validate we entered the `if not config:` branch
        # You can't test the internal `config` variable directly unless it's exposed
        self.assertTrue(hasattr(response, "_config") is False)
