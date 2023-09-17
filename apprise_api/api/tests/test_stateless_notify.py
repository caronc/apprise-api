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
from django.test.utils import override_settings
from unittest.mock import patch
from ..forms import NotifyByUrlForm
import json
import apprise


class StatelessNotifyTests(SimpleTestCase):
    """
    Test stateless notifications
    """

    @patch('apprise.Apprise.notify')
    def test_notify(self, mock_notify):
        """
        Test sending a simple notification
        """

        # Set our return value
        mock_notify.return_value = True

        # Preare our form data
        form_data = {
            'urls': 'mailto://user:pass@hotmail.com',
            'body': 'test notifiction',
        }

        # At a minimum 'body' is requred
        form = NotifyByUrlForm(data=form_data)
        assert form.is_valid()

        # Required to prevent None from being passed into self.client.post()
        del form.cleaned_data['attachment']

        response = self.client.post('/notify', form.cleaned_data)
        assert response.status_code == 200
        assert mock_notify.call_count == 1

        # Reset our count
        mock_notify.reset_mock()
        form_data = {
            'urls': 'mailto://user:pass@hotmail.com',
            'body': 'test notifiction',
            'format': apprise.NotifyFormat.MARKDOWN,
        }
        form = NotifyByUrlForm(data=form_data)
        assert form.is_valid()

        # Required to prevent None from being passed into self.client.post()
        del form.cleaned_data['attachment']

        response = self.client.post('/notify', form.cleaned_data)
        assert response.status_code == 200
        assert mock_notify.call_count == 1

        # Reset our count
        mock_notify.reset_mock()
        form_data = {
            'urls': 'mailto://user:pass@hotmail.com',
            'body': 'test notifiction',
            # Invalid formats cause an error
            'format': 'invalid'
        }
        form = NotifyByUrlForm(data=form_data)
        assert not form.is_valid()

    @patch('apprise.NotifyBase.notify')
    def test_partial_notify(self, mock_notify):
        """
        Test sending multiple notifications where one fails
        """

        # Ensure we're enabled for the purpose of our testing
        apprise.common.NOTIFY_SCHEMA_MAP['mailto'].enabled = True

        # Set our return value; first we return a true, then we fail
        # on the second call
        mock_notify.side_effect = (True, False)

        # Preare our form data
        form_data = {
            'urls': ', '.join([
                'mailto://user:pass@hotmail.com',
                'mailto://user:pass@gmail.com',
            ]),
            'body': 'test notifiction',
        }

        # At a minimum 'body' is requred
        form = NotifyByUrlForm(data=form_data)
        assert form.is_valid()

        # Required to prevent None from being passed into self.client.post()
        del form.cleaned_data['attachment']

        response = self.client.post('/notify', form.cleaned_data)
        assert response.status_code == 424
        assert mock_notify.call_count == 2

    @override_settings(APPRISE_RECURSION_MAX=1)
    @patch('apprise.Apprise.notify')
    def test_stateless_notify_recursion(self, mock_notify):
        """
        Test recursion an id header details as part of post
        """

        # Set our return value
        mock_notify.return_value = True

        headers = {
            'HTTP_X-APPRISE-ID': 'abc123',
            'HTTP_X-APPRISE-RECURSION-COUNT': str(1),
        }

        # Preare our form data (without url specified)
        # content will fall back to default configuration
        form_data = {
            'urls': 'mailto://user:pass@hotmail.com',
            'body': 'test notifiction',
        }

        # Monkey Patch
        apprise.plugins.NotifyEmail.NotifyEmail.enabled = True

        # At a minimum 'body' is requred
        form = NotifyByUrlForm(data=form_data)
        assert form.is_valid()

        # Required to prevent None from being passed into self.client.post()
        del form.cleaned_data['attachment']

        # recursion value is within correct limits
        response = self.client.post('/notify', form.cleaned_data, **headers)
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
        response = self.client.post('/notify', form.cleaned_data, **headers)
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
        response = self.client.post('/notify', form.cleaned_data, **headers)
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
        response = self.client.post('/notify', form.cleaned_data, **headers)
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
        response = self.client.post('/notify', form.cleaned_data, **headers)
        assert response.status_code == 400
        assert mock_notify.call_count == 0

    @override_settings(APPRISE_STATELESS_URLS="mailto://user:pass@localhost")
    @patch('apprise.Apprise.notify')
    def test_notify_default_urls(self, mock_notify):
        """
        Test fallback to default URLS if none were otherwise specified
        in the post
        """

        # Set our return value
        mock_notify.return_value = True

        # Preare our form data (without url specified)
        # content will fall back to default configuration
        form_data = {
            'body': 'test notifiction',
        }

        # At a minimum 'body' is requred
        form = NotifyByUrlForm(data=form_data)
        assert form.is_valid()

        # Required to prevent None from being passed into self.client.post()
        del form.cleaned_data['attachment']

        # This still works as the environment variable kicks in
        response = self.client.post('/notify', form.cleaned_data)
        assert response.status_code == 200
        assert mock_notify.call_count == 1

    @patch('apprise.Apprise.notify')
    def test_notify_by_loaded_urls_with_json(self, mock_notify):
        """
        Test sending a simple notification using JSON
        """

        # Set our return value
        mock_notify.return_value = True

        # Preare our JSON data without any urls
        json_data = {
            'urls': '',
            'body': 'test notifiction',
            'type': apprise.NotifyType.WARNING,
        }

        # Send our empty notification as a JSON object
        response = self.client.post(
            '/notify',
            data=json.dumps(json_data),
            content_type='application/json',
        )

        # Nothing notified
        assert response.status_code == 204
        assert mock_notify.call_count == 0

        # Preare our JSON data
        json_data = {
            'urls': 'mailto://user:pass@yahoo.ca',
            'body': 'test notifiction',
            'type': apprise.NotifyType.WARNING,
        }

        # Send our notification as a JSON object
        response = self.client.post(
            '/notify',
            data=json.dumps(json_data),
            content_type='application/json',
        )

        # Still supported
        assert response.status_code == 200
        assert mock_notify.call_count == 1

        # Reset our count
        mock_notify.reset_mock()

        # Test sending a garbage JSON object
        response = self.client.post(
            '/notify/',
            data="{",
            content_type='application/json',
        )

        assert response.status_code == 400
        assert mock_notify.call_count == 0

        # Test sending with an invalid content type
        response = self.client.post(
            '/notify',
            data="{}",
            content_type='application/xml',
        )

        assert response.status_code == 400
        assert mock_notify.call_count == 0

        # Test sending without any content at all
        response = self.client.post(
            '/notify/',
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
            '/notify',
            data=json.dumps(json_data),
            content_type='application/json',
        )

        assert response.status_code == 400
        assert mock_notify.call_count == 0

        # Reset our count
        mock_notify.reset_mock()

        # Preare our JSON data
        json_data = {
            'urls': 'mailto://user:pass@yahoo.ca',
            'body': 'test notifiction',
            # invalid server side format
            'format': 'invalid'
        }

        # Send our notification as a JSON object
        response = self.client.post(
            '/notify',
            data=json.dumps(json_data),
            content_type='application/json',
        )

        # Still supported
        assert response.status_code == 400
        assert mock_notify.call_count == 0

    @patch('apprise.plugins.NotifyJSON.NotifyJSON.send')
    def test_notify_with_filters(self, mock_send):
        """
        Test workings of APPRISE_DENY_SERVICES and APPRISE_ALLOW_SERVICES
        """

        # Set our return value
        mock_send.return_value = True

        # Preare our JSON data
        json_data = {
            'urls': 'json://user:pass@yahoo.ca',
            'body': 'test notifiction',
            'type': apprise.NotifyType.WARNING,
        }

        # Send our notification as a JSON object
        response = self.client.post(
            '/notify',
            data=json.dumps(json_data),
            content_type='application/json',
        )

        # Ensure we're enabled for the purpose of our testing
        apprise.common.NOTIFY_SCHEMA_MAP['json'].enabled = True

        # Reset Mock
        mock_send.reset_mock()

        # Send our service with the `json://` denied
        with override_settings(APPRISE_ALLOW_SERVICES=""):
            with override_settings(APPRISE_DENY_SERVICES="json"):
                # Send our notification as a JSON object
                response = self.client.post(
                    '/notify',
                    data=json.dumps(json_data),
                    content_type='application/json',
                )

                # json:// is disabled
                assert response.status_code == 204
                assert mock_send.call_count == 0

                # What actually took place behind close doors:
                assert \
                    apprise.common.NOTIFY_SCHEMA_MAP['json'].enabled is False

                # Reset our flag (for next test)
                apprise.common.NOTIFY_SCHEMA_MAP['json'].enabled = True

        # Reset Mock
        mock_send.reset_mock()

        # Send our service with the `json://` denied
        with override_settings(APPRISE_ALLOW_SERVICES=""):
            with override_settings(APPRISE_DENY_SERVICES="invalid, syslog"):
                # Send our notification as a JSON object
                response = self.client.post(
                    '/notify',
                    data=json.dumps(json_data),
                    content_type='application/json',
                )

                # json:// is enabled
                assert response.status_code == 200
                assert mock_send.call_count == 1

                # Verify that json was never turned off
                assert apprise.common.NOTIFY_SCHEMA_MAP['json'].enabled is True

        # Reset Mock
        mock_send.reset_mock()

        # Send our service with the `json://` being the only accepted type
        with override_settings(APPRISE_ALLOW_SERVICES="json"):
            with override_settings(APPRISE_DENY_SERVICES=""):
                # Send our notification as a JSON object
                response = self.client.post(
                    '/notify',
                    data=json.dumps(json_data),
                    content_type='application/json',
                )

                # json:// is enabled
                assert response.status_code == 200
                assert mock_send.call_count == 1

                # Verify email was never turned off
                assert apprise.common.NOTIFY_SCHEMA_MAP['json'].enabled is True

        # Reset Mock
        mock_send.reset_mock()

        # Send our service with the `json://` being the only accepted type
        with override_settings(APPRISE_ALLOW_SERVICES="invalid, jsons"):
            with override_settings(APPRISE_DENY_SERVICES=""):
                # Send our notification as a JSON object
                response = self.client.post(
                    '/notify',
                    data=json.dumps(json_data),
                    content_type='application/json',
                )

                # json:// is enabled
                assert response.status_code == 200
                assert mock_send.call_count == 1

                # Verify email was never turned off
                assert apprise.common.NOTIFY_SCHEMA_MAP['json'].enabled is True

        # Reset Mock
        mock_send.reset_mock()

        # Send our service with the `json://` being the only accepted type
        with override_settings(APPRISE_ALLOW_SERVICES="syslog"):
            with override_settings(APPRISE_DENY_SERVICES=""):
                # Send our notification as a JSON object
                response = self.client.post(
                    '/notify',
                    data=json.dumps(json_data),
                    content_type='application/json',
                )

                # json:// is disabled
                assert response.status_code == 204
                assert mock_send.call_count == 0

                # What actually took place behind close doors:
                assert \
                    apprise.common.NOTIFY_SCHEMA_MAP['json'].enabled is False

                # Reset our flag (for next test)
                apprise.common.NOTIFY_SCHEMA_MAP['json'].enabled = True

        # Reset Mock
        mock_send.reset_mock()

        # Test case where there is simply no over-rides defined
        with override_settings(APPRISE_ALLOW_SERVICES=""):
            with override_settings(APPRISE_DENY_SERVICES=""):
                # Send our notification as a JSON object
                response = self.client.post(
                    '/notify',
                    data=json.dumps(json_data),
                    content_type='application/json',
                )

                # json:// is disabled
                assert response.status_code == 200
                assert mock_send.call_count == 1

                # nothing was changed
                assert apprise.common.NOTIFY_SCHEMA_MAP['json'].enabled is True
