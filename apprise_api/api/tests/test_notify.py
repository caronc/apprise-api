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

        # Send our notification
        response = self.client.post(
            '/notify/{}'.format(key), form.cleaned_data)
        assert response.status_code == 200
        assert mock_notify.call_count == 1

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

        with patch('tempfile.NamedTemporaryFile') as mock_ntf:
            mock_ntf.side_effect = OSError
            # we won't be able to write our retrieved configuration
            # to disk for processing; we'll get a 500 error
            response = self.client.post(
                '/notify/{}'.format(key),
                data=json.dumps(json_data),
                content_type='application/json',
            )

            # internal errors are correctly identified
            assert response.status_code == 500
            assert mock_notify.call_count == 0

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
