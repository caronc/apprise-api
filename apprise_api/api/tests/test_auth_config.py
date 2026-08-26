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

from django.test import SimpleTestCase
from django.test.utils import override_settings

from ..utils import ConfigCache


def _basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


_MASTER_TOKEN = base64.b64encode(b"master:pass").decode()
_GOOD_MASTER = {"authorization": _basic("master", "pass")}


class AuthViewTests(SimpleTestCase):
    """Test Basic Auth management and enforcement for configuration keys."""

    def tearDown(self):
        # These tests write real files under APPRISE_CONFIG_DIR; clean up
        # after each so keys never leak state into a later test.
        for key in (
            "auth_new_key",
            "auth_existing_key",
            "auth_wrong_creds",
            "auth_delete_key",
            "auth_del_sweep_key",
            "auth_format_switch_key",
            "auth_status_key",
            "auth_master_key",
            "auth_missing_fields_key",
            "auth_no_master_existing_key",
            "auth_locked_config_key",
            "auth_colon_username_key",
            "auth_shared_rules_key",
            "auth_password_only_key",
            "auth_long_credentials_key",
        ):
            ConfigCache.clear(key)
            ConfigCache.clear_auth(key)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_first_lock_requires_master(self):
        """A key's first lock requires global administrator credentials."""
        key = "auth_new_key"
        response = self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertFalse(ConfigCache.has_auth(key))

    def test_auth_requires_configured_master(self):
        """Per-key authentication is unavailable without a global administrator."""
        key = "auth_new_key"
        response = self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ConfigCache.has_auth(key))

    def test_replace_requires_configured_master(self):
        """Existing key credentials cannot replace a lock without an administrator."""
        key = "auth_no_master_existing_key"
        with override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN):
            self.client.post(
                "/auth/{}".format(key),
                data=dumps({"username": "alice", "password": "secret"}),
                content_type="application/json",
                headers=_GOOD_MASTER,
            )
        self.assertTrue(ConfigCache.verify_auth(key, "alice", "secret"))

        # Key credentials cannot manage the lock after global auth is disabled.
        good_key_creds = {"authorization": _basic("alice", "secret")}
        post_response = self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "new-secret"}),
            content_type="application/json",
            headers=good_key_creds,
        )
        self.assertEqual(post_response.status_code, 403)
        self.assertTrue(ConfigCache.verify_auth(key, "alice", "secret"))

        delete_response = self.client.delete("/auth/{}".format(key), headers=good_key_creds)
        self.assertEqual(delete_response.status_code, 403)
        self.assertTrue(ConfigCache.has_auth(key))

    def test_unavailable_auth_returns_json(self):
        response = self.client.post(
            "/auth/auth_no_master_existing_key",
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers={"accept": "application/json"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIsNotNone(loads(response.content)["error"])

    @override_settings(APPRISE_CONFIG_LOCK=True, APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_auth_works_with_config_lock(self):
        """The configuration lock does not block per-key auth management."""
        key = "auth_locked_config_key"
        response = self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ConfigCache.verify_auth(key, "alice", "secret"))

        # Replacing it while still locked also works.
        response = self.client.post(
            "/auth/{}".format(key),
            data=dumps(
                {
                    "username": "alice",
                    "password": "rotated",
                    "password_confirm": "rotated",
                }
            ),
            content_type="application/json",
            headers={"authorization": _basic("alice", "secret")},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ConfigCache.verify_auth(key, "alice", "rotated"))

        # A configuration user cannot remove their own account.
        response = self.client.delete(
            "/auth/{}".format(key),
            headers={"authorization": _basic("alice", "rotated")},
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(ConfigCache.has_auth(key))

        # The global administrator can remove it.
        response = self.client.delete("/auth/{}".format(key), headers=_GOOD_MASTER)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ConfigCache.has_auth(key))

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_auth_does_not_create_config(self):
        """Adding a key lock does not create configuration content."""
        key = "auth_new_key"
        self.assertIsNone(ConfigCache.get(key)[0])

        response = self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ConfigCache.has_auth(key))
        self.assertTrue(ConfigCache.verify_auth(key, "alice", "secret"))
        self.assertFalse(ConfigCache.verify_auth(key, "alice", "wrong"))

        self.assertIsNone(ConfigCache.get(key)[0])

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_post_preserves_existing_config(self):
        key = "auth_existing_key"
        self.client.post("/add/{}".format(key), {"urls": "json://localhost"}, headers=_GOOD_MASTER)
        config_before, fmt_before = ConfigCache.get(key)
        self.assertIsNotNone(config_before)

        response = self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        self.assertEqual(response.status_code, 200)

        config_after, fmt_after = ConfigCache.get(key)
        self.assertEqual(config_before, config_after)
        self.assertEqual(fmt_before, fmt_after)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_post_rejects_missing_fields(self):
        key = "auth_missing_fields_key"
        response = self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ConfigCache.has_auth(key))

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_colon_username_rejected(self):
        """Reject usernames that conflict with Basic Auth's colon separator."""
        key = "auth_colon_username_key"
        response = self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "ali:ce", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ConfigCache.has_auth(key))

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_blank_credentials_rejected(self):
        """A blank username and password provide no protection."""
        key = "auth_colon_username_key"
        response = self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "", "password": ""}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ConfigCache.has_auth(key))

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_one_blank_credential_allowed(self):
        """Either credential may be blank when the other provides protection."""
        key = "auth_colon_username_key"
        response = self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ConfigCache.has_auth(key))

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_post_rejects_invalid_json(self):
        key = "auth_missing_fields_key"
        response = self.client.post(
            "/auth/{}".format(key),
            data="not json",
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        self.assertEqual(response.status_code, 400)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_changes_require_existing_credentials(self):
        key = "auth_wrong_creds"
        self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )

        # Wrong credentials can't replace it.
        response = self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "wrong"}),
            content_type="application/json",
            headers={"authorization": _basic("alice", "wrong")},
        )
        self.assertEqual(response.status_code, 401)
        self.assertTrue(ConfigCache.verify_auth(key, "alice", "secret"))

        # Wrong credentials can't delete it either.
        response = self.client.delete("/auth/{}".format(key), headers={"authorization": _basic("bob", "nope")})
        self.assertEqual(response.status_code, 401)
        self.assertTrue(ConfigCache.has_auth(key))

        # The correct, existing credentials can replace it.
        response = self.client.post(
            "/auth/{}".format(key),
            data=dumps(
                {
                    "username": "alice",
                    "password": "newpass",
                    "password_confirm": "newpass",
                }
            ),
            content_type="application/json",
            headers={"authorization": _basic("alice", "secret")},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ConfigCache.verify_auth(key, "alice", "newpass"))

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_shared_user_can_only_change_password(self):
        """A configuration user may change only the password."""
        key = "auth_shared_rules_key"
        ConfigCache.set_auth(key, "alice", "secret")
        headers = {"authorization": _basic("alice", "secret")}

        unchanged_password = self.client.post(
            "/auth/{}".format(key),
            data=dumps(
                {
                    "username": "alice",
                    "password": "secret",
                    "password_confirm": "secret",
                }
            ),
            content_type="application/json",
            headers=headers,
        )
        self.assertEqual(unchanged_password.status_code, 400)
        self.assertEqual(unchanged_password.json()["field"], "password")

        changed_user = self.client.post(
            "/auth/{}".format(key),
            data=dumps(
                {
                    "username": "bob",
                    "password": "new-secret",
                    "password_confirm": "new-secret",
                }
            ),
            content_type="application/json",
            headers=headers,
        )
        self.assertEqual(changed_user.status_code, 403)
        self.assertEqual(changed_user.json()["field"], "username")
        self.assertTrue(ConfigCache.verify_auth(key, "alice", "secret"))

        mismatch = self.client.post(
            "/auth/{}".format(key),
            data=dumps(
                {
                    "username": "alice",
                    "password": "new-secret",
                    "password_confirm": "different",
                }
            ),
            content_type="application/json",
            headers=headers,
        )
        self.assertEqual(mismatch.status_code, 400)
        self.assertTrue(ConfigCache.verify_auth(key, "alice", "secret"))

        same_username = self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "new-secret"}),
            content_type="application/json",
            headers=headers,
        )
        self.assertEqual(same_username.status_code, 200)
        self.assertTrue(ConfigCache.verify_auth(key, "alice", "new-secret"))

        missing_username = self.client.post(
            "/auth/{}".format(key),
            data=dumps({"password": "newer-secret"}),
            content_type="application/json",
            headers={"authorization": _basic("alice", "new-secret")},
        )
        self.assertEqual(missing_username.status_code, 400)
        self.assertTrue(ConfigCache.verify_auth(key, "alice", "new-secret"))

        changed_both = self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "bob", "password": "admin-secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        self.assertEqual(changed_both.status_code, 200)
        self.assertTrue(ConfigCache.verify_auth(key, "bob", "admin-secret"))

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_password_only_user_repeats_blank_username(self):
        """A password-only account sends its intentionally blank username."""
        key = "auth_password_only_key"
        ConfigCache.set_auth(key, "", "secret")
        response = self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "", "password": "new-secret"}),
            content_type="application/json",
            headers={"authorization": _basic("", "secret")},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(ConfigCache.verify_auth(key, "", "new-secret"))

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_password_only_lock_accepts_whitespace_username(self):
        """Treat missing, blank, and whitespace usernames alike."""
        key = "auth_password_only_whitespace_key"
        ConfigCache.set_auth(key, "", "secret")

        response = self.client.get(
            "/status",
            headers={"authorization": _basic(" ", "secret"), "X-Apprise-Config-ID": key},
        )

        self.assertEqual(response.status_code, 200)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_credential_lengths_are_limited(self):
        """Reject credentials that are too long for the login form."""
        key = "auth_long_credentials_key"
        response = self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "a" * 256, "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(ConfigCache.has_auth(key))

        response = self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "s" * 256}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ConfigCache.has_auth(key))

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_delete_preserves_config(self):
        key = "auth_delete_key"
        self.client.post("/add/{}".format(key), {"urls": "json://localhost"}, headers=_GOOD_MASTER)
        self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        self.assertTrue(ConfigCache.has_auth(key))

        response = self.client.delete("/auth/{}".format(key), headers=_GOOD_MASTER)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ConfigCache.has_auth(key))
        # The configuration itself is untouched.
        self.assertIsNotNone(ConfigCache.get(key)[0])

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_delete_config_removes_auth(self):
        key = "auth_del_sweep_key"
        self.client.post("/add/{}".format(key), {"urls": "json://localhost"}, headers=_GOOD_MASTER)
        self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        self.assertTrue(ConfigCache.has_auth(key))

        # Deleting is a global-administrator-only action.
        response = self.client.post(
            "/del/{}".format(key),
            headers=_GOOD_MASTER,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ConfigCache.has_auth(key))
        self.assertIsNone(ConfigCache.get(key)[0])

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_format_switch_preserves_auth(self):
        key = "auth_format_switch_key"
        self.client.post(
            "/add/{}".format(key),
            {"urls": "json://localhost", "format": "text"},
            headers=_GOOD_MASTER,
        )
        self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        self.assertTrue(ConfigCache.has_auth(key))

        # Writing is a global-administrator-only action, even to switch
        # this key's own format YAML<->TEXT.
        response = self.client.post(
            "/add/{}".format(key),
            {"urls": "json://localhost", "format": "yaml"},
            headers=_GOOD_MASTER,
        )
        self.assertEqual(response.status_code, 200)

        # The lock survived the format switch and is still enforced.
        self.assertTrue(ConfigCache.verify_auth(key, "alice", "secret"))
        response = self.client.post("/get/{}".format(key))
        self.assertEqual(response.status_code, 401)
        response = self.client.post(
            "/get/{}".format(key),
            headers={"authorization": _basic("alice", "secret")},
        )
        self.assertEqual(response.status_code, 200)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_keyed_endpoints_enforce_auth(self):
        key = "auth_status_key"
        self.client.post("/add/{}".format(key), {"urls": "json://localhost"}, headers=_GOOD_MASTER)
        self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        good = {"authorization": _basic("alice", "secret")}

        self.assertEqual(self.client.post("/get/{}".format(key)).status_code, 401)
        self.assertEqual(self.client.post("/get/{}".format(key), headers=good).status_code, 200)

        self.assertEqual(self.client.get("/json/urls/{}".format(key)).status_code, 401)
        self.assertEqual(self.client.get("/json/urls/{}".format(key), headers=good).status_code, 200)

        with override_settings(APPRISE_CONFIG_LOCK=True):
            # Authentication is checked first. The lock hides the listing
            # from configuration users but not the global administrator.
            self.assertEqual(self.client.get("/json/urls/{}".format(key)).status_code, 401)
            self.assertEqual(self.client.get("/json/urls/{}".format(key), headers=good).status_code, 403)
            self.assertEqual(
                self.client.get("/json/urls/{}".format(key), headers=_GOOD_MASTER).status_code,
                200,
            )

        self.assertEqual(
            self.client.post("/add/{}".format(key), {"urls": "json://localhost"}).status_code,
            401,
        )
        # A valid per-key credential authenticates but still isn't enough to
        # write -- that's a global-administrator-only action.
        self.assertEqual(
            self.client.post("/add/{}".format(key), {"urls": "json://localhost"}, headers=good).status_code,
            403,
        )

    def test_keyed_status_matches_status_shape(self):
        key = "auth_status_key"
        response_keyless = self.client.get("/status", **{"HTTP_ACCEPT": "application/json"})
        response_keyed = self.client.get("/status/{}".format(key), **{"HTTP_ACCEPT": "application/json"})
        self.assertEqual(response_keyless.status_code, response_keyed.status_code)
        self.assertEqual(loads(response_keyless.content).keys(), loads(response_keyed.content).keys())

    @override_settings(
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_BASIC_AUTH_REALM="Home Alerts",
    )
    def test_keyed_status_enforces_auth(self):
        key = "auth_status_key"
        self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=_GOOD_MASTER,
        )
        denied = self.client.get("/status/{}".format(key))
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied["WWW-Authenticate"], 'Basic realm="Home Alerts: {}"'.format(key))
        self.assertEqual(
            self.client.get(
                "/status/{}".format(key),
                headers={"authorization": _basic("alice", "secret")},
            ).status_code,
            200,
        )

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=base64.b64encode(b"master:pass").decode())
    def test_global_and_per_key_credentials_work(self):
        key = "auth_master_key"
        good_master = {"authorization": _basic("master", "pass")}
        self.client.post("/add/{}".format(key), {"urls": "json://localhost"}, headers=good_master)
        self.client.post(
            "/auth/{}".format(key),
            data=dumps({"username": "alice", "password": "secret"}),
            content_type="application/json",
            headers=good_master,
        )
        self.assertTrue(ConfigCache.has_auth(key))

        # Master credentials satisfy this key's own lock too.
        self.assertEqual(
            self.client.post("/get/{}".format(key), headers=good_master).status_code,
            200,
        )
        # The key's own credentials still work independently of master.
        self.assertEqual(
            self.client.post(
                "/get/{}".format(key),
                headers={"authorization": _basic("alice", "secret")},
            ).status_code,
            200,
        )
        # Master can remove the lock too, without knowing alice/secret.
        self.assertEqual(
            self.client.delete("/auth/{}".format(key), headers=good_master).status_code,
            200,
        )
        self.assertFalse(ConfigCache.has_auth(key))

    def test_admin_listing_ignores_per_key_auth(self):
        """A lock on one key must not protect the admin config listing."""
        key = "auth_status_key"
        with override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN):
            # Use administrator credentials only while creating the first lock.
            self.client.post(
                "/auth/{}".format(key),
                data=dumps({"username": "alice", "password": "secret"}),
                content_type="application/json",
                headers=_GOOD_MASTER,
            )
        with override_settings(APPRISE_STATEFUL_MODE="simple", APPRISE_ADMIN=True):
            response = self.client.get("/cfg", **{"HTTP_ACCEPT": "application/json"})
        # Admin settings allow this independently of the per-key lock above.
        self.assertEqual(response.status_code, 200)
