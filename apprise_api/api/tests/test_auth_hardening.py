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
"""Regression tests for per-key authentication security."""

import base64
from contextlib import suppress
from json import dumps
import os
import stat
import unittest
from unittest import mock

import apprise
from django.contrib.auth.hashers import check_password
from django.core.cache import cache
from django.test import SimpleTestCase
from django.test.utils import override_settings

from ..utils import ConfigCache


def _basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


# First-time locks require global administrator credentials.
# Per-test overrides keep each test's remaining auth state independent.
_MASTER_TOKEN = base64.b64encode(b"master:pass").decode()
_GOOD_MASTER = {"authorization": _basic("master", "pass")}


class AuthStorageHardeningTests(SimpleTestCase):
    """The lock file itself: how it's hashed, written, and read back."""

    def tearDown(self):
        for key in (
            "hardening_unreadable_key",
            "hardening_salted_a",
            "hardening_salted_b",
            "hardening_mode_key",
            "hardening_surrogate_key",
            "hardening_corrupt_lock_key",
        ):
            path, filename = ConfigCache.auth_path(key)
            full_path = os.path.join(path, filename)
            with suppress(OSError):
                os.chmod(full_path, 0o600)
            ConfigCache.clear(key)
            ConfigCache.clear_auth(key)

    @unittest.skipIf(os.getuid() == 0, "root bypasses file permissions; can't simulate a real read failure")
    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_unreadable_lock_fails_closed(self):
        """A lock file that can't be read must never look "unprotected"."""
        key = "hardening_unreadable_key"
        self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        self.assertTrue(ConfigCache.has_auth(key))

        path, filename = ConfigCache.auth_path(key)
        full_path = os.path.join(path, filename)
        os.chmod(full_path, 0o000)
        try:
            # No credentials at all -- if the read failure were treated as
            # "no lock", this would succeed with a 200.
            response = self.client.post("/get/{}".format(key))
            self.assertEqual(response.status_code, 401)

            # Even the right credentials can't help while the digest can't
            # be read back to compare against.
            response = self.client.post(
                "/get/{}".format(key),
                headers={"authorization": _basic("alice", "secret")},
            )
            self.assertEqual(response.status_code, 401)
        finally:
            os.chmod(full_path, 0o600)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_corrupt_lock_fails_closed(self):
        """Invalid lock-file text denies access without returning an error."""
        key = "hardening_corrupt_lock_key"
        self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        self.assertTrue(ConfigCache.has_auth(key))

        path, filename = ConfigCache.auth_path(key)
        full_path = os.path.join(path, filename)
        with open(full_path, "wb") as f:
            f.write(b"\xff\xfe not valid utf-8")

        # A corrupt lock must not look unlocked or cause an internal error.
        response = self.client.post("/get/{}".format(key))
        self.assertEqual(response.status_code, 401)

        # Credentials cannot be checked until the stored digest is readable.
        response = self.client.post(
            "/get/{}".format(key),
            headers={"authorization": _basic("alice", "secret")},
        )
        self.assertEqual(response.status_code, 401)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_credentials_use_salted_hashes(self):
        """Identical credentials on two different keys must not hash identically."""
        key_a, key_b = "hardening_salted_a", "hardening_salted_b"
        for key in (key_a, key_b):
            self.client.post(
                "/auth/{}".format(key),
                data=dumps({"username": "alice", "password": "secret"}),
                content_type="application/json",
                headers=_GOOD_MASTER,
            )

        digest_a = ConfigCache.get_auth(key_a)
        digest_b = ConfigCache.get_auth(key_b)
        self.assertIsNotNone(digest_a)
        self.assertIsNotNone(digest_b)
        self.assertNotEqual(digest_a, digest_b)
        # Django's encoded hash includes its algorithm and settings.
        self.assertTrue(digest_a.startswith("pbkdf2_"))

        # Both still verify correctly despite the different stored digests.
        self.assertTrue(ConfigCache.verify_auth(key_a, "alice", "secret"))
        self.assertTrue(ConfigCache.verify_auth(key_b, "alice", "secret"))

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_lock_file_is_written_with_restrictive_permissions(self):
        key = "hardening_mode_key"
        self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        path, filename = ConfigCache.auth_path(key)
        full_path = os.path.join(path, filename)
        mode = stat.S_IMODE(os.stat(full_path).st_mode)
        self.assertEqual(mode, 0o600)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_bad_unicode_preserves_lock(self):
        """Invalid Unicode must not replace an existing lock."""
        key = "hardening_surrogate_key"
        self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        self.assertTrue(ConfigCache.verify_auth(key, "alice", "secret"))

        response = self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "\ud800", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        self.assertEqual(response.status_code, 500)

        # The original lock remains usable after the failed update.
        self.assertTrue(ConfigCache.verify_auth(key, "alice", "secret"))

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_bad_unicode_leaves_no_new_lock(self):
        key = "hardening_surrogate_key"
        self.assertFalse(ConfigCache.has_auth(key))

        response = self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "\ud800", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        self.assertEqual(response.status_code, 500)
        self.assertFalse(ConfigCache.has_auth(key))


