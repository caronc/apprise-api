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
from django.core.exceptions import RequestDataTooBig
from apprise import ConfigFormat
from unittest.mock import patch
from unittest import mock
from django.test.utils import override_settings
from ..forms import AUTO_DETECT_CONFIG_KEYWORD
import json
import hashlib


class AddTests(SimpleTestCase):

    def test_add_invalid_key_status_code(self):
        """
        Test GET requests to invalid key
        """
        response = self.client.get('/add/**invalid-key**')
        assert response.status_code == 404

    def test_key_lengths(self):
        """
        Test our key lengths
        """

        # our key to use
        h = hashlib.sha512()
        h.update(b'string')
        key = h.hexdigest()

        # Our limit
        assert len(key) == 128

        # Add our URL
        response = self.client.post(
            '/add/{}'.format(key), {'urls': 'mailto://user:pass@yahoo.ca'})
        assert response.status_code == 200

        # However adding just 1 more character exceeds our limit and the save
        # will fail
        response = self.client.post(
            '/add/{}'.format(key + 'x'),
            {'urls': 'mailto://user:pass@yahoo.ca'})
        assert response.status_code == 404

    @override_settings(APPRISE_CONFIG_LOCK=True)
    def test_save_config_by_urls_with_lock(self):
        """
        Test adding a configuration by URLs with lock set won't work
        """
        # our key to use
        key = 'test_save_config_by_urls_with_lock'

        # We simply do not have permission to do so
        response = self.client.post(
            '/add/{}'.format(key), {'urls': 'mailto://user:pass@yahoo.ca'})
        assert response.status_code == 403

    def test_save_config_by_urls(self):
        """
        Test adding an configuration by URLs
        """

        # our key to use
        key = 'test_save_config_by_urls'

        # GET returns 405 (not allowed)
        response = self.client.get('/add/{}'.format(key))
        assert response.status_code == 405

        # no data
        response = self.client.post('/add/{}'.format(key))
        assert response.status_code == 400

        # No entries specified
        response = self.client.post(
            '/add/{}'.format(key), {'urls': ''})
        assert response.status_code == 400

        # Added successfully
        response = self.client.post(
            '/add/{}'.format(key), {'urls': 'mailto://user:pass@yahoo.ca'})
        assert response.status_code == 200

        # No URLs loaded
        response = self.client.post(
            '/add/{}'.format(key),
            {'config': 'invalid content', 'format': 'text'})
        assert response.status_code == 400

        # Test a case where we fail to load a valid configuration file
        with patch('apprise.AppriseConfig.add', return_value=False):
            response = self.client.post(
                '/add/{}'.format(key),
                {'config': 'garbage://', 'format': 'text'})
        assert response.status_code == 400

        with patch('os.remove', side_effect=OSError):
            # We will fail to remove the device first prior to placing a new
            # one;  This will result in a 500 error
            response = self.client.post(
                '/add/{}'.format(key), {
                    'urls': 'mailto://user:newpass@gmail.com'})
            assert response.status_code == 500

        # URL is actually not a valid one (invalid Slack tokens specified
        # below)
        response = self.client.post(
            '/add/{}'.format(key), {'urls': 'slack://-/-/-'})
        assert response.status_code == 400

        # Test with JSON
        response = self.client.post(
            '/add/{}'.format(key),
            data=json.dumps({'urls': 'mailto://user:pass@yahoo.ca'}),
            content_type='application/json',
        )
        assert response.status_code == 200

        with mock.patch('json.loads') as mock_loads:
            mock_loads.side_effect = RequestDataTooBig()
            # Send our notification by specifying the tag in the parameters
            response = self.client.post(
                '/add/{}'.format(key),
                data=json.dumps({'urls': 'mailto://user:pass@yahoo.ca'}),
                content_type='application/json',
            )

            # Our notification failed
            assert response.status_code == 431

        # Test with JSON (and no payload provided)
        response = self.client.post(
            '/add/{}'.format(key),
            data=json.dumps({}),
            content_type='application/json',
        )
        assert response.status_code == 400

        # Test with XML which simply isn't supported
        response = self.client.post(
            '/add/{}'.format(key),
            data='<urls><url>mailto://user:pass@yahoo.ca</url></urls>',
            content_type='application/xml',
        )
        assert response.status_code == 400

        # Invalid JSON
        response = self.client.post(
            '/add/{}'.format(key),
            data='{',
            content_type='application/json',
        )
        assert response.status_code == 400

        # Test the handling of underlining disk/write exceptions
        with patch('os.makedirs') as mock_mkdirs:
            mock_mkdirs.side_effect = OSError()
            # We'll fail to write our key now
            response = self.client.post(
                '/add/{}'.format(key),
                data=json.dumps({'urls': 'mailto://user:pass@yahoo.ca'}),
                content_type='application/json',
            )

            # internal errors are correctly identified
            assert response.status_code == 500

        # Test the handling of underlining disk/write exceptions
        with patch('gzip.open') as mock_open:
            mock_open.side_effect = OSError()
            # We'll fail to write our key now
            response = self.client.post(
                '/add/{}'.format(key),
                data=json.dumps({'urls': 'mailto://user:pass@yahoo.ca'}),
                content_type='application/json',
            )

            # internal errors are correctly identified
            assert response.status_code == 500

    def test_save_config_by_config(self):
        """
        Test adding an configuration by a config file
        """

        # our key to use
        key = 'test_save_config_by_config'

        # Empty Text Configuration
        config = """

        """  # noqa W293
        response = self.client.post(
            '/add/{}'.format(key), {
                'format': ConfigFormat.TEXT, 'config': config})
        assert response.status_code == 400

        # Valid Text Configuration
        config = """
        browser,media=notica://VTokenC
        home=mailto://user:pass@hotmail.com
        """
        response = self.client.post(
            '/add/{}'.format(key),
            {'format': ConfigFormat.TEXT, 'config': config})
        assert response.status_code == 200

        # Test with JSON
        response = self.client.post(
            '/add/{}'.format(key),
            data=json.dumps({'format': ConfigFormat.TEXT, 'config': config}),
            content_type='application/json',
        )
        assert response.status_code == 200

        # Valid Yaml Configuration
        config = """
        urls:
          - notica://VTokenD:
              tag: browser,media
          - mailto://user:pass@hotmail.com:
              tag: home
        """
        response = self.client.post(
            '/add/{}'.format(key),
            {'format': ConfigFormat.YAML, 'config': config})
        assert response.status_code == 200

        # Test with JSON
        response = self.client.post(
            '/add/{}'.format(key),
            data=json.dumps({'format': ConfigFormat.YAML, 'config': config}),
            content_type='application/json',
        )
        assert response.status_code == 200

        # Test invalid config format
        response = self.client.post(
            '/add/{}'.format(key),
            data=json.dumps({'format': 'INVALID', 'config': config}),
            content_type='application/json',
        )
        assert response.status_code == 400

        # Test the handling of underlining disk/write exceptions
        with patch('gzip.open') as mock_open:
            mock_open.side_effect = OSError()
            # We'll fail to write our key now
            response = self.client.post(
                '/add/{}'.format(key),
                data=json.dumps(
                    {'format': ConfigFormat.YAML, 'config': config}),
                content_type='application/json',
            )

            # internal errors are correctly identified
            assert response.status_code == 500

    def test_save_auto_detect_config_format(self):
        """
        Test adding an configuration and using the autodetect feature
        """

        # our key to use
        key = 'test_save_auto_detect_config_format'

        # Empty Text Configuration
        config = """

        """  # noqa W293
        response = self.client.post(
            '/add/{}'.format(key), {
                'format': AUTO_DETECT_CONFIG_KEYWORD, 'config': config})
        assert response.status_code == 400

        # Valid Text Configuration
        config = """
        browser,media=notica://VTokenA
        home=mailto://user:pass@hotmail.com
        """
        response = self.client.post(
            '/add/{}'.format(key),
            {'format': AUTO_DETECT_CONFIG_KEYWORD, 'config': config})
        assert response.status_code == 200

        # Test with JSON
        response = self.client.post(
            '/add/{}'.format(key),
            data=json.dumps({'format': ConfigFormat.TEXT, 'config': config}),
            content_type='application/json',
        )
        assert response.status_code == 200

        # Valid Yaml Configuration
        config = """
        urls:
          - notica://VTokenB:
              tag: browser,media

          - mailto://user:pass@hotmail.com:
              tag: home
        """
        response = self.client.post(
            '/add/{}'.format(key),
            {'format': AUTO_DETECT_CONFIG_KEYWORD, 'config': config})
        assert response.status_code == 200

        # Test with JSON
        response = self.client.post(
            '/add/{}'.format(key),
            data=json.dumps(
                {'format': AUTO_DETECT_CONFIG_KEYWORD, 'config': config}),
            content_type='application/json',
        )
        assert response.status_code == 200

        # Test invalid config format that can not be auto-detected
        config = """
        42
        """
        response = self.client.post(
            '/add/{}'.format(key),
            {'format': AUTO_DETECT_CONFIG_KEYWORD, 'config': config})
        assert response.status_code == 400

        # Test with JSON
        response = self.client.post(
            '/add/{}'.format(key),
            data=json.dumps(
                {'format': AUTO_DETECT_CONFIG_KEYWORD, 'config': config}),
            content_type='application/json',
        )
        assert response.status_code == 400

    def test_save_with_bad_input(self):
        """
        Test adding with bad input in general
        """

        # our key to use
        key = 'test_save_with_bad_input'
        # Test with JSON
        response = self.client.post(
            '/add/{}'.format(key),
            data=json.dumps({'garbage': 'input'}),
            content_type='application/json',
        )
        assert response.status_code == 400
