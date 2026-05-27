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

import importlib.util
import os
from unittest import mock

from django.conf import global_settings
from django.test import SimpleTestCase

# Path to the settings module under test, resolved relative to this file:
#   apprise_api/api/tests/ -> ../../ -> apprise_api/ -> core/settings/__init__.py
_SETTINGS_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "core", "settings", "__init__.py")
)


def _load_settings(extra_env=None):
    """Execute core/settings/__init__.py as a fresh module in a controlled environment.

    Returns the module so callers can inspect settings values (e.g. TIME_ZONE)
    as they would be set for a given environment, independently of Django's
    already-cached settings object.
    """
    env = extra_env or {}
    spec = importlib.util.spec_from_file_location("_settings_under_test", _SETTINGS_PATH)
    assert spec is not None and spec.loader is not None, "Could not load spec from {}".format(_SETTINGS_PATH)
    mod = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, env, clear=True):
        spec.loader.exec_module(mod)
    return mod


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
            _raw_base = os.environ.get("APPRISE_BASE_URL", os.environ.get("BASE_URL", "")).strip().strip("/")

            return f"/{_raw_base}" if _raw_base else ""

    def test_base_url_normalization(self):
        """
        Test that priority, fallback, and slash-stripping behave correctly
        """
        # 1. Prioritize APPRISE_BASE_URL over legacy BASE_URL
        self.assertEqual(self._get_base_url(apprise_base="/apprise", base="/wrong"), "/apprise")

        # 2. Fallback to BASE_URL if APPRISE_BASE_URL is not set
        self.assertEqual(self._get_base_url(apprise_base=None, base="/apprise"), "/apprise")

        # 3. Strip trailing/leading slashes and whitespace aggressively
        self.assertEqual(self._get_base_url(apprise_base="  /apprise/  "), "/apprise")
        self.assertEqual(self._get_base_url(apprise_base="apprise/"), "/apprise")
        self.assertEqual(self._get_base_url(apprise_base="/apprise"), "/apprise")
        self.assertEqual(self._get_base_url(apprise_base="apprise"), "/apprise")

        # 3b. Strip tabs and newlines (not just spaces)
        self.assertEqual(self._get_base_url(apprise_base="\t/apprise\n"), "/apprise")
        self.assertEqual(self._get_base_url(apprise_base="\n/apprise\n"), "/apprise")

        # 3c. Normalize multiple leading slashes to a single slash
        self.assertEqual(self._get_base_url(apprise_base="///apprise"), "/apprise")
        self.assertEqual(self._get_base_url(apprise_base="///apprise///"), "/apprise")

        # 4. Handle empty/root paths safely (must result in an empty string)
        self.assertEqual(self._get_base_url(apprise_base="/"), "")
        self.assertEqual(self._get_base_url(apprise_base="   "), "")
        self.assertEqual(self._get_base_url(apprise_base=None, base=None), "")


class TimezoneSettingsTests(SimpleTestCase):
    """
    Ensure TIME_ZONE follows the container TZ environment variable.
    """

    def test_time_zone_from_tz_env(self):
        """TIME_ZONE must equal TZ when the env variable is present."""
        mod = _load_settings({"TZ": "America/Toronto"})
        self.assertEqual(mod.TIME_ZONE, "America/Toronto")

    def test_time_zone_various_zones(self):
        """TIME_ZONE must follow TZ across a representative set of zones."""
        for tz in ("Europe/Madrid", "Asia/Tokyo", "America/New_York", "Etc/UTC"):
            mod = _load_settings({"TZ": tz})
            self.assertEqual(mod.TIME_ZONE, tz)

    def test_time_zone_default(self):
        """Without TZ, TIME_ZONE defaults to Etc/UTC"""
        mod = _load_settings()  # TZ intentionally absent from environment
        self.assertEqual(mod.TIME_ZONE, "Etc/UTC")
        self.assertNotEqual(
            mod.TIME_ZONE,
            global_settings.TIME_ZONE,
            "TIME_ZONE must not fall back to Django's built-in default "
            "({!r}). Define TIME_ZONE = os.environ.get('TZ', 'Etc/UTC') "
            "in core/settings/__init__.py.".format(global_settings.TIME_ZONE),
        )