class DelViewAuthOrderingTests(SimpleTestCase):
    """Keep auth locks when configuration deletion fails."""

    def tearDown(self):
        for key in ("hardening_del_fail_key",):
            ConfigCache.clear(key)
            ConfigCache.clear_auth(key)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_failed_delete_keeps_auth_lock(self):
        key = "hardening_del_fail_key"
        self.client.post("/add/{}".format(key), {"urls": "json://localhost"}, headers=_GOOD_MASTER)
        self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        self.assertTrue(ConfigCache.has_auth(key))

        with mock.patch("api.utils.ConfigCache.clear", return_value=False):
            response = self.client.post(
                "/del/{}".format(key),
                headers={"authorization": _basic("alice", "secret")},
            )
        self.assertEqual(response.status_code, 500)
        # The configuration deletion failed, so its auth lock must survive.
        self.assertTrue(ConfigCache.verify_auth(key, "alice", "secret"))

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_successful_delete_removes_auth_lock(self):
        key = "hardening_del_fail_key"
        self.client.post("/add/{}".format(key), {"urls": "json://localhost"}, headers=_GOOD_MASTER)
        self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )

        response = self.client.post(
            "/del/{}".format(key),
            headers={"authorization": _basic("alice", "secret")},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ConfigCache.has_auth(key))


class AuthViewConfigLockTests(SimpleTestCase):
    """Manage key authentication independently of configuration writes."""

    def tearDown(self):
        for key in ("hardening_lock_seed_key",):
            ConfigCache.clear(key)
            ConfigCache.clear_auth(key)

    @override_settings(APPRISE_CONFIG_LOCK=True, APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_config_lock_does_not_block_setting_auth(self):
        key = "hardening_lock_seed_key"

        response = self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ConfigCache.verify_auth(key, "alice", "secret"))


