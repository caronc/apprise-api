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
import json
import re
from unittest import mock

from apprise.manager_plugins import NotificationManager
from django.test import SimpleTestCase, override_settings

from ..utils import service_turned_off

N_MGR = NotificationManager()


class DetailTests(SimpleTestCase):
    def test_post_not_supported(self):
        """
        Test POST requests
        """
        response = self.client.post("/details")
        # 405 as posting is not allowed
        assert response.status_code == 405

    def test_details_simple(self):
        """
        Test retrieving details
        """

        # Nothing to return
        response = self.client.get("/details")
        self.assertEqual(response.status_code, 200)
        assert response["Content-Type"].startswith("text/html")

        # JSON Response
        response = self.client.get(
            "/details",
            content_type="application/json",
            **{"HTTP_CONTENT_TYPE": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        assert response["Content-Type"].startswith("application/json")

        # JSON Response
        response = self.client.get(
            "/details",
            content_type="application/json",
            **{"HTTP_ACCEPT": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        assert response["Content-Type"].startswith("application/json")

        response = self.client.get("/details?all=yes")
        self.assertEqual(response.status_code, 200)
        assert response["Content-Type"].startswith("text/html")

        # JSON Response
        response = self.client.get(
            "/details?all=yes",
            content_type="application/json",
            **{"HTTP_CONTENT_TYPE": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        assert response["Content-Type"].startswith("application/json")

        # JSON Response
        response = self.client.get(
            "/details?all=yes",
            content_type="application/json",
            **{"HTTP_ACCEPT": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        assert response["Content-Type"].startswith("application/json")

    def test_details_accept_header_priority(self):
        """
        Test that the Accept header takes priority over Content-Type
        """
        response = self.client.get(
            "/details",
            CONTENT_TYPE="text/plain",
            **{"HTTP_ACCEPT": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        assert response["Content-Type"].startswith("application/json")

    def test_details_no_accept_header_falls_back_to_content_type(self):
        """
        With no Accept header at all, a real JSON-only client (one that
        sets Content-Type but not Accept) is still served JSON.
        """
        response = self.client.get("/details", CONTENT_TYPE="application/json")
        self.assertEqual(response.status_code, 200)
        assert response["Content-Type"].startswith("application/json")

    @override_settings(APPRISE_API_ONLY=True)
    def test_details_api_only_still_serves_json_api_clients(self):
        """
        APPRISE_API_ONLY disables the browsable HTML page, not the JSON
        API: a client that explicitly asks for JSON is still "using the
        API" and must not be turned away with a 421, even though a
        browser (no Accept override) still gets blocked.
        """
        response = self.client.get("/details", **{"HTTP_ACCEPT": "application/json"})
        self.assertEqual(response.status_code, 200)
        assert response["Content-Type"].startswith("application/json")

        # A browser-style request (no explicit JSON preference) is still
        # denied the HTML admin page in API-only mode.
        response = self.client.get("/details")
        self.assertEqual(response.status_code, 421)

    def test_details_page_builds_urls(self):
        """The page lists every service for the builder and sends its fields to /resolve."""
        response = self.client.get("/details")
        self.assertEqual(response.status_code, 200)
        for marker in ('id="details-search"', 'class="details-service"', 'id="details-data"', 'id="details-url"'):
            self.assertContains(response, marker)

        # Every service's details and the settings they share come with the page
        content = response.content.decode()
        data = json.loads(re.search(r'id="details-data"[^>]*>(.*?)</script>', content, re.S).group(1))
        self.assertTrue(any("json" in (entry["protocols"] or ()) for entry in data["schemas"]))
        shared = json.loads(re.search(r'id="details-shared-args"[^>]*>(.*?)</script>', content, re.S).group(1))
        self.assertIn("verify", shared)

        # The fields are put together by /resolve for the browser's Config ID
        self.assertContains(response, "/resolve/")

    def test_no_matching_services_note(self):
        """A search with no results shows the shared info note, hidden at first."""
        content = self.client.get("/details").content.decode()
        self.assertRegex(
            content,
            r'<div id="details-search-no-match"\s+'
            r'class="notify-target-guidance notify-target-guidance--info details-no-match"'
            r'\s+aria-live="polite"\s+hidden>',
        )
        self.assertIn("No Matching Services", content)
        self.assertIn("No services match your search.", content)

    def test_lists_of_several_kinds_say_how_entries_are_read(self):
        """A list told apart by marks (+ phone, @ group) says which means what, and how an unmarked entry was read."""
        content = self.client.get("/details").content.decode()
        # The page has what it needs: BulkSMS lists its two marked kinds
        data = json.loads(re.search(r'id="details-data"[^>]*>(.*?)</script>', content, re.S).group(1))
        bulksms = next(
            entry
            for entry in data["schemas"]
            if "bulksms" in (entry["protocols"] or []) + (entry["secure_protocols"] or [])
        )
        tokens = bulksms["details"]["tokens"]
        self.assertEqual(sorted(tokens[member]["prefix"] for member in tokens["targets"]["group"]), ["+", "@"])
        self.assertIn("Mark each entry with its kind: {}.", content)
        # How each unmarked entry was read comes from the URL Apprise wrote
        self.assertIn("kinds.find(function (kind) { return parts.includes(kind.prefix + entry); })", content)
        self.assertIn("{} was read as a {}.", content)
        self.assertIn("detailsShowReading();", content)

    def test_fields_still_needed_are_named_under_the_url(self):
        """The fields /resolve says are missing are named under the URL, or the required ones on an older server."""
        content = self.client.get("/details").content.decode()
        self.assertIn('<span id="details-url-missing" class="details-field-hint" hidden></span>', content)
        self.assertIn("Still needed: {}.", content)
        self.assertIn("detailsShowMissing(Array.isArray(answer.missing) ? answer.missing : empty);", content)
        # A pasted URL names what it lacks too
        self.assertIn("detailsShowMissing(Array.isArray(answer.missing) ? answer.missing : []);", content)

    def test_service_website_hidden_without_one(self):
        """A service with no website or setup guide shows no button for it."""
        content = self.client.get("/details").content.decode()
        self.assertIn("website.hidden = !service.service_url;", content)
        self.assertIn("setup.hidden = !service.setup_url;", content)
        self.assertNotIn("service.service_url || '#'", content)
        self.assertNotIn("service.setup_url || '#'", content)

    def test_messages_are_shown_as_plain_text(self):
        """Toasts show their message as text, so text from the server cannot add markup."""
        content = self.client.get("/details").content.decode()
        self.assertIn("titleText: message,", content)
        self.assertNotIn("title: message,", content)

    def test_details_page_links_to_the_api_examples(self):
        """The page points to the home page for its API examples instead of showing them."""
        content = self.client.get("/details").content.decode()
        self.assertIn('href="/#url-endpoints"', content)
        self.assertNotIn("curl ", content)
        self.assertNotIn("details-api ", content)

    def test_search_has_a_clear_button(self):
        """The service search uses the shared search box, with its own clear button."""
        content = self.client.get("/details").content.decode()
        self.assertIn('class="btn-flat value-visibility-toggle list-search-clear"', content)
        self.assertIn("function appriseWireListSearch(input, items, textOf, onFilter)", content)
        self.assertIn("appriseWireListSearch(\n    detailsSearch,", content)

    def test_listing_and_plugin_count_share_a_row(self):
        """The listing link and the plugin count are two cards in one row."""
        content = self.client.get("/details").content.decode()
        overview = content.split('<div class="details-overview">', 1)[1].split("{# Step 1", 1)[0]
        self.assertIn("Available Services", overview)
        self.assertIn("Only the services this server can use are listed.", overview)
        self.assertIn('<input id="details-show-all"', overview)
        self.assertIn('class="notify-form-section details-summary"', overview)
        self.assertRegex(overview, r'<span class="details-summary-count">\d+</span>')

    def test_show_all_switch_reloads_with_every_service(self):
        """The Show All switch is on with ?all=yes, and reloads the page with or without it."""
        content = self.client.get("/details").content.decode()
        self.assertNotRegex(content, r'id="details-show-all"\s+type="checkbox"\s+checked')
        self.assertIn("url.searchParams.set('all', 'yes');", content)
        self.assertIn("url.searchParams.delete('all');", content)

        content = self.client.get("/details?all=yes").content.decode()
        self.assertRegex(content, r'id="details-show-all"\s+type="checkbox"\s+checked')
        self.assertIn("Every Apprise service is listed, including the ones this server cannot use.", content)

    def test_typed_map_names_stay_plain_data(self):
        """A typed name such as __proto__ is kept as an ordinary entry, not lost to the object's prototype."""
        content = self.client.get("/details").content.decode()
        self.assertIn("const map = Object.create(null);", content)
        self.assertIn("const fields = Object.create(null);", content)

    def test_keyboard_moves_through_the_steps_in_order(self):
        """The service list is one Tab stop, and picking a service moves on to its first field."""
        content = self.client.get("/details").content.decode()
        # The arrow keys move within the list; Tab leaves it
        self.assertIn("button.tabIndex = button === stop ? 0 : -1;", content)
        self.assertIn("ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1", content)
        # The first field to fill in gets the focus
        self.assertIn("return !field.readOnly && field.getClientRects().length > 0;", content)

    def test_single_choice_is_shown_without_a_list(self):
        """A field with one possible value, like most schemas, is shown as text that Tab passes by."""
        content = self.client.get("/details").content.decode()
        self.assertIn("if (choices.length === 1) {", content)
        self.assertIn("input.readOnly = true;\n        input.tabIndex = -1;", content)

    def test_schemas_are_shown_with_their_separator(self):
        """A schema is shown the way it starts a URL (json://), but sent without the separator."""
        content = self.client.get("/details").content.decode()
        self.assertIn("option.textContent = holder.dataset.kind === 'schema' ? choice + '://' : choice;", content)
        self.assertIn("input.value += '://';", content)
        self.assertIn("return input.value.replace(/:\\/\\/$/, '');", content)

    def test_copy_button_sits_inside_the_url_box(self):
        """The copy button is placed inside the URL box, like the reveal button on a secret field."""
        content = self.client.get("/details").content.decode()
        self.assertRegex(
            content,
            r'<div class="notify-template-value-control concealed-field">\s+<input id="details-url"',
        )
        self.assertRegex(content, r'id="details-copy"\s+class="btn-flat value-visibility-toggle"')

    def test_fields_are_checked_against_their_patterns(self):
        """The builder marks a field that does not fit its service's pattern, and says so under the URL."""
        content = self.client.get("/details").content.decode()
        self.assertIn("This value is not in the form this service expects.", content)
        self.assertIn("This URL could not be loaded. Check the highlighted fields.", content)
        # Python's named groups are rewritten so the browser can read them
        self.assertIn("replace(/\\(\\?P</g, '(?<')", content)

    def test_details_page_explains_why_a_service_is_unavailable(self):
        """A turned-off service says whether the administrator or a missing package is the cause."""
        with (
            override_settings(APPRISE_ALLOW_SERVICES="", APPRISE_DENY_SERVICES="json"),
            mock.patch.object(N_MGR["json"], "enabled", False),
            mock.patch.object(N_MGR["growl"], "enabled", False),
        ):
            content = self.client.get("/details?all=yes").content.decode()
            api = self.client.get("/details?all=yes", headers={"accept": "application/json"}).json()

        data = json.loads(re.search(r'id="details-data"[^>]*>(.*?)</script>', content, re.S).group(1))
        found = {entry["protocols"][0]: entry for entry in data["schemas"] if entry["protocols"]}
        self.assertIs(found["json"]["admin_disabled"], True)
        self.assertIs(found["growl"]["admin_disabled"], False)
        self.assertEqual(found["growl"]["requirements"]["packages_required"], ["gntp"])

        # A turned-off service points to the online URL Builder instead
        self.assertIn('id="details-disabled-builder"', content)
        self.assertIn("https://appriseit.com/url-builder/", content)

        # Working services carry neither, and the JSON reply is unchanged
        self.assertNotIn("requirements", found["form"])
        self.assertNotIn("admin_disabled", found["form"])
        self.assertFalse(any("requirements" in entry or "admin_disabled" in entry for entry in api["schemas"]))

    def test_allow_and_deny_lists_decide_which_services_are_turned_off(self):
        """Only the allow list, or else the deny list, turns a service off."""
        with override_settings(APPRISE_ALLOW_SERVICES="form", APPRISE_DENY_SERVICES="form"):
            self.assertTrue(service_turned_off(["json", "jsons"]))
            self.assertFalse(service_turned_off(["FORM"]))

        with override_settings(APPRISE_ALLOW_SERVICES="", APPRISE_DENY_SERVICES="form"):
            self.assertTrue(service_turned_off(["form"]))
            self.assertFalse(service_turned_off(["json"]))

        with override_settings(APPRISE_ALLOW_SERVICES="", APPRISE_DENY_SERVICES=""):
            self.assertFalse(service_turned_off(["json"]))
