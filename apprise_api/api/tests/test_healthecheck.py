# -*- coding: utf-8 -*-
#
# Copyright (C) 2024 Chris Caron <lead2gold@gmail.com>
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
import mock
from django.test import SimpleTestCase
from json import loads
from django.test.utils import override_settings
from ..utils import healthcheck


class HealthCheckTests(SimpleTestCase):

    def test_post_not_supported(self):
        """
        Test POST requests
        """
        response = self.client.post('/status')
        # 405 as posting is not allowed
        assert response.status_code == 405

    def test_healthcheck_simple(self):
        """
        Test retrieving basic successful health-checks
        """

        # First Status Check
        response = self.client.get('/status')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'OK')
        assert response['Content-Type'].startswith('text/plain')

        # Second Status Check (Lazy Mode kicks in)
        response = self.client.get('/status')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'OK')
        assert response['Content-Type'].startswith('text/plain')

        # JSON Response
        response = self.client.get(
            '/status', content_type='application/json',
            **{'HTTP_CONTENT_TYPE': 'application/json'})
        self.assertEqual(response.status_code, 200)
        content = loads(response.content)
        assert content == {
            'config_lock': False,
            'status': {
                'can_write_config': True,
                'can_write_attach': True,
                'details': ['OK']
            }
        }
        assert response['Content-Type'].startswith('application/json')

        with override_settings(APPRISE_CONFIG_LOCK=True):
            # Status Check (Form based)
            response = self.client.get('/status')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b'OK')
            assert response['Content-Type'].startswith('text/plain')

            # JSON Response
            response = self.client.get(
                '/status', content_type='application/json',
                **{'HTTP_CONTENT_TYPE': 'application/json'})
            self.assertEqual(response.status_code, 200)
            content = loads(response.content)
            assert content == {
                'config_lock': True,
                'status': {
                    'can_write_config': False,
                    'can_write_attach': True,
                    'details': ['OK']
                }
            }

        with override_settings(APPRISE_STATEFUL_MODE='disabled'):
            # Status Check (Form based)
            response = self.client.get('/status')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b'OK')
            assert response['Content-Type'].startswith('text/plain')

            # JSON Response
            response = self.client.get(
                '/status', content_type='application/json',
                **{'HTTP_CONTENT_TYPE': 'application/json'})
            self.assertEqual(response.status_code, 200)
            content = loads(response.content)
            assert content == {
                'config_lock': False,
                'status': {
                    'can_write_config': False,
                    'can_write_attach': True,
                    'details': ['OK']
                }
            }

        with override_settings(APPRISE_ATTACH_SIZE=0):
            # Status Check (Form based)
            response = self.client.get('/status')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b'OK')
            assert response['Content-Type'].startswith('text/plain')

            # JSON Response
            response = self.client.get(
                '/status', content_type='application/json',
                **{'HTTP_CONTENT_TYPE': 'application/json'})
            self.assertEqual(response.status_code, 200)
            content = loads(response.content)
            assert content == {
                'config_lock': False,
                'status': {
                    'can_write_config': True,
                    'can_write_attach': False,
                    'details': ['OK']
                }
            }

        with override_settings(APPRISE_MAX_ATTACHMENTS=0):
            # Status Check (Form based)
            response = self.client.get('/status')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b'OK')
            assert response['Content-Type'].startswith('text/plain')

            # JSON Response
            response = self.client.get(
                '/status', content_type='application/json',
                **{'HTTP_CONTENT_TYPE': 'application/json'})
            self.assertEqual(response.status_code, 200)
            content = loads(response.content)
            assert content == {
                'config_lock': False,
                'status': {
                    'can_write_config': True,
                    'can_write_attach': False,
                    'details': ['OK']
                }
            }

    def test_healthcheck_library(self):
        """
        Test underlining healthcheck library
        """

        result = healthcheck(lazy=True)
        assert result == {
            'can_write_config': True,
            'can_write_attach': True,
            'details': ['OK']
        }

        # A Double lazy check
        result = healthcheck(lazy=True)
        assert result == {
            'can_write_config': True,
            'can_write_attach': True,
            'details': ['OK']
        }

        # Force a lazy check where we can't acquire the modify time
        with mock.patch('os.path.getmtime') as mock_getmtime:
            mock_getmtime.side_effect = FileNotFoundError()
            result = healthcheck(lazy=True)
            # We still succeed; we just don't leverage our lazy check
            # which prevents addition (unnessisary) writes
            assert result == {
                'can_write_config': True,
                'can_write_attach': True,
                'details': ['OK'],
            }

        # Force a non-lazy check
        with mock.patch('os.makedirs') as mock_makedirs:
            mock_makedirs.side_effect = OSError()
            result = healthcheck(lazy=False)
            assert result == {
                'can_write_config': False,
                'can_write_attach': False,
                'details': [
                    'CONFIG_PERMISSION_ISSUE',
                    'ATTACH_PERMISSION_ISSUE',
                ]}

            mock_makedirs.side_effect = (None, OSError())
            result = healthcheck(lazy=False)
            assert result == {
                'can_write_config': True,
                'can_write_attach': False,
                'details': [
                    'ATTACH_PERMISSION_ISSUE',
                ]}

            mock_makedirs.side_effect = (OSError(), None)
            result = healthcheck(lazy=False)
            assert result == {
                'can_write_config': False,
                'can_write_attach': True,
                'details': [
                    'CONFIG_PERMISSION_ISSUE',
                ]}
