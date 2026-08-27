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
import datetime
import re

from api.auth import Authentication
from api.utils import CONFIG_KEY_PATTERN, CONFIG_KEY_REGEX
from django.conf import settings


class DetectConfigMiddleware:
    """
    Using the `key=` variable, allow one pre-configure the default
    configuration to use.

    """

    _is_cfg_path = re.compile(r"/(cfg|auth)/(?P<key>{})".format(CONFIG_KEY_REGEX))

    def __init__(self, get_response):
        """
        Prepare our initialization
        """
        self.get_response = get_response

    def __call__(self, request):
        """
        Define our middleware hook
        """

        # A shared login remains tied to its authenticated Config ID.
        shared_key = getattr(request, "apprise_web_auth_key", None)
        if (
            getattr(request, "apprise_auth_permission", None) == Authentication.ROLE_USER
            and isinstance(shared_key, str)
            and CONFIG_KEY_PATTERN.match(shared_key)
        ):
            config = shared_key
        else:
            # path_info excludes APPRISE_BASE_URL, leaving the route itself.
            result = self._is_cfg_path.match(request.path_info)
            config = result.group("key") if result else None

        if config is None:
            # Our current config
            config = request.COOKIES.get("key", settings.APPRISE_DEFAULT_CONFIG_ID)

            # Extract our key (fall back to our default if not set)
            config = request.GET.get("key", config).strip()

        if not config or not CONFIG_KEY_PATTERN.match(config):
            # Invalid browser state never becomes a filename or route value.
            config = settings.APPRISE_DEFAULT_CONFIG_ID

        # Set our theme to a cookie
        request.default_config_id = config

        # Get our response object
        response = self.get_response(request)

        # Set our cookie
        max_age = 365 * 24 * 60 * 60  # 1 year
        expires = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=max_age)

        if getattr(request, "clear_config_cookie", False):
            # Logout must not be undone while the response unwinds through
            # middleware after the view deletes the browser state.
            response.delete_cookie("key", path="/", samesite="Lax")
        else:
            # Remember the Config ID without exposing it to page scripts.
            secure = (
                request.is_secure()
                or "https://{}".format(request.get_host()).lower() in settings.APPRISE_TRUSTED_ORIGINS
            )
            response.set_cookie(
                "key",
                getattr(request, "default_config_id", config),
                expires=expires,
                httponly=True,
                secure=secure,
                samesite="Lax",
            )

        # return our response
        return response
