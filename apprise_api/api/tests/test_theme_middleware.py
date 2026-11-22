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
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from apprise_api.core.middleware.theme import AutoThemeMiddleware, SiteTheme


class AutoThemeMiddlewareTest(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_theme_fallback_when_invalid_theme_given(self):
        request = self.factory.get("/", {"theme": "foobar"})
        request.COOKIES = {}

        def get_response(req):
            return HttpResponse()

        middleware = AutoThemeMiddleware(get_response)
        _response = middleware(request)

        self.assertEqual(request.theme, SiteTheme.LIGHT)

    def test_theme_set_when_valid_theme_given(self):
        def get_response(req):
            return HttpResponse()

        middleware = AutoThemeMiddleware(get_response)

        for theme in (SiteTheme.DARK, SiteTheme.LIGHT):
            request = self.factory.get("/", {"theme": theme})
            request.COOKIES = {}
            _response = middleware(request)
            self.assertEqual(request.theme, theme)

    def test_auto_theme_accepted_in_full_or_by_first_letter(self):
        def get_response(req):
            return HttpResponse()

        middleware = AutoThemeMiddleware(get_response)

        for value in ("auto", "a", "AUTO"):
            request = self.factory.get("/", {"theme": value})
            request.COOKIES = {}
            response = middleware(request)
            self.assertEqual(request.theme, SiteTheme.AUTO)
            self.assertEqual(response.cookies["theme"].value, SiteTheme.AUTO)

    @override_settings(APPRISE_DEFAULT_THEME=SiteTheme.AUTO)
    def test_auto_default_theme_used_without_cookie(self):
        request = self.factory.get("/")
        request.COOKIES = {}
        AutoThemeMiddleware(lambda req: HttpResponse())(request)
        self.assertEqual(request.theme, SiteTheme.AUTO)

    def test_auto_theme_page_follows_system_setting(self):
        """
        - auto: both stylesheets load, each behind a system setting media query
        - light/dark: a single stylesheet loads with no media query
        """
        response = self.client.get("/", {"theme": "auto"})
        content = response.content.decode()
        self.assertIn('media="(prefers-color-scheme: light)"', content)
        self.assertIn('media="(prefers-color-scheme: dark)"', content)
        self.assertIn('id="theme-stylesheet-dark"', content)
        self.assertIn('data-theme="auto"', content)

        response = self.client.get("/", {"theme": "dark"})
        content = response.content.decode()
        self.assertNotIn("prefers-color-scheme", content.split("</head>")[0])
        self.assertNotIn('id="theme-stylesheet-dark"', content)
        self.assertIn("css/theme-dark.css", content)
