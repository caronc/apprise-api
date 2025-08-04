# -*- coding: utf-8 -*-
#
# Copyright (C) 2019 Chris Caron <lead2gold@gmail.com>
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
from unittest.mock import patch


class GetTests(SimpleTestCase):
    def test_get_invalid_key_status_code(self):
        """
        Test GET requests to invalid key
        """
        response = self.client.get("/get/**invalid-key**")
        assert response.status_code == 404

    def test_get_config(self):
        """
        Test retrieving configuration
        """

        # our key to use
        key = "test_get_config_"

        # GET returns 405 (not allowed)
        response = self.client.get("/get/{}".format(key))
        assert response.status_code == 405

        # No content saved to the location yet
        response = self.client.post("/get/{}".format(key))
        self.assertEqual(response.status_code, 204)

        # Add some content
        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200

        # Handle case when we try to retrieve our content but we have no idea
        # what the format is in. Essentialy there had to have been disk
        # corruption here or someone meddling with the backend.
        with patch("gzip.open", side_effect=OSError):
            response = self.client.post("/get/{}".format(key))
            assert response.status_code == 500

        # Now we should be able to see our content
        response = self.client.post("/get/{}".format(key))
        assert response.status_code == 200

        # Add a YAML file
        response = self.client.post(
            "/add/{}".format(key),
            {
                "format": "yaml",
                "config": """
                urls:
                   - dbus://""",
            },
        )
        assert response.status_code == 200

        # Now retrieve our YAML configuration
        response = self.client.post("/get/{}".format(key))
        assert response.status_code == 200

        # Verify that the correct Content-Type is set in the header of the
        # response
        assert "Content-Type" in response
        assert response["Content-Type"].startswith("text/yaml")
