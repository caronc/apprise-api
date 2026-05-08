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
from unittest.mock import patch

from django.test import SimpleTestCase
from django.test.utils import override_settings

from ..views import service_optional, service_retry, tag_detail, tag_names


class _Tag:
    def __init__(self, name, priority=0, has_priority=False):
        self.name = name
        self.priority = priority
        self.has_priority = has_priority

    def __str__(self):
        return self.name


class _Notification:
    def __init__(self, retry=None, optional=None):
        if retry is not None:
            self.retry = retry
        if optional is not None:
            self.optional = optional


class JsonUrlsTests(SimpleTestCase):
    def test_get_invalid_key_status_code(self):
        """
        Test GET requests to invalid key
        """
        response = self.client.get("/get/**invalid-key**")
        assert response.status_code == 404

    def test_post_not_supported(self):
        """
        Test POST requests with key
        """
        response = self.client.post("/json/urls/test")
        # 405 as posting is not allowed
        assert response.status_code == 405

    def test_json_urls_config(self):
        """
        Test retrieving configuration
        """

        # our key to use
        key = "test_json_urls_config"

        # Nothing to return
        response = self.client.get("/json/urls/{}".format(key))
        self.assertEqual(response.status_code, 204)

        # Add some content
        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200

        # Handle case when we try to retrieve our content but we have no idea
        # what the format is in. Essentialy there had to have been disk
        # corruption here or someone meddling with the backend.
        with patch("gzip.open", side_effect=OSError):
            response = self.client.get("/json/urls/{}".format(key))
            assert response.status_code == 500
            assert response["Content-Type"].startswith("application/json")
            assert "tags" in response.json()
            assert "urls" in response.json()

            # has error directive
            assert "error" in response.json()

            # entries exist by are empty
            assert len(response.json()["tags"]) == 0
            assert len(response.json()["urls"]) == 0

        # Now we should be able to see our content
        response = self.client.get("/json/urls/{}".format(key))
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/json")
        assert "tags" in response.json()
        assert "urls" in response.json()

        # No errors occurred, therefore no error entry
        assert "error" not in response.json()

        # No tags (but can be assumed "all") is always present
        assert len(response.json()["tags"]) == 0

        # Same request as above but we set the privacy flag
        response = self.client.get("/json/urls/{}?privacy=1".format(key))
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/json")
        assert "tags" in response.json()
        assert "urls" in response.json()

        # No errors occurred, therefore no error entry
        assert "error" not in response.json()

        # No tags (but can be assumed "all") is always present
        assert len(response.json()["tags"]) == 0

        # One URL loaded
        assert len(response.json()["urls"]) == 1
        assert "url" in response.json()["urls"][0]
        assert "tags" in response.json()["urls"][0]
        assert len(response.json()["urls"][0]["tags"]) == 0

        # We can see that th URLs are not the same when the privacy flag is set
        without_privacy = self.client.get("/json/urls/{}?privacy=1".format(key))
        with_privacy = self.client.get("/json/urls/{}".format(key))
        assert with_privacy.json()["urls"][0] != without_privacy.json()["urls"][0]

        with override_settings(APPRISE_CONFIG_LOCK=True):
            # When our configuration lock is set, our result set enforces the
            # privacy flag even if it was otherwise set:
            with_privacy = self.client.get("/json/urls/{}?privacy=1".format(key))

            # But now they're the same under this new condition
            assert with_privacy.json()["urls"][0] == without_privacy.json()["urls"][0]

        # Add a YAML file
        response = self.client.post(
            "/add/{}".format(key),
            {
                "format": "yaml",
                "config": """
                urls:
                   - dbus://:
                       - tag: tag1, tag2""",
            },
        )
        assert response.status_code == 200

        # Now retrieve our JSON resonse
        response = self.client.get("/json/urls/{}".format(key))
        assert response.status_code == 200

        # No errors occured, therefore no error entry
        assert "error" not in response.json()

        # No tags (but can be assumed "all") is always present
        assert len(response.json()["tags"]) == 2

        # One URL loaded
        assert len(response.json()["urls"]) == 1
        assert "url" in response.json()["urls"][0]
        assert "tags" in response.json()["urls"][0]
        assert len(response.json()["urls"][0]["tags"]) == 2

        # Verify that the correct Content-Type is set in the header of the
        # response
        assert "Content-Type" in response
        assert response["Content-Type"].startswith("application/json")

    def test_priority_tag_detail_shape(self):
        """
        Advanced tags should expose bare names and per-entry priority details.
        """
        plain = tag_detail(_Tag("family"))
        prioritized = tag_detail(_Tag("family", priority=1, has_priority=True))
        prioritized_string = tag_detail("2:email")

        assert plain == {
            "name": "family",
            "priority": 0,
            "token": "family",
            "exact": "0:family",
        }
        assert prioritized == {
            "name": "family",
            "priority": 1,
            "token": "1:family",
            "exact": "1:family",
        }
        assert prioritized_string == {
            "name": "email",
            "priority": 2,
            "token": "2:email",
            "exact": "2:email",
        }
        assert tag_names(["2:email", _Tag("family")]) == {"email", "family"}

    def test_service_retry_falls_back_to_url_parameter(self):
        """
        Retry is available from newer Apprise objects or their rendered URLs.
        """
        assert service_retry(_Notification(3), "mailto://user:pass@example.com?retry=2") == 3
        assert service_retry(_Notification(), "mailto://user:pass@example.com?retry=2") == 2
        assert service_retry(_Notification(), "mailto://user:pass@example.com?retry=bad") == 0
        assert service_retry(_Notification(), None) == 0
        assert service_retry(_Notification(), "mailto://user:pass@example.com") == 0

    def test_service_optional_falls_back_to_url_parameter(self):
        """
        Optional is available from newer Apprise objects or their rendered URLs.
        """
        assert service_optional(_Notification(optional=True), "mailto://host?optional=no") is True
        assert service_optional(_Notification(), "mailto://host?optional=yes") is True
        assert service_optional(_Notification(), "mailto://host?optional=true") is True
        assert service_optional(_Notification(), "mailto://host?optional=no") is False
        assert service_optional(_Notification(), None) is False
        with patch("apprise_api.api.views.urlsplit", side_effect=TypeError):
            assert service_optional(_Notification(), "mailto://host?optional=yes") is False
        assert service_optional(_Notification(), "mailto://host") is False
