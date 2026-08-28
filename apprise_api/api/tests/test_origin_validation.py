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
"""Reject cross-origin writes without affecting non-browser API clients."""

import base64
from json import dumps

from django.test import SimpleTestCase, override_settings

from ..utils import ConfigCache

_MASTER_TOKEN = base64.b64encode(b"master:pass").decode()
_BROWSER = {"accept": "text/html,application/xhtml+xml"}


class OriginValidationTests(SimpleTestCase):
    def tearDown(self):
        for key in ("origin_add_key", "origin_del_key", "origin_notify_key", "origin_auth_key"):
            ConfigCache.clear(key)
            ConfigCache.clear_auth(key)

    def test_no_origin_header_is_unaffected(self):
        """Requests without an Origin header continue normally."""
        response = self.client.post("/add/origin_add_key", {"urls": "json://localhost"})
        self.assertEqual(response.status_code, 200)

    def test_same_origin_header_is_unaffected(self):
        response = self.client.post(
            "/add/origin_add_key",
            {"urls": "json://localhost"},
            headers={"origin": "http://testserver"},
        )
        self.assertEqual(response.status_code, 200)

    def test_cross_origin_add_is_rejected(self):
        response = self.client.post(
            "/add/origin_add_key",
            {"urls": "json://localhost"},
            headers={"origin": "https://evil.example.com"},
        )
        self.assertEqual(response.status_code, 403)

    def test_cross_origin_del_is_rejected(self):
        self.client.post("/add/origin_del_key", {"urls": "json://localhost"})
        response = self.client.post(
            "/del/origin_del_key",
            headers={"origin": "https://evil.example.com"},
        )
        self.assertEqual(response.status_code, 403)

    def test_cross_origin_notify_is_rejected(self):
        response = self.client.post(
            "/notify/origin_notify_key",
            {"body": "hello"},
            headers={"origin": "https://evil.example.com"},
        )
        self.assertEqual(response.status_code, 403)

    def test_cross_origin_auth_is_rejected(self):
        response = self.client.post(
            "/auth/origin_auth_key",
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers={"origin": "https://evil.example.com"},
        )
        self.assertEqual(response.status_code, 403)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_cross_origin_logout_is_rejected(self):
        """Session deletion uses POST and is covered by origin validation."""
        response = self.client.post(
            "/logout",
            headers={**_BROWSER, "origin": "https://evil.example.com"},
        )
        self.assertEqual(response.status_code, 403)

    def test_get_requests_are_never_affected(self):
        """GET is a safe method: never gated, regardless of Origin."""
        response = self.client.get(
            "/status/origin_add_key",
            headers={"origin": "https://evil.example.com"},
        )
        self.assertEqual(response.status_code, 200)

    def test_json_accept_gets_a_json_error_body(self):
        response = self.client.post(
            "/add/origin_add_key",
            {"urls": "json://localhost"},
            headers={"origin": "https://evil.example.com", "accept": "application/json"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response["Content-Type"], "application/json")

    def test_null_origin_is_rejected(self):
        """A sandboxed browsing context (e.g. a data: URI) sends the literal string "null"."""
        response = self.client.post(
            "/add/origin_add_key",
            {"urls": "json://localhost"},
            headers={"origin": "null"},
        )
        self.assertEqual(response.status_code, 403)

    def test_malformed_origin_is_rejected(self):
        """An invalid host is denied instead of raising an exception."""
        response = self.client.post(
            "/add/origin_add_key",
            {"urls": "json://localhost"},
            headers={"origin": "http://["},
        )
        self.assertEqual(response.status_code, 403)


class TrustedOriginsSchemeTests(SimpleTestCase):
    """Match configured origins by scheme, host, and port."""

    def tearDown(self):
        ConfigCache.clear("origin_trusted_key")
        ConfigCache.clear_auth("origin_trusted_key")

    @override_settings(APPRISE_TRUSTED_ORIGINS=["https://testserver"])
    def test_matching_scheme_and_host_is_accepted(self):
        response = self.client.post(
            "/add/origin_trusted_key",
            {"urls": "json://localhost"},
            headers={"origin": "https://testserver"},
        )
        self.assertEqual(response.status_code, 200)

    @override_settings(APPRISE_TRUSTED_ORIGINS=["https://testserver"])
    def test_wrong_scheme_is_rejected(self):
        """A trusted host still requires the configured scheme."""
        response = self.client.post(
            "/add/origin_trusted_key",
            {"urls": "json://localhost"},
            headers={"origin": "http://testserver"},
        )
        self.assertEqual(response.status_code, 403)

    @override_settings(APPRISE_TRUSTED_ORIGINS=["https://apprise.example.com"])
    def test_host_not_in_the_trusted_list_is_rejected(self):
        response = self.client.post(
            "/add/origin_trusted_key",
            {"urls": "json://localhost"},
            headers={"origin": "https://testserver"},
        )
        self.assertEqual(response.status_code, 403)

    @override_settings(
        ALLOWED_HOSTS=["apprise.example.com"],
        APPRISE_TRUSTED_ORIGINS=["https://apprise.example.com"],
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_USER="master",
    )
    def test_custom_host_can_create_browser_login(self):
        """A self-hosted HTTPS name works with the origin safeguard."""
        response = self.client.post(
            "/login",
            data={"username": "master", "password": "pass", "next": "/"},
            headers={
                **_BROWSER,
                "host": "apprise.example.com",
                "origin": "https://apprise.example.com",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.cookies["apprise_web_auth"]["secure"])

        page = self.client.get(
            "/",
            secure=True,
            headers={**_BROWSER, "host": "apprise.example.com"},
        )
        self.assertEqual(page.status_code, 200)

        # The custom host and browser Origin also work for a GUI write.
        saved = self.client.post(
            "/add/origin_trusted_key",
            {"urls": "json://localhost"},
            secure=True,
            headers={
                "accept": "application/json",
                "host": "apprise.example.com",
                "origin": "https://apprise.example.com",
                "X-Apprise-Web-Auth": "1",
            },
        )
        self.assertEqual(saved.status_code, 200)

        # The browser cookie alone is never accepted as API authentication.
        denied_api = self.client.get(
            "/status",
            secure=True,
            headers={"accept": "application/json", "host": "apprise.example.com"},
        )
        self.assertEqual(denied_api.status_code, 401)
        allowed_api = self.client.get(
            "/status",
            secure=True,
            headers={
                "accept": "application/json",
                "authorization": "Basic {}".format(_MASTER_TOKEN),
                "host": "apprise.example.com",
            },
        )
        self.assertEqual(allowed_api.status_code, 200)

    @override_settings(
        ALLOWED_HOSTS=["apprise.example.com"],
        APPRISE_TRUSTED_ORIGINS=["https://apprise.example.com"],
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_USER="master",
    )
    def test_custom_host_rejects_foreign_login_origin(self):
        response = self.client.post(
            "/login",
            data={"username": "master", "password": "pass"},
            secure=True,
            headers={
                **_BROWSER,
                "host": "apprise.example.com",
                "origin": "https://evil.example.com",
            },
        )
        self.assertEqual(response.status_code, 403)
