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
"""Drive the web interface in a real browser.

- Needs Playwright and a Chromium (Playwright's own, or an installed Chrome).
- Skipped when neither is there, unless APPRISE_BROWSER_TESTS=required (CI).
- Always skipped with APPRISE_BROWSER_TESTS=skip.
"""

import base64
import json
import os
import re
import sys
import unittest
from unittest import mock

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import resolve

try:
    from playwright.sync_api import Error as PlaywrightError, sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

KEY = "browser_tag_field"

# Tag names that would run script, or upset a plain object, if shown as markup.
HOSTILE_TAGS = [
    "<img/src/onerror=window.__xss=1>",
    "<svg/onload=window.__xss=2>",
    "&ltb&gt",
    "quote\"tag'",
    "__proto__",
    "constructor",
]

# Anything a tag name turned into a live element, in the chosen tags or the
# suggestions (the only places tag names are drawn).
MARKUP_FOUND = """() => {
  const drawn = '#notify .chips .chip, #notify .autocomplete-content li';
  const inside = [...document.querySelectorAll(drawn)].flatMap(item => [...item.querySelectorAll('*')]);
  const live = inside.filter(element => element.matches('img, svg, script, iframe, object'));
  const handlers = inside.filter(function (element) {
    return [...element.attributes].some(function (attribute) { return attribute.name.startsWith('on'); });
  });
  return live.length + handlers.length;
}"""

# Exactly one configuration row is left after a search.
SHOWN_ROWS_IS_ONE = (
    "document.querySelectorAll('.config-list-item:not(.config-list-header):not(.list-search-hidden)').length === 1"
)

# The tags chosen so far, as the tag field holds them.
CHOSEN_TAGS = "() => M.Chips.getInstance(document.querySelector('#notify .chips')).chipsData.map(c => c.id)"


def _reminder(name):
    """Return a daily reminder, sent to the "family" tag, as JSON."""
    return json.dumps({"name": name, "body": "Time", "tag": "family", "when": {"tz": "Etc/UTC", "cron": "0 0 9"}})


def _server_cache():
    """Return the ConfigCache the server's views use.

    - The views are loaded under a second module name, so they hold their own
      copy; anything stored or patched on another copy does not reach them.
    """
    return sys.modules[resolve("/cfg/").func.__module__].ConfigCache


def _stored_keys(keys):
    """Pretend these Config IDs are stored, where the server's views look them up."""
    return mock.patch.object(_server_cache(), "keys", return_value=keys)


def _launch(playwright):
    """Start Playwright's Chromium, or else an installed Chrome; None when neither starts."""
    for options in ({}, {"channel": "chrome"}):
        try:
            return playwright.chromium.launch(**options)
        except PlaywrightError:
            continue
    return None  # pragma: no cover


# The page's scripts and styles, served the same way as by the development server.
@override_settings(STATICFILES_DIRS=(os.path.join(settings.BASE_DIR, "static"),))
class BrowserTestCase(StaticLiveServerTestCase):
    """Starts one browser for a class of tests, and fails a test on any script error."""

    # The app keeps no database.
    databases = set()

    # How configurations are stored during each test (hash, simple or disabled).
    store_mode = "hash"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        mode = os.environ.get("APPRISE_BROWSER_TESTS")
        if mode == "skip":  # pragma: no cover
            super().tearDownClass()
            raise unittest.SkipTest("APPRISE_BROWSER_TESTS=skip")

        required = mode == "required"
        cls.playwright = sync_playwright().start() if sync_playwright else None
        cls.browser = _launch(cls.playwright) if cls.playwright else None
        if cls.browser is None:  # pragma: no cover
            if cls.playwright:
                cls.playwright.stop()
            super().tearDownClass()
            if required:
                raise RuntimeError("APPRISE_BROWSER_TESTS=required, but no browser could be started")
            raise unittest.SkipTest("Playwright and a Chromium browser are needed for browser tests")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()

    def setUp(self):
        # The storage mode is set on the server's copy for every test, so the
        # order the tests run in never changes what the server sees.
        self.cache = _server_cache()
        store_mode = mock.patch.object(self.cache, "mode", self.store_mode)
        store_mode.start()
        self.addCleanup(store_mode.stop)

        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.on("console", self.console_error)
        self.page.on("response", self.failed_response)

    def tearDown(self):
        self.context.close()
        # A test passes only when no page it opened reported a script error.
        self.assertEqual(self.errors, [])

    def console_error(self, message):
        """Keep each error the page logs, except the browser's note about a failed download."""
        # A failed download is kept by failed_response(), with its address.
        if message.type == "error" and not message.text.startswith("Failed to load resource"):
            self.errors.append(message.text)

    def failed_response(self, response):
        """Keep each file or request the page could not load; the page itself may be an error page."""
        if response.status >= 400 and response.request.resource_type != "document":
            self.errors.append(f"{response.status} {response.request.method} {response.url}")

    def open(self, path):
        """Open a page of the live server and wait for it to settle."""
        self.page.goto(f"{self.live_server_url}{path}")
        self.page.wait_for_load_state("networkidle")


