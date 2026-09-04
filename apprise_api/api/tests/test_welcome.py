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

    def test_mobile_beta_dialog_is_available(self):
        """The shared beta dialog presents the required steps and safe links."""
        response = self.client.get("/")
        self.assertContains(response, 'id="mobile-beta-dialog"')
        self.assertContains(response, 'class="apprise-dialog-close mobile-beta-dialog__close"')
        self.assertContains(response, 'class="nav-divider mobile-nav-divider"', count=2)
        self.assertContains(response, "https://groups.google.com/g/apprise-testers/")
        self.assertContains(response, "https://play.google.com/apps/testing/com.appriseit.mobile")
        self.assertContains(response, "https://play.google.com/store/apps/details?id=com.appriseit.mobile")
        self.assertContains(response, 'target="_blank"')
        self.assertContains(response, 'rel="noopener noreferrer"')

    @override_settings(APPRISE_API_ONLY=True)
    def test_welcome_page_api_only_returns_421(self) -> None:
        response = self.client.get("/")
        assert response.status_code == 421
