#
# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
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
import time

from django.test import Client, SimpleTestCase
from django.urls import reverse


class RealisticReDoSTests(SimpleTestCase):
    """
    Test ReDoS vulnerability through the actual API endpoints
    """

    def setUp(self):
        self.client = Client()

    def test_notify_endpoint_redos_via_get(self):
        """
        Verify that a malicious 'tag' in a GET request can cause a DoS
        """
        # We use a smaller payload here just to keep the test reasonably fast if it fails,
        # but large enough to show the delay.
        # With the old regex, 2000 characters would take ~30 seconds.
        # With the new regex, it's instant.
        payload = "\t" * 2000 + "!"

        # We target the notify endpoint
        url = reverse("notify", kwargs={"key": "mykey"})

        start_time = time.perf_counter()
        # Sending the malicious tag via GET
        response = self.client.post(url + f"?tag={payload}")
        duration = time.perf_counter() - start_time

        # In a real exploit, this would hang the worker.
        # Here we just verify it's fast with the fix.
        self.assertLess(duration, 0.5, f"API call took too long ({duration:.2f}s). Vulnerable to ReDoS?")
        self.assertEqual(response.status_code, 400)  # Should be rejected by validation

    def test_notify_endpoint_redos_via_json(self):
        """
        Verify that a malicious 'tag' in a JSON POST body can cause a DoS
        """
        payload = "\t" * 2000 + "!"
        url = reverse("notify", kwargs={"key": "mykey"})

        start_time = time.perf_counter()
        # Sending the malicious tag via JSON body
        response = self.client.post(url, data={"tag": payload}, content_type="application/json")
        duration = time.perf_counter() - start_time

        self.assertLess(duration, 0.5, f"JSON API call took too long ({duration:.2f}s). Vulnerable to ReDoS?")
        self.assertEqual(response.status_code, 400)