class TagFieldBrowserTests(BrowserTestCase):
    """The tag field still works, and stays safe, with the bundled Materialize."""

    def setUp(self):
        super().setUp()
        tags = "\n".join(f"        - {json.dumps(tag)}" for tag in ["family", *HOSTILE_TAGS])
        config = f"urls:\n  - json://localhost:\n      tag:\n{tags}\n"
        self.assertTrue(self.cache.put(KEY, config, "yaml"))
        self.open(f"/cfg/{KEY}#notify")
        self.field = self.page.locator("#notify .chips input")
        self.field.wait_for()

    def tearDown(self):
        self.cache.clear(KEY)
        super().tearDown()

    def suggestions(self, typed=""):
        """Return the text of each suggestion shown under the tag field, once they match what was typed."""
        self.page.wait_for_function(
            """(typed) => {
              const items = [...document.querySelectorAll('#notify .autocomplete-content li')];
              return items.length > 0 && items.every(item => item.textContent.includes(typed));
            }""",
            arg=typed,
        )
        return [text.strip() for text in self.page.locator("#notify .autocomplete-content li").all_text_contents()]

    def test_hostile_tag_names_are_shown_as_plain_text(self):
        """Tag names that look like markup are listed as text, and none of them runs."""
        self.field.click()
        shown = self.suggestions()
        for tag in HOSTILE_TAGS:
            self.assertIn(tag, shown)
        self.assertEqual(self.page.evaluate(MARKUP_FOUND), 0)

        # Choosing one adds it as a tag, still as text
        self.field.press_sequentially("onerror")
        self.suggestions("onerror")
        self.page.locator("#notify .autocomplete-content li", has_text="onerror").first.click()
        self.assertIn(HOSTILE_TAGS[0], self.page.evaluate(CHOSEN_TAGS))
        self.assertEqual(self.page.evaluate(MARKUP_FOUND), 0)
        self.assertIsNone(self.page.evaluate("window.__xss"))

    def test_tags_are_suggested_added_and_removed(self):
        """Typing narrows the suggestions, Enter adds a tag without sending the form, and a tag can be removed."""
        self.field.click()
        self.field.press_sequentially("fam")
        self.assertEqual(self.suggestions("fam"), ["family"])

        page_url = self.page.url
        self.field.press_sequentially("ily")
        self.field.press("Enter")
        self.page.wait_for_function(f"({CHOSEN_TAGS})().includes('family')")
        self.assertEqual(self.page.url, page_url)

        # Clicking a suggestion adds it, not the text typed so far, and a name
        # such as __proto__ is an ordinary tag
        self.field.press_sequentially("proto")
        self.suggestions("proto")
        self.page.locator("#notify .autocomplete-content li", has_text="__proto__").first.click()
        self.assertEqual(self.page.evaluate(CHOSEN_TAGS), ["family", "__proto__"])

        self.page.locator("#notify .chips .chip", has_text="family").locator(".close").click()
        self.assertEqual(self.page.evaluate(CHOSEN_TAGS), ["__proto__"])


