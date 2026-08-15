# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
# All rights reserved.
#
# This code is licensed under the MIT License.
"""Cover authentication failure paths that protect browser and API access."""

import base64
from json import dumps
from unittest.mock import MagicMock, mock_open, patch

from django.core.cache import cache
from django.core.exceptions import RequestDataTooBig
from django.core.signing import dumps as sign
from django.test import RequestFactory, SimpleTestCase
from django.test.utils import override_settings

from .. import utils, views
from ..utils import (
    AUTH_MODE_MASTER,
    AUTH_MODE_SHARED,
    WEB_AUTH_COOKIE,
    WEB_AUTH_HEADER,
    AppriseAuthStorageError,
    AppriseConfigCache,
    AppriseStoreMode,
    ConfigCache,
)


def _basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


_MASTER_TOKEN = base64.b64encode(b"master:pass").decode()
_MASTER = {"authorization": _basic("master", "pass")}
_BROWSER = {"accept": "text/html"}


class AuthUtilityCoverageTests(SimpleTestCase):
    """Exercise safe failure behavior in the authentication helpers."""

    def setUp(self):
        self.factory = RequestFactory()

    def tearDown(self):
        cache.clear()

    def test_basic_auth_disabled_and_malformed_values(self):
        request = self.factory.get("/")
        self.assertTrue(utils.is_authenticated(request))
        self.assertFalse(utils.global_credentials_ok("user", "pass"))

        with override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN):
            self.assertFalse(utils.global_credentials_ok("bad\udcff", "pass"))
            missing_colon = base64.b64encode(b"username-only").decode()
            request = self.factory.get("/", HTTP_AUTHORIZATION="Basic " + missing_colon)
            self.assertEqual(utils.basic_auth_credentials(request), (None, None))
            binary = base64.b64encode(b"\xff:\xff").decode()
            request = self.factory.get("/", HTTP_AUTHORIZATION="Basic " + binary)
            self.assertEqual(utils.basic_auth_credentials(request), (None, None))

    def test_auth_record_failures_fail_closed(self):
        store = AppriseConfigCache("/tmp/auth-coverage", mode=AppriseStoreMode.SIMPLE)
        with (
            patch("builtins.open", mock_open(read_data='{"version":0,"username":"a","digest":"b"}')),
            self.assertRaises(AppriseAuthStorageError),
        ):
            store.get_auth_record("bad-version")

        with patch.object(store, "get_auth", side_effect=AppriseAuthStorageError("bad")):
            self.assertTrue(store.has_auth("key"))
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
        with patch("tempfile.mkstemp", side_effect=OSError):
            self.assertFalse(store.set_auth("temp", "user", "pass"))
        with (
            patch("tempfile.mkstemp", return_value=(10, "/tmp/auth-write-failure")),
            patch("os.fdopen", side_effect=OSError),
            patch("os.remove"),
        ):
            self.assertFalse(store.set_auth("write", "user", "pass"))

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
        with (
            patch.object(ConfigCache, "has_auth", return_value=True),
            patch.object(ConfigCache, "get_auth_username", side_effect=AppriseAuthStorageError("bad")),
        ):
            self.assertIsNone(utils.config_auth_username("key"))

        request = self.factory.get("/")
        with patch.object(ConfigCache, "get_auth", return_value=None):
            self.assertFalse(utils.key_credentials_ok(request, "key", "user", "pass"))
        with patch.object(ConfigCache, "get_auth", side_effect=AppriseAuthStorageError("bad")):
            self.assertIsNone(utils._web_auth_proof(AUTH_MODE_SHARED, "key"))
        self.assertIsNone(utils._web_auth_proof(AUTH_MODE_SHARED))
        with override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=""):
            self.assertIsNone(utils._web_auth_proof(AUTH_MODE_MASTER))

    def test_auth_failure_cache_recovers_from_expiry(self):
        with (
            patch.object(cache, "add", side_effect=[False, True]) as add,
            patch.object(cache, "incr", side_effect=ValueError),
        ):
            utils._record_auth_failure("127.0.0.1", "key")
        self.assertEqual(add.call_count, 2)

    def test_invalid_signed_payload_is_rejected(self):
        request = self.factory.get("/")
        request.COOKIES[WEB_AUTH_COOKIE] = sign("not-a-dict", salt="apprise-api.web-auth")
        self.assertFalse(utils.restore_web_auth(request))


