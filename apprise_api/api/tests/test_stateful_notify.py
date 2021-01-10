# -*- coding: utf-8 -*-
#
# Copyright (C) 2020 Chris Caron <lead2gold@gmail.com>
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
from ..utils import ConfigCache
import os
import re
import apprise


class StatefulNotifyTests(SimpleTestCase):
    """
    Test stateless notifications
    """

    @patch('apprise.Apprise.notify')
    def test_stateful_configuration_io(self, mock_notify):
        """
        Test the writing, removal, writing and removal of configuration to
        verify it persists and is removed when expected
        """

        # our key to use
        key = 'test_stateful'

        # Set our return value
        mock_notify.return_value = True

        # Preare our list of URLs we want to save
        urls = [
            'mail=mailto://user:pass@hotmail.com',
            'devops=slack://TokenA/TokenB/TokenC',
            'pusbullet=pbul://tokendetails',
        ]

        # For 10 iterations, repeat these tests to verify that don't change
        # and our saved content is not different on subsequent calls.
        for _ in range(10):
            # No content saved to the location yet
            response = self.client.post('/get/{}'.format(key))
            assert response.status_code == 204

            # Add our content
            response = self.client.post(
                '/add/{}'.format(key),
                {'config': '\r\n'.join(urls)})
            assert response.status_code == 200

            # Now we should be able to see our content
            response = self.client.post('/get/{}'.format(key))
            assert response.status_code == 200

            entries = re.split(r'[\r*\n]+', response.content.decode('utf-8'))
            assert len(entries) == 3

            form_data = {
                'body': '## test notifiction',
                'format': apprise.NotifyFormat.MARKDOWN,
            }

            form = NotifyForm(data=form_data)
            assert form.is_valid()

            response = self.client.post(
                '/notify/{}'.format(key), form.cleaned_data)
            assert response.status_code == 200
            assert mock_notify.call_count == 1

            # Now empty our data
            response = self.client.post('/del/{}'.format(key))
            assert response.status_code == 200

            # A second call; but there is nothing to remove
            response = self.client.post('/del/{}'.format(key))
            assert response.status_code == 204

            # Reset our count
            mock_notify.reset_mock()


        # Now we do a similar approach as the above except we remove the
        # configuration from under the application
        for _ in range(10):
            # No content saved to the location yet
            response = self.client.post('/get/{}'.format(key))
            assert response.status_code == 204

            # Add our content
            response = self.client.post(
                '/add/{}'.format(key),
                {'config': '\r\n'.join(urls)})
            assert response.status_code == 200

            # Now we should be able to see our content
            response = self.client.post('/get/{}'.format(key))
            assert response.status_code == 200

            entries = re.split(r'[\r*\n]+', response.content.decode('utf-8'))
            assert len(entries) == 3

            form_data = {
                'body': '## test notifiction',
                'format': apprise.NotifyFormat.MARKDOWN,
            }

            form = NotifyForm(data=form_data)
            assert form.is_valid()

            response = self.client.post(
                '/notify/{}'.format(key), form.cleaned_data)
            assert response.status_code == 200
            assert mock_notify.call_count == 1

            # Now remove the file directly (as though one
            # removed the configuration directory)
            result = ConfigCache.path(key)
            entry = os.path.join(result[0], '{}.text'.format(result[1]))
            assert os.path.isfile(entry)
            # The removal
            os.unlink(entry)
            # Verify
            assert not os.path.isfile(entry)

            # Call /del/ but now there is nothing to remove
            response = self.client.post('/del/{}'.format(key))
            assert response.status_code == 204

            # Reset our count
            mock_notify.reset_mock()
