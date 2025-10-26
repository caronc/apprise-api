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
import json
from unittest import mock

from django.test import SimpleTestCase, override_settings
import requests

import apprise

from ..forms import NotifyForm

# Grant access to our Notification Manager Singleton
N_MGR = apprise.manager_plugins.NotificationManager()


class NotifyWithEmojiTests(SimpleTestCase):
    """
    Test notifications with Emoji Settings
    """

    @mock.patch("requests.post")
    def test_stateful_notify_with_emoji(self, mock_post):
        """
        Test adding a simple stateful notification with emoji flags
        """

        # Prepare our response
        response = requests.Request()
        response.status_code = requests.codes.ok

        # Prepare Mock
        mock_post.return_value = response

        # our key to use
        key = "test_notify_stateful_emoji"

        # Add some content
        response = self.client.post("/add/{}".format(key), {"urls": "json://user:pass@localhost"})
        assert response.status_code == 200

        # Preare our form data (with emoji's in it)
        form_data = {
            "title": ":grin:",
            "body": "test notifiction :smile:",
        }

        # At a minimum, just a body is required
        form = NotifyForm(data=form_data)
        assert form.is_valid()

        # Required to prevent None from being passed into self.client.post()
        del form.cleaned_data["attachment"]

        # we always set a type if one wasn't done so already
        assert form.cleaned_data["type"] == apprise.NotifyType.INFO.value

        # we always set a format if one wasn't done so already
        assert form.cleaned_data["format"] == apprise.NotifyFormat.TEXT.value

        # Send our notification
        with override_settings(APPRISE_INTERPRET_EMOJIS=True):
            response = self.client.post("/notify/{}".format(key), form.cleaned_data)
            assert response.status_code == 200
            assert mock_post.call_count == 1

            details = mock_post.call_args_list[0]
            assert details[0][0].startswith("http://localhost")

            payload = json.loads(details[1]["data"])
            assert payload["title"] == "😃"
            assert payload["message"] == "test notifiction 😄"

        # Reset our mock object
        mock_post.reset_mock()

        # Send our notification
        with override_settings(APPRISE_INTERPRET_EMOJIS=False):
            response = self.client.post("/notify/{}".format(key), form.cleaned_data)
            assert response.status_code == 200
            assert mock_post.call_count == 1

            details = mock_post.call_args_list[0]
            assert details[0][0].startswith("http://localhost")

            payload = json.loads(details[1]["data"])
            assert payload["title"] == ":grin:"
            assert payload["message"] == "test notifiction :smile:"

        # Reset our mock object
        mock_post.reset_mock()

    @mock.patch("requests.post")
    def test_stateless_notify_with_emoji(self, mock_post):
        """
        Test adding a simple stateless notification with emoji flags
        """

        # Prepare our response
        response = requests.Request()
        response.status_code = requests.codes.ok

        # Prepare Mock
        mock_post.return_value = response

        # Preare our payload (with emoji entries)
        json_data = {
            "urls": "json://user:pass@localhost",
            "title": ":grin:",
            "body": "test notifiction :smile:",
        }

        with override_settings(APPRISE_INTERPRET_EMOJIS=True):
            # Send our notification as a JSON object
            response = self.client.post(
                "/notify",
                data=json.dumps(json_data),
                content_type="application/json",
            )
            assert response.status_code == 200
            assert mock_post.call_count == 1

            details = mock_post.call_args_list[0]
            assert details[0][0].startswith("http://localhost")

            payload = json.loads(details[1]["data"])
            assert payload["title"] == "😃"
            assert payload["message"] == "test notifiction 😄"

        # Reset Mock
        mock_post.reset_mock()

        with override_settings(APPRISE_INTERPRET_EMOJIS=False):
            # Send our notification as a JSON object
            response = self.client.post(
                "/notify",
                data=json.dumps(json_data),
                content_type="application/json",
            )
            assert response.status_code == 200
            assert mock_post.call_count == 1

            details = mock_post.call_args_list[0]
            assert details[0][0].startswith("http://localhost")

            payload = json.loads(details[1]["data"])
            assert payload["title"] == ":grin:"
            assert payload["message"] == "test notifiction :smile:"

        # Reset Mock
        mock_post.reset_mock()
