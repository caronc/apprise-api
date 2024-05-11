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
from unittest import mock
from json import loads
import requests
from ..utils import send_webhook
from django.test.utils import override_settings


class WebhookTests(SimpleTestCase):

    @mock.patch('requests.post')
    def test_webhook_testing(self, mock_post):
        """
        Test webhook handling
        """

        # Response object
        response = mock.Mock()
        response.status_code = requests.codes.ok
        mock_post.return_value = response

        with override_settings(
                APPRISE_WEBHOOK_URL='https://'
                'user:pass@localhost/webhook'):
            send_webhook({})
            assert mock_post.call_count == 1

            details = mock_post.call_args_list[0]
            assert details[0][0] == 'https://localhost/webhook'
            assert loads(details[1]['data']) == {}
            assert 'User-Agent' in details[1]['headers']
            assert 'Content-Type' in details[1]['headers']
            assert details[1]['headers']['User-Agent'] == 'Apprise-API'
            assert details[1]['headers']['Content-Type'] == 'application/json'
            assert details[1]['auth'] == ('user', 'pass')
            assert details[1]['verify'] is True
            assert details[1]['params'] == {}
            assert details[1]['timeout'] == (4.0, 4.0)

        mock_post.reset_mock()

        with override_settings(
                APPRISE_WEBHOOK_URL='http://'
                'user@localhost/webhook/here'
                '?verify=False&key=value&cto=2.0&rto=1.0'):
            send_webhook({})
            assert mock_post.call_count == 1

            details = mock_post.call_args_list[0]
            assert details[0][0] == 'http://localhost/webhook/here'
            assert loads(details[1]['data']) == {}
            assert 'User-Agent' in details[1]['headers']
            assert 'Content-Type' in details[1]['headers']
            assert details[1]['headers']['User-Agent'] == 'Apprise-API'
            assert details[1]['headers']['Content-Type'] == 'application/json'
            assert details[1]['auth'] == ('user', None)
            assert details[1]['verify'] is False
            assert details[1]['params'] == {'key': 'value'}
            assert details[1]['timeout'] == (2.0, 1.0)

        mock_post.reset_mock()

        with override_settings(APPRISE_WEBHOOK_URL='invalid'):
            # Invalid webhook defined
            send_webhook({})
            assert mock_post.call_count == 0

        mock_post.reset_mock()

        with override_settings(APPRISE_WEBHOOK_URL=None):
            # Invalid webhook defined
            send_webhook({})
            assert mock_post.call_count == 0

        mock_post.reset_mock()

        with override_settings(APPRISE_WEBHOOK_URL='http://$#@'):
            # Invalid hostname defined
            send_webhook({})
            assert mock_post.call_count == 0

        mock_post.reset_mock()

        with override_settings(
                APPRISE_WEBHOOK_URL='invalid://hostname'):
            # Invalid webhook defined
            send_webhook({})
            assert mock_post.call_count == 0

        mock_post.reset_mock()

        # A valid URL with a bad server response:
        response.status_code = requests.codes.internal_server_error
        mock_post.return_value = response
        with override_settings(
                APPRISE_WEBHOOK_URL='http://localhost'):

            send_webhook({})
            assert mock_post.call_count == 1

        mock_post.reset_mock()

        # A valid URL with a bad server response:
        mock_post.return_value = None
        mock_post.side_effect = requests.RequestException("error")
        with override_settings(
                APPRISE_WEBHOOK_URL='http://localhost'):

            send_webhook({})
            assert mock_post.call_count == 1
