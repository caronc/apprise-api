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
"""Custom plugins load on the config views, not just the notify views."""

from pathlib import Path
import tempfile
import textwrap
from unittest import mock

from apprise.manager_plugins import NotificationManager
from django.test import RequestFactory, SimpleTestCase
from django.test.utils import override_settings

from ..views import _apprise_asset

# Apprise keeps loaded plugins in one shared manager per process
N_MGR = NotificationManager()


class PluginPathsTests(SimpleTestCase):
    def test_asset_loads_plugin_paths(self):
        """Every asset is built with the configured plugin paths."""
        request = RequestFactory().get("/")
        captured = {}

        class SpyAsset:
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)

        paths = ["/plugin"]
        with (
            override_settings(APPRISE_PLUGIN_PATHS=paths),
            mock.patch("apprise.AppriseAsset", SpyAsset),
        ):
            _apprise_asset(request)

        self.assertEqual(captured.get("plugin_paths"), paths)

    def test_custom_plugin_review(self):
        """A saved custom plugin URL shows on Review before any notify call."""
        schema = "custplug"

        # Forget the test plugin afterwards so later tests start clean
        self.addCleanup(N_MGR.remove, schema)

        # Each way of saving a configuration through /add
        payloads = {
            "urls": {"urls": f"{schema}://"},
            "config": {"format": "text", "config": f"{schema}://"},
        }

        with tempfile.TemporaryDirectory() as plugin_dir:
            # A minimal custom plugin built with the @notify decorator
            Path(plugin_dir, f"{schema}.py").write_text(
                textwrap.dedent(
                    f"""
                    from apprise.decorators import notify

                    @notify(on="{schema}")
                    def my_wrapper(body, title, notify_type, *args, **kwargs):
                        return True
                    """
                ),
                encoding="utf-8",
            )

            with override_settings(APPRISE_PLUGIN_PATHS=[plugin_dir]):
                for name, payload in payloads.items():
                    with self.subTest(name):
                        key = f"test_custom_plugin_review_{name}"

                        # Save the configuration
                        response = self.client.post(f"/add/{key}", payload)
                        self.assertEqual(response.status_code, 200)

                        # The Review tab reads its URL list from here
                        response = self.client.get(f"/json/urls/{key}")
                        self.assertEqual(response.status_code, 200)
                        urls = response.json()["urls"]
                        self.assertEqual(len(urls), 1)
                        self.assertTrue(urls[0]["url"].startswith(f"{schema}://"))
