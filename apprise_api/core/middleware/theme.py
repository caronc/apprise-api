# -*- coding: utf-8 -*-
#
# Copyright (C) 2023 Chris Caron <lead2gold@gmail.com>
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
from django.conf import settings
from core.themes import SiteTheme, SITE_THEMES
import datetime


class AutoThemeMiddleware:
    """
    Using the `theme=` variable, allow one to fix the language to either
    'dark' or 'light'

    """

    def __init__(self, get_response):
        """
        Prepare our initialization
        """
        self.get_response = get_response

    def __call__(self, request):
        """
        Define our middleware hook
        """

        # Our current theme
        current_theme = \
            request.COOKIES.get('t', request.COOKIES.get(
                'theme', settings.APPRISE_DEFAULT_THEME))

        # Extract our theme (fall back to our default if not set)
        theme = request.GET.get("theme", current_theme).strip().lower()
        theme = next((entry for entry in SITE_THEMES
                     if entry.startswith(theme)), None) \
            if theme else None

        if theme not in SITE_THEMES:
            # Fallback to default theme
            theme = SiteTheme.LIGHT

        # Set our theme to a cookie
        request.theme = theme

        # Set our next theme
        request.next_theme = SiteTheme.LIGHT \
            if theme == SiteTheme.DARK \
            else SiteTheme.DARK

        # Get our response object
        response = self.get_response(request)

        # Set our cookie
        max_age = 365 * 24 * 60 * 60  # 1 year
        expires = datetime.datetime.utcnow() + \
            datetime.timedelta(seconds=max_age)

        # Set our cookie
        response.set_cookie('theme', theme, expires=expires.utctimetuple())

        # return our response
        return response
