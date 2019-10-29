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

        response = self.client.post('/notify', form.cleaned_data)
        assert response.status_code == 200
        assert mock_notify.call_count == 1

    @override_settings(APPRISE_STATELESS_URLS="windows://")
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