@override_settings(
    APPRISE_AUTH_REQUIRED=True,
    APPRISE_BASIC_AUTH_TOKEN=base64.b64encode(b"master:pass").decode(),
    APPRISE_USER="master",
    APPRISE_ADMIN=True,
    APPRISE_STATEFUL_MODE="simple",
)
class PageBrowserTests(BrowserTestCase):
    """Each page loads and its main controls work, signed in as the administrator."""

    store_mode = "simple"

    def setUp(self):
        super().setUp()
        self.assertTrue(self.cache.put(KEY, "urls:\n  - json://localhost:\n      tag: family\n", "yaml"))
        self.assertTrue(self.cache.set_auth(KEY, "alice", "secret", access="user"))
        self.open("/login")
        self.page.fill("input[name=username]", "master")
        self.page.fill("input[name=password]", "pass")
        with self.page.expect_navigation():
            self.page.press("input[name=password]", "Enter")

    def tearDown(self):
        self.cache.clear(KEY)
        self.cache.clear_auth(KEY)
        super().tearDown()

    def test_every_page_loads_without_script_errors(self):
        """The main pages, and each configuration tab, run without a script error."""
        with _stored_keys([KEY]):
            for path in ("/", "/details", "/cfg", "/reminders", f"/auth/{KEY}"):
                self.open(path)
        self.open(f"/cfg/{KEY}")
        for tab in ("#config", "#review", "#notify", "#reminders", "#overview"):
            self.page.click(f".tabs.config-overview a[href='{tab}']")
            self.page.wait_for_selector(f"{tab}.active, {tab}[style*='block']", state="attached")

        # Every menu keeps the label the server gave it, and opens when clicked.
        served = self.page.request.get(f"{self.live_server_url}/cfg/{KEY}").text()
        labelled = re.findall(r'<label for="([^"]+)"', served)
        self.page.click(".tabs.config-overview a[href='#notify']")
        lost = self.page.evaluate(
            """(labelled) => [...document.querySelectorAll('select:not(.browser-default)')]
              .filter(s => labelled.includes(s.id) && !document.querySelector('label[for="' + s.id + '"]'))
              .map(s => s.id)""",
            labelled,
        )
        self.assertEqual(lost, [])
        self.page.locator("#notify .select-wrapper input.select-dropdown").first.click()
        self.page.locator("#notify .select-wrapper .dropdown-content").first.wait_for()

    def test_configuration_format_label_sits_above_its_menu(self):
        """The Format label on the Configuration tab is shown above its menu, not on top of it."""
        self.open(f"/cfg/{KEY}#config")
        gap = self.page.evaluate(
            """() => {
              const label = document.querySelector('#addconfig label[for="id_format"]');
              const menu = label.parentElement.querySelector('.select-wrapper');
              return menu.getBoundingClientRect().top - label.getBoundingClientRect().bottom;
            }"""
        )
        self.assertGreaterEqual(gap, 0)

    def test_reset_dialog_starts_with_removing_the_configuration(self):
        """Reset Access removes the configuration unless switched off, which then offers to keep reminders."""
        self.open(f"/auth/{KEY}")
        self.page.click("#auth-remove")
        switch = self.page.locator("#swal-reset-configuration")
        switch.wait_for()
        self.assertEqual(switch.get_attribute("aria-checked"), "true")
        self.assertFalse(self.page.is_visible("#swal-keep-reminders"))
        self.assertEqual(self.page.inner_text(".swal2-confirm"), "Reset Access and Configuration")

        switch.click()
        self.assertEqual(switch.get_attribute("aria-checked"), "false")
        self.assertTrue(self.page.is_visible("#swal-keep-reminders"))
        self.assertEqual(self.page.inner_text(".swal2-confirm"), "Reset User Access")
        # Leave without resetting anything
        self.page.click(".swal2-cancel")
        self.assertTrue(self.cache.get_auth_record(KEY))

    def test_details_search_and_first_field(self):
        """The service search filters and clears, and choosing a service moves to its first field."""
        self.open("/details")
        self.page.fill("#details-search", "no-such-service")
        self.page.locator("#details-search-no-match").wait_for()
        self.page.click(".list-search-clear")
        self.page.locator("#details-search-no-match").wait_for(state="hidden")
        self.assertEqual(self.page.input_value("#details-search"), "")

        # Africa's Talking has one schema, shown as text that focus passes by
        self.page.fill("#details-search", "africas")
        self.page.locator(".details-service:visible").first.click()
        self.page.wait_for_function("document.activeElement && document.activeElement.closest('#details-service')")
        focused = self.page.evaluate("document.activeElement.closest('[data-field]').dataset.field")
        self.assertNotEqual(focused, "schema")
        self.assertTrue(self.page.locator("[data-field=schema] input").evaluate("i => i.readOnly"))

    def test_configuration_list_search(self):
        """The configuration list search finds a row by its Config ID or username, and clears."""
        rows = ".config-list-item:not(.config-list-header):visible"
        with _stored_keys([KEY, "other-config", "third-config"]):
            self.open("/cfg")
        self.assertEqual(self.page.locator(rows).count(), 3)
        self.page.fill("#config-list-search", "ALICE")
        self.page.wait_for_function(SHOWN_ROWS_IS_ONE)
        self.page.fill("#config-list-search", "other")
        self.page.wait_for_function(SHOWN_ROWS_IS_ONE)
        self.page.click(".list-search-clear")
        self.assertEqual(self.page.locator(rows).count(), 3)

    def test_long_configuration_list_shows_more_on_request(self):
        """The list shows 100 rows at a time, with a Show More button that counts the rest."""
        rows = ".config-list-item:not(.config-list-header):visible"
        with _stored_keys([f"config-{index:03d}" for index in range(101)]):
            self.open("/cfg")
        self.assertEqual(self.page.locator(rows).count(), 100)
        more = self.page.locator("#config-list-search-more")
        self.assertEqual(more.locator(".list-search-more-count").inner_text(), "(1)")

        more.click()
        self.assertEqual(self.page.locator(rows).count(), 101)
        self.assertFalse(more.is_visible())

        # A new search starts from the first page again, and finds every match
        self.page.fill("#config-list-search", "config-100")
        self.page.wait_for_function(SHOWN_ROWS_IS_ONE)
        self.assertFalse(more.is_visible())

    def test_reminder_list_search(self):
        """The reminder list search finds a reminder by its title."""
        for name in ("Standup", "Payroll"):
            response = self.client.post(
                f"/reminder/{KEY}/",
                _reminder(name),
                content_type="application/json",
                headers={"authorization": "Basic " + base64.b64encode(b"master:pass").decode()},
            )
            self.assertEqual(response.status_code, 200, response.content)

        cards = "#reminder-list .reminder-card:visible"
        self.open("/reminders")
        self.page.locator(cards).first.wait_for()
        self.assertEqual(self.page.locator(cards).count(), 2)
        self.page.fill("#reminder-list-search", "pay")
        self.page.wait_for_function(
            "document.querySelectorAll('#reminder-list .reminder-card:not(.list-search-hidden)').length === 1"
        )
        self.assertIn("Payroll", self.page.locator(cards).inner_text())

    def test_dice_in_the_instructions_matches_its_button(self):
        """The dice drawn in the instructions has the button's picture and name."""
        self.open(f"/auth/{KEY}")
        self.page.click(".auth-generate summary")
        dice = self.page.locator(".auth-generate-dice")
        self.assertEqual(dice.get_attribute("aria-label"), "Randomize the Selected Fields")
        self.assertEqual(dice.locator("svg").count(), 1)

    def test_saving_a_login_shows_the_reminder_limits(self):
        """The reminder limits card waits until the Config ID has a login, then shows without a reload."""
        key = "browser_new_login"
        self.assertTrue(self.cache.put(key, "json://localhost", "text"))
        try:
            self.open(f"/auth/{key}")
            card = self.page.locator("#auth-reminder-limits")
            self.assertFalse(card.is_visible())
            self.page.fill("#id_username", "bob")
            self.page.fill("#id_password", "secret-password")
            self.page.click(".auth-form-actions button[type=submit]")
            card.wait_for()
            # Close the dialog that confirms the save
            self.page.locator(".swal2-confirm").click()

        finally:
            self.cache.clear(key)
            self.cache.clear_auth(key)


