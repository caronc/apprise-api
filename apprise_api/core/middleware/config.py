#
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
#
import datetime
import re

from django.conf import settings


class DetectConfigMiddleware:
    """
    Using the `key=` variable, allow one pre-configure the default
    configuration to use.

    """

    _is_cfg_path = re.compile(r"/cfg/(?P<key>[\w_-]{1,128})")

    def __init__(self, get_response):
        """
        Prepare our initialization
        """
        self.get_response = get_response

    def __call__(self, request):
        """
        Define our middleware hook
        """

        result = self._is_cfg_path.match(request.path)
        if not result:
            # Our current config
            config = request.COOKIES.get("key", settings.APPRISE_DEFAULT_CONFIG_ID)

            # Extract our key (fall back to our default if not set)
            config = request.GET.get("key", config).strip()

        else:
            config = result.group("key")

        if not config:
            # Fallback to default config
            config = settings.APPRISE_DEFAULT_CONFIG_ID

        # Set our theme to a cookie
        request.default_config_id = config

        # Get our response object
        response = self.get_response(request)

        # Set our cookie
        max_age = 365 * 24 * 60 * 60  # 1 year
        expires = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=max_age)

        # Set our cookie
        response.set_cookie("key", config, expires=expires)

        # return our response
        return response