class RouteAuthConsistencyTests(SimpleTestCase):
    """Apply key authentication consistently across protected routes."""

    def tearDown(self):
        for key in ("hardening_cfg_page_key", "hardening_header_key", "hardening_url_key"):
            ConfigCache.clear(key)
            ConfigCache.clear_auth(key)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_config_editor_enforces_key_auth(self):
        key = "hardening_cfg_page_key"
        self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )

        denied = self.client.get("/cfg/{}".format(key))
        self.assertEqual(denied.status_code, 401)

        allowed = self.client.get(
            "/cfg/{}".format(key),
            headers={"authorization": _basic("alice", "secret")},
        )
        self.assertEqual(allowed.status_code, 200)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_status_header_precedes_url_key(self):
        url_key, header_key = "hardening_url_key", "hardening_header_key"
        self.client.post(
            "/auth/{}".format(header_key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )

        # The protected header key controls access instead of the URL key.
        response = self.client.get(
            "/status/{}".format(url_key),
            headers={"X-Apprise-Config-ID": header_key},
        )
        self.assertEqual(response.status_code, 401)

        response = self.client.get(
            "/status/{}".format(url_key),
            headers={"X-Apprise-Config-ID": header_key, "authorization": _basic("alice", "secret")},
        )
        self.assertEqual(response.status_code, 200)

    @staticmethod
    def _asset_spy_class():
        """AppriseAsset that ignores kwargs the installed apprise doesn't support yet."""
        real_asset_cls = apprise.AppriseAsset

        class SpyAsset(real_asset_cls):
            def __init__(self, **kwargs):
                safe_kwargs = {k: v for k, v in kwargs.items() if hasattr(real_asset_cls, k)}
                super().__init__(**safe_kwargs)

        return SpyAsset

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    @mock.patch("apprise.Apprise.notify")
    def test_notify_header_precedes_url_key(self, mock_notify):
        url_key, header_key = "hardening_url_key", "hardening_header_key"
        self.client.post("/add/{}".format(url_key), {"urls": "json://url-target"}, headers=_GOOD_MASTER)
        self.client.post("/add/{}".format(header_key), {"urls": "json://header-target"}, headers=_GOOD_MASTER)
        self.client.post(
            "/auth/{}".format(header_key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )

        # The header key is locked, so its credentials control access.
        response = self.client.post(
            "/notify/{}".format(url_key),
            {"body": "hello"},
            headers={"X-Apprise-Config-ID": header_key},
        )
        self.assertEqual(response.status_code, 401)
        mock_notify.assert_not_called()

        with mock.patch("apprise.AppriseAsset", self._asset_spy_class()):
            response = self.client.post(
                "/notify/{}".format(url_key),
                {"body": "hello"},
                headers={"X-Apprise-Config-ID": header_key, "authorization": _basic("alice", "secret")},
            )
        self.assertEqual(response.status_code, 200)
        mock_notify.assert_called_once()


class BaseUrlKeyedRouteTests(SimpleTestCase):
    """Test keyed routes when Apprise API uses a base URL."""

    def tearDown(self):
        ConfigCache.clear("hardening_base_url_key")
        ConfigCache.clear_auth("hardening_base_url_key")

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, FORCE_SCRIPT_NAME="/apprise")
    def test_per_key_auth_works_with_base_url(self):
        key = "hardening_base_url_key"
        self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )

        response = self.client.get(
            "/status/{}".format(key),
            headers={"authorization": _basic("alice", "secret")},
        )
        self.assertEqual(response.status_code, 200)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, FORCE_SCRIPT_NAME="/apprise")
    def test_header_auth_works_with_base_url(self):
        key = "hardening_base_url_key"
        self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )

        response = self.client.get(
            "/status/",
            headers={"X-Apprise-Config-ID": key, "authorization": _basic("alice", "secret")},
        )
        self.assertEqual(response.status_code, 200)


