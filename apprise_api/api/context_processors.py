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
import apprise
from django.conf import settings

from .utils import (
    CONFIG_KEY_MAX_LENGTH,
    WEB_AUTH_COOKIE,
    ConfigCache,
    can_list_configurations,
    can_move_or_delete_configuration,
    gen_unique_config_id,
)


def stateful_mode(request):
    """
    Returns our loaded Stateful Mode
    """
    return {"STATEFUL_MODE": ConfigCache.mode}


def config_lock(request):
    """
    Returns the state of our global configuration lock
    """
    return {"CONFIG_LOCK": settings.APPRISE_CONFIG_LOCK}


def admin_enabled(request):
    """
    Returns whether we allow the config list to be displayed
    """
    return {"APPRISE_ADMIN": settings.APPRISE_ADMIN}


def authentication(request):
    """Expose the current browser login state to HTML templates."""
    auth_enabled = settings.APPRISE_AUTH_REQUIRED
    return {
        "AUTH_ENABLED": auth_enabled,
        "AUTH_ADMIN_ENABLED": settings.APPRISE_BASIC_AUTH_TOKEN is not None,
        "AUTH_PERMISSION": getattr(request, "apprise_auth_permission", "disabled"),
        "AUTH_USERNAME": getattr(request, "apprise_auth_username", None),
        # Cookies let browser pages omit the Config ID from the URL.
        # Explicit keyed URLs still work for cookie-free clients.
        "COOKIE_CONFIG_URLS": bool(
            request.COOKIES.get(WEB_AUTH_COOKIE) or (not auth_enabled and request.COOKIES.get("key"))
        ),
        "CAN_LIST_CONFIGS": can_list_configurations(request),
        "CAN_MOVE_CONFIG": can_move_or_delete_configuration(request),
    }


def apprise_metadata(request):
    """
    Returns the current details of the Apprise Library and API under the hood
    """

    return {
        "APPRISE_LIB_VERSION": apprise.__version__,
        "APPRISE_LIB_URL": "http://github.com/caronc/apprise",
        "APPRISE_API_VERSION": settings.APP_VERSION,
        "APPRISE_API_URL": settings.APP_URL,
        "APPRISE_API_LICENSE": settings.APP_LICENSE,
        "APPRISE_API_COPYRIGHT": settings.APP_COPYRIGHT,
        "APPRISE_AUTHOR": settings.APP_AUTHOR,
    }


def default_config_id(request):
    """
    Returns a unique config identifier
    """
    # Authentication can reject a request before config detection runs.
    config_id = getattr(request, "default_config_id", settings.APPRISE_DEFAULT_CONFIG_ID)
    return {
        "CONFIG_KEY_MAX_LENGTH": CONFIG_KEY_MAX_LENGTH,
        "DEFAULT_CONFIG_ID": config_id,
    }


def unique_config_id(request):
    """
    Returns a unique config identifier
    """
    return {"UNIQUE_CONFIG_ID": gen_unique_config_id()}
