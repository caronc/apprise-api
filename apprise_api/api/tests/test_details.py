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
