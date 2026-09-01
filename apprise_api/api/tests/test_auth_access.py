# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
# All rights reserved.

"""Test per-configuration user, locked, public, and disabled access."""

import base64
from json import dumps, loads
import os
from types import SimpleNamespace
from unittest.mock import mock_open, patch

import apprise
from django.test import SimpleTestCase
from django.test.utils import override_settings

from ..auth import Authentication, AuthStorageError, ConfigAuthState
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
        "access_disabled",
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

    def test_admin_can_create_disabled_access_without_credentials(self):
        """A frozen access record does not need an unusable dummy password."""
        response = self.client.post(
            "/auth/access_disabled",
            data=dumps({"access": Authentication.ACCESS_DISABLED}),
            content_type="application/json",
            headers=_MASTER,
        )

        self.assertEqual(response.status_code, 200)
        record = ConfigCache.get_auth_record("access_disabled")
        self.assertEqual(record.access, Authentication.ACCESS_DISABLED)
        self.assertIsNone(record.username)
        self.assertIsNone(record.digest)

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

        with patch.object(apprise.Apprise, "notify", return_value=True):
            stateful_v2 = self.client.post(
                "/notify",
                {"body": "hello", "tag": "known"},
                headers={"X-Apprise-Config-ID": "access_public"},
            )
        self.assertEqual(stateful_v2.status_code, 200)

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

    def test_public_notify_supports_url_and_attachments(self):
        """Public stateful notifications retain normal attachment support."""
        self._seed("access_public", Authentication.ACCESS_PUBLIC, credentials=False)
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
            by_list = self.client.post(
                "/notify/access_public",
                data=dumps({"body": "hello", "tag": ["known"]}),
                content_type="application/json",
                headers={"accept": "application/json"},
            )

        self.assertEqual(by_url.status_code, 200)
        self.assertEqual(by_list.status_code, 200)
        self.assertEqual(parse_attachments.call_count, 1)
        self.assertEqual(parse_attachments.call_args_list[0].args[0], "https://example.com/a")
        self.assertEqual(notify.call_count, 2)
        self.assertIs(notify.call_args_list[0].kwargs["attach"], attachment)

    def test_stateless_access_requires_admin_or_user_mode_scope(self):
        """Only admin or matching user-mode credentials may send arbitrary URLs."""
        self._seed("access_user", Authentication.ACCESS_USER)
        self._seed("access_locked", Authentication.ACCESS_LOCK)
        self._seed("access_public", Authentication.ACCESS_PUBLIC)
        self._seed("access_disabled", Authentication.ACCESS_DISABLED)

        payload = {"urls": "json://remote-target", "body": "hello"}
        json_headers = {"accept": "application/json"}
        with patch.object(apprise.Apprise, "notify", return_value=True) as notify:
            user = self.client.post(
                "/notify",
                payload,
                headers={**_USER, **json_headers, "X-Apprise-Config-ID": "access_user"},
            )
            locked = self.client.post(
                "/notify",
                payload,
                headers={**_USER, **json_headers, "X-Apprise-Config-ID": "access_locked"},
            )
            public = self.client.post(
                "/notify",
                payload,
                headers={**_USER, **json_headers, "X-Apprise-Config-ID": "access_public"},
            )
            public_without_credentials = self.client.post(
                "/notify",
                payload,
                headers={**json_headers, "X-Apprise-Config-ID": "access_public"},
            )
            disabled = self.client.post(
                "/notify",
                payload,
                headers={**_USER, **json_headers, "X-Apprise-Config-ID": "access_disabled"},
            )
            unscoped_user = self.client.post("/notify", payload, headers={**_USER, **json_headers})
            mismatched_user = self.client.post(
                "/notify",
                payload,
                headers={**_USER, **json_headers, "X-Apprise-Config-ID": "unknown_config"},
            )
            malformed_header = self.client.post(
                "/notify",
                payload,
                headers={**_USER, **json_headers, "X-Apprise-Config-ID": "not valid!"},
            )
            admin = self.client.post("/notify", payload, headers=_MASTER)
            admin_with_header = self.client.post(
                "/notify",
                payload,
                headers={**_MASTER, "X-Apprise-Config-ID": "access_disabled"},
            )
            admin_with_unknown_header = self.client.post(
                "/notify",
                payload,
                headers={**_MASTER, "X-Apprise-Config-ID": "mobile_config"},
            )
            admin_with_malformed_header = self.client.post(
                "/notify",
                payload,
                headers={**_MASTER, "X-Apprise-Config-ID": "not valid!"},
            )

        self.assertEqual(user.status_code, 200)
        self.assertEqual(locked.status_code, 403)
        self.assertEqual(public.status_code, 403)
        self.assertEqual(public_without_credentials.status_code, 401)
        self.assertEqual(disabled.status_code, 403)
        self.assertEqual(unscoped_user.status_code, 401)
        self.assertEqual(mismatched_user.status_code, 401)
        self.assertEqual(malformed_header.status_code, 400)
        self.assertEqual(admin.status_code, 200)
        self.assertEqual(admin_with_header.status_code, 200)
        self.assertEqual(admin_with_unknown_header.status_code, 200)
        self.assertEqual(admin_with_malformed_header.status_code, 400)
        self.assertIn("WWW-Authenticate", unscoped_user)
        self.assertIn("WWW-Authenticate", mismatched_user)
        self.assertIn("WWW-Authenticate", public_without_credentials)
        self.assertNotIn("WWW-Authenticate", locked)
        self.assertNotIn("WWW-Authenticate", public)
        self.assertNotIn("WWW-Authenticate", disabled)
        self.assertEqual(
            disabled.json()["error"],
            "This configuration has been disabled by an administrator",
        )
        self.assertEqual(notify.call_count, 4)

    def test_global_notification_modes_apply_to_admin_and_users(self):
        """Neither administrator nor configuration credentials bypass a disabled mode."""
        self._seed("access_user", Authentication.ACCESS_USER)
        stateless = {"urls": "json://remote-target", "body": "hello"}
        stateful = {"body": "hello"}

        with (
            patch.object(apprise.Apprise, "notify", return_value=True) as notify,
            override_settings(APPRISE_STATELESS_MODE="disabled"),
        ):
            admin_stateless = self.client.post("/notify", stateless, headers=_MASTER)
            user_stateless = self.client.post(
                "/notify",
                stateless,
                headers={**_USER, "X-Apprise-Config-ID": "access_user"},
            )
            admin_stateful = self.client.post(
                "/notify",
                stateful,
                headers={**_MASTER, "X-Apprise-Config-ID": "access_user"},
            )

        self.assertEqual(admin_stateless.status_code, 403)
        self.assertEqual(user_stateless.status_code, 403)
        self.assertEqual(admin_stateful.status_code, 200)
        notify.assert_called_once()

        with (
            patch.object(apprise.Apprise, "notify", return_value=True) as notify,
            override_settings(APPRISE_STATEFUL_MODE="disabled"),
        ):
            admin_stateful = self.client.post(
                "/notify",
                stateful,
                headers={**_MASTER, "X-Apprise-Config-ID": "access_user"},
            )
            user_stateful = self.client.post(
                "/notify",
                stateful,
                headers={**_USER, "X-Apprise-Config-ID": "access_user"},
            )
            admin_stateless = self.client.post("/notify", stateless, headers=_MASTER)

        self.assertEqual(admin_stateful.status_code, 403)
        self.assertEqual(user_stateful.status_code, 403)
        self.assertEqual(admin_stateless.status_code, 200)
        notify.assert_called_once()

    def test_admin_bypasses_config_lock_but_user_does_not(self):
        """The administrator bypass is limited to configuration policy."""
        self._seed("access_user", Authentication.ACCESS_USER)
        with override_settings(APPRISE_CONFIG_LOCK=True):
            admin = self.client.post("/get/access_user", headers=_MASTER)
            user = self.client.post("/get/access_user", headers=_USER)

        self.assertEqual(admin.status_code, 200)
        self.assertEqual(user.status_code, 403)

    @override_settings(APPRISE_CONFIG_LOCK=True)
    def test_global_config_lock_is_the_minimum_access_policy(self):
        """Weaker saved modes cannot bypass the site-wide configuration lock."""
        self._seed("access_user", Authentication.ACCESS_USER)
        self._seed("access_public", Authentication.ACCESS_PUBLIC, credentials=False)
        self._seed("access_disabled", Authentication.ACCESS_DISABLED, credentials=False)

        # Directly seeded records model files created before the global lock or
        # edited by hand. Their stored choice is preserved for a later unlock.
        self.assertEqual(
            ConfigCache.get_auth_record("access_user").access,
            Authentication.ACCESS_USER,
        )
        self.assertEqual(
            ConfigCache.get_auth_record("access_public").access,
            Authentication.ACCESS_PUBLIC,
        )
        self.assertEqual(
            Authentication.config_state("access_user").access,
            Authentication.ACCESS_LOCK,
        )
        self.assertEqual(
            Authentication.config_state("access_public").access,
            Authentication.ACCESS_LOCK,
        )
        self.assertEqual(
            Authentication.config_state("access_disabled").access,
            Authentication.ACCESS_DISABLED,
        )
        self.assertEqual(
            Authentication.config_state("unused_config").access,
            Authentication.ACCESS_LOCK,
        )

        api_state = self.client.get(
            "/auth/access_user",
            headers={**_MASTER, "accept": "application/json"},
        )
        login = self.client.post(
            "/login",
            {"username": "master", "password": "pass"},
            headers={"accept": "text/html"},
        )
        self.assertEqual(login.status_code, 302)
        editor = self.client.get(
            "/auth/access_user",
            headers={"accept": "text/html"},
        )
        self.assertEqual(api_state.json()["access"], Authentication.ACCESS_USER)
        self.assertEqual(api_state.json()["effective_access"], Authentication.ACCESS_LOCK)
        editor_content = editor.content.decode()
        self.assertIn('<option value="user" selected', editor_content)
        self.assertIn('<option value="public"', editor_content)
        self.assertIn('<option value="locked"', editor_content)
        self.assertIn('<option value="disabled"', editor_content)
        self.assertIn('id="auth-access-help"', editor_content)
        self.assertIn('class="auth-access-table"', editor_content)
        self.assertIn("Saved user access is currently enforced as locked", editor_content)

        preserved_user = self.client.post(
            "/auth/access_user",
            data=dumps({"access": Authentication.ACCESS_USER}),
            content_type="application/json",
            headers=_MASTER,
        )
        self.assertEqual(preserved_user.status_code, 200)
        self.assertEqual(
            ConfigCache.get_auth_record("access_user").access,
            Authentication.ACCESS_USER,
        )

        preserved_public = self.client.post(
            "/auth/access_public",
            data=dumps({"access": Authentication.ACCESS_PUBLIC}),
            content_type="application/json",
            headers=_MASTER,
        )
        self.assertEqual(preserved_public.status_code, 200)
        self.assertEqual(
            ConfigCache.get_auth_record("access_public").access,
            Authentication.ACCESS_PUBLIC,
        )

        # Disabled is stricter than the global lock and remains selectable.
        disabled = self.client.post(
            "/auth/access_disabled",
            data=dumps({"access": Authentication.ACCESS_DISABLED}),
            content_type="application/json",
            headers=_MASTER,
        )
        self.assertEqual(disabled.status_code, 200)

        with patch.object(apprise.Apprise, "notify", return_value=True) as notify:
            user_stateful = self.client.post(
                "/notify/access_user",
                {"body": "hello", "tag": "known"},
                headers=_USER,
            )
            user_stateless = self.client.post(
                "/notify",
                {"urls": "json://remote-target", "body": "hello"},
                headers={**_USER, "X-Apprise-Config-ID": "access_user"},
            )
            public_stateful = self.client.post(
                "/notify/access_public",
                {"body": "hello", "tag": "known"},
            )
            admin_stateless = self.client.post(
                "/notify",
                {"urls": "json://remote-target", "body": "hello"},
                headers={**_MASTER, "X-Apprise-Config-ID": "access_user"},
            )

        self.assertEqual(user_stateful.status_code, 200)
        self.assertEqual(user_stateless.status_code, 403)
        self.assertEqual(public_stateful.status_code, 401)
        self.assertEqual(admin_stateless.status_code, 200)
        self.assertEqual(notify.call_count, 2)

        rotated = self.client.post(
            "/auth/access_user",
            data=dumps({"username": "alice", "password": "new-secret"}),
            content_type="application/json",
            headers=_USER,
        )
        self.assertEqual(rotated.status_code, 200)
        self.assertEqual(
            ConfigCache.get_auth_record("access_user").access,
            Authentication.ACCESS_USER,
        )

        # The effective policy remains visible even when per-key authentication
        # is globally disabled and its saved records are otherwise ignored.
        with override_settings(APPRISE_AUTH_REQUIRED=False):
            self.assertEqual(
                Authentication.config_state("access_user").access,
                Authentication.ACCESS_LOCK,
            )

        # Removing only the global setting restores each untouched file's mode.
        with override_settings(APPRISE_CONFIG_LOCK=False):
            self.assertEqual(
                Authentication.config_state("access_user").access,
                Authentication.ACCESS_USER,
            )
            self.assertEqual(
                Authentication.config_state("access_public").access,
                Authentication.ACCESS_PUBLIC,
            )

    def test_disabled_access_freezes_user_but_preserves_credentials(self):
        """An administrator can freeze and later restore an existing account."""
        self._seed("access_disabled", Authentication.ACCESS_USER)
        frozen = self.client.post(
            "/auth/access_disabled",
            data=dumps({"access": Authentication.ACCESS_DISABLED}),
            content_type="application/json",
            headers=_MASTER,
        )
        self.assertEqual(frozen.status_code, 200)
        self.assertTrue(ConfigCache.verify_auth("access_disabled", "alice", "secret"))

        payload = {"urls": "json://remote-target", "body": "hello"}
        with patch.object(apprise.Apprise, "notify", return_value=True) as notify:
            stateful_user = self.client.post(
                "/notify/access_disabled",
                {"body": "hello", "tag": "known"},
                headers=_USER,
            )
            stateless_user = self.client.post(
                "/notify",
                payload,
                headers={**_USER, "X-Apprise-Config-ID": "access_disabled"},
            )
            admin_stateful = self.client.post(
                "/notify/access_disabled",
                {"body": "hello"},
                headers=_MASTER,
            )

        self.assertEqual(stateful_user.status_code, 403)
        self.assertEqual(stateless_user.status_code, 403)
        self.assertEqual(
            stateless_user.content.decode(),
            "This configuration has been disabled by an administrator",
        )
        self.assertEqual(admin_stateful.status_code, 200)
        notify.assert_called_once()

        restored = self.client.post(
            "/auth/access_disabled",
            data=dumps({"access": Authentication.ACCESS_USER}),
            content_type="application/json",
            headers=_MASTER,
        )
        self.assertEqual(restored.status_code, 200)
        self.assertTrue(ConfigCache.verify_auth("access_disabled", "alice", "secret"))

    def test_disabled_access_ends_an_existing_browser_session(self):
        """Disabled access rejects fresh logins and an existing browser cookie."""
        self._seed("access_disabled", Authentication.ACCESS_USER)
        login = self.client.post(
            "/login",
            {"username": "alice", "password": "secret", "key": "access_disabled"},
            headers={"accept": "text/html"},
        )
        self.assertEqual(login.status_code, 302)

        frozen = self.client.post(
            "/auth/access_disabled",
            data=dumps({"access": Authentication.ACCESS_DISABLED}),
            content_type="application/json",
            headers=_MASTER,
        )
        self.assertEqual(frozen.status_code, 200)

        existing_session = self.client.get("/details", headers={"accept": "text/html"})
        self.assertEqual(existing_session.status_code, 302)
        self.assertTrue(existing_session.url.startswith("/login?"))

        self.client.cookies.clear()
        rejected_login = self.client.post(
            "/login",
            {"username": "alice", "password": "secret", "key": "access_disabled"},
            headers={"accept": "text/html"},
        )
        self.assertEqual(rejected_login.status_code, 401)
        self.assertEqual(rejected_login.cookies[Authentication.WEB_COOKIE]["max-age"], 0)

        # The lower-level keyed policy remains fail-closed too.
        request = SimpleNamespace(
            globally_authenticated=False,
            apprise_auth_permission=Authentication.ROLE_USER,
            apprise_web_auth_key="access_disabled",
        )
        state = ConfigAuthState(
            mode=Authentication.MODE_ASSIGNED,
            access=Authentication.ACCESS_DISABLED,
        )

        with patch.object(Authentication, "config_state", return_value=state):
            self.assertFalse(Authentication.key_ok(request, "access_disabled"))

        self.assertEqual(request.apprise_disabled_config_key, "access_disabled")

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

        with patch.object(apprise.Apprise, "notify", return_value=True):
            stateful = self.client.post(
                "/notify",
                {"body": "hello", "tag": "known"},
                headers={**_USER, "X-Apprise-Config-ID": "access_locked"},
            )
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

        self.assertEqual(stateful.status_code, 200)
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

    def test_locked_configuration_status_is_relative_to_the_authenticated_caller(self):
        """Admin status mirrors its real access while a config user stays locked."""
        self._seed("access_locked", Authentication.ACCESS_LOCK)

        admin = self.client.get(
            "/status/access_locked",
            headers={**_MASTER, "accept": "application/json"},
        )
        user = self.client.get(
            "/status/access_locked",
            headers={**_USER, "accept": "application/json"},
        )

        self.assertEqual(admin.status_code, 200)
        self.assertEqual(admin.json()["privilege"], Authentication.ROLE_ADMIN)
        self.assertFalse(admin.json()["config_lock"])
        self.assertEqual(
            self.client.get("/json/urls/access_locked", headers=_MASTER).status_code,
            200,
        )

        self.assertEqual(user.status_code, 200)
        self.assertEqual(user.json()["privilege"], Authentication.ROLE_USER)
        self.assertTrue(user.json()["config_lock"])
        self.assertEqual(
            self.client.get("/json/urls/access_locked", headers=_USER).status_code,
            403,
        )

    def test_header_scoped_information_access_matrix(self):
        """User and locked accounts may inspect service health; disabled accounts may not."""
        self._seed("access_user", Authentication.ACCESS_USER)
        self._seed("access_locked", Authentication.ACCESS_LOCK)
        self._seed("access_disabled", Authentication.ACCESS_DISABLED)
        json_header = {"accept": "application/json"}

        user = self.client.get(
            "/status",
            headers={**_USER, **json_header, "X-Apprise-Config-ID": "access_user"},
        )
        locked = self.client.get(
            "/status",
            headers={**_USER, **json_header, "X-Apprise-Config-ID": "access_locked"},
        )
        locked_details = self.client.get(
            "/details",
            headers={**_USER, **json_header, "X-Apprise-Config-ID": "access_locked"},
        )
        disabled = self.client.get(
            "/status",
            headers={**_USER, **json_header, "X-Apprise-Config-ID": "access_disabled"},
        )
        missing_scope = self.client.get("/status", headers={**_USER, **json_header})
        wrong_scope = self.client.get(
            "/status",
            headers={**_USER, **json_header, "X-Apprise-Config-ID": "unknown_config"},
        )
        malformed_scope = self.client.get(
            "/status",
            headers={**_USER, **json_header, "X-Apprise-Config-ID": "not valid!"},
        )
        admin = self.client.get("/status", headers={**_MASTER, **json_header})
        admin_with_scope = self.client.get(
            "/status",
            headers={**_MASTER, **json_header, "X-Apprise-Config-ID": "access_disabled"},
        )

        self.assertEqual(user.status_code, 200)
        self.assertEqual(user.json()["privilege"], Authentication.ROLE_USER)
        self.assertFalse(user.json()["config_lock"])
        self.assertEqual(locked.status_code, 200)
        self.assertEqual(locked.json()["privilege"], Authentication.ROLE_USER)
        self.assertTrue(locked.json()["config_lock"])
        self.assertEqual(locked_details.status_code, 200)
        self.assertEqual(disabled.status_code, 403)
        self.assertEqual(missing_scope.status_code, 401)
        self.assertEqual(wrong_scope.status_code, 401)
        self.assertEqual(malformed_scope.status_code, 400)
        self.assertEqual(admin.status_code, 200)
        self.assertEqual(admin.json()["privilege"], Authentication.ROLE_ADMIN)
        self.assertEqual(admin_with_scope.status_code, 200)
        self.assertEqual(admin_with_scope.json()["privilege"], Authentication.ROLE_ADMIN)

    def test_locked_user_cannot_change_access(self):
        """Only an administrator may submit the saved access field."""
        self._seed("access_locked", Authentication.ACCESS_LOCK)
        for access in (Authentication.ACCESS_LOCK, Authentication.ACCESS_PUBLIC):
            with self.subTest(access=access):
                response = self.client.post(
                    "/auth/access_locked",
                    data=dumps(
                        {
                            "access": access,
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

    def test_public_and_disabled_policy_may_exist_without_credentials(self):
        """Only public and disabled access can be stored without a login."""
        store = AppriseConfigCache("/tmp/apprise-public-record", mode=AppriseStoreMode.SIMPLE)
        self.addCleanup(store.clear_auth, "record")

        self.assertFalse(store.set_access("record", Authentication.ACCESS_USER))
        self.assertFalse(store.set_access("record", Authentication.ACCESS_LOCK))
        self.assertFalse(store.set_access("record", "invalid"))
        self.assertTrue(store.set_access("record", Authentication.ACCESS_PUBLIC))
        self.assertFalse(store.set_access("record", Authentication.ACCESS_LOCK))
        self.assertFalse(store.verify_auth("record", "", "secret"))

        self.assertTrue(store.set_access("record", Authentication.ACCESS_DISABLED))
        self.assertEqual(store.get_auth_record("record").access, Authentication.ACCESS_DISABLED)

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
