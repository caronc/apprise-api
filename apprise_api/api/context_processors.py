# -*- coding: utf-8 -*-
#
# Copyright (C) 2020 Chris Caron <lead2gold@gmail.com>
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
from .utils import gen_unique_config_id
from .utils import ConfigCache
from django.conf import settings
import apprise


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


def apprise_version(request):
    """
    Returns the current version of apprise loaded under the hood
    """
    return {"APPRISE_VERSION": apprise.__version__}


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
