# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
# All rights reserved.

"""Test per-configuration user, locked, and public access."""

import base64
from json import dumps, loads
import os
from unittest.mock import mock_open, patch

import apprise
from django.test import SimpleTestCase
from django.test.utils import override_settings

from ..auth import Authentication, AuthStorageError
from ..forms import AuthForm
from ..utils import (
    AppriseConfigCache,
    AppriseStoreMode,
    ConfigCache,
)
from ..views import tag_expression_is_specific, tag_expression_uses_all


def _basic(username, password):
    """Build one Basic Auth header value."""
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return "Basic " + token


_MASTER_TOKEN = base64.b64encode(b"master:pass").decode()
_MASTER = {"authorization": _basic("master", "pass")}
_USER = {"authorization": _basic("alice", "secret")}


@override_settings(
    APPRISE_AUTH_REQUIRED=True,
    APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
    APPRISE_USER="master",
)
class ConfigAccessViewTests(SimpleTestCase):
    """Exercise each access mode through the public API."""

    keys = (
        "access_public",
        "access_public_header",
        "access_locked",
        "access_locked_moved",
        "access_user",
    )

    def tearDown(self):
        """Remove the persistent files created by each test."""
        for key in self.keys:
            ConfigCache.clear(key)
            ConfigCache.clear_auth(key)

    def _seed(self, key, access, credentials=True):
        """Save a tagged destination and its access record."""
        self.assertTrue(ConfigCache.put(key, "<known>=json://localhost", "text"))
        if credentials:
            self.assertTrue(ConfigCache.set_auth(key, "alice", "secret", access=access))
        else:
            self.assertTrue(ConfigCache.set_access(key, access))

    def test_admin_can_create_public_access_without_credentials(self):
        """An admin may expose tagged notifications without a dummy login."""
        response = self.client.post(
            "/auth/access_public",
            data=dumps({"access": Authentication.ACCESS_PUBLIC}),
            content_type="application/json",
            headers=_MASTER,
        )

        self.assertEqual(response.status_code, 200)
        record = ConfigCache.get_auth_record("access_public")
        self.assertEqual(record.access, Authentication.ACCESS_PUBLIC)
        self.assertIsNone(record.username)
        self.assertIsNone(record.digest)

        # JSON null follows the same password-only username normalization.
        response = self.client.post(
            "/auth/access_public",
            data=dumps({"access": Authentication.ACCESS_PUBLIC, "username": None}),
            content_type="application/json",
            headers=_MASTER,
        )
        self.assertEqual(response.status_code, 200)

    def test_browser_admin_credentials_win_a_configuration_collision(self):
        """The browser grants admin scope when both credential sets match."""
        self.assertTrue(ConfigCache.set_auth("access_user", "master", "pass"))
        response = self.client.post(
            "/login",
            {"username": " master ", "password": "pass", "key": "access_user"},
            headers={"accept": "text/html"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")

    def test_public_access_is_limited_to_tagged_notifications(self):
        """A public Config ID grants no access outside stateful notification."""
        self._seed("access_public", Authentication.ACCESS_PUBLIC, credentials=False)

        for path, method in (
            ("/status/access_public", self.client.get),
            ("/get/access_public", self.client.post),
            ("/auth/access_public", self.client.get),
            ("/move/access_public", self.client.post),
        ):
            response = method(path, headers={"accept": "application/json"})
            self.assertEqual(response.status_code, 401)

    def test_public_notify_requires_a_specific_tag_before_attachments(self):
        """Missing and broad tags fail before attachment handling begins."""
        self._seed("access_public", Authentication.ACCESS_PUBLIC, credentials=False)
        with patch("api.views.parse_attachments") as parse_attachments:
            missing = self.client.post(
                "/notify/access_public",
                data=dumps({"body": "hello", "attachment": "https://example.com/a"}),
                content_type="application/json",
                headers={"accept": "application/json"},
            )
            broad = self.client.post(
                "/notify/access_public",
                data=dumps({"body": "hello", "tag": "1:ALL:2", "attachment": "https://example.com/a"}),
                content_type="application/json",
                headers={"accept": "application/json"},
            )
            malformed = self.client.post(
                "/notify/access_public",
                data=dumps({"body": "hello", "tag": 4, "attachment": "https://example.com/a"}),
                content_type="application/json",
                headers={"accept": "application/json"},
            )

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(broad.status_code, 400)
        self.assertEqual(malformed.status_code, 400)
        parse_attachments.assert_not_called()

    def test_public_notify_supports_url_header_and_attachments(self):
        """Both Config ID forms retain normal public attachment support."""
        self._seed("access_public", Authentication.ACCESS_PUBLIC, credentials=False)
        self._seed("access_public_header", Authentication.ACCESS_PUBLIC, credentials=False)
        attachment = object()
        with (
            patch("api.views.parse_attachments", return_value=attachment) as parse_attachments,
            patch.object(apprise.Apprise, "notify", return_value=True) as notify,
        ):
            by_url = self.client.post(
                "/notify/access_public",
                data=dumps({"body": "hello", "tag": "known", "attachment": "https://example.com/a"}),
                content_type="application/json",
                headers={"accept": "application/json"},
            )
            by_header = self.client.post(
                "/notify/",
                {"body": "hello", "tag": "known"},
                headers={
                    "X-Apprise-Config-ID": "access_public_header",
                    "accept": "application/json",
                },
            )
            by_list = self.client.post(
                "/notify/access_public",
                data=dumps({"body": "hello", "tag": ["known"]}),
                content_type="application/json",
                headers={"accept": "application/json"},
            )

        self.assertEqual(by_url.status_code, 200)
        self.assertEqual(by_header.status_code, 200)
        self.assertEqual(by_list.status_code, 200)
        self.assertEqual(parse_attachments.call_count, 2)
        self.assertEqual(parse_attachments.call_args_list[0].args[0], "https://example.com/a")
        self.assertEqual(notify.call_count, 3)
        self.assertIs(notify.call_args_list[0].kwargs["attach"], attachment)

    def test_admin_may_use_all_on_a_public_configuration(self):
        """The administrator remains unrestricted in every access mode."""
        self._seed("access_public", Authentication.ACCESS_PUBLIC, credentials=False)
        with patch.object(apprise.Apprise, "notify", return_value=True):
            response = self.client.post(
                "/notify/access_public",
                {"body": "hello", "tag": "all"},
                headers={**_MASTER, "accept": "application/json"},
            )
        self.assertEqual(response.status_code, 200)

    def test_locked_user_gets_health_password_rotation_and_move(self):
        """A locked user keeps account tools while configuration stays hidden."""
        self._seed("access_locked", Authentication.ACCESS_LOCK)

        health = self.client.get(
            "/status/access_locked",
            headers={**_USER, "accept": "application/json"},
        )
        hidden = self.client.post(
            "/get/access_locked",
            headers={**_USER, "accept": "application/json"},
        )
        rotated = self.client.post(
            "/auth/access_locked",
            data=dumps({"username": "alice", "password": "new-secret"}),
            content_type="application/json",
            headers=_USER,
        )

        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["config_lock"])
        self.assertEqual(hidden.status_code, 403)
        self.assertEqual(rotated.status_code, 200)
        record = ConfigCache.get_auth_record("access_locked")
        self.assertEqual(record.access, Authentication.ACCESS_LOCK)
        self.assertTrue(ConfigCache.verify_auth("access_locked", "alice", "new-secret"))

        moved = self.client.post(
            "/move/access_locked",
            data=dumps({"to": "access_locked_moved"}),
            content_type="application/json",
            headers={
                "authorization": _basic("alice", "new-secret"),
                "accept": "application/json",
            },
        )
        self.assertEqual(moved.status_code, 200)
        self.assertEqual(
            ConfigCache.get_auth_record("access_locked_moved").access,
            Authentication.ACCESS_LOCK,
        )

    def test_locked_user_cannot_change_access(self):
        """Only an administrator may change the saved access field."""
        self._seed("access_locked", Authentication.ACCESS_LOCK)
        response = self.client.post(
            "/auth/access_locked",
            data=dumps(
                {
                    "access": Authentication.ACCESS_PUBLIC,
                    "username": "alice",
                    "password": "new-secret",
                }
            ),
            content_type="application/json",
            headers=_USER,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["field"], "access")
        self.assertEqual(ConfigCache.get_auth_record("access_locked").access, Authentication.ACCESS_LOCK)

    def test_user_health_reports_unlocked_configuration(self):
        """The existing user mode continues to report normal configuration access."""
        self._seed("access_user", Authentication.ACCESS_USER)
        response = self.client.get(
            "/status/access_user",
            headers={**_USER, "accept": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["config_lock"])

    def test_public_query_tag_is_supported(self):
        """A public caller may place the required tag in the query string."""
        self._seed("access_public", Authentication.ACCESS_PUBLIC, credentials=False)
        with patch.object(apprise.Apprise, "notify", return_value=True):
            response = self.client.post(
                "/notify/access_public?tag=known",
                {"body": "hello"},
                headers={"accept": "application/json"},
            )
        self.assertEqual(response.status_code, 200)


class ConfigAccessStorageTests(SimpleTestCase):
    """Cover access-record validation and safe storage failures."""

    def test_access_record_has_one_unversioned_policy_field(self):
        """The debut record stores access directly without migration metadata."""
        store = AppriseConfigCache("/tmp/apprise-access-record", mode=AppriseStoreMode.SIMPLE)
        self.addCleanup(store.clear_auth, "record")
        self.assertTrue(store.set_auth("record", None, " secret ", access=Authentication.ACCESS_LOCK))

        path, filename = store.auth_path("record")
        with open(os.path.join(path, filename)) as handle:
            saved = loads(handle.read())

        self.assertEqual(set(saved), {"access", "username", "digest"})
        self.assertEqual(saved["access"], Authentication.ACCESS_LOCK)
        self.assertEqual(saved["username"], "")
        self.assertTrue(store.verify_auth("record", "", " secret "))
        self.assertFalse(store.verify_auth("record", "", "secret"))

    def test_public_policy_may_exist_without_credentials(self):
        """Only public access can be stored without a login."""
        store = AppriseConfigCache("/tmp/apprise-public-record", mode=AppriseStoreMode.SIMPLE)
        self.addCleanup(store.clear_auth, "record")

        self.assertFalse(store.set_access("record", Authentication.ACCESS_USER))
        self.assertFalse(store.set_access("record", Authentication.ACCESS_LOCK))
        self.assertFalse(store.set_access("record", "invalid"))
        self.assertTrue(store.set_access("record", Authentication.ACCESS_PUBLIC))
        self.assertFalse(store.set_access("record", Authentication.ACCESS_LOCK))
        self.assertFalse(store.verify_auth("record", "", "secret"))

        # Credentials allow safe transitions between all three modes.
        self.assertTrue(store.set_auth("record", "alice", "secret"))
        self.assertTrue(store.set_access("record", Authentication.ACCESS_LOCK))
        self.assertEqual(store.get_auth_record("record").access, Authentication.ACCESS_LOCK)

    def test_access_storage_errors_fail_closed(self):
        """Policy read and guard failures return false instead of raising."""
        store = AppriseConfigCache("/tmp/apprise-access-errors", mode=AppriseStoreMode.SIMPLE)
        disabled = AppriseConfigCache("/tmp/apprise-access-errors", mode=AppriseStoreMode.DISABLED)
        self.assertFalse(disabled.set_access("record", Authentication.ACCESS_PUBLIC))

        with patch.object(store, "_acquire_auth_guard", side_effect=OSError):
            self.assertFalse(store.set_access("record", Authentication.ACCESS_PUBLIC))
        with (
            patch.object(store, "_acquire_auth_guard", return_value=10),
            patch.object(store, "_release_auth_guard"),
            patch.object(store, "get_auth_record", side_effect=AuthStorageError("bad")),
        ):
            self.assertFalse(store.set_access("record", Authentication.ACCESS_PUBLIC))

    def test_tag_helpers_reject_malformed_and_all_values(self):
        """Tag-only access accepts complete specific tag expressions."""
        self.assertTrue(tag_expression_uses_all(("known", "1:ALL:2")))
        self.assertFalse(tag_expression_uses_all(4))
        self.assertTrue(tag_expression_is_specific(("known", "2:other:1")))
        self.assertFalse(tag_expression_is_specific([]))
        self.assertFalse(tag_expression_is_specific(("known", 4)))
        self.assertFalse(tag_expression_is_specific("all"))

        cyclic = []
        cyclic.append(cyclic)
        self.assertFalse(tag_expression_is_specific(cyclic))
        self.assertFalse(tag_expression_is_specific(["known"] * 257))

    def test_forms_and_helpers_reject_invalid_access_inputs(self):
        """Invalid policy and credential types fail without an exception."""
        store = AppriseConfigCache("/tmp/apprise-access-inputs", mode=AppriseStoreMode.SIMPLE)
        self.assertFalse(store.set_auth("record", "alice", "secret", access="invalid"))
        self.assertFalse(store.set_auth("record", 4, "secret"))
        self.assertFalse(store.set_auth("record", "alice", 4))

        changed_access = AuthForm(
            {
                "access": Authentication.ACCESS_PUBLIC,
                "username": "alice",
                "password": "new",
                "password_confirm": "new",
            },
            shared=True,
            current_username="alice",
            current_access=Authentication.ACCESS_LOCK,
            has_credentials=True,
        )
        self.assertFalse(changed_access.is_valid())
        self.assertIn("access", changed_access.errors)

        missing_password = AuthForm(
            {
                "access": Authentication.ACCESS_LOCK,
                "username": "alice",
                "password_confirm": "",
            },
            shared=True,
            current_username="alice",
            current_access=Authentication.ACCESS_LOCK,
            has_credentials=True,
        )
        self.assertFalse(missing_password.is_valid())
        self.assertIn("password", missing_password.errors)

        renamed_without_password = AuthForm(
            {"access": Authentication.ACCESS_USER, "username": "bob"},
            current_username="alice",
            has_credentials=True,
        )
        self.assertFalse(renamed_without_password.is_valid())
        self.assertIn("username", renamed_without_password.errors)

    @override_settings(APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_global_usernames_are_normalized_and_types_are_safe(self):
        """Admin usernames ignore whitespace while passwords stay exact."""
        self.assertTrue(Authentication.global_credentials_ok(" master ", "pass"))
        self.assertFalse(Authentication.global_credentials_ok(None, "pass"))
        self.assertFalse(Authentication.global_credentials_ok(4, "pass"))
        self.assertFalse(Authentication.global_credentials_ok("master", None))

    def test_every_malformed_record_shape_fails_closed(self):
        """Invalid access records are never mistaken for open configurations."""
        store = AppriseConfigCache("/tmp/apprise-access-malformed", mode=AppriseStoreMode.SIMPLE)
        malformed = (
            {"access": "invalid", "username": "alice", "digest": "digest"},
            {"access": Authentication.ACCESS_USER, "username": None, "digest": "digest"},
            {"access": Authentication.ACCESS_USER, "username": 4, "digest": "digest"},
            {"access": Authentication.ACCESS_USER, "username": "alice", "digest": 4},
            {"access": Authentication.ACCESS_USER, "username": None, "digest": None},
        )
        for record in malformed:
            with (
                self.subTest(record=record),
                patch("builtins.open", mock_open(read_data=dumps(record))),
                self.assertRaises(AuthStorageError),
            ):
                store.get_auth_record("record")
