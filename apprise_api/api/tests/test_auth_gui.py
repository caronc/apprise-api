# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
# All rights reserved.
#
# This code is licensed under the MIT License.
"""Test the Basic Auth presentation shown in the web interface."""

import base64
from contextlib import suppress
import os

from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.test import SimpleTestCase
from django.test.utils import override_settings

from ..utils import (
    AUTH_MODE_DISABLED,
    AUTH_MODE_MASTER,
    AUTH_MODE_SHARED,
    WEB_AUTH_COOKIE,
    WEB_AUTH_HEADER,
    ConfigCache,
    _auth_throttle_cache_key,
    config_auth_mode,
)


def _basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


_MASTER_TOKEN = base64.b64encode(b"master:pass").decode()
_MASTER = {"authorization": _basic("master", "pass")}
_SHARED = {"authorization": _basic("alice", "secret")}
_BROWSER = {"accept": "text/html,application/xhtml+xml"}


class AuthGuiTests(SimpleTestCase):
    """Exercise auth state, permissions, and rendered controls together."""

    key = "auth_gui_key"

    def tearDown(self):
        path, filename = ConfigCache.auth_path(self.key)
        with suppress(OSError):
            os.chmod(os.path.join(path, filename), 0o600)
        ConfigCache.clear(self.key)
        ConfigCache.clear_auth(self.key)
        cache.delete(_auth_throttle_cache_key("127.0.0.1", self.key))

    def test_disabled_mode_ignores_a_stale_lock(self):
        """Turning global auth off restores the original open GUI."""
        ConfigCache.set_auth(self.key, "alice", "secret")
        self.assertEqual(config_auth_mode(self.key), AUTH_MODE_DISABLED)

        response = self.client.get("/cfg/{}".format(self.key))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn('id="cfg-auth"', content)
        self.assertNotIn("config-auth-link", content)
        self.assertIn("config-auth-status is-disabled", content)
        self.assertIn("Authentication is disabled", content)
        self.assertIn('viewBox="0 0 512 512"', content)
        self.assertNotIn("auth-divider", content)

        auth_page = self.client.get("/auth/", headers=_BROWSER)
        self.assertEqual(auth_page.status_code, 200)
        auth_content = auth_page.content.decode()
        self.assertIn("auth-disabled-card", auth_content)
        self.assertIn("Authentication Not Required", auth_content)
        self.assertIn(
            "No authentication is required for this hosted version of Apprise.",
            auth_content,
        )
        self.assertNotIn('id="cfg-auth"', auth_content)

    @override_settings(
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_USER="master",
    )
    def test_master_sees_auth_editor_and_generator(self):
        """The administrator can create the key's first shared login."""
        self.assertEqual(config_auth_mode(self.key), AUTH_MODE_MASTER)
        response = self.client.get("/auth/{}".format(self.key), headers=_MASTER)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Configuration Authentication", content)
        self.assertIn("auth-card-grid is-admin", content)
        self.assertIn("auth-credentials-card", content)
        self.assertIn("auth-lock-card-icon", content)
        self.assertIn("Configuration Login", content)
        self.assertIn("config-auth-status is-unassigned", content)
        self.assertLess(content.index("config-auth-status"), content.index("config-id-label"))
        self.assertIn('class="config-id is-concealed"', content)
        self.assertIn("data-config-id-toggle", content)
        self.assertIn('data-config-id-copy="auth_gui_key"', content)
        self.assertIn("auth-tools-card", content)
        self.assertIn("auth-tools-card-icon", content)
        self.assertIn(">Tools</h5>", content)
        self.assertIn('class="btn waves-effect waves-light auth-remove-button"', content)
        self.assertIn('class="auth-mode-badge">', content)
        self.assertIn("Admin", content)
        self.assertNotIn("Global Login Only", content)
        self.assertNotIn("Global administrator credentials can always access", content)
        self.assertNotIn("The saved password is never displayed", content)
        self.assertIn('id="auth-generate"', content)
        self.assertIn('id="auth-generate-user"', content)
        self.assertIn('id="auth-generate-password"', content)
        self.assertEqual(content.count('aria-pressed="true"'), 2)
        self.assertIn(">Username</button>", content)
        self.assertIn("Randomize", content)
        self.assertIn("Reset Access", content)
        self.assertIn('name="username"', content)
        self.assertIn('autocomplete="off"', content)
        self.assertEqual(content.count('data-1p-ignore="true"'), 2)
        self.assertEqual(content.count('data-bwignore="true"'), 2)
        self.assertEqual(content.count('data-lpignore="true"'), 2)
        self.assertIn('autocomplete="new-password"', content)
        self.assertNotIn('name="password_confirm"', content)
        self.assertNotIn('value="master"', content)
        self.assertIn("Open Authentication Settings", content)
        self.assertIn("auth-account is-master", content)
        self.assertIn("auth-divider", content)
        self.assertIn("abstract-user-cutout", content)
        self.assertLess(content.index("auth-account"), content.index("API HEALTH"))
        self.assertIn('href="/auth/auth_gui_key"', content)
        self.assertIn('href="/logout"', content)
        self.assertIn('data-tooltip="Logout"', content)
        self.assertIn("auth-logout-icon", content)
        self.assertIn("logout-confirm-icon", content)
        self.assertIn("Are you sure you wish to log out?", content)
        self.assertNotIn("Abstract user", content)
        self.assertIn("M.updateTextFields()", content)
        self.assertEqual(content.count('class="btn-flat value-visibility-toggle"'), 1)
        self.assertIn("optionSelected(generateUser)", content)
        self.assertIn("optionSelected(generatePassword)", content)
        self.assertIn("setOptionSelected(other, true)", content)
        self.assertIn("(authCurrentPassword || authUsername || authPassword).focus()", content)
        self.assertIn("const requiredFields = [authCurrentPassword, authPassword, authPasswordConfirm]", content)
        self.assertNotIn("Enter a password before saving.", content)
        self.assertIn("novalidate", content)
        self.assertIn("authUsername.value = '';", content)
        self.assertIn('id="auth-form-error"', content)
        self.assertIn("authShowError", content)

    @override_settings(
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_USER="master",
        APPRISE_ADMIN=True,
        APPRISE_STATEFUL_MODE="simple",
    )
    def test_master_login_wins_when_shared_lock_exists(self):
        """Master credentials retain full access when the key has a lock."""
        ConfigCache.set_auth(self.key, "alice", "secret")

        response = self.client.get("/cfg/{}".format(self.key), headers=_MASTER)
        config_list = self.client.get("/cfg", headers=_MASTER)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("auth-account is-master", content)
        self.assertIn('auth-account-name">master</span>', content)
        self.assertEqual(config_list.status_code, 200)

    @override_settings(
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_USER="master",
    )
    def test_shared_user_sees_saved_username(self):
        """A key user sees their username but never the saved password."""
        ConfigCache.set_auth(self.key, "alice", "secret")
        self.assertEqual(config_auth_mode(self.key), AUTH_MODE_SHARED)

        response = self.client.get("/auth/{}".format(self.key), headers=_SHARED)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('value="alice"', content)
        self.assertIn('class="auth-mode-badge is-shared">', content)
        self.assertIn("auth-card-grid is-user", content)
        self.assertIn("auth-credentials-card", content)
        self.assertNotIn("auth-tools-card", content)
        self.assertIn("User Profile", content)
        self.assertIn("Updates to account information are made here.", content)
        self.assertIn("Update Credentials", content)
        self.assertIn("config-auth-status is-assigned", content)
        self.assertIn("User", content)
        self.assertIn("readonly", content)
        self.assertIn('name="current_password"', content)
        self.assertIn("auth-password-fields", content)
        self.assertIn("Current Password", content)
        self.assertIn("New Password", content)
        self.assertIn("Confirm New Password", content)
        self.assertIn('name="password_confirm"', content)
        self.assertEqual(content.count('data-1p-ignore="true"'), 4)
        self.assertEqual(content.count('data-bwignore="true"'), 4)
        self.assertEqual(content.count('data-lpignore="true"'), 4)
        self.assertEqual(content.count('class="btn-flat value-visibility-toggle"'), 3)
        self.assertNotIn('id="auth-generate"', content)
        self.assertNotIn('id="auth-remove"', content)
        self.assertNotIn("secret", content)
        self.assertIn("Open Authentication Settings", content)
        self.assertNotIn("auth-account is-master", content)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_password_only_user_does_not_see_username(self):
        """A password-only configuration login shows only password fields."""
        ConfigCache.set_auth(self.key, "", "secret")
        response = self.client.get(
            "/auth/{}".format(self.key),
            headers={"authorization": _basic("", "secret")},
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn('name="username"', content)
        self.assertIn('name="current_password"', content)
        self.assertIn('name="password"', content)
        self.assertIn('name="password_confirm"', content)
        self.assertEqual(content.count('class="btn-flat value-visibility-toggle"'), 3)
        self.assertNotIn("auth-account", content)
        self.assertIn("auth-logout-link", content)

    @override_settings(
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_USER="master",
    )
    def test_json_state_uses_accept_header(self):
        """The GET endpoint keeps the API and HTML representations aligned."""
        ConfigCache.set_auth(self.key, "alice", "secret")
        response = self.client.get(
            "/auth/{}".format(self.key),
            headers={**_SHARED, "accept": "application/json"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"mode": AUTH_MODE_SHARED, "username": "alice"})

    @override_settings(
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_USER="master",
        APPRISE_ADMIN=True,
        APPRISE_STATEFUL_MODE="simple",
    )
    def test_shared_gui_hides_admin_controls_and_masks_examples(self):
        """Per-key access shows only key-scoped controls and safe examples."""
        ConfigCache.set_auth(self.key, "alice", "secret")
        response = self.client.get("/cfg/{}".format(self.key), headers=_SHARED)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('id="cfg-auth"', content)
        self.assertNotIn('id="cfg-list"', content)
        self.assertIn("config-auth-link", content)
        self.assertIn("-u &quot;alice:****&quot;", content)
        self.assertNotIn("secret", content)

    @override_settings(
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_USER="master",
        APPRISE_ADMIN=True,
        APPRISE_STATEFUL_MODE="simple",
    )
    def test_shared_login_cannot_list_configuration_ids(self):
        """A key login cannot open the administrator's configuration list."""
        ConfigCache.set_auth(self.key, "alice", "secret")

        browser = self.client.get("/cfg", headers={**_SHARED, "accept": "text/html"})
        api = self.client.get("/cfg", headers={**_SHARED, "accept": "application/json"})

        self.assertEqual(browser.status_code, 302)
        self.assertTrue(browser.url.startswith("/login?"))
        self.assertNotIn(self.key, browser.content.decode())
        self.assertEqual(api.status_code, 401)
        self.assertNotIn(self.key, api.content.decode())

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_lock_file_retains_username_and_supports_legacy_digest(self):
        """New locks support prefill while old digest-only locks still work."""
        self.assertTrue(ConfigCache.set_auth(self.key, "alice", "secret"))
        self.assertEqual(ConfigCache.get_auth_username(self.key), "alice")
        self.assertTrue(ConfigCache.verify_auth(self.key, "alice", "secret"))

        path, filename = ConfigCache.auth_path(self.key)
        with open(os.path.join(path, filename), "w") as lock_file:
            lock_file.write(make_password("legacy:password"))

        self.assertIsNone(ConfigCache.get_auth_username(self.key))
        self.assertTrue(ConfigCache.verify_auth(self.key, "legacy", "password"))

    @override_settings(
        APPRISE_API_ONLY=True,
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_USER="master",
    )
    def test_api_only_hides_auth_page(self):
        """API-only mode keeps the HTML authentication page unavailable."""
        response = self.client.get("/auth/{}".format(self.key), headers=_MASTER)
        self.assertEqual(response.status_code, 421)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_browser_denials_use_error_pages(self):
        """Browser requests use the login form and keep shared throttling."""
        response = self.client.get("/auth/{}".format(self.key), headers=_BROWSER)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/login?"))
        self.assertNotIn("WWW-Authenticate", response)

        ConfigCache.set_auth(self.key, "alice", "secret")
        cache.set(_auth_throttle_cache_key("127.0.0.1", self.key), 20, timeout=60)
        response = self.client.post(
            "/login",
            data={"username": "alice", "password": "secret", "key": self.key},
            headers=_BROWSER,
        )
        self.assertEqual(response.status_code, 429)
        self.assertIn("Too many login attempts", response.content.decode())
        self.assertEqual(response["Retry-After"], "60")

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_login_form_validation(self):
        """The browser form rejects invalid fields without a Basic challenge."""
        page = self.client.get("/login", headers=_BROWSER)
        self.assertEqual(page.status_code, 200)
        content = page.content.decode()
        self.assertIn('autocomplete="username" autofocus', content)
        self.assertIn('autocomplete="current-password"', content)
        self.assertNotIn("data-1p-ignore", content)
        self.assertIn("browser-login-card", content)
        self.assertIn("browser-login-icon", content)
        self.assertIn('class="page-footer-legal"', content)
        self.assertIn("Licensed under the MIT License.", content)
        self.assertIn(
            "Provide the login credentials required to access the Apprise API.",
            content,
        )
        self.assertEqual(content.count('class="btn-flat value-visibility-toggle"'), 2)
        self.assertEqual(content.count('tabindex="-1"'), 2)
        self.assertIn('aria-controls="id_password"', content)
        self.assertIn('aria-controls="id_key"', content)
        self.assertIn("A Configuration ID is required for non-administrative accounts", content)
        self.assertIn("loginError || document.querySelector('#id_username')", content)
        self.assertIn("novalidate", content)

        not_browser = self.client.post("/login", {"username": "master", "password": "pass"})
        self.assertEqual(not_browser.status_code, 406)

        missing = self.client.post("/login", {"username": "master"}, headers=_BROWSER)
        self.assertEqual(missing.status_code, 400)
        self.assertIn("has-errors", missing.content.decode())

        colon = self.client.post(
            "/login",
            {"username": "master:extra", "password": "pass"},
            headers=_BROWSER,
        )
        self.assertEqual(colon.status_code, 400)

        wrong = self.client.post(
            "/login",
            {"username": "master", "password": "wrong"},
            headers=_BROWSER,
        )
        self.assertEqual(wrong.status_code, 401)
        self.assertIn('class="auth-login-error" role="alert"', wrong.content.decode())
        self.assertNotIn("WWW-Authenticate", wrong)
        self.assertNotIn(WEB_AUTH_COOKIE, wrong.cookies)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_login_prefills_key_from_destination(self):
        """A keyed local destination fills in the optional Config ID."""
        page = self.client.get(
            "/login",
            {"next": "/notify/{}?tags=all".format(self.key)},
            headers=_BROWSER,
        )

        self.assertEqual(page.status_code, 200)
        content = page.content.decode()
        self.assertIn('name="key" value="{}"'.format(self.key), content)
        self.assertIn('type="password"', content)

        # An explicit valid key takes priority over the destination.
        explicit = self.client.get(
            "/login",
            {"next": "/cfg/other_key", "key": self.key},
            headers=_BROWSER,
        )
        self.assertIn(
            'name="key" value="{}"'.format(self.key),
            explicit.content.decode(),
        )

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_shared_login_with_entered_key_opens_configuration(self):
        """A manually entered Config ID gives a shared login a useful destination."""
        ConfigCache.set_auth(self.key, "alice", "secret")

        response = self.client.post(
            "/login",
            {"username": "alice", "password": "secret", "key": self.key},
            headers=_BROWSER,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/cfg/{}".format(self.key))
        self.assertIn(WEB_AUTH_COOKIE, response.cookies)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_password_only_login_accepts_blank_username(self):
        """A configuration login may use a password without a username."""
        ConfigCache.set_auth(self.key, "", "secret")

        response = self.client.post(
            "/login",
            {"username": "", "password": "secret", "key": self.key},
            headers=_BROWSER,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/cfg/{}".format(self.key))
        self.assertIn(WEB_AUTH_COOKIE, response.cookies)

    @override_settings(
        APPRISE_API_ONLY=True,
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_USER="master",
    )
    def test_api_only_hides_login_page(self):
        """API-only mode does not add a browser login surface."""
        response = self.client.get("/login", headers=_BROWSER)
        self.assertEqual(response.status_code, 421)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_other_gui_examples_mask_the_selected_username(self):
        """General examples show the relevant username and a masked password."""
        ConfigCache.set_auth(self.key, "alice", "secret")

        welcome = self.client.get("/?key={}".format(self.key), headers=_MASTER)
        details = self.client.get("/details", headers=_MASTER)

        self.assertIn("alice:****@", welcome.content.decode())
        self.assertNotIn("secret", welcome.content.decode())
        self.assertIn("master:****@", details.content.decode())
        self.assertNotIn("master:pass@", details.content.decode())

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_logout_ends_browser_session_despite_cached_basic_auth(self):
        """Chrome-style cached Basic credentials cannot restore a web login."""
        ConfigCache.set_auth(self.key, "alice", "secret")
        login = self.client.post(
            "/login",
            data={"username": "master", "password": "pass", "next": "/cfg/{}".format(self.key)},
            headers=_BROWSER,
        )
        self.assertEqual(login.status_code, 302)
        self.assertIn(WEB_AUTH_COOKIE, login.cookies)
        self.assertTrue(login.cookies[WEB_AUTH_COOKIE]["httponly"])
        self.assertEqual(login.cookies[WEB_AUTH_COOKIE]["samesite"], "Lax")
        self.assertFalse(login.cookies[WEB_AUTH_COOKIE]["expires"])

        page = self.client.get("/cfg/{}".format(self.key), headers=_BROWSER)
        self.assertEqual(page.status_code, 200)
        self.assertIn("auth-account is-master", page.content.decode())

        response = self.client.get("/logout", headers=_BROWSER)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response.cookies[WEB_AUTH_COOKIE]["max-age"], 0)
        content = response.content.decode()
        self.assertIn("Logged Out", content)
        self.assertNotIn("auth-account", content)

        # Chrome may still send this header, but HTML now requires the cookie.
        refresh = self.client.get(
            "/cfg/{}".format(self.key),
            headers={**_BROWSER, **_MASTER},
        )
        self.assertEqual(refresh.status_code, 302)
        self.assertTrue(refresh.url.startswith("/login?"))

        # API clients still authenticate independently after GUI logout.
        api = self.client.get(
            "/status",
            headers={"accept": "application/json", **_MASTER},
        )
        self.assertEqual(api.status_code, 200)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_login_rejects_external_return_url(self):
        """A successful login cannot redirect the browser to another site."""
        response = self.client.post(
            "/login",
            data={
                "username": "master",
                "password": "pass",
                "next": "https://evil.example.com/collect",
            },
            headers=_BROWSER,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_browser_cookie_never_replaces_api_basic_auth(self):
        """API calls ignore the GUI cookie unless made by the GUI itself."""
        login = self.client.post(
            "/login",
            data={"username": "master", "password": "pass"},
            headers=_BROWSER,
        )
        self.assertEqual(login.status_code, 302)

        api = self.client.get("/status", headers={"accept": "application/json"})
        self.assertEqual(api.status_code, 401)

        api = self.client.get(
            "/status",
            headers={"accept": "application/json", **_MASTER},
        )
        self.assertEqual(api.status_code, 200)

        gui_fetch = self.client.get(
            "/status",
            headers={"accept": "application/json", WEB_AUTH_HEADER: "1"},
        )
        self.assertEqual(gui_fetch.status_code, 200)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_tampered_browser_cookie_is_rejected(self):
        """Changing any signed cookie content returns the browser to login."""
        login = self.client.post(
            "/login",
            data={"username": "master", "password": "pass"},
            headers=_BROWSER,
        )
        value = login.cookies[WEB_AUTH_COOKIE].value
        self.client.cookies[WEB_AUTH_COOKIE] = value[:-1] + ("a" if value[-1] != "a" else "b")

        response = self.client.get("/", headers=_BROWSER)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/login?"))

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_shared_password_change_refreshes_browser_cookie(self):
        """A shared password change replaces and invalidates its old cookie."""
        ConfigCache.set_auth(self.key, "alice", "secret")
        login = self.client.post(
            "/login",
            data={"username": "alice", "password": "secret", "key": self.key},
            headers=_BROWSER,
        )
        old_cookie = login.cookies[WEB_AUTH_COOKIE].value

        missing_current = self.client.post(
            "/auth/{}".format(self.key),
            data='{"username":"alice","password":"new-secret","password_confirm":"new-secret"}',
            content_type="application/json",
            headers={"accept": "application/json", WEB_AUTH_HEADER: "1"},
        )
        self.assertEqual(missing_current.status_code, 400)
        self.assertEqual(missing_current.json()["field"], "current_password")

        invalid_current = self.client.post(
            "/auth/{}".format(self.key),
            data='{"username":"alice","current_password":1,"password":"new-secret","password_confirm":"new-secret"}',
            content_type="application/json",
            headers={"accept": "application/json", WEB_AUTH_HEADER: "1"},
        )
        self.assertEqual(invalid_current.status_code, 400)

        wrong_current = self.client.post(
            "/auth/{}".format(self.key),
            data=(
                '{"username":"alice","current_password":"wrong",'
                '"password":"new-secret","password_confirm":"new-secret"}'
            ),
            content_type="application/json",
            headers={"accept": "application/json", WEB_AUTH_HEADER: "1"},
        )
        self.assertEqual(wrong_current.status_code, 400)
        self.assertEqual(wrong_current.json()["field"], "current_password")
        self.assertTrue(ConfigCache.verify_auth(self.key, "alice", "secret"))

        text_error = self.client.post(
            "/auth/{}".format(self.key),
            data=(
                '{"username":"alice","current_password":"wrong",'
                '"password":"new-secret","password_confirm":"new-secret"}'
            ),
            content_type="application/json",
            headers={"accept": "text/plain", WEB_AUTH_HEADER: "1"},
        )
        self.assertEqual(text_error.status_code, 400)
        self.assertEqual(text_error.content, b"The current password was not accepted")

        unchanged = self.client.post(
            "/auth/{}".format(self.key),
            data=('{"username":"alice","current_password":"secret","password":"secret","password_confirm":"secret"}'),
            content_type="application/json",
            headers={"accept": "application/json", WEB_AUTH_HEADER: "1"},
        )
        self.assertEqual(unchanged.status_code, 400)
        self.assertEqual(unchanged.json()["field"], "password")

        changed = self.client.post(
            "/auth/{}".format(self.key),
            data=(
                '{"username":"alice","current_password":"secret",'
                '"password":"new-secret","password_confirm":"new-secret"}'
            ),
            content_type="application/json",
            headers={"accept": "application/json", WEB_AUTH_HEADER: "1"},
        )

        self.assertEqual(changed.status_code, 200)
        self.assertIn(WEB_AUTH_COOKIE, changed.cookies)
        self.assertNotEqual(changed.cookies[WEB_AUTH_COOKIE].value, old_cookie)
        self.assertEqual(
            self.client.get("/cfg/{}".format(self.key), headers=_BROWSER).status_code,
            200,
        )

        # Replaying the cookie from before the password change must fail.
        self.client.cookies[WEB_AUTH_COOKIE] = old_cookie
        rejected = self.client.get("/cfg/{}".format(self.key), headers=_BROWSER)
        self.assertEqual(rejected.status_code, 302)

    @override_settings(
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_USER="master",
        APPRISE_ADMIN=True,
        APPRISE_STATEFUL_MODE="simple",
    )
    def test_shared_browser_session_stays_with_its_config(self):
        """A shared web login works in the GUI but cannot cross key boundaries."""
        ConfigCache.set_auth(self.key, "alice", "secret")
        login = self.client.post(
            "/login",
            data={
                "username": "alice",
                "password": "secret",
                "key": self.key,
                "next": "/cfg/{}".format(self.key),
            },
            headers=_BROWSER,
        )
        self.assertEqual(login.status_code, 302)

        page = self.client.get("/cfg/{}".format(self.key), headers=_BROWSER)
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("auth-account is-master", page.content.decode())
        self.assertNotIn('id="cfg-list"', page.content.decode())

        config_list = self.client.get("/cfg", headers=_BROWSER)
        self.assertEqual(config_list.status_code, 403)
        other_key = self.client.get("/cfg/some_other_key", headers=_BROWSER)
        self.assertEqual(other_key.status_code, 302)

        welcome = self.client.get("/?key=some_other_key", headers=_BROWSER)
        self.assertEqual(welcome.status_code, 200)
        self.assertIn("alice:****@", welcome.content.decode())

        # Future unkeyed pages do not inherit shared access automatically.
        metrics = self.client.get("/metrics", headers=_BROWSER)
        self.assertEqual(metrics.status_code, 302)

        gui_fetch = self.client.post(
            "/get/{}".format(self.key),
            headers={"accept": "application/json", WEB_AUTH_HEADER: "1"},
        )
        self.assertEqual(gui_fetch.status_code, 204)

        api = self.client.post(
            "/get/{}".format(self.key),
            headers={"accept": "application/json"},
        )
        self.assertEqual(api.status_code, 401)

    def test_login_controls_hidden_when_auth_is_disabled(self):
        """Without Basic Auth, login controls return to the welcome page."""
        for path in ("/login", "/logout"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, "/")
