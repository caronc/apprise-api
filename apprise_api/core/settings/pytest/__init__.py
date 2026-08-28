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

# To create a valid debug settings.py we need to intentionally pollute our
# file with all of the content found in the master configuration.
from tempfile import TemporaryDirectory

from .. import *  # noqa F403

# Debug is always on when running in debug mode
DEBUG = True

# Allowed hosts is not required in debug mode
ALLOWED_HOSTS = []

# A temporary directory to work in for unit testing. Keep the owner alive for
# the lifetime of the settings module; otherwise Python may garbage-collect the
# temporary directory while tests are still using its path.
_APPRISE_CONFIG_TEMP_DIR = TemporaryDirectory()
APPRISE_CONFIG_DIR = _APPRISE_CONFIG_TEMP_DIR.name

# Tests enable browser authentication through override_settings. Give those
# sessions a private, stable key without changing production defaults.
APPRISE_WEB_AUTH_SECRET = "apprise-api-pytest-web-auth-secret"

# Setup our runner
TEST_RUNNER = "core.settings.pytest.runner.PytestTestRunner"
