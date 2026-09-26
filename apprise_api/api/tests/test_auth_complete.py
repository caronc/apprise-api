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
"""Cover authentication failure paths that protect browser and API access."""

import base64
from json import dumps
from unittest.mock import MagicMock, mock_open, patch

from api.auth import Authentication as MiddlewareAuthentication
from api.utils import ConfigCache as MiddlewareConfigCache
from core.middleware import auth as auth_middleware
from django.core.exceptions import RequestDataTooBig
from django.core.signing import TimestampSigner, dumps as sign
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase
from django.test.utils import override_settings

from .. import views
from ..auth import Authentication, AuthStorageError, ConfigAuthRecord, ConfigAuthState
from ..forms import AuthForm
from ..utils import (
    AppriseConfigCache,
    AppriseStoreMode,
    ConfigCache,
)

MIDDLEWARE_CREDENTIAL_VERIFIER = MiddlewareAuthentication.credential_verifier


def _basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


_MASTER_TOKEN = base64.b64encode(b"master:pass").decode()
_MASTER = {"authorization": _basic("master", "pass")}
_BROWSER = {"accept": "text/html"}


class AuthUtilityCoverageTests(SimpleTestCase):
    """Exercise safe failure behavior in the authentication helpers."""

    def setUp(self):
        self.factory = RequestFactory()

    def test_basic_auth_disabled_and_malformed_values(self):
        request = self.factory.get("/")
        self.assertTrue(Authentication.is_authenticated(request))
        self.assertFalse(Authentication.global_credentials_ok("user", "pass"))

        with override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN):
            self.assertFalse(Authentication.global_credentials_ok("user", "wrong"))
            self.assertFalse(Authentication.global_credentials_ok("bad\udcff", "pass"))
            missing_colon = base64.b64encode(b"username-only").decode()
            request = self.factory.get("/", HTTP_AUTHORIZATION="Basic " + missing_colon)
            self.assertEqual(Authentication.basic_credentials(request), (None, None))
            binary = base64.b64encode(b"\xff:\xff").decode()
            request = self.factory.get("/", HTTP_AUTHORIZATION="Basic " + binary)
            self.assertEqual(Authentication.basic_credentials(request), (None, None))
            request = self.factory.get("/", HTTP_AUTHORIZATION="Basic dXNlcjpwYXNz!")
            self.assertEqual(Authentication.basic_credentials(request), (None, None))
            request = self.factory.get("/", HTTP_AUTHORIZATION="Basic \N{SNOWMAN}")
            self.assertEqual(Authentication.basic_credentials(request), (None, None))

    def test_shared_auth_form_rejects_changed_username(self):
        """The form keeps the username guard even when used outside the view."""
        form = AuthForm(
            {
                "username": "bob",
                "password": "new-secret",
                "password_confirm": "new-secret",
            },
            shared=True,
            current_username="alice",
        )
        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_auth_record_failures_fail_closed(self):
        store = AppriseConfigCache("/tmp/auth-coverage", mode=AppriseStoreMode.SIMPLE)
        with (
            patch("builtins.open", mock_open(read_data='{"version":0,"username":"a","digest":"b"}')),
            self.assertRaises(AuthStorageError),
        ):
            store.get_auth_record("bad-version")

        with patch.object(store, "get_auth", side_effect=AuthStorageError("bad")):
            self.assertTrue(store.has_auth("key"))

        with patch.object(store, "get_auth_record", side_effect=AuthStorageError("bad")):
            self.assertFalse(store.verify_auth("key", "user", "pass"))

        self.assertFalse(store.verify_auth("missing", "user", "pass"))
        with patch("os.remove", side_effect=OSError(13, "denied")):
            self.assertFalse(store.clear_auth("key"))

    def test_atomic_auth_write_failures(self):
        store = AppriseConfigCache("/tmp/auth-coverage", mode=AppriseStoreMode.SIMPLE)
        disabled = AppriseConfigCache("/tmp/auth-coverage", mode=AppriseStoreMode.DISABLED)
        self.assertFalse(disabled.set_auth("key", "user", "pass"))

        with patch("os.makedirs", side_effect=OSError):
            self.assertFalse(store.set_auth("mkdir", "user", "pass"))
        with (
            patch.object(store, "_acquire_auth_guard", return_value=10),
            patch.object(store, "_release_auth_guard"),
            patch("os.makedirs", side_effect=OSError),
        ):
            # The root guard can succeed while the hashed key directory fails.
            self.assertFalse(store.set_auth("key-dir", "user", "pass"))
        with patch("tempfile.mkstemp", side_effect=OSError):
            self.assertFalse(store.set_auth("temp", "user", "pass"))
        with (
            patch("tempfile.mkstemp", return_value=(10, "/tmp/auth-write-failure")),
            patch("os.fdopen", side_effect=OSError),
            patch("os.remove"),
        ):
            self.assertFalse(store.set_auth("write", "user", "pass"))

        with patch.object(store, "_acquire_auth_guard", side_effect=OSError):
            self.assertFalse(store.clear_auth("guard-failure"))

        with patch("tempfile.mkstemp", side_effect=OSError):
            self.assertFalse(store._exclusive_copy("source", "/tmp/auth-coverage/destination"))
        with (
            patch("tempfile.mkstemp", return_value=(10, None)),
            patch("os.close"),
            patch("shutil.copy2"),
            patch("os.link"),
        ):
            # Keep cleanup safe even if a platform returns no temporary path.
            self.assertTrue(store._exclusive_copy("source", "/tmp/auth-coverage/destination"))

    def test_config_write_temp_and_move_failures(self):
        store = AppriseConfigCache("/tmp/auth-coverage", mode=AppriseStoreMode.SIMPLE)
        with patch("os.makedirs"), patch("tempfile.mkstemp", side_effect=OSError):
            self.assertFalse(store.put("temp", "content", "text"))
        with (
            patch("os.makedirs"),
            patch("tempfile.mkstemp", return_value=(10, "/tmp/config-move-failure")),
            patch("os.close"),
            patch("builtins.open", mock_open()),
            patch("shutil.move", side_effect=OSError),
            patch("os.remove"),
        ):
            self.assertFalse(store.put("move", "content", "text"))

    def test_hash_prune_skips_unreadable_directories(self):
        store = AppriseConfigCache("/tmp/auth-coverage", mode=AppriseStoreMode.HASH)
        with patch("os.path.isdir", return_value=True), patch("os.scandir", side_effect=OSError):
            self.assertEqual(store.prune_unused_locks(0), 0)

        entry = MagicMock(name="hash-directory")
        entry.name = "aa"
        entry.is_dir.side_effect = OSError
        listing = MagicMock()
        listing.__enter__.return_value = [entry]
        with patch("os.path.isdir", return_value=True), patch("os.scandir", return_value=listing):
            self.assertEqual(store.prune_unused_locks(0), 0)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_auth_helper_error_and_empty_paths(self):
        with patch.object(ConfigCache, "get_auth_record", side_effect=AuthStorageError("bad")):
            state = Authentication.config_state("key")
        self.assertEqual(state.mode, Authentication.MODE_ASSIGNED)
        self.assertTrue(state.unreadable)

        request = self.factory.get("/")
        with patch.object(ConfigCache, "get_auth", return_value=None):
            self.assertFalse(Authentication.key_credentials_ok(request, "key", "user", "pass"))
        with patch.object(ConfigCache, "get_auth", side_effect=AuthStorageError("bad")):
            self.assertIsNone(Authentication._web_proof(Authentication.ROLE_USER, "key"))
        self.assertIsNone(Authentication._web_proof(Authentication.ROLE_USER))
        with override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=""):
            self.assertIsNone(Authentication._web_proof(Authentication.ROLE_ADMIN))

    def test_invalid_signed_payload_is_rejected(self):
        request = self.factory.get("/")
        request.COOKIES[Authentication.WEB_COOKIE] = sign(
            "not-a-dict",
            key="apprise-api-pytest-web-auth-secret",
            salt="apprise-api.web-auth",
        )
        self.assertFalse(Authentication.restore_web(request))

        # Compressed and oversized values are rejected before deserialization,
        # even when an old public fallback can produce a valid signature.
        signer = TimestampSigner(
            key="apprise-api-pytest-web-auth-secret",
            salt="apprise-api.web-auth",
        )
        request.COOKIES[Authentication.WEB_COOKIE] = signer.sign(".eA")
        self.assertFalse(Authentication.restore_web(request))
        request.COOKIES[Authentication.WEB_COOKIE] = "x" * 4097
        self.assertFalse(Authentication.restore_web(request))

        request.COOKIES[Authentication.WEB_COOKIE] = sign(
            {"mode": Authentication.ROLE_ADMIN},
            key="apprise-api-pytest-web-auth-secret",
            salt="apprise-api.web-auth",
        )
        self.assertFalse(Authentication.restore_web(request))

    @override_settings(
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_WEB_AUTH_SECRET="browser-secret",
    )
    def test_web_cookie_uses_its_own_secret(self):
        """Rotating the web secret invalidates only the browser login."""
        request = self.factory.get("/")
        response = HttpResponse()
        Authentication.set_web_cookie(response, request, Authentication.ROLE_ADMIN, "master")

        restored = self.factory.get("/")
        restored.COOKIES[Authentication.WEB_COOKIE] = response.cookies[Authentication.WEB_COOKIE].value
        self.assertTrue(Authentication.restore_web(restored))

        rejected = self.factory.get("/")
        rejected.COOKIES[Authentication.WEB_COOKIE] = response.cookies[Authentication.WEB_COOKIE].value
        with override_settings(APPRISE_WEB_AUTH_SECRET="rotated-secret"):
            self.assertFalse(Authentication.restore_web(rejected))


