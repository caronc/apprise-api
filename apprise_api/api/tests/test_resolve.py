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
        """Save a configuration for the Config ID."""
        self.assertTrue(ConfigCache.put(self.key, "json://localhost", "text"))

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

    def test_bad_requests_are_refused(self):
        """Wrong types, too many URLs and other bodies are refused."""
        for urls in (5, ["json://a", 5], {"a": "json://a"}):
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
