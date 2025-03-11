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
from django.test import override_settings


class ManagerPageTests(SimpleTestCase):
    """
    Manager Webpage testing
    """

    def test_manage_status_code(self):
        """
        General testing of management page
        """
        # No permission to get keys
        response = self.client.get('/cfg/')
        assert response.status_code == 403

        with override_settings(APPRISE_ADMIN=True, APPRISE_STATEFUL_MODE='hash'):
            response = self.client.get('/cfg/')
            assert response.status_code == 403

        with override_settings(APPRISE_ADMIN=False, APPRISE_STATEFUL_MODE='simple'):
            response = self.client.get('/cfg/')
            assert response.status_code == 403

        with override_settings(APPRISE_ADMIN=False, APPRISE_STATEFUL_MODE='disabled'):
            response = self.client.get('/cfg/')
            assert response.status_code == 403

        with override_settings(APPRISE_ADMIN=True, APPRISE_STATEFUL_MODE='disabled'):
            response = self.client.get('/cfg/')
            assert response.status_code == 403

        # But only when the setting is enabled
        with override_settings(APPRISE_ADMIN=True, APPRISE_STATEFUL_MODE='simple'):
            response = self.client.get('/cfg/')
            assert response.status_code == 200

        # An invalid key was specified
        response = self.client.get('/cfg/**invalid-key**')
        assert response.status_code == 404

        # An invalid key was specified
        response = self.client.get('/cfg/valid-key')
        assert response.status_code == 200
