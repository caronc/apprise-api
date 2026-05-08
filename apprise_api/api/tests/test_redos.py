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

from django.test import SimpleTestCase

from ..views import TAG_VALIDATION_RE


class ReDoSTests(SimpleTestCase):
    """
    Test Regex logic and performance (ReDoS prevention)
    """

    def test_tag_validation_regex_performance(self):
        """
        Ensure TAG_VALIDATION_RE is not vulnerable to ReDoS
        """
        # Payload designed to trigger catastrophic backtracking in the old regex:
        # ^\s*[...]+\s*$
        # The overlap between \s* and [\s] caused exponential complexity.
        # This payload with 5000 tabs would take > 200 seconds on the old regex.
        payload = "\t" * 5000 + "!"

        start_time = time.perf_counter()
        match = TAG_VALIDATION_RE.match(payload)
        duration = time.perf_counter() - start_time

        # It should be instant (< 0.1s)
        self.assertLess(duration, 0.5, "Regex took too long to execute (ReDoS vulnerable?)")
        self.assertFalse(match, "Should not match invalid payload")

    def test_tag_validation_logic(self):
        """
        Ensure the regex still correctly validates legitimate tags
        """
        valid_tags = [
            "tag",
            "tag1",
            "tag 1",
            "tag_one",
            "tag-two",
            "tag1, tag2",
            "tag1 | tag2",
            "tag&tag",
            "tag+tag",
            "  tag  ",  # Leading/trailing spaces are allowed inside the match
            "\t\ttag\t\t",
            "tag\nnewline",
        ]

        for tag in valid_tags:
            self.assertTrue(TAG_VALIDATION_RE.match(tag), f"Should match valid tag: '{tag}'")

        invalid_tags = [
            "tag!",
            "tag@home",
            "<script>",
        ]

        for tag in invalid_tags:
            self.assertFalse(TAG_VALIDATION_RE.match(tag), f"Should NOT match invalid tag: '{tag}'")
