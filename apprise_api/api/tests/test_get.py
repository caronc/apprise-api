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


class GetTests(SimpleTestCase):

    def test_get_invalid_key_status_code(self):
        """
        Test GET requests to invalid key
        """
        response = self.client.get('/get/**invalid-key**')
        assert response.status_code == 404

    def test_get_config(self):
        """
        Test retrieving configuration
        """

        # our key to use
        key = 'test_get_config'

        # GET returns 405 (not allowed)
        response = self.client.get('/get/{}'.format(key))
        assert response.status_code == 405

        # No content saved to the location yet
        response = self.client.post('/get/{}'.format(key))
        assert response.status_code == 204

        # Add some content
        response = self.client.post(
            '/add/{}'.format(key),
            {'urls': 'mailto://user:pass@yahoo.ca'})
        assert response.status_code == 200

        # Now we should be able to see our content
        response = self.client.post('/get/{}'.format(key))
        assert response.status_code == 200