class AuthMiddlewareCoverageTests(SimpleTestCase):
    """Cover middleware exits used by assets, logout, and browser fetches."""

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_asset_and_api_logout_reach_the_view(self):
        self.assertEqual(self.client.get("/s/missing.css").status_code, 404)
        self.assertEqual(self.client.get("/logout").status_code, 200)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_unsigned_web_fetch_denials_match_format(self):
        response = self.client.get(
            "/status",
            headers={WEB_AUTH_HEADER: "1", "accept": "application/json"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "Access Denied")

        response = self.client.get("/status", headers={WEB_AUTH_HEADER: "1"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["Content-Type"], "text/plain")


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
        request.apprise_auth_permission = AUTH_MODE_MASTER
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
        with override_settings(APPRISE_AUTH_REQUIRED=False, APPRISE_BASIC_AUTH_TOKEN=None):
            self.assertEqual(views.LoginView.as_view()(self._request("post", "/login")).status_code, 302)

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
            (views.NotifyView.as_view(), self._request("post", "/notify/key", **bad), {"key": self.key}),
            (views.JsonUrlView.as_view(), self._request(path="/json/urls/key", **bad), {"key": self.key}),
        )
        for view, request, kwargs in calls:
            self.assertEqual(view(request, **kwargs).status_code, 400)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_auth_get_unavailable_missing_denied_and_legacy(self):
        with override_settings(APPRISE_AUTH_REQUIRED=False, APPRISE_BASIC_AUTH_TOKEN=None):
            response = views.AuthView.as_view()(self._request(path="/auth/key"), key=self.key)
            self.assertEqual(response.status_code, 403)

        response = views.AuthView.as_view()(self._request(path="/auth/"), key="")
        self.assertEqual(response.status_code, 400)

        with patch.object(views, "key_auth_ok", return_value=False):
            response = views.AuthView.as_view()(self._request(path="/auth/key"), key=self.key)
            self.assertEqual(response.status_code, 401)

        request = self._request(path="/auth/key")
        request.apprise_auth_permission = AUTH_MODE_SHARED
        request.apprise_auth_username = "legacy"
        with (
            patch.object(views, "config_auth_mode", return_value=AUTH_MODE_SHARED),
            patch.object(views, "config_auth_username", return_value=None),
            patch.object(views, "key_auth_ok", return_value=True),
        ):
            response = views.AuthView.as_view()(request, key=self.key)
        self.assertContains(response, 'value="legacy"')

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_config_missing_denied_and_legacy_user(self):
        response = views.ConfigView.as_view()(self._request(path="/cfg/"), key="")
        self.assertEqual(response.status_code, 400)

        with patch.object(views, "key_auth_ok", return_value=False):
            response = views.DelView.as_view()(self._request("post", "/del/key"), key=self.key)
        self.assertEqual(response.status_code, 401)

        request = self._request(path="/cfg/key")
        request.apprise_auth_permission = AUTH_MODE_SHARED
        request.apprise_auth_username = "legacy"
        with (
            patch.object(views, "key_auth_ok", return_value=True),
            patch.object(views, "config_auth_mode", return_value=AUTH_MODE_SHARED),
            patch.object(views, "config_auth_username", return_value=None),
        ):
            response = views.ConfigView.as_view()(request, key=self.key)
        self.assertContains(response, "legacy")

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_auth_post_payload_guards_and_legacy_user(self):
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
        request.apprise_auth_permission = AUTH_MODE_SHARED
        request.apprise_auth_username = "legacy"
        with (
            patch.object(ConfigCache, "has_auth", return_value=True),
            patch.object(views, "key_auth_ok", return_value=True),
            patch.object(views, "config_auth_username", return_value=None),
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
        with patch.object(views.json, "loads", side_effect=RequestDataTooBig):
            response = views.AuthView.as_view()(request, key=self.key)
        self.assertEqual(response.status_code, 431)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_delete_cleanup_failures(self):
        request = self._request("post", "/del/key")
        with (
            patch.object(views, "key_auth_ok", return_value=True),
            patch.object(ConfigCache, "clear", return_value=True),
            patch.object(ConfigCache, "clear_auth", return_value=False),
        ):
            self.assertEqual(views.DelView.as_view()(request, key=self.key).status_code, 200)

        request = self._request("delete", "/auth/key")
        with (
            patch.object(views, "key_auth_ok", return_value=True),
            patch.object(ConfigCache, "clear_auth", return_value=False),
        ):
            self.assertEqual(views.AuthView.as_view()(request, key=self.key).status_code, 500)

        request = self._request("delete", "/auth/")
        self.assertEqual(views.AuthView.as_view()(request, key="").status_code, 400)
