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
import json
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from django.urls import Resolver404, resolve

from ..utils import AppriseStoreMode


class ManagerPageTests(SimpleTestCase):
    """
    Manager Webpage testing
    """

    def test_manage_status_code(self):
        """
        General testing of management page
        """
        # No permission to get keys
        response = self.client.get("/cfg/")
        assert response.status_code == 403

        with override_settings(APPRISE_ADMIN=True, APPRISE_STATEFUL_MODE="hash"):
            response = self.client.get("/cfg/")
            assert response.status_code == 403

        with override_settings(APPRISE_ADMIN=False, APPRISE_STATEFUL_MODE="simple"):
            response = self.client.get("/cfg/")
            assert response.status_code == 403

        with override_settings(APPRISE_ADMIN=False, APPRISE_STATEFUL_MODE="disabled"):
            response = self.client.get("/cfg/")
            assert response.status_code == 403

        with override_settings(APPRISE_ADMIN=True, APPRISE_STATEFUL_MODE="disabled"):
            response = self.client.get("/cfg/")
            assert response.status_code == 403

        # But only when the setting is enabled
        with override_settings(APPRISE_ADMIN=True, APPRISE_STATEFUL_MODE="simple"):
            response = self.client.get("/cfg/")
            assert response.status_code == 200

        # An invalid key was specified
        response = self.client.get("/cfg/**invalid-key**")
        assert response.status_code == 404

        # An invalid key was specified
        response = self.client.get("/cfg/valid-key")
        assert response.status_code == 200

    def test_new_configuration_link_captures_href_before_confirmation(self):
        """
        The new configuration confirmation resolves asynchronously, so the
        click event's currentTarget can no longer be used inside the callback.
        """
        response = self.client.get("/cfg/valid-key")
        assert response.status_code == 200

        content = response.content.decode("utf-8")
        assert "const newConfigurationHref = cfgGenLink.href;" in content
        assert "window.location.href = newConfigurationHref;" in content
        assert "window.location.href = e.currentTarget.href;" not in content
        assert "cfggen-id-copy" in content
        assert "content_copy" in content
        assert "appriseCopyToClipboard(" in content
        assert "Config ID copied to clipboard" in content

    def test_configuration_fetch_requests_json(self):
        """The configuration editor explicitly requests a JSON response."""
        response = self.client.get("/cfg/valid-key")
        assert response.status_code == 200

        content = response.content.decode("utf-8")
        request = content.split("let response = await fetch('/get/valid-key'", 1)[1].split("});", 1)[0]
        assert "'Accept': 'application/json'" in request
        assert "'Content-Type'" not in request

    def test_get_config(self):
        """
        Test retrieving configuration
        """

        # our key to use
        key = "test_cfg_config_"

        # No content saved to the location yet
        response = self.client.post("/cfg/{}".format(key))
        self.assertEqual(response.status_code, 204)

        # Add some content
        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200

        # Handle case when we try to retrieve our content but we have no idea
        # what the format is in. Essentially there had to have been disk
        # corruption here or someone meddling with the backend.
        with patch("gzip.open", side_effect=OSError):
            response = self.client.post("/cfg/{}".format(key))
            assert response.status_code == 500

        # Now we should be able to see our content
        response = self.client.post("/cfg/{}".format(key))
        assert response.status_code == 200

        # Add a YAML file
        response = self.client.post(
            "/add/{}".format(key),
            {
                "format": "yaml",
                "config": """
                urls:
                   - dbus://""",
            },
        )
        assert response.status_code == 200

        # Now retrieve our YAML configuration
        response = self.client.post("/cfg/{}".format(key))
        assert response.status_code == 200

        # Verify that the correct Content-Type is set in the header of the
        # response
        assert "Content-Type" in response
        assert response["Content-Type"].startswith("text/yaml")

        # The configuration editor requests the same content as JSON.
        response = self.client.post("/cfg/{}".format(key), HTTP_ACCEPT="application/json")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/json")
        assert response.json()["format"] == "yaml"

    def test_get_config_json_content_type_without_explicit_accept(self):
        """
        The web UI's config tab loads existing configuration via a
        fetch() call carrying an explicit JSON Content-Type but no
        Accept override (so Accept defaults to */*). That must still be
        honored as a request for a JSON response.
        """

        key = "test_cfg_config_json_"
        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200

        response = self.client.post(
            "/cfg/{}".format(key),
            content_type="application/json",
            HTTP_ACCEPT="*/*",
        )
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/json")
        payload = response.json()
        assert "config" in payload
        assert "format" in payload

    def test_manage_cfg_list_content_type_defaults_to_html(self):
        """
        /cfg/ should render HTML by default when allowed.
        """
        with override_settings(APPRISE_ADMIN=True, APPRISE_STATEFUL_MODE="simple"):
            response = self.client.get("/cfg/")
            assert response.status_code == 200
            assert response["Content-Type"].startswith("text/html")

    def test_manage_cfg_list_json_when_requested(self):
        """
        /cfg/ should return JSON list when requested via Accept header.
        """
        with override_settings(APPRISE_ADMIN=True, APPRISE_STATEFUL_MODE="simple"):
            response = self.client.get("/cfg/", HTTP_ACCEPT="application/json")
            assert response.status_code == 200
            assert response["Content-Type"].startswith("application/json")
            payload = json.loads(response.content.decode("utf-8"))
            assert isinstance(payload, list)

    def test_manage_cfg_list_denied_content_type_plain_text(self):
        """
        /cfg/ denied case should be plain text when JSON is not requested.
        """
        response = self.client.get("/cfg/")
        assert response.status_code == 403
        assert response["Content-Type"].startswith("text/plain")

    def test_manage_cfg_list_denied_content_type_json(self):
        """
        /cfg/ denied case should return JSON when requested.
        """
        response = self.client.get("/cfg/", HTTP_ACCEPT="application/json")
        assert response.status_code == 403
        assert response["Content-Type"].startswith("application/json")
        payload = json.loads(response.content.decode("utf-8"))
        assert "error" in payload

    def test_manage_cfg_list_json_returns_store_keys(self):
        with override_settings(APPRISE_ADMIN=True, APPRISE_STATEFUL_MODE="simple"):
            mod = resolve("/cfg/").func.__module__
            with patch(f"{mod}.ConfigCache.keys", return_value=["abc", "def"]) as m:
                response = self.client.get("/cfg/", HTTP_ACCEPT="application/json")
                assert response.status_code == 200
                assert response["Content-Type"].startswith("application/json")

                payload = json.loads(response.content.decode("utf-8"))
                assert payload == ["abc", "def"]
                m.assert_called_once_with()

    @override_settings(APPRISE_API_ONLY=True)
    def test_api_only_blocks_config_list_if_present(self) -> None:
        """
        Test our inability to access /cfg if APPRISE_API_ONLY set to true
        """
        paths = ("/", "/cfg", "/cfg/key", "/details")
        for path in paths:
            try:
                resolve(path)
            except Resolver404:
                self.skipTest(f"Path not present in URLconf: {path}")

            response = self.client.get(path)
            assert response.status_code == 421

    @override_settings(APPRISE_API_ONLY=True, APPRISE_ADMIN=True, APPRISE_STATEFUL_MODE=AppriseStoreMode.SIMPLE)
    def test_api_only_still_serves_json_for_details_and_config_list(self) -> None:
        """
        APPRISE_API_ONLY disables the browsable HTML pages, not the JSON
        API itself: a client explicitly asking for JSON is still "using
        the API" (see the README's own description of this setting) and
        must get its data back, even though the same paths remain blocked
        for a browser-style request (no explicit JSON preference).
        """
        json_capable_paths = ("/cfg", "/details")
        for path in json_capable_paths:
            response = self.client.get(path, **{"HTTP_ACCEPT": "application/json"})
            self.assertEqual(response.status_code, 200, path)
            assert response["Content-Type"].startswith("application/json")

        # These pages have no JSON form at all - API-only mode always
        # blocks them, Accept header or not.
        html_only_paths = ("/", "/cfg/key")
        for path in html_only_paths:
            response = self.client.get(path, **{"HTTP_ACCEPT": "application/json"})
            self.assertEqual(response.status_code, 421, path)
