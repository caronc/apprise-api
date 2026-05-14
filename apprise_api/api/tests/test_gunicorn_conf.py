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
import sys
import types
from unittest import mock

from django.test import SimpleTestCase

# Absolute path to gunicorn.conf.py relative to this test file:
#   apprise_api/api/tests/ -> ../../ -> apprise_api/
_GUNICORN_CONF_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "gunicorn.conf.py"))


def _load_gunicorn_conf(extra_env=None):
    """
    Execute gunicorn.conf.py as a fresh module inside a controlled environment.
    Returns a ``(module, tzset_call_count)`` tuple so callers can assert on the
    number of times ``time.tzset()`` was invoked during module load.

    Each call gets a clean import so module-level side-effects (like the
    ``time.tzset()`` call) are re-executed.  ``extra_env`` overlays the minimal
    base environment; omitting ``TZ`` exercises the ``Etc/UTC`` default path.
    """
    env = {
        "DJANGO_SETTINGS_MODULE": "core.settings",
        "LANG": "en_US.UTF-8",
    }
    if extra_env:
        env.update(extra_env)

    # Discard any cached copy so the module body always re-runs on load.
    sys.modules.pop("gunicorn_conf", None)

    spec = importlib.util.spec_from_file_location("gunicorn_conf", _GUNICORN_CONF_PATH)
    assert spec is not None and spec.loader is not None, f"Could not load spec from {_GUNICORN_CONF_PATH}"
    module = importlib.util.module_from_spec(spec)

    with mock.patch.dict(os.environ, env, clear=True), mock.patch("time.tzset") as mock_tzset:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        tzset_call_count = mock_tzset.call_count

    return module, tzset_call_count


class GunicornConfTzTests(SimpleTestCase):
    """
    Verify that gunicorn.conf.py correctly propagates the TZ environment
    variable so that Python's logging timestamps stay consistent with the
    timezone established by supervisord-startup.
    """

    def test_tz_general(self):
        """TZ is forwarded into raw_env and time.tzset() fires at import time."""
        mod, tzset_calls = _load_gunicorn_conf(extra_env={"TZ": "America/New_York"})

        tz_entries = [e for e in mod.raw_env if e.startswith("TZ=")]
        self.assertEqual(len(tz_entries), 1, "Exactly one TZ entry expected in raw_env")
        self.assertEqual(tz_entries[0], "TZ=America/New_York")

        # The module-level tzset() must fire before any worker is forked so the
        # master process itself uses the correct timezone.
        self.assertGreater(tzset_calls, 0)

    def test_tz_default(self):
        """TZ defaults to Etc/UTC when the environment variable is not present."""
        mod, _ = _load_gunicorn_conf()  # TZ intentionally absent from env

        tz_entries = [e for e in mod.raw_env if e.startswith("TZ=")]
        self.assertEqual(len(tz_entries), 1)
        self.assertEqual(tz_entries[0], "TZ=Etc/UTC")

    def test_tz_utc_explicit(self):
        """An explicit TZ=Etc/UTC passes through raw_env unchanged."""
        mod, _ = _load_gunicorn_conf(extra_env={"TZ": "Etc/UTC"})

        tz_entries = [e for e in mod.raw_env if e.startswith("TZ=")]
        self.assertEqual(tz_entries[0], "TZ=Etc/UTC")

    def test_post_fork_calls_tzset(self):
        """post_fork() must call time.tzset() to re-initialise timezone state in each worker."""
        mod, _ = _load_gunicorn_conf()

        with mock.patch("time.tzset") as mock_tzset:
            mod.post_fork(None, None)

        mock_tzset.assert_called_once()

    def test_post_fork_skipped_when_no_tzset(self):
        """post_fork() must not raise on platforms where time.tzset() is unavailable (e.g. Windows)."""
        mod, _ = _load_gunicorn_conf()

        # Replace the module's reference to `time` with a namespace that has no
        # tzset attribute so the hasattr() guard inside post_fork() is exercised.
        mock_time = types.SimpleNamespace()  # intentionally has no tzset
        with mock.patch.dict(mod.__dict__, {"time": mock_time}):
            mod.post_fork(None, None)  # must not raise
