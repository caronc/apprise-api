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
"""Custom plugin paths must apply on config UI /add and /json/urls paths (#383)."""

from pathlib import Path
import textwrap
from unittest import mock

from django.test import RequestFactory, SimpleTestCase
from django.test.utils import override_settings

from ..views import _apprise_asset


class PluginPathsConfigTests(SimpleTestCase):
    def _write_plugin(self, directory, schema):
        Path(directory, f"{schema}.py").write_text(
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

    def test_apprise_asset_defaults_plugin_paths(self):
        """Config UI helpers must pass APPRISE_PLUGIN_PATHS into AppriseAsset."""
        request = RequestFactory().get("/")
        captured = {}

        class SpyAsset:
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)

        paths = ["/tmp/apprise-plugin-paths-test"]
        with (
            override_settings(APPRISE_PLUGIN_PATHS=paths),
            mock.patch("apprise.AppriseAsset", SpyAsset),
        ):
            _apprise_asset(request, allow_templates=True)

        self.assertEqual(captured.get("plugin_paths"), paths)
        self.assertTrue(captured.get("allow_templates"))

    def test_custom_plugin_urls_load_via_add_and_json(self):
        """Saving foobar:// through /add must leave it readable on /json/urls."""
        import tempfile

        schema = "custplug383"
        with tempfile.TemporaryDirectory() as plugin_dir:
            self._write_plugin(plugin_dir, schema)
            key = "test_custom_plugin_urls_load_via_add_and_json"
            with override_settings(APPRISE_PLUGIN_PATHS=[plugin_dir]):
                response = self.client.post(f"/add/{key}", {"urls": f"{schema}://"})
                self.assertEqual(response.status_code, 200)

                response = self.client.get(f"/json/urls/{key}")
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertGreaterEqual(len(payload.get("urls", [])), 1)
                self.assertTrue(
                    any(schema in (entry.get("url") or "") for entry in payload["urls"]),
                    payload,
                )

    def test_custom_plugin_config_text_loads_via_add_and_json(self):
        """Text configuration referencing a custom schema must parse for Review."""
        import tempfile

        schema = "custplug383cfg"
        with tempfile.TemporaryDirectory() as plugin_dir:
            self._write_plugin(plugin_dir, schema)
            key = "test_custom_plugin_config_text_loads_via_add_and_json"
            with override_settings(APPRISE_PLUGIN_PATHS=[plugin_dir]):
                response = self.client.post(
                    f"/add/{key}",
                    {"format": "text", "config": f"{schema}://"},
                )
                self.assertEqual(response.status_code, 200)

                response = self.client.get(f"/json/urls/{key}")
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertGreaterEqual(len(payload.get("urls", [])), 1)
                self.assertTrue(
                    any(schema in (entry.get("url") or "") for entry in payload["urls"]),
                    payload,
                )