# The administrator's login, for the settings that turn on logins.
SIGN_IN = {
    "APPRISE_AUTH_REQUIRED": True,
    "APPRISE_BASIC_AUTH_TOKEN": base64.b64encode(b"master:pass").decode(),
    "APPRISE_USER": "master",
}

# Every page in the web interface, as a visitor would open it.
PAGES = (
    "/",
    "/login",
    "/details",
    "/details?all=yes",
    "/cfg",
    "/cfg/@",
    f"/cfg/{KEY}",
    "/reminders",
    f"/auth/{KEY}",
    "/auth/@",
    "/no-such-page",
)

# Each scenario turns parts of the site on or off, then signs someone in.
# - settings: the settings to use
# - mode: how configurations are stored (hash, simple or disabled)
# - access: the access the Config ID's own login has (none when not set)
# - who: admin, user (the Config ID's own login), or none
# - look: optional, "phone" for a dark, right-to-left page at phone width
SCENARIOS = {
    "open": {},
    "open_simple_store": {"mode": "simple"},
    "open_without_saved_configurations": {"mode": "disabled"},
    "open_without_stateless": {"settings": {"APPRISE_STATELESS_MODE": "disabled"}},
    "open_without_any_storage": {"mode": "disabled", "settings": {"APPRISE_STATELESS_MODE": "disabled"}},
    "open_with_config_lock": {"settings": {"APPRISE_CONFIG_LOCK": True}},
    "open_without_reminders": {"settings": {"APPRISE_REMINDER_ENABLED": False}},
    "open_without_admin_list": {"settings": {"APPRISE_ADMIN": False}},
    "open_without_templates": {"settings": {"APPRISE_ALLOW_TEMPLATES": False}},
    "open_api_only": {"settings": {"APPRISE_API_ONLY": True}},
    "open_on_a_phone": {"look": "phone"},
    "login_signed_out": {"settings": SIGN_IN, "access": "user"},
    "login_admin": {"settings": SIGN_IN, "access": "user", "who": "admin"},
    "login_admin_without_a_config_login": {"settings": SIGN_IN, "who": "admin"},
    "login_admin_with_config_lock": {"settings": {**SIGN_IN, "APPRISE_CONFIG_LOCK": True}, "who": "admin"},
    "login_admin_without_reminders": {"settings": {**SIGN_IN, "APPRISE_REMINDER_ENABLED": False}, "who": "admin"},
    "login_admin_without_stateless": {"settings": {**SIGN_IN, "APPRISE_STATELESS_MODE": "disabled"}, "who": "admin"},
    "login_admin_without_saved_configurations": {"settings": SIGN_IN, "mode": "disabled", "who": "admin"},
    "login_admin_without_admin_list": {"settings": {**SIGN_IN, "APPRISE_ADMIN": False}, "who": "admin"},
    "login_admin_disabled_config": {"settings": SIGN_IN, "access": "disabled", "who": "admin"},
    "login_admin_on_a_phone": {"settings": SIGN_IN, "access": "user", "who": "admin", "look": "phone"},
    "login_user": {"settings": SIGN_IN, "access": "user", "who": "user"},
    "login_user_with_config_lock": {
        "settings": {**SIGN_IN, "APPRISE_CONFIG_LOCK": True},
        "access": "user",
        "who": "user",
    },
    "login_user_without_reminders": {
        "settings": {**SIGN_IN, "APPRISE_REMINDER_ENABLED": False},
        "access": "user",
        "who": "user",
    },
    "login_user_without_stateless": {
        "settings": {**SIGN_IN, "APPRISE_STATELESS_MODE": "disabled"},
        "access": "user",
        "who": "user",
    },
    "login_locked_user": {"settings": SIGN_IN, "access": "locked", "who": "user"},
    "login_public_visitor": {"settings": SIGN_IN, "access": "public"},
}


