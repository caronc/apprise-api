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
from django.test import SimpleTestCase, override_settings


class WelcomePageTests(SimpleTestCase):
    def test_welcome_page_status_code(self):
        response = self.client.get("/")
        assert response.status_code == 200
        self.assertContains(response, "document.querySelectorAll('.api-welcome pre')")

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

    @override_settings(APPRISE_API_ONLY=True)
    def test_welcome_page_api_only_returns_421(self) -> None:
        response = self.client.get("/")
        assert response.status_code == 421
