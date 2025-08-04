# -*- coding: utf-8 -*-
#
# Copyright (C) 2025 Chris Caron <lead2gold@gmail.com>
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
from ..urlfilter import AppriseURLFilter


class AttachmentTests(SimpleTestCase):
    def test_apprise_url_filter(self):
        """
        Test the apprise url filter
        """
        # empty allow and deny lists
        af = AppriseURLFilter("", "")

        # Test garbage entries
        self.assertFalse(af.is_allowed("$"))
        self.assertFalse(af.is_allowed(b"13"))
        self.assertFalse(af.is_allowed("Mālō e Lelei"))
        self.assertFalse(af.is_allowed(""))
        self.assertFalse(af.is_allowed(None))
        self.assertFalse(af.is_allowed(True))
        self.assertFalse(af.is_allowed(42))

        # These ar blocked too since we have no allow list
        self.assertFalse(af.is_allowed("http://localhost"))
        self.assertFalse(af.is_allowed("http://localhost"))

        #
        # We have a wildcard for accept all in our allow list
        #
        af = AppriseURLFilter("*", "")

        # We still block junk
        self.assertFalse(af.is_allowed("$"))
        self.assertFalse(af.is_allowed(b"13"))
        self.assertFalse(af.is_allowed("Mālō e Lelei"))
        self.assertFalse(af.is_allowed(""))
        self.assertFalse(af.is_allowed(None))
        self.assertFalse(af.is_allowed(True))
        self.assertFalse(af.is_allowed(42))

        # We however allow localhost now (caught with *)
        self.assertTrue(af.is_allowed("http://localhost"))
        self.assertTrue(af.is_allowed("http://localhost/resources"))
        self.assertTrue(af.is_allowed("http://localhost/images"))

        #
        # Allow list accepts all, except we want to explicitely block https://localhost/resources
        #
        af = AppriseURLFilter("*", "https://localhost/resources")

        # We still block junk
        self.assertFalse(af.is_allowed("$"))
        self.assertFalse(af.is_allowed(b"13"))
        self.assertFalse(af.is_allowed("Mālō e Lelei"))
        self.assertFalse(af.is_allowed(""))
        self.assertFalse(af.is_allowed(None))
        self.assertFalse(af.is_allowed(True))
        self.assertFalse(af.is_allowed(42))

        # Takeaway is https:// was blocked to resources, but not http:// request
        # because it was explicitly identify as so:
        self.assertTrue(af.is_allowed("http://localhost"))
        self.assertTrue(af.is_allowed("http://localhost/resources"))
        self.assertTrue(af.is_allowed("http://localhost/resources/sub/path/"))
        self.assertFalse(af.is_allowed("https://localhost/resources"))
        self.assertFalse(af.is_allowed("https://localhost/resources/sub/path/"))
        self.assertTrue(af.is_allowed("http://localhost/images"))

        #
        # Allow list accepts all, except we want to explicitely block both
        #   https://localhost/resources and http://localhost/resources
        #
        af = AppriseURLFilter("*", "localhost/resources")

        # We still block junk
        self.assertFalse(af.is_allowed("$"))
        self.assertFalse(af.is_allowed(b"13"))
        self.assertFalse(af.is_allowed("Mālō e Lelei"))
        self.assertFalse(af.is_allowed(""))
        self.assertFalse(af.is_allowed(None))
        self.assertFalse(af.is_allowed(True))
        self.assertFalse(af.is_allowed(42))

        # Takeaway is https:// was blocked to resources, but not http:// request
        # because it was explicitly identify as so:
        self.assertTrue(af.is_allowed("http://localhost"))
        self.assertFalse(af.is_allowed("http://localhost/resources"))
        self.assertFalse(af.is_allowed("http://localhost/resources/sub/path"))
        self.assertFalse(af.is_allowed("https://localhost/resources"))
        self.assertFalse(af.is_allowed("https://localhost/resources/sub/path/"))
        self.assertTrue(af.is_allowed("http://localhost/images"))

        #
        # A more restrictive allow/block list
        #   https://localhost/resources and http://localhost/resources
        #
        af = AppriseURLFilter("https://localhost, http://myserver.*", "localhost/resources")

        # We still block junk
        self.assertFalse(af.is_allowed("$"))
        self.assertFalse(af.is_allowed(b"13"))
        self.assertFalse(af.is_allowed("Mālō e Lelei"))
        self.assertFalse(af.is_allowed(""))
        self.assertFalse(af.is_allowed(None))
        self.assertFalse(af.is_allowed(True))
        self.assertFalse(af.is_allowed(42))

        # Explicitly only allows https
        self.assertFalse(af.is_allowed("http://localhost"))
        self.assertTrue(af.is_allowed("https://localhost"))
        self.assertFalse(af.is_allowed("https://localhost:8000"))
        self.assertTrue(af.is_allowed("https://localhost/images"))
        self.assertFalse(af.is_allowed("https://localhost/resources"))
        self.assertFalse(af.is_allowed("https://localhost/resources/sub/path/"))
        self.assertFalse(af.is_allowed("http://localhost/resources"))
        self.assertFalse(af.is_allowed("http://localhost/resources/sub/path"))
        self.assertFalse(af.is_allowed("http://not-in-list"))

        # Explicitly definition of allowed hostname prohibits the below from working:
        self.assertFalse(af.is_allowed("localhost"))

        #
        # Testing of hostnames only and ports
        #
        af = AppriseURLFilter("localhost, myserver:3000", "localhost/resources")

        # We still block junk
        self.assertFalse(af.is_allowed("$"))
        self.assertFalse(af.is_allowed(b"13"))
        self.assertFalse(af.is_allowed("Mālō e Lelei"))
        self.assertFalse(af.is_allowed(""))
        self.assertFalse(af.is_allowed(None))
        self.assertFalse(af.is_allowed(True))
        self.assertFalse(af.is_allowed(42))

        # all forms of localhost is allowed (provided there is no port)
        self.assertTrue(af.is_allowed("http://localhost"))
        self.assertTrue(af.is_allowed("https://localhost"))
        self.assertFalse(af.is_allowed("https://localhost:8000"))
        self.assertFalse(af.is_allowed("https://localhost:80"))
        self.assertFalse(af.is_allowed("https://localhost:443"))
        self.assertTrue(af.is_allowed("https://localhost/images"))
        self.assertFalse(af.is_allowed("https://localhost/resources"))
        self.assertFalse(af.is_allowed("https://localhost/resources/sub/path"))
        self.assertFalse(af.is_allowed("http://localhost/resources"))
        self.assertTrue(af.is_allowed("http://localhost/resourcesssssssss"))
        self.assertFalse(af.is_allowed("http://localhost/resources/sub/path/"))
        self.assertFalse(af.is_allowed("http://not-in-list"))

        # myserver is only allowed if port is provided
        self.assertFalse(af.is_allowed("http://myserver"))
        self.assertFalse(af.is_allowed("https://myserver"))
        self.assertTrue(af.is_allowed("http://myserver:3000"))
        self.assertTrue(af.is_allowed("https://myserver:3000"))

        # Open range of hosts allows these to be accepted:
        self.assertTrue(af.is_allowed("localhost"))
        self.assertTrue(af.is_allowed("myserver:3000"))
        self.assertTrue(af.is_allowed("https://myserver:3000"))
        self.assertTrue(af.is_allowed("http://myserver:3000"))

        #
        # Testing of hostnames only and ports but via URLs (explicit http://)
        # Also tests path ending with `/` (slash)
        #
        af = AppriseURLFilter("http://localhost, http://myserver:3000", "http://localhost/resources/")

        # We still block junk
        self.assertFalse(af.is_allowed("$"))
        self.assertFalse(af.is_allowed(b"13"))
        self.assertFalse(af.is_allowed("Mālō e Lelei"))
        self.assertFalse(af.is_allowed(""))
        self.assertFalse(af.is_allowed(None))
        self.assertFalse(af.is_allowed(True))
        self.assertFalse(af.is_allowed(42))

        # http://localhost acceptance only
        self.assertTrue(af.is_allowed("http://localhost"))
        self.assertFalse(af.is_allowed("https://localhost"))
        self.assertFalse(af.is_allowed("http://localhost:8000"))
        self.assertFalse(af.is_allowed("http://localhost:80"))
        self.assertTrue(af.is_allowed("http://localhost/images"))
        self.assertFalse(af.is_allowed("https://localhost/images"))
        self.assertFalse(af.is_allowed("https://localhost/resources"))
        self.assertFalse(af.is_allowed("https://localhost/resources/sub/path"))
        self.assertFalse(af.is_allowed("http://localhost/resources"))
        self.assertFalse(af.is_allowed("http://not-in-list"))

        # myserver is only allowed if port is provided and http://
        self.assertFalse(af.is_allowed("http://myserver"))
        self.assertFalse(af.is_allowed("https://myserver"))
        self.assertTrue(af.is_allowed("http://myserver:3000"))
        self.assertFalse(af.is_allowed("https://myserver:3000"))
        self.assertTrue(af.is_allowed("http://myserver:3000/path/"))

        # Open range of hosts is no longer allowed due to explicit http:// reference
        self.assertFalse(af.is_allowed("localhost"))
        self.assertFalse(af.is_allowed("myserver:3000"))
        self.assertFalse(af.is_allowed("https://myserver:3000"))

        #
        # Testing of hostnames only and ports but via URLs (explicit https://)
        # Also tests path ending with `/` (slash)
        #
        af = AppriseURLFilter("https://localhost, https://myserver:3000", "https://localhost/resources/")

        # We still block junk
        self.assertFalse(af.is_allowed("$"))
        self.assertFalse(af.is_allowed(b"13"))
        self.assertFalse(af.is_allowed("Mālō e Lelei"))
        self.assertFalse(af.is_allowed(""))
        self.assertFalse(af.is_allowed(None))
        self.assertFalse(af.is_allowed(True))
        self.assertFalse(af.is_allowed(42))

        # http://localhost acceptance only
        self.assertTrue(af.is_allowed("https://localhost"))
        self.assertFalse(af.is_allowed("http://localhost"))
        self.assertFalse(af.is_allowed("localhost"))
        self.assertFalse(af.is_allowed("https://localhost:8000"))
        self.assertFalse(af.is_allowed("https://localhost:80"))
        self.assertTrue(af.is_allowed("https://localhost/images"))
        self.assertFalse(af.is_allowed("http://localhost/images"))
        self.assertFalse(af.is_allowed("http://localhost/resources"))
        self.assertFalse(af.is_allowed("http://localhost/resources/sub/path"))
        self.assertFalse(af.is_allowed("https://localhost/resources"))
        self.assertFalse(af.is_allowed("https://not-in-list"))

        # myserver is only allowed if port is provided and http://
        self.assertFalse(af.is_allowed("https://myserver"))
        self.assertFalse(af.is_allowed("http://myserver"))
        self.assertFalse(af.is_allowed("myserver"))
        self.assertTrue(af.is_allowed("https://myserver:3000"))
        self.assertFalse(af.is_allowed("http://myserver:3000"))
        self.assertTrue(af.is_allowed("https://myserver:3000/path/"))

        # Open range of hosts is no longer allowed due to explicit http:// reference
        self.assertFalse(af.is_allowed("localhost"))
        self.assertFalse(af.is_allowed("myserver:3000"))
        self.assertFalse(af.is_allowed("http://myserver:3000"))

        #
        # Testing Regular Expressions
        #
        af = AppriseURLFilter("https://localhost/incoming/*/*", "https://localhost/*/*/var")

        # We still block junk
        self.assertFalse(af.is_allowed("$"))
        self.assertFalse(af.is_allowed(b"13"))
        self.assertFalse(af.is_allowed("Mālō e Lelei"))
        self.assertFalse(af.is_allowed(""))
        self.assertFalse(af.is_allowed(None))
        self.assertFalse(af.is_allowed(True))
        self.assertFalse(af.is_allowed(42))

        # Very specific paths are supported now in https://localhost only:
        self.assertFalse(af.is_allowed("https://localhost"))
        self.assertFalse(af.is_allowed("http://localhost"))
        self.assertFalse(af.is_allowed("https://localhost/incoming"))
        self.assertFalse(af.is_allowed("https://localhost/incoming/dir1"))
        self.assertFalse(af.is_allowed("https://localhost/incoming/dir1/"))

        self.assertTrue(af.is_allowed("https://localhost/incoming/dir1/dir2"))
        self.assertTrue(af.is_allowed("https://localhost/incoming/dir1/dir2/"))
        self.assertFalse(af.is_allowed("http://localhost/incoming/dir1/dir2"))
        self.assertFalse(af.is_allowed("http://localhost/incoming/dir1/dir2/"))

        # our incoming directory we restricted
        self.assertFalse(af.is_allowed("https://localhost/incoming/dir1/var"))
        self.assertFalse(af.is_allowed("https://localhost/incoming/dir1/var/"))
        self.assertFalse(af.is_allowed("https://localhost/incoming/dir1/var/sub/dir"))
        self.assertFalse(af.is_allowed("https://localhost/incoming/dir1/var/sub"))

        # Test the ? out
        af = AppriseURLFilter("localhost?", "")

        # Test garbage entries
        self.assertFalse(af.is_allowed("$"))
        self.assertFalse(af.is_allowed(b"13"))
        self.assertFalse(af.is_allowed("Mālō e Lelei"))
        self.assertFalse(af.is_allowed(""))
        self.assertFalse(af.is_allowed(None))
        self.assertFalse(af.is_allowed(True))
        self.assertFalse(af.is_allowed(42))

        # These are blocked too since we have no allow list
        self.assertFalse(af.is_allowed("http://localhost"))
        self.assertTrue(af.is_allowed("http://localhost1"))
        self.assertTrue(af.is_allowed("https://localhost1"))
        self.assertFalse(af.is_allowed("http://localhost%"))
        self.assertFalse(af.is_allowed("http://localhost10"))

        # conflicting elements cancel one another
        af = AppriseURLFilter("localhost", "localhost")

        # These are blocked too since we have no allow list
        self.assertFalse(af.is_allowed("localhost"))