class SettingsBrowserTests(BrowserTestCase):
    """Every page runs without a script error, whichever parts of the site are turned off."""

    def check_pages(self, scenario):
        """Open every page, and each configuration tab, under one scenario."""
        mode = scenario.get("mode", "hash")
        with (
            mock.patch.object(self.cache, "mode", mode),
            override_settings(APPRISE_STATEFUL_MODE=mode),
        ):
            try:
                self.prepare(scenario)
                with override_settings(**scenario.get("settings", {})):
                    self.sign_in(scenario.get("who"))
                    with _stored_keys([KEY]):
                        for path in PAGES:
                            self.open(path)
                            self.assertEqual(self.errors, [], path)
                            if path == f"/cfg/{KEY}":
                                self.open_each_tab()
                            if scenario.get("look") == "phone":
                                self.open_the_menu()

            finally:
                self.clean_up()

    def prepare(self, scenario):
        """Store the configuration, its login, a reminder, and the page's look."""
        if scenario.get("mode") == "disabled":
            return

        self.assertTrue(self.cache.put(KEY, "urls:\n  - json://localhost:\n      tag: family, team\n", "yaml"))
        response = self.client.post(
            f"/reminder/{KEY}/",
            _reminder("Standup"),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        if scenario.get("access"):
            self.assertTrue(self.cache.set_auth(KEY, "alice", "secret", access=scenario["access"]))

        if scenario.get("look") == "phone":
            # Arabic is written right to left
            self.page.set_viewport_size({"width": 390, "height": 844})
            self.context.add_cookies(
                [
                    {"name": "theme", "value": "dark", "url": self.live_server_url},
                    {"name": settings.LANGUAGE_COOKIE_NAME, "value": "ar", "url": self.live_server_url},
                ]
            )

    def sign_in(self, who):
        """Log in through the login page as the administrator or the Config ID's own user."""
        if not who:
            return

        self.open("/login")
        self.page.fill("input[name=username]", "master" if who == "admin" else "alice")
        self.page.fill("input[name=password]", "pass" if who == "admin" else "secret")
        if who == "user":
            self.page.fill("input[name=key]", KEY)
        with self.page.expect_navigation():
            self.page.press("input[name=password]", "Enter")
        self.assertNotIn("/login", self.page.url)

    def open_each_tab(self):
        """Click each tab the configuration page shows."""
        tabs = self.page.locator(".tabs.config-overview li.tab a[href^='#']:visible")
        for href in [tab.get_attribute("href") for tab in tabs.all()]:
            self.page.click(f".tabs.config-overview a[href='{href}']")
            self.page.wait_for_load_state("networkidle")
            self.assertEqual(self.errors, [], href)

    def open_the_menu(self):
        """Open and close the side menu, which is a drawer at phone width."""
        opener = self.page.locator("#mobile-menu-open")
        if opener.is_visible():
            opener.click()
            self.page.keyboard.press("Escape")

    def clean_up(self):
        """Remove the configuration and everything stored beside it."""
        if self.cache.mode == "disabled":
            return

        self.cache.clear(KEY)
        self.cache.clear_auth(KEY)
        with self.cache.reminder_guard():
            self.cache.write_reminders(self.cache.reminder_path(KEY), [])


def _scenario_test(name):
    """Build the test for one scenario."""

    def test(self):
        self.check_pages(SCENARIOS[name])

    test.__doc__ = f"Every page runs without a script error: {name.replace('_', ' ')}."
    return test


for _name in SCENARIOS:
    setattr(SettingsBrowserTests, f"test_pages_run_without_script_errors_{_name}", _scenario_test(_name))
