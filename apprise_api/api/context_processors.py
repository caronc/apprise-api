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
from django.conf import settings

import apprise
from apprise_api import __author__, __copyright__, __license__, __url__, __version__

from .utils import ConfigCache, gen_unique_config_id


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


def apprise_details(request):
    """
    Returns the current details of the Apprise Library and API under the hood
    """
    return {
        "APPRISE_LIB_VERSION": apprise.__version__,

        "APPRISE_API_VERSION": __version__,
        "APPRISE_API_URL": __url__,
        "APPRISE_API_LICENSE": __license__,
        "APPRISE_API_COPYRIGHT": __copyright__,

        "APPRISE_AUTHOR": __author__,
    }


def default_config_id(request):
    """
    Returns a unique config identifier
    """
    return {"DEFAULT_CONFIG_ID": request.default_config_id}


def unique_config_id(request):
    """
    Returns a unique config identifier
    """
    return {"UNIQUE_CONFIG_ID": gen_unique_config_id()}
