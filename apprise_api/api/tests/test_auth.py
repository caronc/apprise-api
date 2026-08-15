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
#
import base64
from json import dumps, loads

from django.test import SimpleTestCase
from django.test.utils import override_settings

from ..utils import ConfigCache


def _basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


class GlobalAuthMiddlewareTests(SimpleTestCase):
    """Test optional global HTTP Basic Auth across API endpoints."""

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=base64.b64encode(b"alice:secret").decode())
    def test_non_keyed_paths_require_global_auth(self):
        """Non-keyed endpoints cannot use per-key credentials."""
        self.assertEqual(self.client.get("/cfg").status_code, 401)
        self.assertEqual(self.client.get("/").status_code, 401)
        self.assertEqual(self.client.get("/details").status_code, 401)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=base64.b64encode(b"alice:secret").decode())
    def test_unlocked_key_requires_global_auth(self):
        """An unlocked key still requires configured global credentials."""
        self.assertEqual(self.client.post("/get/unlocked-key").status_code, 401)

    def test_unset_leaves_every_endpoint_open(self):
        """No global credentials leaves the authentication gate disabled."""
        # Exercise middleware while global auth is disabled.
        response = self.client.get("/status")
        self.assertEqual(response.status_code, 200)

        response = self.client.post("/add/testkey", {"urls": "json://localhost"})
        # The disabled global gate must not return 401.
        self.assertNotEqual(response.status_code, 401)

    def test_auth_headers_are_ignored_while_disabled(self):
        """Web and API requests ignore every Authorization header while disabled."""
        for header in (
            _basic("someone", "something"),
            "Basic not-even-base64!!",
            "Bearer some-jwt-token",
            "",
        ):
            with self.subTest(header=header):
                api_response = self.client.get(
                    "/status",
                    headers={
                        "accept": "application/json",
                        "authorization": header,
                    },
                )
                self.assertEqual(api_response.status_code, 200)

                web_response = self.client.get(
                    "/",
                    headers={
                        "accept": "text/html",
                        "authorization": header,
                    },
                )
                self.assertEqual(web_response.status_code, 200)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=None)
    def test_users_only_mode_denies_keyless_and_unlocked_access(self):
        """Without an administrator, only saved configuration logins work."""
        self.assertEqual(self.client.get("/status").status_code, 401)
        self.assertEqual(self.client.get("/cfg", HTTP_ACCEPT="application/json").status_code, 401)
        self.assertEqual(self.client.post("/get/unlocked-key").status_code, 401)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=None)
    def test_users_only_mode_accepts_and_rotates_saved_login(self):
        """A configuration user remains usable without an administrator."""
        key = "users-only-key"
        self.addCleanup(ConfigCache.clear_auth, key)
        ConfigCache.set_auth(key, "alice", "secret")

        response = self.client.post(
            "/get/{}".format(key),
            headers={"authorization": _basic("alice", "secret")},
        )
        self.assertNotEqual(response.status_code, 401)

        response = self.client.post(
            "/auth/{}".format(key),
            data=dumps(
                {
                    "username": "alice",
                    "password": "new-secret",
                    "password_confirm": "new-secret",
                }
            ),
            content_type="application/json",
            headers={"authorization": _basic("alice", "secret")},
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            "/get/{}".format(key),
            headers={"authorization": _basic("alice", "new-secret")},
        )
        self.assertNotEqual(response.status_code, 401)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=None)
    def test_users_only_mode_cannot_create_first_login(self):
        """Creating a configuration login still requires an administrator."""
        response = self.client.post(
            "/auth/new-users-only-key",
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers={"authorization": _basic("alice", "secret")},
        )
        self.assertEqual(response.status_code, 401)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=base64.b64encode(b"alice:secret").decode())
    def test_missing_header_is_denied(self):
        response = self.client.get("/status")
        self.assertEqual(response.status_code, 401)
        # The challenge tells clients how to retry authentication.
        self.assertEqual(response["WWW-Authenticate"], 'Basic realm="Apprise API"')

    @override_settings(
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=base64.b64encode(b"alice:secret").decode(),
        APPRISE_BASIC_AUTH_REALM="Home Alerts",
    )
    def test_custom_realm_is_used(self):
        """The challenge uses the configured instance label."""
        response = self.client.get("/status")
        self.assertEqual(response["WWW-Authenticate"], 'Basic realm="Home Alerts"')

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=base64.b64encode(b"alice:secret").decode())
    def test_wrong_credentials_are_denied(self):
        response = self.client.get("/status", headers={"authorization": _basic("alice", "wrong")})
        self.assertEqual(response.status_code, 401)

        response = self.client.get("/status", headers={"authorization": _basic("bob", "secret")})
        self.assertEqual(response.status_code, 401)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=base64.b64encode(b"alice:secret").decode())
    def test_malformed_header_is_denied(self):
        response = self.client.get("/status", headers={"authorization": "NotBasic xyz"})
        self.assertEqual(response.status_code, 401)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=base64.b64encode(b"alice:secret").decode())
    def test_correct_credentials_are_accepted(self):
        response = self.client.get("/status", headers={"authorization": _basic("alice", "secret")})
        self.assertEqual(response.status_code, 200)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=base64.b64encode(b"alice:secret").decode())
    def test_denial_matches_response_format(self):
        response = self.client.get("/status")
        self.assertEqual(response.status_code, 401)
        self.assertTrue(response["Content-Type"].startswith("text/plain"))

        response = self.client.get("/status", **{"HTTP_ACCEPT": "application/json"})
        self.assertEqual(response.status_code, 401)
        self.assertTrue(response["Content-Type"].startswith("application/json"))
        content = loads(response.content)
        self.assertIn("error", content)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=base64.b64encode(b"alice:secret").decode())
    def test_global_auth_covers_multiple_endpoints(self):
        """Global auth covers health checks and stateful endpoints."""
        good = {"authorization": _basic("alice", "secret")}

        # Health check: denied without creds, allowed with.
        self.assertEqual(self.client.get("/status").status_code, 401)
        self.assertEqual(self.client.get("/status", headers=good).status_code, 200)

        # Stateful add: denied without creds.
        self.assertEqual(
            self.client.post("/add/testkey", {"urls": "json://localhost"}).status_code,
            401,
        )

        # Stateful get: denied without creds.
        self.assertEqual(self.client.post("/get/testkey").status_code, 401)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=base64.b64encode(b"alice:secret").decode())
    def test_bare_route_without_key_requires_global_auth(self):
        """A bare keyed route needs global auth when no key is supplied."""
        self.assertEqual(self.client.post("/get/").status_code, 401)
        self.assertEqual(self.client.post("/add/", {"urls": "json://localhost"}).status_code, 401)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=base64.b64encode(b"alice:secret").decode())
    def test_header_route_accepts_per_key_auth(self):
        """A header route accepts the key's credentials without global auth."""
        key = "middleware_defer_key"
        self.addCleanup(ConfigCache.clear_auth, key)
        self.addCleanup(ConfigCache.clear, key)
        good_master = {"authorization": _basic("alice", "secret")}
        self.client.post("/add/{}".format(key), {"urls": "json://localhost"}, headers=good_master)
        self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "bob", "password": "s3cret"}),
            content_type="application/json",
            headers=good_master,
        )

        # Use only the key's credentials through the header-based route.
        response = self.client.post(
            "/get/",
            headers={"X-Apprise-Config-ID": key, "authorization": _basic("bob", "s3cret")},
        )
        self.assertEqual(response.status_code, 200)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=base64.b64encode(b"alice:secret").decode())
    def test_cfg_listing_requires_global_auth(self):
        """The admin listing ignores per-key headers and requires global auth."""
        response = self.client.get("/cfg", headers={"X-Apprise-Config-ID": "some-key"})
        self.assertEqual(response.status_code, 401)
