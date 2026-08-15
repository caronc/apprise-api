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
import base64
from json import dumps, loads
import os
from unittest import mock

import apprise
from django.test import SimpleTestCase
from django.test.utils import override_settings

from ..utils import ConfigCache

_MASTER_TOKEN = base64.b64encode(b"master:pass").decode()


def _basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


_GOOD_MASTER = {"authorization": _basic("master", "pass")}


class ConfigIdHeaderTests(SimpleTestCase):
    """Test X-Apprise-Config-ID as an alternative to URL-based keys."""

    def tearDown(self):
        for key in (
            "header_get_key",
            "header_add_key",
            "header_del_key",
            "header_auth_key",
            "header_urls_key",
            "header_notify_key",
            "header_status_key",
            "header_reject_key",
            "header_cfg_key",
        ):
            ConfigCache.clear(key)
            ConfigCache.clear_auth(key)

    def test_get_via_header_matches_get_via_url(self):
        key = "header_get_key"
        self.client.post("/add/{}".format(key), {"urls": "json://localhost"})

        by_url = self.client.post("/get/{}".format(key))
        by_header = self.client.post("/get/", headers={"X-Apprise-Config-ID": key})

        self.assertEqual(by_url.status_code, 200)
        self.assertEqual(by_header.status_code, 200)
        self.assertEqual(by_url.content, by_header.content)

    def test_add_via_header(self):
        key = "header_add_key"
        self.assertIsNone(ConfigCache.get(key)[0])

        response = self.client.post(
            "/add/",
            {"urls": "json://localhost"},
            headers={"X-Apprise-Config-ID": key},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(ConfigCache.get(key)[0])

    def test_del_via_header(self):
        key = "header_del_key"
        self.client.post("/add/{}".format(key), {"urls": "json://localhost"})
        self.assertIsNotNone(ConfigCache.get(key)[0])

        response = self.client.post("/del/", headers={"X-Apprise-Config-ID": key})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(ConfigCache.get(key)[0])

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_auth_via_header(self):
        key = "header_auth_key"
        # Header-based first locks also require administrator credentials.
        response = self.client.post(
            "/auth/",
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers={"X-Apprise-Config-ID": key, **_GOOD_MASTER},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ConfigCache.verify_auth(key, "alice", "secret"))

        response = self.client.delete(
            "/auth/",
            headers={"X-Apprise-Config-ID": key, "authorization": _basic("alice", "secret")},
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(ConfigCache.has_auth(key))

        response = self.client.delete(
            "/auth/",
            headers={"X-Apprise-Config-ID": key, **_GOOD_MASTER},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ConfigCache.has_auth(key))

    def test_json_urls_via_header(self):
        key = "header_urls_key"
        self.client.post("/add/{}".format(key), {"urls": "json://localhost"})

        response = self.client.get("/json/urls/", headers={"X-Apprise-Config-ID": key})
        self.assertEqual(response.status_code, 200)

    def test_status_via_header_matches_status_via_url(self):
        key = "header_status_key"
        by_url = self.client.get("/status/{}".format(key), **{"HTTP_ACCEPT": "application/json"})
        by_header = self.client.get(
            "/status", headers={"X-Apprise-Config-ID": key}, **{"HTTP_ACCEPT": "application/json"}
        )
        self.assertEqual(by_url.status_code, by_header.status_code)
        self.assertEqual(loads(by_url.content).keys(), loads(by_header.content).keys())

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_status_header_enforces_per_key_auth(self):
        key = "header_status_key"
        self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )

        denied = self.client.get("/status", headers={"X-Apprise-Config-ID": key})
        self.assertEqual(denied.status_code, 401)

        allowed = self.client.get(
            "/status",
            headers={"X-Apprise-Config-ID": key, "authorization": _basic("alice", "secret")},
        )
        self.assertEqual(allowed.status_code, 200)

    def test_status_without_header_is_unaffected(self):
        """A keyless status request remains unchanged."""
        response = self.client.get("/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"OK")

    def test_status_reports_admin_privilege_when_auth_disabled(self):
        """No global token configured means every caller is unrestricted."""
        response = self.client.get("/status", **{"HTTP_ACCEPT": "application/json"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(loads(response.content)["privilege"], "admin")

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_status_reports_admin_and_user_privilege(self):
        key = "header_status_key"
        self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )

        admin = self.client.get(
            "/status",
            headers={"X-Apprise-Config-ID": key, **_GOOD_MASTER},
            **{"HTTP_ACCEPT": "application/json"},
        )
        self.assertEqual(admin.status_code, 200)
        self.assertEqual(loads(admin.content)["privilege"], "admin")

        user = self.client.get(
            "/status",
            headers={"X-Apprise-Config-ID": key, "authorization": _basic("alice", "secret")},
            **{"HTTP_ACCEPT": "application/json"},
        )
        self.assertEqual(user.status_code, 200)
        self.assertEqual(loads(user.content)["privilege"], "user")

    @staticmethod
    def _asset_spy_class():
        """Build an AppriseAsset that ignores unsupported test arguments."""
        real_asset_cls = apprise.AppriseAsset

        class SpyAsset(real_asset_cls):
            def __init__(self, **kwargs):
                safe_kwargs = {k: v for k, v in kwargs.items() if hasattr(real_asset_cls, k)}
                super().__init__(**safe_kwargs)

        return SpyAsset

    @mock.patch("apprise.Apprise.notify")
    def test_notify_header_uses_stateful_route(self, mock_notify):
        key = "header_notify_key"
        self.client.post("/add/{}".format(key), {"urls": "json://localhost"})

        # A successful request without payload URLs confirms it used storage.
        with mock.patch("apprise.AppriseAsset", self._asset_spy_class()):
            response = self.client.post(
                "/notify",
                {"body": "hello"},
                headers={"X-Apprise-Config-ID": key},
            )
        self.assertEqual(response.status_code, 200)
        mock_notify.assert_called_once()

    @mock.patch("apprise.Apprise.notify")
    def test_notify_without_header_stays_stateless(self, mock_notify):
        """Without the header, /notify remains stateless."""
        with mock.patch("apprise.AppriseAsset", self._asset_spy_class()):
            response = self.client.post("/notify", {"urls": "json://localhost", "body": "hello"})
        self.assertEqual(response.status_code, 200)
        mock_notify.assert_called_once()

    def test_bare_route_without_key_is_rejected(self):
        for path in ("/get/", "/add/", "/del/"):
            self.assertEqual(self.client.post(path).status_code, 400, msg=path)
        self.assertEqual(self.client.get("/json/urls/").status_code, 400, msg="/json/urls/")

    def test_invalid_header_is_rejected(self):
        response = self.client.post("/get/", headers={"X-Apprise-Config-ID": "not a valid key!!"})
        self.assertEqual(response.status_code, 400)

    @mock.patch("apprise.Apprise.notify")
    def test_invalid_notify_header_is_rejected(self, mock_notify):
        """An invalid key must not turn a requested stateful send into stateless."""
        response = self.client.post(
            "/notify",
            {"body": "hello"},
            headers={"X-Apprise-Config-ID": "not a valid key!!"},
        )
        self.assertEqual(response.status_code, 400)
        mock_notify.assert_not_called()

    @override_settings(APPRISE_STATEFUL_MODE="simple")
    def test_path_traversal_header_is_rejected(self):
        """Reject unsafe header keys before SIMPLE mode uses them as filenames."""
        malicious = "../../../../tmp/apprise-traversal-poc"
        response = self.client.post(
            "/add/",
            {"urls": "json://localhost"},
            headers={"X-Apprise-Config-ID": malicious},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(os.path.exists("/tmp/apprise-traversal-poc.cfg"))
        self.assertFalse(os.path.exists("/tmp/apprise-traversal-poc.yml"))

    def test_header_takes_precedence_over_url_key(self):
        """The header key wins when the URL also supplies a key."""
        url_key = "header_get_key"
        header_key = "header_status_key"
        self.client.post("/add/{}".format(url_key), {"urls": "json://localhost"})
        self.client.post("/add/{}".format(header_key), {"urls": "json://example.com"})

        response = self.client.post(
            "/get/{}".format(url_key),
            headers={"X-Apprise-Config-ID": header_key},
        )
        self.assertEqual(response.status_code, 200)
        # Confirms it read header_key's config, not url_key's.
        self.assertIn("example.com", response.content.decode())
        self.assertNotIn("localhost", response.content.decode())

    def test_invalid_header_does_not_fall_back_to_url_key(self):
        """An invalid header is rejected even when the URL key is valid."""
        url_key = "header_reject_key"
        self.client.post("/add/{}".format(url_key), {"urls": "json://localhost"})

        response = self.client.post(
            "/get/{}".format(url_key),
            headers={"X-Apprise-Config-ID": "../../etc/passwd"},
        )
        self.assertEqual(response.status_code, 400)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_cfg_header_controls_auth(self):
        """The header key controls configuration-page access and content."""
        url_key, header_key = "header_reject_key", "header_cfg_key"
        self.client.post(
            "/auth/{}".format(header_key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )

        # The protected header key takes precedence over the unlocked URL key.
        denied = self.client.get(
            "/cfg/{}".format(url_key),
            headers={"X-Apprise-Config-ID": header_key},
        )
        self.assertEqual(denied.status_code, 401)

        allowed = self.client.get(
            "/cfg/{}".format(url_key),
            headers={"X-Apprise-Config-ID": header_key, "authorization": _basic("alice", "secret")},
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertIn(header_key, allowed.content.decode())

    def test_cfg_rejects_invalid_header(self):
        """An invalid header is rejected even when the URL key is valid."""
        url_key = "header_reject_key"

        response = self.client.get(
            "/cfg/{}".format(url_key),
            headers={"X-Apprise-Config-ID": "../../etc/passwd"},
        )
        self.assertEqual(response.status_code, 400)
