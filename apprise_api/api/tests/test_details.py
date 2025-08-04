# -*- coding: utf-8 -*-
#
# Copyright (C) 2025 Chris Caron <lead2gold@gmail.com>
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
from django.test import SimpleTestCase


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
        response = self.client.get("/details", content_type="application/json", **{"HTTP_CONTENT_TYPE": "application/json"})
        self.assertEqual(response.status_code, 200)
        assert response["Content-Type"].startswith("application/json")

        # JSON Response
        response = self.client.get("/details", content_type="application/json", **{"HTTP_ACCEPT": "application/json"})
        self.assertEqual(response.status_code, 200)
        assert response["Content-Type"].startswith("application/json")

        response = self.client.get("/details?all=yes")
        self.assertEqual(response.status_code, 200)
        assert response["Content-Type"].startswith("text/html")

        # JSON Response
        response = self.client.get("/details?all=yes", content_type="application/json", **{"HTTP_CONTENT_TYPE": "application/json"})
        self.assertEqual(response.status_code, 200)
        assert response["Content-Type"].startswith("application/json")

        # JSON Response
        response = self.client.get("/details?all=yes", content_type="application/json", **{"HTTP_ACCEPT": "application/json"})
        self.assertEqual(response.status_code, 200)
        assert response["Content-Type"].startswith("application/json")
