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
"""Test the Basic Auth presentation shown in the web interface."""

import base64
from contextlib import suppress
import os
import shutil

from django.contrib.auth.hashers import make_password
from django.test import SimpleTestCase
from django.test.utils import override_settings

from ..utils import (
    CONFIG_AUTH_ASSIGNED,
    CONFIG_AUTH_DISABLED,
    CONFIG_AUTH_GLOBAL,
    WEB_AUTH_COOKIE,
    WEB_AUTH_HEADER,
    ConfigCache,
    config_auth_state,
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

    def test_disabled_mode_ignores_a_stale_lock(self):
        """Turning global auth off restores the original open GUI."""
        ConfigCache.set_auth(self.key, "alice", "secret")
        self.assertEqual(config_auth_state(self.key).mode, CONFIG_AUTH_DISABLED)

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

    def test_current_alias_requires_browser_state(self):
        """The private aliases never guess a key when browser state is absent."""
        self.assertEqual(self.client.get("/cfg/@", headers=_BROWSER).status_code, 400)
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/cfg/@", headers={"accept": "application/json"}).status_code, 400)
        self.client.cookies.clear()
        self.assertEqual(self.client.post("/cfg/@", headers={"accept": "application/json"}).status_code, 400)
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/auth/@", headers=_BROWSER).status_code, 400)
        self.client.cookies.clear()
        self.assertEqual(self.client.post("/auth/@").status_code, 400)
        self.client.cookies.clear()
        self.assertEqual(self.client.delete("/auth/@").status_code, 400)

    @override_settings(
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_USER="master",
    )
    def test_master_sees_auth_editor_and_generator(self):
        """The administrator can create the key's first shared login."""
        self.assertEqual(config_auth_state(self.key).mode, CONFIG_AUTH_GLOBAL)
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
        self.assertIn('id="cfg-gen"', content)
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
        # Password and both move IDs use the same concealed-input component.
        self.assertEqual(content.count('class="btn-flat value-visibility-toggle"'), 3)
        self.assertIn(">visibility_off</i>", content)
        self.assertIn(
            "toggle.querySelector('i').textContent = showing ? 'visibility_off' : 'visibility';",
            content,
        )
        self.assertIn(
            "toggle.querySelector('i').textContent = show ? 'visibility' : 'visibility_off';",
            content,
        )
        self.assertIn('type="password" name="from"', content)
        self.assertIn('type="password" name="to"', content)
        self.assertEqual(content.count('data-show-label="Show Config ID"'), 2)
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
        self.assertIn("Save Configuration", content)
        self.assertNotIn("Global administrator credentials are required to save", content)
        self.assertEqual(config_list.status_code, 200)
        list_content = config_list.content.decode()
        self.assertIn("hideMode ? 'visibility' : 'visibility_off'", list_content)
        self.assertNotIn('tabindex="-1"', list_content)
        # One list-wide toggle and both move fields use Config-ID labels.
        self.assertEqual(list_content.count('data-show-label="Show Config ID"'), 3)
        self.assertEqual(list_content.count('data-hide-label="Hide Config ID"'), 3)

    @override_settings(
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_USER="master",
    )
    def test_master_switches_to_locked_key_and_saves(self):
        """Selecting a locked Config ID never reduces administrator access."""
        ConfigCache.set_auth(self.key, "alice", "secret")
        login = self.client.post(
            "/login",
            {"username": "master", "password": "pass"},
            headers=_BROWSER,
        )
        self.assertEqual(login.status_code, 302)

        switched = self.client.post(
            "/cfg/@",
            {"key": self.key},
            headers=_BROWSER,
        )
        self.assertEqual(switched.status_code, 302)
        self.assertEqual(switched.url, "/cfg/@")
        self.assertIn(WEB_AUTH_COOKIE, self.client.cookies)
        self.assertNotEqual(self.client.cookies[WEB_AUTH_COOKIE]["max-age"], 0)

        page = self.client.get("/cfg/@", headers=_BROWSER)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Save Configuration", page.content.decode())
        self.assertNotIn(
            "Global administrator credentials are required to save",
            page.content.decode(),
        )

        saved = self.client.post(
            "/add/{}".format(self.key),
            {"urls": "json://localhost"},
            headers={"accept": "application/json", WEB_AUTH_HEADER: "1"},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(ConfigCache.get(self.key)[0].startswith("json://localhost/"))

    @override_settings(
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_USER="master",
    )
    def test_shared_user_sees_saved_username(self):
        """A key user sees their username but never the saved password."""
        ConfigCache.set_auth(self.key, "alice", "secret")
        self.assertEqual(config_auth_state(self.key).mode, CONFIG_AUTH_ASSIGNED)

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
        self.assertEqual(content.count('class="btn-flat value-visibility-toggle"'), 5)
        self.assertNotIn('id="auth-generate"', content)
        self.assertNotIn('id="auth-remove"', content)
        self.assertNotIn("secret", content)
        self.assertIn("Open Authentication Settings", content)
        self.assertNotIn("auth-account is-master", content)
        self.assertNotIn('id="cfg-gen"', content)

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
        self.assertIn("username: authUsername ? authUsername.value : ''", content)
        self.assertEqual(content.count('class="btn-flat value-visibility-toggle"'), 5)
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
        self.assertEqual(response.json(), {"mode": CONFIG_AUTH_ASSIGNED, "username": "alice"})

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
    def test_browser_denials_use_login_page(self):
        """Browser requests use the login form without a Basic challenge."""
        response = self.client.get("/auth/{}".format(self.key), headers=_BROWSER)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/login?"))
        self.assertNotIn("WWW-Authenticate", response)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_login_form_validation(self):
        """The browser form rejects invalid fields without a Basic challenge."""
        page = self.client.get("/login", headers=_BROWSER)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.cookies[WEB_AUTH_COOKIE]["max-age"], 0)
        self.assertEqual(page.cookies["key"]["max-age"], 0)
        self.assertNotIn("apprise_support_dismissed", page.cookies)
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
        self.assertNotIn('tabindex="-1"', content)
        self.assertIn('aria-controls="id_password"', content)
        self.assertIn('aria-controls="id_key"', content)
        self.assertIn("A Configuration ID is required for non-administrative accounts", content)
        self.assertNotIn('name="key" value="apprise"', content)
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
        self.assertEqual(wrong.cookies[WEB_AUTH_COOKIE]["max-age"], 0)
        self.assertEqual(wrong.cookies["key"]["max-age"], 0)
        # Login cleanup intentionally leaves the support-banner cycle alone.
        self.assertNotIn("apprise_support_dismissed", wrong.cookies)

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
        self.assertEqual(response.url, "/cfg/@")
        self.assertIn(WEB_AUTH_COOKIE, response.cookies)

        auth_destination = self.client.post(
            "/login",
            {
                "username": "alice",
                "password": "secret",
                "key": self.key,
                "next": "/auth/{}".format(self.key),
            },
            headers=_BROWSER,
        )
        self.assertEqual(auth_destination.url, "/auth/@")

        welcome_destination = self.client.post(
            "/login",
            {
                "username": "alice",
                "password": "secret",
                "key": self.key,
                "next": "/",
            },
            headers=_BROWSER,
        )
        # Only matching keyed destinations are shortened to cookie aliases.
        self.assertEqual(welcome_destination.url, "/")

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
        self.assertEqual(response.url, "/cfg/@")
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
        self.assertEqual(response.cookies["key"]["max-age"], 0)
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
    def test_master_login_replaces_a_previous_config_cookie(self):
        """A new administrator does not inherit the prior user's selected ID."""
        self.client.cookies["key"] = "previous_private_key"

        response = self.client.post(
            "/login",
            data={"username": "master", "password": "pass"},
            headers=_BROWSER,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.cookies["key"].value, "apprise")

    @override_settings(APPRISE_CONFIG_LOCK=True)
    def test_config_lock_keeps_tabs_in_their_grid_columns(self):
        """Locked tabs retain normal sizing while remaining disabled."""
        response = self.client.get("/cfg/{}".format(self.key), headers=_BROWSER)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertEqual(content.count('class="tab disabled col s3"'), 2)
        self.assertNotIn("disabledcol", content)
        self.assertIn("Choose at least one tag", content)
        self.assertIn("Required Tag", content)
        self.assertIn("commitNotifyTags();", content)
        self.assertIn("chipInput.addEventListener('input'", content)
        self.assertIn("chipInput.addEventListener('blur'", content)
        self.assertIn("commitLockedNotifyTags(true)", content)
        self.assertIn("rawValue.split(/[\\s,]+/)", content)
        self.assertIn("getSelectedNotifyTargetCount() === 0", content)
        self.assertNotIn('class="auth-editor-card auth-move-card"', content)

    @override_settings(
        APPRISE_CONFIG_LOCK=True,
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_USER="master",
    )
    def test_locked_admin_keeps_configuration_editor(self):
        """The global administrator retains the complete configuration UI."""
        login = self.client.post(
            "/login",
            data={"username": "master", "password": "pass"},
            headers=_BROWSER,
        )
        self.assertEqual(login.status_code, 302)

        response = self.client.get("/cfg/{}".format(self.key), headers=_BROWSER)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('id="addconfig"', content)
        self.assertIn('id="url-list"', content)
        self.assertNotIn("Your Configuration Is Locked", content)

    def test_unlocked_tags_keep_saved_tag_autocomplete(self):
        """Unlocked tags retain autocomplete without delimiter handling."""
        response = self.client.get("/cfg/{}".format(self.key), headers=_BROWSER)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("notifyTagOptionMap", content)
        self.assertIn("decorateNotifyAutocompleteOptions", content)
        self.assertNotIn("commitLockedNotifyTags", content)
        self.assertNotIn("rawValue.split(/[\\s,]+/)", content)

    @override_settings(
        APPRISE_CONFIG_LOCK=True,
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_USER="master",
    )
    def test_locked_admin_browser_can_poll_health(self):
        """CONFIG_LOCK does not interfere with an administrator health poll."""
        login = self.client.post(
            "/login",
            data={"username": "master", "password": "pass"},
            headers=_BROWSER,
        )
        self.assertEqual(login.status_code, 302)

        response = self.client.get(
            "/status",
            headers={"accept": "application/json", WEB_AUTH_HEADER: "1"},
        )

        self.assertEqual(response.status_code, 200)
        status = response.json()
        self.assertTrue(status["config_lock"])
        self.assertFalse(status["status"]["can_write_config"])
        self.assertEqual(status["status"]["details"], ["OK"])
        self.assertEqual(status["privilege"], "admin")

        auth_page = self.client.get("/auth/{}".format(self.key), headers=_BROWSER)
        self.assertEqual(auth_page.status_code, 200)
        content = auth_page.content.decode()
        self.assertIn('class="auth-editor-card auth-move-card"', content)
        # The browser suppresses only the expected CONFIG_LOCK write notice.
        self.assertIn("data.config_lock !== true", content)
        self.assertNotIn("data.config_lock === false", content)

    @override_settings(
        APPRISE_CONFIG_LOCK=True,
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_USER="master",
    )
    def test_locked_shared_browser_does_not_load_move_tools(self):
        """A configuration user does not receive unavailable move markup or JavaScript."""
        ConfigCache.set_auth(self.key, "alice", "secret")
        login = self.client.post(
            "/login",
            data={"username": "alice", "password": "secret", "key": self.key},
            headers=_BROWSER,
        )
        self.assertEqual(login.status_code, 302)

        response = self.client.get("/auth/{}".format(self.key), headers=_BROWSER)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn('class="auth-editor-card auth-move-card"', content)
        self.assertNotIn("const moveForm", content)

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
        page_content = page.content.decode()
        self.assertNotIn("auth-account is-master", page_content)
        self.assertNotIn('id="cfg-list"', page_content)
        self.assertNotIn('id="cfg-gen"', page_content)
        self.assertIn('id="config-id-select-form"', page_content)
        self.assertNotIn("Save Configuration", page_content)
        self.assertIn(
            "Global administrator credentials are required to save",
            page_content,
        )
        self.assertIn("'X-Apprise-Config-ID': '{}',".format(self.key), page_content)

        config_list = self.client.get("/cfg", headers=_BROWSER)
        self.assertEqual(config_list.status_code, 403)
        other_key = self.client.get("/cfg/some_other_key", headers=_BROWSER)
        self.assertEqual(other_key.status_code, 302)

        welcome = self.client.get("/?key=some_other_key", headers=_BROWSER)
        self.assertEqual(welcome.status_code, 200)
        welcome_content = welcome.content.decode()
        self.assertIn("alice:****@", welcome_content)
        self.assertIn('href="/cfg/@"', welcome_content)
        self.assertIn('href="/auth/@"', welcome_content)
        self.assertIn('href="/status/@"', welcome_content)
        self.assertIn("'X-Apprise-Config-ID': '{}',".format(self.key), welcome_content)
        self.assertEqual(welcome.cookies["key"].value, self.key)

        # Future unkeyed pages do not inherit shared access automatically.
        metrics = self.client.get("/metrics", headers=_BROWSER)
        self.assertEqual(metrics.status_code, 302)

        gui_fetch = self.client.post(
            "/get/{}".format(self.key),
            headers={"accept": "application/json", WEB_AUTH_HEADER: "1"},
        )
        self.assertEqual(gui_fetch.status_code, 204)

        # Shared health requests must name the configuration they authenticate.
        health_without_key = self.client.get(
            "/status",
            headers={"accept": "application/json", WEB_AUTH_HEADER: "1"},
        )
        self.assertEqual(health_without_key.status_code, 401)
        health_with_key = self.client.get(
            "/status",
            headers={
                "accept": "application/json",
                WEB_AUTH_HEADER: "1",
                "X-Apprise-Config-ID": self.key,
            },
        )
        self.assertEqual(health_with_key.status_code, 200)

        # Navigation uses the signed key without exposing it in the address.
        browser_health = self.client.get("/status/@", headers=_BROWSER)
        self.assertEqual(browser_health.status_code, 200)

        api = self.client.post(
            "/get/{}".format(self.key),
            headers={"accept": "application/json"},
        )
        self.assertEqual(api.status_code, 401)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_current_aliases_follow_signed_browser_state(self):
        """Cookie-backed aliases hide the key while explicit URLs keep working."""
        ConfigCache.set_auth(self.key, "alice", "secret")
        login = self.client.post(
            "/login",
            {"username": "alice", "password": "secret", "key": self.key},
            headers=_BROWSER,
        )
        self.assertEqual(login.url, "/cfg/@")

        config_page = self.client.get("/cfg/@", headers=_BROWSER)
        auth_page = self.client.get("/auth/@", headers=_BROWSER)
        health_page = self.client.get("/status/@", headers=_BROWSER)
        self.assertEqual(config_page.status_code, 200)
        self.assertEqual(auth_page.status_code, 200)
        self.assertEqual(health_page.status_code, 200)
        self.assertContains(config_page, self.key)
        self.assertContains(auth_page, self.key)

        # The current-key alias is only for a signed browser session.
        api_health = self.client.get("/status/@", headers={"accept": "application/json"})
        self.assertEqual(api_health.status_code, 401)

        # The original address remains available for bookmarks and clients
        # that do not use the convenience alias.
        explicit = self.client.get("/cfg/{}".format(self.key), headers=_BROWSER)
        self.assertEqual(explicit.status_code, 200)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_switching_users_requires_the_destination_login(self):
        """A shared browser session cannot cross into another user's key."""
        other_key = "auth_gui_other_key"
        try:
            ConfigCache.put(self.key, "json://first.example", "text")
            ConfigCache.set_auth(self.key, "alice", "secret")
            ConfigCache.put(other_key, "json://second.example", "text")
            ConfigCache.set_auth(other_key, "bob", "other-secret")
            self.client.post(
                "/login",
                {"username": "alice", "password": "secret", "key": self.key},
                headers=_BROWSER,
            )

            # API and browser fetches both reject the first user's credentials
            # before any content belonging to the second key is returned.
            api = self.client.post(
                "/get/{}".format(other_key),
                headers={"accept": "application/json", **_SHARED},
            )
            browser_fetch = self.client.post(
                "/get/{}".format(other_key),
                headers={"accept": "application/json", WEB_AUTH_HEADER: "1"},
            )
            self.assertEqual(api.status_code, 401)
            self.assertEqual(browser_fetch.status_code, 401)
            self.assertNotIn("second.example", api.content.decode())
            self.assertNotIn("second.example", browser_fetch.content.decode())

            switched = self.client.post(
                "/cfg/@",
                {"key": other_key},
                headers=_BROWSER,
            )
            self.assertEqual(switched.status_code, 302)
            self.assertEqual(switched.url, "/login?next=%2Fcfg%2F%40")
            self.assertNotIn(other_key, switched.url)
            self.assertEqual(switched.cookies[WEB_AUTH_COOKIE]["max-age"], 0)
            self.assertEqual(switched.cookies["key"].value, other_key)

            login_page = self.client.get(switched.url, headers=_BROWSER)
            self.assertEqual(login_page.status_code, 200)
            self.assertIn(
                'name="key" value="{}"'.format(other_key),
                login_page.content.decode(),
            )

            rejected = self.client.post(
                "/login",
                {"username": "alice", "password": "secret", "key": other_key},
                headers=_BROWSER,
            )
            self.assertEqual(rejected.status_code, 401)

            accepted = self.client.post(
                "/login",
                {"username": "bob", "password": "other-secret", "key": other_key},
                headers=_BROWSER,
            )
            self.assertEqual(accepted.status_code, 302)
            self.assertEqual(accepted.url, "/cfg/@")
            loaded = self.client.post(
                "/get/{}".format(other_key),
                headers={"accept": "application/json", WEB_AUTH_HEADER: "1"},
            )
            self.assertEqual(loaded.status_code, 200)
            self.assertEqual(loaded.json()["config"], "json://second.example")
        finally:
            ConfigCache.clear(other_key)
            ConfigCache.clear_auth(other_key)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_switching_identical_locks_requires_login(self):
        """A copied lock never extends a browser session to another Config ID."""
        other_key = "auth_gui_same_login"
        try:
            ConfigCache.set_auth(self.key, "alice", "secret")
            source_path, source_name = ConfigCache.auth_path(self.key)
            target_path, target_name = ConfigCache.auth_path(other_key)
            os.makedirs(target_path, exist_ok=True)
            shutil.copyfile(
                os.path.join(source_path, source_name),
                os.path.join(target_path, target_name),
            )
            self.client.post(
                "/login",
                {"username": "alice", "password": "secret", "key": self.key},
                headers=_BROWSER,
            )

            switched = self.client.post(
                "/cfg/@",
                {"key": other_key},
                headers=_BROWSER,
            )

            self.assertEqual(switched.status_code, 302)
            self.assertTrue(switched.url.startswith("/login?"))
            self.assertEqual(switched.cookies[WEB_AUTH_COOKIE]["max-age"], 0)
        finally:
            ConfigCache.clear(other_key)
            ConfigCache.clear_auth(other_key)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_current_auth_alias_supports_update_and_admin_delete(self):
        """The private alias supports the same credential operations as a keyed URL."""
        ConfigCache.set_auth(self.key, "alice", "secret")
        self.client.post(
            "/login",
            {"username": "alice", "password": "secret", "key": self.key},
            headers=_BROWSER,
        )
        changed = self.client.post(
            "/auth/@",
            data=(
                '{"username":"alice","current_password":"secret",'
                '"password":"new-secret","password_confirm":"new-secret"}'
            ),
            content_type="application/json",
            headers={"accept": "application/json", WEB_AUTH_HEADER: "1"},
        )
        self.assertEqual(changed.status_code, 200)
        self.assertTrue(ConfigCache.verify_auth(self.key, "alice", "new-secret"))

        # An administrator can use the same alias after selecting this key.
        self.client.cookies.clear()
        self.client.post("/login", {"username": "master", "password": "pass"}, headers=_BROWSER)
        self.client.get("/cfg/{}".format(self.key), headers=_BROWSER)
        removed = self.client.delete(
            "/auth/@",
            headers={"accept": "application/json", WEB_AUTH_HEADER: "1"},
        )
        self.assertEqual(removed.status_code, 200)
        self.assertFalse(ConfigCache.has_auth(self.key))

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_shared_move_refreshes_current_browser_key(self):
        """Moving a shared configuration keeps its browser session signed in."""
        destination = "auth_gui_moved_key"
        try:
            ConfigCache.put(self.key, "json://localhost", "text")
            ConfigCache.set_auth(self.key, "alice", "secret")
            self.client.post(
                "/login",
                {"username": "alice", "password": "secret", "key": self.key},
                headers=_BROWSER,
            )

            moved = self.client.post(
                "/move/{}".format(self.key),
                {"from": self.key, "to": destination},
                headers={"accept": "application/json", WEB_AUTH_HEADER: "1"},
            )
            self.assertEqual(moved.status_code, 200)
            self.assertIn(WEB_AUTH_COOKIE, moved.cookies)
            self.assertEqual(moved.cookies["key"].value, destination)
            self.assertEqual(self.client.get("/auth/@", headers=_BROWSER).status_code, 200)
        finally:
            ConfigCache.clear(destination)
            ConfigCache.clear_auth(destination)

    def test_login_controls_hidden_when_auth_is_disabled(self):
        """Without Basic Auth, login controls return to the welcome page."""
        for path in ("/login", "/logout"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, "/")
