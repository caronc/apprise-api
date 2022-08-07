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
from django.test import SimpleTestCase, override_settings
from unittest.mock import patch
import requests
from ..forms import NotifyForm
import json
import apprise


class NotifyTests(SimpleTestCase):
    """
    Test notifications
    """

    @patch('apprise.Apprise.notify')
    def test_notify_by_loaded_urls(self, mock_notify):
        """
        Test adding a simple notification and notifying it
        """

        # Set our return value
        mock_notify.return_value = True

        # our key to use
        key = 'test_notify_by_loaded_urls'

        # Add some content
        response = self.client.post(
            '/add/{}'.format(key),
            {'urls': 'mailto://user:pass@yahoo.ca'})
        assert response.status_code == 200

        # Preare our form data
        form_data = {
            'body': 'test notifiction',
        }

        # At a minimum, just a body is required
        form = NotifyForm(data=form_data)
        assert form.is_valid()

        # we always set a type if one wasn't done so already
        assert form.cleaned_data['type'] == apprise.NotifyType.INFO

        # we always set a format if one wasn't done so already
        assert form.cleaned_data['format'] == apprise.NotifyFormat.TEXT

        # Send our notification
        response = self.client.post(
            '/notify/{}'.format(key), form.cleaned_data)
        assert response.status_code == 200
        assert mock_notify.call_count == 1

    @patch('requests.post')
    def test_notify_with_tags(self, mock_post):
        """
        Test notification handling when setting tags
        """

        # Disable Throttling to speed testing
        apprise.plugins.NotifyBase.request_rate_per_sec = 0
        # Ensure we're enabled for the purpose of our testing
        apprise.common.NOTIFY_SCHEMA_MAP['json'].enabled = True

        # Prepare our response
        response = requests.Request()
        response.status_code = requests.codes.ok
        mock_post.return_value = response

        # our key to use
        key = 'test_notify_with_tags'

        # Valid Yaml Configuration
        config = """
        urls:
          - json://user:pass@localhost:
              tag: home
        """

        # Load our configuration (it will be detected as YAML)
        response = self.client.post(
            '/add/{}'.format(key),
            {'config': config})
        assert response.status_code == 200

        # Preare our form data
        form_data = {
            'body': 'test notifiction',
            'type': apprise.NotifyType.INFO,
            'format': apprise.NotifyFormat.TEXT,
        }

        # Send our notification
        response = self.client.post(
            '/notify/{}'.format(key), form_data)

        # Nothing could be notified as there were no tag matches
        assert response.status_code == 424
        assert mock_post.call_count == 0

        # Now let's send our notification by specifying the tag in the
        # parameters
        response = self.client.post(
            '/notify/{}?tag=home'.format(key), form_data)

        # Our notification was sent
        assert response.status_code == 200
        assert mock_post.call_count == 1

        # Test our posted data
        response = json.loads(mock_post.call_args_list[0][1]['data'])
        assert response['title'] == ''
        assert response['message'] == form_data['body']
        assert response['type'] == apprise.NotifyType.INFO

        # Preare our form data (body is actually the minimum requirement)
        # All of the rest of the variables can actually be over-ridden
        # by the GET Parameter (ONLY if not otherwise identified in the
        # payload). The Payload contents of the POST request always take
        # priority to eliminate any ambiguity
        form_data = {
            'body': 'test notifiction',
        }

        # Reset our count
        mock_post.reset_mock()

        # Send our notification by specifying the tag in the parameters
        response = self.client.post(
            '/notify/{}?tag=home&format={}&type={}&title={}&body=ignored'
            .format(
                key, apprise.NotifyFormat.TEXT,
                apprise.NotifyType.WARNING, "Test Title"),
            form_data,
            content_type='application/json')

        # Our notification was sent
        assert response.status_code == 200
        assert mock_post.call_count == 1

        response = json.loads(mock_post.call_args_list[0][1]['data'])
        assert response['title'] == "Test Title"
        assert response['message'] == form_data['body']
        assert response['type'] == apprise.NotifyType.WARNING

    @patch('apprise.NotifyBase.notify')
    def test_partial_notify_by_loaded_urls(self, mock_notify):
        """
        Test notification handling when one or more of the services
        can not be notified.
        """

        # our key to use
        key = 'test_partial_notify_by_loaded_urls'

        # Add some content
        response = self.client.post(
            '/add/{}'.format(key),
            {
                'urls': ', '.join([
                    'mailto://user:pass@hotmail.com',
                    'mailto://user:pass@gmail.com',
                    ],
                ),
            })
        assert response.status_code == 200

        # Preare our form data
        form_data = {
            'body': 'test notifiction',
        }

        # At a minimum, just a body is required
        form = NotifyForm(data=form_data)
        assert form.is_valid()

        # we always set a type if one wasn't done so already
        assert form.cleaned_data['type'] == apprise.NotifyType.INFO

        # we always set a format if one wasn't done so already
        assert form.cleaned_data['format'] == apprise.NotifyFormat.TEXT

        # Set our return value; first we return a true, then we fail
        # on the second call
        mock_notify.side_effect = (True, False)

        # Send our notification
        response = self.client.post(
            '/notify/{}'.format(key), form.cleaned_data)
        assert response.status_code == 424
        assert mock_notify.call_count == 2

    @patch('apprise.Apprise.notify')
    def test_notify_by_loaded_urls_with_json(self, mock_notify):
        """
        Test adding a simple notification and notifying it using JSON
        """

        # Set our return value
        mock_notify.return_value = True

        # our key to use
        key = 'test_notify_by_loaded_urls_with_json'

        # Add some content
        response = self.client.post(
            '/add/{}'.format(key),
            {'urls': 'mailto://user:pass@yahoo.ca'})
        assert response.status_code == 200

        # Preare our JSON data
        json_data = {
            'body': 'test notifiction',
            'type': apprise.NotifyType.WARNING,
        }

        # Send our notification as a JSON object
        response = self.client.post(
            '/notify/{}'.format(key),
            data=json.dumps(json_data),
            content_type='application/json',
        )

        # Still supported
        assert response.status_code == 200
        assert mock_notify.call_count == 1

        # Reset our count
        mock_notify.reset_mock()

        # Test referencing a key that doesn't exist
        response = self.client.post(
            '/notify/non-existant-key',
            data=json.dumps(json_data),
            content_type='application/json',
        )

        # Nothing notified
        assert response.status_code == 204
        assert mock_notify.call_count == 0

        # Test sending a garbage JSON object
        response = self.client.post(
            '/notify/{}'.format(key),
            data="{",
            content_type='application/json',
        )

        assert response.status_code == 400
        assert mock_notify.call_count == 0

        # Test sending with an invalid content type
        response = self.client.post(
            '/notify/{}'.format(key),
            data="{}",
            content_type='application/xml',
        )

        assert response.status_code == 400
        assert mock_notify.call_count == 0

        # Test sending without any content at all
        response = self.client.post(
            '/notify/{}'.format(key),
            data="{}",
            content_type='application/json',
        )

        assert response.status_code == 400
        assert mock_notify.call_count == 0

        # Test sending without a body
        json_data = {
            'type': apprise.NotifyType.WARNING,
        }

        response = self.client.post(
            '/notify/{}'.format(key),
            data=json.dumps(json_data),
            content_type='application/json',
        )

        assert response.status_code == 400
        assert mock_notify.call_count == 0

        # Test inability to prepare writing config to disk
        json_data = {
            'body': 'test message'
        }

        # Test the handling of underlining disk/write exceptions
        with patch('gzip.open') as mock_open:
            mock_open.side_effect = OSError()
            # We'll fail to write our key now
            response = self.client.post(
                '/notify/{}'.format(key),
                data=json.dumps(json_data),
                content_type='application/json',
            )

            # internal errors are correctly identified
            assert response.status_code == 500
            assert mock_notify.call_count == 0

        # Reset our count
        mock_notify.reset_mock()

        # Test with invalid format
        json_data = {
            'body': 'test message',
            'format': 'invalid'
        }

        # Test case with format set to invalid
        response = self.client.post(
            '/notify/{}'.format(key),
            data=json.dumps(json_data),
            content_type='application/json',
        )

        assert response.status_code == 400
        assert mock_notify.call_count == 0

        # Reset our count
        mock_notify.reset_mock()

        # If an empty format is specified, it is accepted and
        # no imput format is specified
        json_data = {
            'body': 'test message',
            'format': None,
        }

        # Test case with format changed
        response = self.client.post(
            '/notify/{}'.format(key),
            data=json.dumps(json_data),
            content_type='application/json',
        )

        assert response.status_code == 200
        assert mock_notify.call_count == 1

        # Reset our count
        mock_notify.reset_mock()

        # Same results for any empty string:
        json_data['format'] = ''
        response = self.client.post(
            '/notify/{}'.format(key),
            data=json.dumps(json_data),
            content_type='application/json',
        )

        assert response.status_code == 200
        assert mock_notify.call_count == 1

        # Reset our count
        mock_notify.reset_mock()

        headers = {
            'HTTP_X_APPRISE_LOG_LEVEL': 'debug',
            'HTTP_ACCEPT': 'text/plain',
        }

        # Test referencing a key that doesn't exist
        response = self.client.post(
            '/notify/{}'.format(key),
            data=json.dumps(json_data),
            content_type='application/json',
            **headers,
        )

        assert response.status_code == 200
        assert mock_notify.call_count == 1
        assert response['content-type'] == 'text/plain'

        headers = {
            'HTTP_X_APPRISE_LOG_LEVEL': 'debug',
            'HTTP_ACCEPT': 'text/html',
        }

        mock_notify.reset_mock()

        # Test referencing a key that doesn't exist
        response = self.client.post(
            '/notify/{}'.format(key),
            data=json.dumps(json_data),
            content_type='application/json',
            **headers,
        )

        assert response.status_code == 200
        assert mock_notify.call_count == 1
        assert response['content-type'] == 'text/html'

        headers = {
            'HTTP_X_APPRISE_LOG_LEVEL': 'invalid',
            'HTTP_ACCEPT': 'text/*',
        }

        mock_notify.reset_mock()

        # Test referencing a key that doesn't exist
        response = self.client.post(
            '/notify/{}'.format(key),
            data=json.dumps(json_data),
            content_type='application/json',
            **headers,
        )

        assert response.status_code == 200
        assert mock_notify.call_count == 1
        assert response['content-type'] == 'text/html'

    @patch('apprise.plugins.NotifyEmail.send')
    def test_notify_with_filters(self, mock_send):
        """
        Test workings of APPRISE_DENY_SERVICES and APPRISE_ALLOW_SERVICES
        """

        # Set our return value
        mock_send.return_value = True

        # our key to use
        key = 'test_notify_with_restrictions'

        # Add some content
        response = self.client.post(
            '/add/{}'.format(key),
            {'urls': 'mailto://user:pass@yahoo.ca'})
        assert response.status_code == 200

        # Preare our JSON data
        json_data = {
            'body': 'test notifiction',
            'type': apprise.NotifyType.WARNING,
        }

        # Verify by default email is enabled
        assert apprise.common.NOTIFY_SCHEMA_MAP['mailto'].enabled is True

        # Send our service with the `mailto://` denied
        with override_settings(APPRISE_ALLOW_SERVICES=""):
            with override_settings(APPRISE_DENY_SERVICES="mailto"):
                # Send our notification as a JSON object
                response = self.client.post(
                    '/notify/{}'.format(key),
                    data=json.dumps(json_data),
                    content_type='application/json',
                )

                # mailto:// is disabled
                assert response.status_code == 424
                assert mock_send.call_count == 0

                # What actually took place behind close doors:
                assert \
                    apprise.common.NOTIFY_SCHEMA_MAP['mailto'].enabled is False

                # Reset our flag (for next test)
                apprise.common.NOTIFY_SCHEMA_MAP['mailto'].enabled = True

        # Reset Mock
        mock_send.reset_mock()

        # Send our service with the `mailto://` denied
        with override_settings(APPRISE_ALLOW_SERVICES=""):
            with override_settings(APPRISE_DENY_SERVICES="invalid, syslog"):
                # Send our notification as a JSON object
                response = self.client.post(
                    '/notify/{}'.format(key),
                    data=json.dumps(json_data),
                    content_type='application/json',
                )

                # mailto:// is enabled
                assert response.status_code == 200
                assert mock_send.call_count == 1

                # Verify that mailto was never turned off
                assert \
                    apprise.common.NOTIFY_SCHEMA_MAP['mailto'].enabled is True

        # Reset Mock
        mock_send.reset_mock()

        # Send our service with the `mailto://` being the only accepted type
        with override_settings(APPRISE_ALLOW_SERVICES="mailto"):
            with override_settings(APPRISE_DENY_SERVICES=""):
                # Send our notification as a JSON object
                response = self.client.post(
                    '/notify/{}'.format(key),
                    data=json.dumps(json_data),
                    content_type='application/json',
                )

                # mailto:// is enabled
                assert response.status_code == 200
                assert mock_send.call_count == 1

                # Verify email was never turned off
                assert \
                    apprise.common.NOTIFY_SCHEMA_MAP['mailto'].enabled is True

        # Reset Mock
        mock_send.reset_mock()

        # Send our service with the `mailto://` being the only accepted type
        with override_settings(APPRISE_ALLOW_SERVICES="invalid, mailtos"):
            with override_settings(APPRISE_DENY_SERVICES=""):
                # Send our notification as a JSON object
                response = self.client.post(
                    '/notify/{}'.format(key),
                    data=json.dumps(json_data),
                    content_type='application/json',
                )

                # mailto:// is enabled
                assert response.status_code == 200
                assert mock_send.call_count == 1

                # Verify email was never turned off
                assert \
                    apprise.common.NOTIFY_SCHEMA_MAP['mailto'].enabled is True

        # Reset Mock
        mock_send.reset_mock()

        # Send our service with the `mailto://` being the only accepted type
        with override_settings(APPRISE_ALLOW_SERVICES="syslog"):
            with override_settings(APPRISE_DENY_SERVICES=""):
                # Send our notification as a JSON object
                response = self.client.post(
                    '/notify/{}'.format(key),
                    data=json.dumps(json_data),
                    content_type='application/json',
                )

                # mailto:// is disabled
                assert response.status_code == 424
                assert mock_send.call_count == 0

                # What actually took place behind close doors:
                assert \
                    apprise.common.NOTIFY_SCHEMA_MAP['mailto']\
                    .enabled is False

                # Reset our flag (for next test)
                apprise.common.NOTIFY_SCHEMA_MAP['mailto'].enabled = True

        # Reset Mock
        mock_send.reset_mock()

        # Test case where there is simply no over-rides defined
        with override_settings(APPRISE_ALLOW_SERVICES=""):
            with override_settings(APPRISE_DENY_SERVICES=""):
                # Send our notification as a JSON object
                response = self.client.post(
                    '/notify/{}'.format(key),
                    data=json.dumps(json_data),
                    content_type='application/json',
                )

                # json:// is disabled
                assert response.status_code == 200
                assert mock_send.call_count == 1

                # nothing was changed
                assert \
                    apprise.common.NOTIFY_SCHEMA_MAP['mailto'].enabled is True

    @override_settings(APPRISE_RECURSION_MAX=1)
    @patch('apprise.Apprise.notify')
    def test_stateful_notify_recursion(self, mock_notify):
        """
        Test recursion an id header details as part of post
        """

        # Set our return value
        mock_notify.return_value = True

        # our key to use
        key = 'test_stateful_notify_recursion'

        # Add some content
        response = self.client.post(
            '/add/{}'.format(key),
            {'urls': 'mailto://user:pass@yahoo.ca'})
        assert response.status_code == 200

        # Form data
        form_data = {
            'body': 'test notifiction',
        }

        # Define our headers we plan to pass along with our request
        headers = {
            'HTTP_X-APPRISE-ID': 'abc123',
            'HTTP_X-APPRISE-RECURSION-COUNT': str(1),
        }

        # Send our notification
        response = self.client.post(
            '/notify/{}'.format(key), data=form_data, **headers)
        assert response.status_code == 200
        assert mock_notify.call_count == 1

        headers = {
            # Header specified but with whitespace
            'HTTP_X-APPRISE-ID': '  ',
            # No Recursion value specified
        }

        # Reset our count
        mock_notify.reset_mock()

        # Recursion limit reached
        response = self.client.post(
            '/notify/{}'.format(key), data=form_data, **headers)
        assert response.status_code == 200
        assert mock_notify.call_count == 1

        headers = {
            'HTTP_X-APPRISE-ID': 'abc123',
            # Recursion Limit hit
            'HTTP_X-APPRISE-RECURSION-COUNT': str(2),
        }

        # Reset our count
        mock_notify.reset_mock()

        # Recursion limit reached
        response = self.client.post(
            '/notify/{}'.format(key), data=form_data, **headers)
        assert response.status_code == 406
        assert mock_notify.call_count == 0

        headers = {
            'HTTP_X-APPRISE-ID': 'abc123',
            # Negative recursion value (bad request)
            'HTTP_X-APPRISE-RECURSION-COUNT': str(-1),
        }

        # Reset our count
        mock_notify.reset_mock()

        # invalid recursion specified
        response = self.client.post(
            '/notify/{}'.format(key), data=form_data, **headers)
        assert response.status_code == 400
        assert mock_notify.call_count == 0

        headers = {
            'HTTP_X-APPRISE-ID': 'abc123',
            # Invalid recursion value (bad request)
            'HTTP_X-APPRISE-RECURSION-COUNT': 'invalid',
        }

        # Reset our count
        mock_notify.reset_mock()

        # invalid recursion specified
        response = self.client.post(
            '/notify/{}'.format(key), data=form_data, **headers)
        assert response.status_code == 400
        assert mock_notify.call_count == 0