class AuthMiddlewareCoverageTests(SimpleTestCase):
    """Cover middleware exits used by assets, logout, and browser fetches."""

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_asset_and_api_logout_reach_the_view(self):
        self.assertEqual(self.client.get("/s/missing.css").status_code, 404)
        self.assertEqual(self.client.get("/logout").status_code, 302)
        response = self.client.post("/logout")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/login")

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_unsigned_web_fetch_denials_match_format(self):
        response = self.client.get(
            "/status",
            headers={Authentication.WEB_HEADER: "1", "accept": "application/json"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "Access Denied")

        response = self.client.get("/status", headers={Authentication.WEB_HEADER: "1"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["Content-Type"], "text/plain")

    def test_request_config_key_follows_route_and_header_rules(self):
        """Middleware selects the same effective Config ID as each view."""
        factory = RequestFactory()
        request = factory.get("/get/url-key")
        self.assertEqual(auth_middleware._request_config_key(request, "get", "url-key"), "url-key")
        self.assertIsNone(auth_middleware._request_config_key(request, "welcome", "url-key"))
        self.assertIsNone(auth_middleware._request_config_key(request, "get", None))

        request = factory.get("/get/url-key", HTTP_X_APPRISE_CONFIG_ID="header-key")
        self.assertEqual(auth_middleware._request_config_key(request, "get", "url-key"), "header-key")

        request = factory.get("/get/url-key", HTTP_X_APPRISE_CONFIG_ID="bad key")
        self.assertIsNone(auth_middleware._request_config_key(request, "get", "url-key"))

        class GetOnlyView:
            def get(self):
                """Provide a method for the middleware's read-only check."""

        self.assertTrue(auth_middleware._view_authenticates_method(GetOnlyView, "GET"))
        self.assertTrue(auth_middleware._view_authenticates_method(GetOnlyView, "HEAD"))
        self.assertFalse(auth_middleware._view_authenticates_method(GetOnlyView, "POST"))
        self.assertFalse(auth_middleware._view_authenticates_method(GetOnlyView, "OPTIONS"))
        self.assertFalse(auth_middleware._view_authenticates_method(None, "GET"))
        self.assertFalse(auth_middleware._view_authenticates_method(GetOnlyView, None))

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_middleware_and_view_share_one_config_verification(self):
        """A keyed API request performs at most one password hash check."""
        MIDDLEWARE_CREDENTIAL_VERIFIER.clear()
        self.addCleanup(MIDDLEWARE_CREDENTIAL_VERIFIER.clear)
        checker = MagicMock(return_value=True)
        with (
            patch.object(MIDDLEWARE_CREDENTIAL_VERIFIER, "_password_checker", checker),
            patch.object(
                MiddlewareConfigCache,
                "get_auth_record",
                return_value=ConfigAuthRecord("user", "alice", "digest"),
            ),
        ):
            response = self.client.post(
                "/get/auth-complete-key",
                headers={"authorization": _basic("alice", "secret")},
            )
        self.assertNotEqual(response.status_code, 401)
        checker.assert_called_once_with("alice:secret", "digest")

        checker.reset_mock()
        checker.return_value = False
        with (
            patch.object(MIDDLEWARE_CREDENTIAL_VERIFIER, "_password_checker", checker),
            patch.object(
                MiddlewareConfigCache,
                "get_auth_record",
                return_value=ConfigAuthRecord("user", "alice", "digest"),
            ),
        ):
            denied = self.client.post(
                "/get/auth-complete-key",
                headers={"authorization": _basic("alice", "wrong")},
            )
        self.assertEqual(denied.status_code, 401)
        checker.assert_called_once_with("alice:wrong", "digest")

        checker.reset_mock()
        with (
            patch.object(MIDDLEWARE_CREDENTIAL_VERIFIER, "_password_checker", checker),
            patch.object(
                MiddlewareConfigCache,
                "get_auth_record",
                return_value=ConfigAuthRecord("user", "alice", "digest"),
            ),
        ):
            unsupported = self.client.get(
                "/get/auth-complete-key",
                headers={"authorization": _basic("alice", "secret")},
            )
        self.assertEqual(unsupported.status_code, 405)
        checker.assert_not_called()


class AuthViewCoverageTests(SimpleTestCase):
    """Cover guarded view responses for every authentication entry point."""

    key = "auth_complete_key"

    def setUp(self):
        self.factory = RequestFactory()

    def tearDown(self):
        ConfigCache.clear(self.key)
        ConfigCache.clear_auth(self.key)

    def _request(self, method="get", path="/", data=None, **extra):
        request = getattr(self.factory, method)(path, data=data, **extra)
        request.globally_authenticated = True
        request.apprise_auth_permission = Authentication.ROLE_ADMIN
        request.apprise_auth_username = "master"
        return request

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, BASE_URL="/apprise")
    def test_login_key_resolution_and_mode_guards(self):
        request = self._request(path="/login")
        self.assertEqual(views._login_config_key(request, "/apprise/cfg/my-key"), "my-key")
        self.assertEqual(views._login_config_key(request, "/not-a-route"), "")

        with override_settings(APPRISE_API_ONLY=True):
            self.assertEqual(views.LoginView.as_view()(self._request("post", "/login")).status_code, 405)
            self.assertEqual(views.LogoutView.as_view()(request).status_code, 421)
            self.assertEqual(views.LogoutView.as_view()(self._request("post", "/logout")).status_code, 405)
        with override_settings(APPRISE_AUTH_REQUIRED=False, APPRISE_BASIC_AUTH_TOKEN=None):
            self.assertEqual(views.LoginView.as_view()(self._request("post", "/login")).status_code, 302)
            self.assertEqual(views.LogoutView.as_view()(self._request("post", "/logout")).status_code, 302)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_invalid_header_reaches_each_keyed_view(self):
        bad = {"HTTP_X_APPRISE_CONFIG_ID": "bad key"}
        calls = (
            (views.HealthCheckView.as_view(), self._request(path="/status", **bad), {}),
            (views.KeyedHealthCheckView.as_view(), self._request(path="/status/key", **bad), {"key": self.key}),
            (views.ConfigView.as_view(), self._request(path="/cfg/key", **bad), {"key": self.key}),
            (views.DelView.as_view(), self._request("post", "/del/key", **bad), {"key": self.key}),
            (views.AuthView.as_view(), self._request(path="/auth/key", **bad), {"key": self.key}),
            (views.AuthView.as_view(), self._request("delete", "/auth/key", **bad), {"key": self.key}),
            (views.StatefulNotifyView.as_view(), self._request("post", "/notify/key", **bad), {"key": self.key}),
            (views.JsonUrlView.as_view(), self._request(path="/json/urls/key", **bad), {"key": self.key}),
        )
        for view, request, kwargs in calls:
            self.assertEqual(view(request, **kwargs).status_code, 400)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_auth_get_unavailable_missing_denied_and_user(self):
        with override_settings(APPRISE_AUTH_REQUIRED=False, APPRISE_BASIC_AUTH_TOKEN=None):
            response = views.AuthView.as_view()(self._request(path="/auth/key"), key=self.key)
            self.assertEqual(response.status_code, 403)

        response = views.AuthView.as_view()(self._request(path="/auth/"), key="")
        self.assertEqual(response.status_code, 400)

        with patch.object(Authentication, "key_ok", return_value=False):
            response = views.AuthView.as_view()(self._request(path="/auth/key"), key=self.key)
            self.assertEqual(response.status_code, 401)

        request = self._request(path="/auth/key")
        request.apprise_auth_permission = Authentication.ROLE_USER
        request.apprise_auth_username = "legacy"
        with (
            patch.object(
                Authentication,
                "config_state",
                return_value=ConfigAuthState(
                    Authentication.MODE_ASSIGNED,
                    username="legacy",
                    digest="digest",
                ),
            ),
            patch.object(Authentication, "key_ok", return_value=True),
        ):
            response = views.AuthView.as_view()(request, key=self.key)
        self.assertContains(response, 'value="legacy"')

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_config_missing_denied_and_user_display(self):
        response = views.ConfigView.as_view()(self._request(path="/cfg/"), key="")
        self.assertEqual(response.status_code, 400)

        with patch.object(Authentication, "key_ok", return_value=False):
            response = views.DelView.as_view()(self._request("post", "/del/key"), key=self.key)
        self.assertEqual(response.status_code, 401)

        request = self._request(path="/cfg/key")
        request.apprise_auth_permission = Authentication.ROLE_USER
        request.apprise_auth_username = "alice"
        with (
            patch.object(Authentication, "key_ok", return_value=True),
            patch.object(
                Authentication,
                "config_state",
                return_value=ConfigAuthState(
                    Authentication.MODE_ASSIGNED,
                    username="alice",
                    digest="digest",
                ),
            ),
        ):
            response = views.ConfigView.as_view()(request, key=self.key)
        self.assertContains(response, "alice")

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_auth_post_payload_guards_and_user(self):
        base = self._request(
            "post",
            "/auth/key",
            data=dumps([]),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(views.AuthView.as_view()(base, key=self.key).status_code, 400)

        request = self._request("post", "/auth/", data=dumps({}), content_type="application/json")
        self.assertEqual(views.AuthView.as_view()(request, key="").status_code, 400)

        request = self._request("post", "/auth/key", data=dumps({}), content_type="application/json")
        request.apprise_auth_permission = Authentication.ROLE_USER
        request.apprise_auth_username = "alice"
        with (
            patch.object(Authentication, "key_ok", return_value=True),
            patch.object(
                Authentication,
                "config_state",
                return_value=ConfigAuthState(
                    Authentication.MODE_ASSIGNED,
                    username="alice",
                    digest="digest",
                ),
            ),
        ):
            response = views.AuthView.as_view()(request, key=self.key)
        self.assertEqual(response.status_code, 400)

        bad_header = self._request(
            "post",
            "/auth/key",
            data=dumps({}),
            content_type="application/json",
            HTTP_X_APPRISE_CONFIG_ID="bad key",
        )
        self.assertEqual(views.AuthView.as_view()(bad_header, key=self.key).status_code, 400)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_auth_post_large_body(self):
        request = self._request("post", "/auth/key", data=b"{}", content_type="application/json")
        with patch("api.responses.json.loads", side_effect=RequestDataTooBig):
            response = views.AuthView.as_view()(request, key=self.key)
        self.assertEqual(response.status_code, 431)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_auth_post_rejects_deep_json(self):
        """Excessively nested JSON is rejected as an invalid payload."""
        request = self._request("post", "/auth/key", data=b"{}", content_type="application/json")
        with patch("api.responses.json.loads", side_effect=RecursionError):
            response = views.AuthView.as_view()(request, key=self.key)
        self.assertEqual(response.status_code, 400)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_delete_cleanup_failures(self):
        request = self._request("post", "/del/key")
        with (
            patch.object(Authentication, "key_ok", return_value=True),
            patch.object(ConfigCache, "clear", return_value=True),
            patch.object(ConfigCache, "clear_auth", return_value=False),
        ):
            self.assertEqual(views.DelView.as_view()(request, key=self.key).status_code, 500)

        request = self._request("delete", "/auth/key")
        with (
            patch.object(Authentication, "key_ok", return_value=True),
            patch.object(ConfigCache, "clear_auth", return_value=False),
        ):
            self.assertEqual(views.AuthView.as_view()(request, key=self.key).status_code, 500)

        request = self._request("delete", "/auth/")
        self.assertEqual(views.AuthView.as_view()(request, key="").status_code, 400)
