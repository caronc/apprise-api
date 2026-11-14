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
"""Test the /resolve endpoint."""

import base64
from json import dumps, loads

import apprise
from django.test import SimpleTestCase
from django.test.utils import override_settings

from ..utils import ConfigCache

_MASTER_TOKEN = base64.b64encode(b"master:pass").decode()
_MASTER = {"authorization": "Basic " + _MASTER_TOKEN}
_USER = {"authorization": "Basic " + base64.b64encode(b"alice:secret").decode()}
_LOGINS = {"APPRISE_AUTH_REQUIRED": True, "APPRISE_BASIC_AUTH_TOKEN": _MASTER_TOKEN, "APPRISE_USER": "master"}


class ResolveTests(SimpleTestCase):
    """URLs are checked and returned with their secrets hidden."""

    key = "resolve-test"

    def setUp(self):
        """Save a configuration for the Config ID, with every service turned on."""
        self.assertTrue(ConfigCache.put(self.key, "json://localhost", "text"))
        # Other tests may leave services turned off, and those give no fields
        for plugin in apprise.manager_plugins.NotificationManager().plugins():
            plugin.enabled = True

    def tearDown(self):
        """Remove the configuration and its login."""
        ConfigCache.clear(self.key)
        ConfigCache.clear_auth(self.key)

    def resolve(self, urls, path=None, **kwargs):
        """Post URLs to /resolve and return the response."""
        return self.client.post(
            path or f"/resolve/{self.key}/", data=dumps({"urls": urls}), content_type="application/json", **kwargs
        )

    def test_urls_come_back_in_full_and_hidden(self):
        """Each URL comes back in order, with its service, in full and with its secrets hidden."""
        response = self.resolve(["mailto://user:hunter2@example.com", "nope://thing", " ", "json://localhost"])
        self.assertEqual(response.status_code, 200)
        first, unknown, blank, last = loads(response.content)["urls"]
        self.assertTrue(first["valid"])
        self.assertTrue(first["service"])
        # The full URL keeps its password and writes out every option
        self.assertIn("hunter2", first["url"])
        self.assertIn("?", first["url"])
        self.assertNotIn("hunter2", first["privacy_url"])
        self.assertTrue(first["privacy_url"].startswith("mailto://"))
        # Its ID is the one /json/urls gives the same URL in a configuration
        self.assertEqual(first["id"], apprise.Apprise.instantiate("mailto://user:hunter2@example.com").url_id())
        # A URL that will not load comes back as sent, hidden down to its schema
        self.assertEqual(
            (unknown["url"], unknown["privacy_url"], unknown["valid"], unknown["service"]),
            ("nope://thing", "nope://", False, None),
        )
        self.assertTrue(unknown["error"])
        self.assertEqual((blank["url"], blank["privacy_url"], blank["valid"]), ("", "", False))
        self.assertTrue(last["valid"])
        # The full URL can be sent again as it is
        again = loads(self.resolve(first["url"]).content)["urls"][0]
        self.assertTrue(again["valid"])
        self.assertIn("hunter2", again["url"])

        # One string is split the same way /notify splits it
        self.assertEqual(len(loads(self.resolve("json://a, json://b").content)["urls"]), 2)

        # The Config ID may also come in the header
        response = self.resolve(["json://a"], path="/resolve/", headers={"X-Apprise-Config-ID": self.key})
        self.assertEqual(response.status_code, 200)

    def test_urls_and_fields_turn_into_each_other(self):
        """A URL comes back as fields, and those fields come back as the same URL."""
        url = "mailtos://user:hunter2@example.com?to=a@example.com,b@example.com"
        first = loads(self.resolve([url]).content)["urls"][0]

        # ?to= joins the other targets; no field is kept under its own name
        self.assertEqual(first["fields"]["targets"], ["a@example.com", "b@example.com"])
        self.assertNotIn("to", first["fields"])
        self.assertEqual(first["fields"]["password"], "hunter2")

        # The hidden fields hide what the hidden URL hides
        self.assertNotIn("hunter2", dumps(first["privacy_fields"]))
        self.assertEqual(first["privacy_fields"]["user"], "user")

        # Sending the fields back gives the same URL, hidden form and ID
        again = loads(self.resolve([first["fields"]]).content)["urls"][0]
        self.assertTrue(again["valid"])
        for name in ("url", "privacy_url", "fields", "privacy_fields", "id", "service"):
            self.assertEqual(again[name], first[name], name)

        # Fields use the names /details lists, such as smtp and mode
        url = "mailtos://user:hunter2@example.com/a@example.com?smtp=smtp.example.com&mode=ssl"
        fields = loads(self.resolve([url]).content)["urls"][0]["fields"]
        self.assertEqual((fields["smtp"], fields["mode"]), ("smtp.example.com", "ssl"))
        self.assertNotIn("smtp_host", fields)

        # Fields typed into a form, in any order, make a working URL
        form = {"schema": "mailtos", "mode": "ssl", "host": "example.com", "user": "user", "password": "p@ss"}
        built = loads(self.resolve([form]).content)["urls"][0]
        self.assertTrue(built["valid"])
        self.assertIn("mode=ssl", built["url"])
        self.assertEqual(built["fields"]["password"], "p@ss")

        # Key/value options come back as maps of their own
        fields = loads(self.resolve(["json://localhost?+X-Token=abc&-p1=v&:key=val"]).content)["urls"][0]["fields"]
        self.assertEqual(
            (fields["headers"], fields["params"], fields["payload"]), ({"X-Token": "abc"}, {"p1": "v"}, {"key": "val"})
        )

        # Fields that name no schema, or one that will not load, come back
        # without a URL, hidden down to their schema
        unknown, unnamed, odd, made_up = loads(
            self.resolve(
                [{"schema": "nope", "host": "x"}, {"host": "x"}, {"schema": ["json"]}, {"schema": "json", "nope": "x"}]
            ).content
        )["urls"]
        self.assertEqual(
            (unknown["url"], unknown["privacy_url"], unknown["valid"], unknown["service"]), ("", "nope://", False, None)
        )
        self.assertTrue(unknown["error"])
        self.assertNotIn("fields", unknown)
        for entry in (unnamed, odd):
            self.assertEqual((entry["url"], entry["privacy_url"], entry["valid"]), ("", "", False))

        # A URL its service would not load still gives back what it holds
        partial = loads(self.resolve(["GOTIFY://hostname", "mailtos:"]).content)["urls"]
        self.assertFalse(partial[0]["valid"])
        self.assertEqual((partial[0]["fields"]["schema"], partial[0]["fields"]["host"]), ("gotify", "hostname"))
        self.assertIn("privacy_fields", partial[0])
        self.assertNotIn("fields", partial[1])

        # A name the service does not list is refused
        self.assertEqual((made_up["url"], made_up["privacy_url"], made_up["valid"]), ("", "json://", False))

    def test_hidden_fields_hide_what_the_hidden_url_hides(self):
        """A secret the hidden URL hides is hidden in the hidden fields too, whatever the service marks private."""
        urls = ["mastodon://SECRETTOKEN123@example.com", "ntfy://tk_SECRET123456@localhost/topic"]
        for answer in loads(self.resolve(urls).content)["urls"]:
            self.assertTrue(answer["valid"])
            self.assertNotIn("SECRET", answer["privacy_url"])
            self.assertNotIn("SECRET", dumps(answer["privacy_fields"]))
            # The plain fields still hold them, so they can be sent back
            self.assertIn("SECRET", dumps(answer["fields"]))

    def test_bad_requests_are_refused(self):
        """Wrong types, too many URLs and other bodies are refused."""
        for urls in (5, ["json://a", 5], {"a": "json://a"}, [["json://a"]]):
            response = self.resolve(urls)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(loads(response.content)["field"], "urls")

        self.assertEqual(loads(self.resolve(["json://a"] * 101).content)["field"], "urls")
        response = self.client.post(f"/resolve/{self.key}/", data={"urls": "json://a"})
        self.assertEqual(response.status_code, 400)
        response = self.client.post(f"/resolve/{self.key}/", data="[1]", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        response = self.resolve(["json://a"], path="/resolve/", headers={"X-Apprise-Config-ID": "bad key!"})
        self.assertEqual(response.status_code, 400)

    def test_needs_a_config_id_and_a_caller_who_may_use_it(self):
        """Any caller who may use the Config ID may, except on public or disabled ones; the modes do not matter."""
        self.assertEqual(self.resolve(["json://a"], path="/resolve/").status_code, 400)
        for mode in ({"APPRISE_STATELESS_MODE": "disabled"}, {"APPRISE_STATEFUL_MODE": "disabled"}):
            with override_settings(**mode):
                self.assertEqual(self.resolve(["json://a"]).status_code, 200, mode)

        self.assertTrue(ConfigCache.set_auth(self.key, "alice", "secret", access="user"))
        with override_settings(**_LOGINS):
            # Without credentials, the login is asked for first
            self.assertEqual(self.resolve(["json://a"]).status_code, 401)
            self.assertEqual(self.resolve(["json://a"], path="/resolve/").status_code, 401)
            self.assertEqual(self.resolve(["json://a"], path="/resolve/", headers=_MASTER).status_code, 400)
            header = {"X-Apprise-Config-ID": self.key}
            self.assertEqual(self.resolve(["json://a"], path="/resolve/", headers=header).status_code, 401)
            self.assertEqual(self.resolve(["json://a"], headers=_USER).status_code, 200)
            self.assertEqual(self.resolve(["json://a"], headers=_MASTER).status_code, 200)

            # A locked configuration's user may still sign in, so may use it
            self.assertTrue(ConfigCache.set_access(self.key, "locked"))
            self.assertEqual(self.resolve(["json://a"], headers=_USER).status_code, 200)

            # On public and disabled ones only the administrator may
            for access in ("public", "disabled"):
                self.assertTrue(ConfigCache.set_access(self.key, access))
                self.assertIn(self.resolve(["json://a"], headers=_USER).status_code, (401, 403), access)
                self.assertEqual(self.resolve(["json://a"]).status_code, 401, access)
                self.assertEqual(self.resolve(["json://a"], headers=_MASTER).status_code, 200, access)

    def test_only_post_is_answered_and_at_finds_the_config_id(self):
        """GET is refused; '@' reads the Config ID from the header or the browser's remembered one."""
        for headers in ({}, {"Accept": "application/json"}):
            self.assertEqual(self.client.get(f"/resolve/{self.key}/", headers=headers).status_code, 405)

        header = {"X-Apprise-Config-ID": self.key}
        self.assertEqual(self.resolve(["json://a"], path="/resolve/@/", headers=header).status_code, 200)
        self.client.cookies.clear()
        self.assertEqual(self.resolve(["json://a"], path="/resolve/@/").status_code, 400)

        # A browser that opened a page earlier remembers its Config ID
        self.client.get("/")
        self.assertEqual(self.resolve(["json://a"], path="/resolve/@/").status_code, 200)
