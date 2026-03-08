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

import os
from unittest import mock

from django.test import SimpleTestCase


class BaseUrlParsingTests(SimpleTestCase):
    """
    Test the BASE_URL environment variable parsing logic to ensure it correctly
    normalizes prefixes, strips trailing slashes, and falls back properly.
    """

    def _get_base_url(self, apprise_base=None, base=None):
        """
        Helper to simulate the exact logic found in settings/__init__.py
        """
        env = {}
        if apprise_base is not None:
            env["APPRISE_BASE_URL"] = apprise_base
        if base is not None:
            env["BASE_URL"] = base

        with mock.patch.dict(os.environ, env, clear=True):
            # Simulate the exact logic from settings/__init__.py
            _raw_base = os.environ.get(
                "APPRISE_BASE_URL",
                os.environ.get("BASE_URL", "")
            ).strip(" /")

            return f"/{_raw_base}" if _raw_base else ""

    def test_base_url_normalization(self):
        """
        Test that priority, fallback, and slash-stripping behave correctly
        """
        # 1. Prioritize APPRISE_BASE_URL over legacy BASE_URL
        self.assertEqual(
            self._get_base_url(apprise_base="/apprise", base="/wrong"),
            "/apprise"
        )

        # 2. Fallback to BASE_URL if APPRISE_BASE_URL is not set
        self.assertEqual(
            self._get_base_url(apprise_base=None, base="/apprise"),
            "/apprise"
        )

        # 3. Strip trailing/leading slashes and whitespace aggressively
        self.assertEqual(self._get_base_url(apprise_base="  /apprise/  "), "/apprise")
        self.assertEqual(self._get_base_url(apprise_base="apprise/"), "/apprise")
        self.assertEqual(self._get_base_url(apprise_base="/apprise"), "/apprise")
        self.assertEqual(self._get_base_url(apprise_base="apprise"), "/apprise")

        # 4. Handle empty/root paths safely (must result in an empty string)
        self.assertEqual(self._get_base_url(apprise_base="/"), "")
        self.assertEqual(self._get_base_url(apprise_base="   "), "")
        self.assertEqual(self._get_base_url(apprise_base=None, base=None), "")
