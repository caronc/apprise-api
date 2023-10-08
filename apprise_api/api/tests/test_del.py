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
import hashlib


class DelTests(SimpleTestCase):

    def test_del_get_invalid_key_status_code(self):
        """
        Test GET requests to invalid key
        """
        response = self.client.get('/del/**invalid-key**')
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

        # remove a key that is too long
        response = self.client.post('/del/{}'.format(key + 'x'))
        assert response.status_code == 404

        # remove the key
        response = self.client.post('/del/{}'.format(key))
        assert response.status_code == 200

        # Test again; key is gone
        response = self.client.post('/del/{}'.format(key))
        assert response.status_code == 204

    @override_settings(APPRISE_CONFIG_LOCK=True)
    def test_del_with_lock(self):
        """
        Test deleting a configuration by URLs with lock set won't work
        """
        # our key to use
        key = 'test_delete_with_lock'

        # We simply do not have permission to do so
        response = self.client.post('/del/{}'.format(key))
        assert response.status_code == 403

    def test_del_post(self):
        """
        Test DEL POST
        """
        # our key to use
        key = 'test_delete'

        # Invalid Key
        response = self.client.post('/del/**invalid-key**')
        assert response.status_code == 404

        # A key that just simply isn't present
        response = self.client.post('/del/{}'.format(key))
        assert response.status_code == 204

        # Add our key
        response = self.client.post(
            '/add/{}'.format(key), {'urls': 'mailto://user:pass@yahoo.ca'})
        assert response.status_code == 200

        # Test removing key when the OS just can't do it:
        with patch('os.remove', side_effect=OSError):
            # We can now remove the key
            response = self.client.post('/del/{}'.format(key))
            assert response.status_code == 500

        # We can now remove the key
        response = self.client.post('/del/{}'.format(key))
        assert response.status_code == 200

        # Key has already been removed
        response = self.client.post('/del/{}'.format(key))
        assert response.status_code == 204
