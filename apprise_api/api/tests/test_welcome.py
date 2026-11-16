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

from django.test import SimpleTestCase, override_settings

from ..utils import ConfigCache


class WelcomePageTests(SimpleTestCase):
    def test_welcome_page_status_code(self):
        response = self.client.get("/")
        assert response.status_code == 200
        self.assertContains(response, "document.querySelectorAll('.api-welcome pre')")

    def test_page_meta_tags(self):
        """The meta tags render without missing template variable logs."""
        # A real browser always sends a Host header, but the test client does not.
        with self.assertNoLogs("django.template", level="DEBUG"):
            response = self.client.get("/", HTTP_HOST="testserver")

        self.assertContains(response, 'content="Apprise API - notification service API"')
        self.assertContains(response, 'content="apprise,notifications,api,alerts"')

    def test_mobile_app_dialog_is_available(self):
        """The shared dialog links to the app and its product page."""
        response = self.client.get("/")
        self.assertContains(response, 'id="mobile-app-dialog"')
        self.assertContains(response, 'class="apprise-dialog mobile-app-dialog"')
        self.assertContains(response, 'class="apprise-dialog-close mobile-app-dialog__close"')
        self.assertContains(response, 'class="nav-divider mobile-nav-divider"', count=2)
        self.assertContains(response, 'class="topbar-icon topbar-icon--mobile"')
        self.assertContains(response, 'class="mobile-app-dialog__icon"')
        self.assertContains(response, 'class="mobile-app-dialog__learn-more"')
        self.assertContains(response, '<rect width="14" height="20" x="5" y="2" rx="2"', count=3)
        self.assertNotContains(response, ">smartphone</i>")
        self.assertContains(response, "https://play.google.com/store/apps/details?id=com.appriseit.mobile")
        self.assertContains(response, "https://appriseit.com/mobile/")
        self.assertNotContains(response, "https://groups.google.com/g/apprise-testers/")
        self.assertNotContains(response, "https://play.google.com/apps/testing/com.appriseit.mobile")
        self.assertContains(response, 'target="_blank"')
        self.assertContains(response, 'rel="nofollow noopener noreferrer"')
        # Every link that opens a new tab is marked not to be followed
        self.assertNotContains(response, 'rel="noopener noreferrer"')
        self.assertContains(response, "window.appriseCloseMobileMenu();")

    def test_shared_popup_shell_and_task_abort_are_rendered(self):
        """Modal defaults distinguish dismissible dialogs from task aborts."""
        response = self.client.get("/cfg/popup_test")
        self.assertContains(response, "function appriseFire(options)")
        self.assertContains(response, "popupClasses.push('apprise-popup--status')")
        self.assertContains(response, 'class="apprise-status-mark"')
        self.assertContains(response, "showCloseButton: !isToast")
        self.assertContains(response, "function appriseBeginTask(options)")
        self.assertContains(response, "const hasDisplayedStatus")
        self.assertContains(response, "if (!hasDisplayedStatus)")
        self.assertContains(response, "if (window.appriseStatusUpdatePending)")
        self.assertContains(response, "window.appriseStatusUpdatePending = false")
        self.assertContains(response, "Are you sure you wish to abort the current task?")
        self.assertContains(response, "signal: task.signal")
        self.assertContains(response, "streamUrl.searchParams.set('stream', '1')")
        self.assertContains(response, "'Accept': 'text/event-stream'")
        self.assertContains(response, "response.body.getReader")
        self.assertContains(response, "event.name === 'log'")
        # The test send has its own words; every other send shares one set
        self.assertContains(response, "responseTitle: 'Delivery Response'", count=2)
        self.assertContains(response, "sendNotifyStreams(requests, notifyStreamMessages);")
        self.assertContains(response, "event.detail === 0")
        self.assertContains(response, "document.activeElement === control")

    def test_code_samples_copy_with_the_shared_helper(self):
        """The copy buttons on code samples use the same helper as every other page."""
        content = self.client.get("/").content.decode()
        self.assertIn("appriseCopyToClipboard(copyText, 'Copied to clipboard');", content)
        self.assertNotIn("function copyToClipboard(", content)
        self.assertNotIn("function showCopyToast(", content)

    @override_settings(APPRISE_STATELESS_MODE="disabled")
    def test_disabled_stateless_mode_hides_endpoint_help(self):
        """The welcome page replaces unusable stateless examples with a notice."""
        response = self.client.get("/")
        self.assertContains(response, "The administrator of this system has disabled stateless URL support.")
        self.assertNotContains(response, "Those who wish to treat this API as nothing but")

    @override_settings(APPRISE_STATELESS_MODE="disabled", APPRISE_STATEFUL_MODE="disabled")
    def test_health_panel_explains_when_both_modes_are_disabled(self):
        """The shared health panel explains the fully disabled state."""
        response = self.client.get("/")
        self.assertContains(response, "Notification Delivery Disabled")
        self.assertContains(response, "An administrator must enable at least one mode")
        self.assertContains(response, "data.degraded === true")
        self.assertContains(response, 'class="health-check-more" hidden')
        self.assertContains(response, "moreDetails.hidden = !(showCfg || showAttach)")

    def test_code_samples_send_their_requests(self):
        """The Python and PHP samples actually send, and log in the way their library expects."""
        token = base64.b64encode(b"master:pass").decode()
        with override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=token, APPRISE_USER="master"):
            content = self.client.get("/", headers={"authorization": "Basic " + token}).content.decode()

        # urllib cannot log in from a user:password@ URL, so it sends a header
        self.assertEqual(content.count("with urlopen(req) as response:"), 7)
        self.assertNotIn('"http://master:****@testserver', content.replace("&quot;", '"'))
        self.assertIn('base64.b64encode("master:****".encode())', content.replace("&quot;", '"'))
        self.assertIn("requests.post(", content)
        # Samples that log in say to replace the placeholder, like /config does
        self.assertIn("snippet-password-warning", content)
        self.assertIn("Replace **** with your password before using this example.", content)
        self.assertNotIn("request.post(", content.replace("requests.post(", ""))
        # Removing a configuration sends no payload
        self.assertNotIn("json.dumps(payload)", content.split("/del/")[2].split("</code></pre>")[0])
        # Every sample holding **** quotes it so the shell leaves it alone
        self.assertNotRegex(content, r"(?:--config=|POST |&nbsp;)http://master:\*{4}@")

    def test_service_and_url_endpoints_are_listed(self):
        """The home page shows the /details and /resolve examples, whatever the modes are."""
        with override_settings(APPRISE_STATELESS_MODE="disabled", APPRISE_STATEFUL_MODE="disabled"):
            content = self.client.get("/").content.decode()

        self.assertIn('id="url-endpoints"', content)
        self.assertIn('/details/"</code></pre>', content)
        self.assertIn("/resolve/<em>KEY</em>", content)
        self.assertNotIn("X-Apprise-Config-ID: ", content)

    def test_endpoint_examples_match_the_caller(self):
        """The examples carry the login, and a configuration login's Config ID header."""
        token = base64.b64encode(b"master:pass").decode()
        key = "welcome-example"
        with override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=token, APPRISE_USER="master"):
            self.assertTrue(ConfigCache.set_auth(key, "alice", "secret"))
            try:
                admin = self.client.get("/", headers={"authorization": "Basic " + token}).content.decode()
                self.assertRegex(admin, r"master:\*{4}@[^\"]*/details/")
                self.assertNotIn("X-Apprise-Config-ID: ", admin)

                # A configuration login signs in through the web login form
                browser = {"accept": "text/html,application/xhtml+xml"}
                login = self.client.post(
                    "/login", {"username": "alice", "password": "secret", "key": key}, headers=browser
                )
                self.assertEqual(login.status_code, 302)
                user = self.client.get("/", headers=browser).content.decode()
                self.assertRegex(user, r"alice:\*{4}@[^\"]*/details/")
                self.assertIn(f"X-Apprise-Config-ID: <em>{key}</em>", user)
                self.assertIn(f"/resolve/<em>{key}</em>", user)
            finally:
                ConfigCache.clear_auth(key)

    @override_settings(APPRISE_API_ONLY=True)
    def test_welcome_page_api_only_returns_421(self) -> None:
        response = self.client.get("/")
        assert response.status_code == 421