class KeyAuthRateLimitTests(SimpleTestCase):
    """Limit costly failed authentication checks across keyed routes."""

    def setUp(self):
        cache.clear()

    def tearDown(self):
        ConfigCache.clear_auth("hardening_throttle_key")
        ConfigCache.clear_auth("hardening_throttle_other_key")
        cache.clear()

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_wrong_passwords_throttle_without_rehashing(self):
        key = "hardening_throttle_key"
        self.assertTrue(ConfigCache.set_auth(key, "alice", "secret"))

        with (
            mock.patch("api.utils._AUTH_FAILURE_MAX_ATTEMPTS", 3),
            mock.patch("api.utils.check_password", wraps=check_password) as spy_check_password,
        ):
            for _ in range(3):
                response = self.client.get(
                    "/status/{}".format(key),
                    headers={"authorization": _basic("alice", "wrong")},
                )
                self.assertEqual(response.status_code, 401)
            self.assertEqual(spy_check_password.call_count, 3)

            # Further attempts return 429 without running the hasher again.
            response = self.client.get(
                "/status/{}".format(key),
                headers={"authorization": _basic("alice", "secret")},
            )
            self.assertEqual(response.status_code, 429)
            self.assertIn("Retry-After", response)
            self.assertEqual(spy_check_password.call_count, 3)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_success_does_not_count_toward_throttle(self):
        key = "hardening_throttle_key"
        self.assertTrue(ConfigCache.set_auth(key, "alice", "secret"))

        with mock.patch("api.utils._AUTH_FAILURE_MAX_ATTEMPTS", 1):
            wrong = self.client.get(
                "/status/{}".format(key),
                headers={"authorization": _basic("alice", "wrong")},
            )
            self.assertEqual(wrong.status_code, 401)

            # The same client can still access a different protected key.
            other_key = "hardening_throttle_other_key"
            self.assertTrue(ConfigCache.set_auth(other_key, "bob", "other-secret"))
            other = self.client.get(
                "/status/{}".format(other_key),
                headers={"authorization": _basic("bob", "other-secret")},
            )
            self.assertEqual(other.status_code, 200)

    def test_unprotected_keys_never_engage_the_throttle(self):
        # An unlocked key does not use the failure counter.
        response = self.client.get("/status/hardening_throttle_unlocked_key")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(cache.get("apprise-auth-fail:127.0.0.1:hardening_throttle_unlocked_key"), None)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_x_real_ip_used_for_throttling(self):
        """Nginx client addresses receive independent throttle counters."""
        key = "hardening_throttle_key"
        self.assertTrue(ConfigCache.set_auth(key, "alice", "secret"))

        with mock.patch("api.utils._AUTH_FAILURE_MAX_ATTEMPTS", 1):
            first = self.client.get(
                "/status/{}".format(key),
                headers={"authorization": _basic("alice", "wrong"), "x-real-ip": "203.0.113.10"},
            )
            self.assertEqual(first.status_code, 401)

            # A different client is not affected by the first failure.
            second = self.client.get(
                "/status/{}".format(key),
                headers={"authorization": _basic("alice", "secret"), "x-real-ip": "203.0.113.20"},
            )
            self.assertEqual(second.status_code, 200)

            # The original client remains throttled with valid credentials.
            third = self.client.get(
                "/status/{}".format(key),
                headers={"authorization": _basic("alice", "secret"), "x-real-ip": "203.0.113.10"},
            )
            self.assertEqual(third.status_code, 429)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_correct_attempts_use_cache(self):
        """Repeated valid credentials use the short-lived success cache."""
        key = "hardening_throttle_key"
        self.assertTrue(ConfigCache.set_auth(key, "alice", "secret"))

        with mock.patch("api.utils.check_password", wraps=check_password) as spy_check_password:
            for _ in range(3):
                response = self.client.get(
                    "/status/{}".format(key),
                    headers={"authorization": _basic("alice", "secret")},
                )
                self.assertEqual(response.status_code, 200)

            self.assertEqual(spy_check_password.call_count, 1)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_success_cache_rejects_other_password(self):
        """A cached success applies only to the exact credentials checked."""
        key = "hardening_throttle_key"
        self.assertTrue(ConfigCache.set_auth(key, "alice", "secret"))

        good = self.client.get(
            "/status/{}".format(key),
            headers={"authorization": _basic("alice", "secret")},
        )
        self.assertEqual(good.status_code, 200)

        bad = self.client.get(
            "/status/{}".format(key),
            headers={"authorization": _basic("alice", "wrong")},
        )
        self.assertEqual(bad.status_code, 401)


class AuthViewCsrfTests(SimpleTestCase):
    """Accept JSON auth changes while rejecting browser form submissions."""

    def tearDown(self):
        for key in ("hardening_csrf_key",):
            ConfigCache.clear(key)
            ConfigCache.clear_auth(key)

    def test_form_encoded_body_is_rejected(self):
        key = "hardening_csrf_key"
        response = self.client.post(
            "/auth/{}".format(key),
            {"username": "alice", "password": "secret"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ConfigCache.has_auth(key))

    def test_text_plain_body_is_rejected(self):
        key = "hardening_csrf_key"
        response = self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="text/plain",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ConfigCache.has_auth(key))

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_application_json_is_accepted(self):
        key = "hardening_csrf_key"
        response = self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ConfigCache.has_auth(key))


class BasicAuthSchemeCaseTests(SimpleTestCase):
    """RFC 7235: the "Basic" auth-scheme token is case-insensitive."""

    def tearDown(self):
        for key in ("hardening_case_key",):
            ConfigCache.clear(key)
            ConfigCache.clear_auth(key)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=base64.b64encode(b"master:pass").decode())
    def test_global_auth_accepts_lowercase_scheme(self):
        token = base64.b64encode(b"master:pass").decode()
        response = self.client.get("/status", headers={"authorization": "basic " + token})
        self.assertEqual(response.status_code, 200)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=base64.b64encode(b"master:pass").decode())
    def test_global_auth_accepts_mixed_case_scheme(self):
        token = base64.b64encode(b"master:pass").decode()
        response = self.client.get("/status", headers={"authorization": "BaSiC " + token})
        self.assertEqual(response.status_code, 200)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_per_key_auth_accepts_lowercase_scheme(self):
        key = "hardening_case_key"
        self.client.post("/add/{}".format(key), {"urls": "json://localhost"}, headers=_GOOD_MASTER)
        self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        token = base64.b64encode(b"alice:secret").decode()
        response = self.client.post("/get/{}".format(key), headers={"authorization": "basic " + token})
        self.assertEqual(response.status_code, 200)
