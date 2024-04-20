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
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import RequestDataTooBig
from django.test.utils import override_settings
from unittest import mock
from ..forms import NotifyByUrlForm
import requests
import json
import apprise

# Grant access to our Notification Manager Singleton
N_MGR = apprise.NotificationManager.NotificationManager()


class StatelessNotifyTests(SimpleTestCase):
    """
    Test stateless notifications
    """

    @mock.patch('apprise.Apprise.notify')
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

        # Reset our mock object
        mock_notify.reset_mock()

        # Test Headers
        for level in ('CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG',
                      'TRACE', 'INVALID'):

            form_data = {
                'urls': 'mailto://user:pass@hotmail.com',
                'body': 'test notifiction',
                'format': apprise.NotifyFormat.MARKDOWN,
            }

            attach_data = {
                'attachment': SimpleUploadedFile(
                    "attach.txt", b"content here", content_type="text/plain")
            }

            # At a minimum, just a body is required
            form = NotifyByUrlForm(form_data, attach_data)
            assert form.is_valid()

            # Prepare our header
            headers = {
                'HTTP_X-APPRISE-LOG-LEVEL': level,
            }

            # Send our notification
            response = self.client.post(
                '/notify', form.cleaned_data, **headers)
            assert response.status_code == 200
            assert mock_notify.call_count == 1

            # Reset our mock object
            mock_notify.reset_mock()

        # Long Filename
        attach_data = {
            'attachment': SimpleUploadedFile(
                "{}.txt".format('a' * 2000),
                b"content here", content_type="text/plain")
        }

        # At a minimum, just a body is required
        form = NotifyByUrlForm(form_data, attach_data)
        assert form.is_valid()

        # Send our notification
        response = self.client.post('/notify', form.cleaned_data)

        # We fail because the filename is too long
        assert response.status_code == 400
        assert mock_notify.call_count == 0

        # Reset our mock object
        mock_notify.reset_mock()

        # Test Webhooks
        with mock.patch('requests.post') as mock_post:
            # Response object
            response = mock.Mock()
            response.status_code = requests.codes.ok
            mock_post.return_value = response

            with override_settings(
                    APPRISE_WEBHOOK_URL='http://localhost/webhook/'):

                # Preare our form data
                form_data = {
                    'urls': 'mailto://user:pass@hotmail.com',
                    'body': 'test notifiction',
                    'format': apprise.NotifyFormat.MARKDOWN,
                }

                # At a minimum, just a body is required
                form = NotifyByUrlForm(data=form_data)
                assert form.is_valid()

                # Required to prevent None from being passed into
                # self.client.post()
                del form.cleaned_data['attachment']

                # Send our notification
                response = self.client.post('/notify', form.cleaned_data)

                # Test our results
                assert response.status_code == 200
                assert mock_notify.call_count == 1
                assert mock_post.call_count == 1

                # Reset our mock object
                mock_notify.reset_mock()

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

        # Required to prevent None from being passed into self.client.post()
        del form.cleaned_data['attachment']

        # Send our notification
        response = self.client.post('/notify', form.cleaned_data)

        # Test our results
        assert response.status_code == 200
        assert mock_notify.call_count == 1

    @mock.patch('apprise.NotifyBase.notify')
    def test_partial_notify(self, mock_notify):
        """
        Test sending multiple notifications where one fails
        """

        # Ensure we're enabled for the purpose of our testing
        N_MGR['mailto'].enabled = True

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

        # Reset our mock object
        mock_notify.reset_mock()

        # Preare our form data
        form_data = {
            'body': 'test notifiction',
            'urls': ', '.join([
                'mailto://user:pass@hotmail.com',
                'mailto://user:pass@gmail.com',
            ]),
            'attachment': 'https://localhost/invalid/path/to/image.png',
        }

        # Send our notification
        response = self.client.post('/notify', form_data)
        # We fail because we couldn't retrieve our attachment
        assert response.status_code == 400
        assert mock_notify.call_count == 0

        # Reset our mock object
        mock_notify.reset_mock()

        # Preare our form data (support attach keyword)
        form_data = {
            'body': 'test notifiction',
            'urls': ', '.join([
                'mailto://user:pass@hotmail.com',
                'mailto://user:pass@gmail.com',
            ]),
            'attach': 'https://localhost/invalid/path/to/image.png',
        }

        # Send our notification
        response = self.client.post('/notify', form_data)
        # We fail because we couldn't retrieve our attachment
        assert response.status_code == 400
        assert mock_notify.call_count == 0

        # Reset our mock object
        mock_notify.reset_mock()

        # Preare our json data (and support attach keyword as alias)
        json_data = {
            'body': 'test notifiction',
            'urls': ', '.join([
                'mailto://user:pass@hotmail.com',
                'mailto://user:pass@gmail.com',
            ]),
            'attach': 'https://localhost/invalid/path/to/image.png',
        }

        # Same results
        response = self.client.post(
            '/notify/',
            data=json.dumps(json_data),
            content_type='application/json',
        )

        # We fail because we couldn't retrieve our attachment
        assert response.status_code == 400
        assert mock_notify.call_count == 0

    @override_settings(APPRISE_RECURSION_MAX=1)
    @mock.patch('apprise.Apprise.notify')
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
    @mock.patch('apprise.Apprise.notify')
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

    @mock.patch('apprise.Apprise.notify')
    def test_notify_with_get_parameters(self, mock_notify):
        """
        Test sending a simple notification using JSON with GET
        parameters
        """

        # Set our return value
        mock_notify.return_value = True

        # Preare our JSON data
        json_data = {
            'urls': 'json://user@my.domain.ca',
            'body': 'test notifiction',
        }

        # Send our notification as a JSON object
        response = self.client.post(
            '/notify/?title=my%20title&format=text&type=info',
            data=json.dumps(json_data),
            content_type='application/json',
        )

        # Still supported
        assert response.status_code == 200
        assert mock_notify.call_count == 1

        # Reset our count
        mock_notify.reset_mock()

        with mock.patch('json.loads') as mock_loads:
            mock_loads.side_effect = RequestDataTooBig()
            # Send our notification
            response = self.client.post(
                '/notify/?title=my%20title&format=text&type=info',
                data=json.dumps(json_data),
                content_type='application/json',
            )

            # Our notification failed
            assert response.status_code == 431
            assert mock_notify.call_count == 0

    @mock.patch('apprise.Apprise.notify')
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

    @mock.patch('apprise.plugins.NotifyJSON.NotifyJSON.send')
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
        N_MGR['json'].enabled = True

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
                assert N_MGR['json'].enabled is False

                # Reset our flag (for next test)
                N_MGR['json'].enabled = True

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
                assert N_MGR['json'].enabled is True

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
                assert N_MGR['json'].enabled is True

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
                assert N_MGR['json'].enabled is True

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
                assert N_MGR['json'].enabled is False

                # Reset our flag (for next test)
                N_MGR['json'].enabled = True

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
                assert N_MGR['json'].enabled is True
