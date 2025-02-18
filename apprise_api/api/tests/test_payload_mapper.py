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
from django.test import SimpleTestCase
from ..payload_mapper import remap_fields


class NotifyPayloadMapper(SimpleTestCase):
    """
    Test Payload Mapper
    """

    def test_remap_fields(self):
        """
        Test payload re-mapper
        """

        #
        # No rules defined
        #
        rules = {}
        payload = {
            'format': 'markdown',
            'title': 'title',
            'body': '# body',
        }
        payload_orig = payload.copy()

        # Map our fields
        remap_fields(rules, payload)

        # no change is made
        assert payload == payload_orig

        #
        # rules defined - test 1
        #
        rules = {
            # map 'as' to 'format'
            'as': 'format',
            # map 'subject' to 'title'
            'subject': 'title',
            # map 'content' to 'body'
            'content': 'body',
            # 'missing' is an invalid entry so this will be skipped
            'unknown': 'missing',

            # Empty field
            'attachment': '',

            # Garbage is an field that can be removed since it doesn't
            # conflict with the form
            'garbage': '',

            # Tag
            'tag': 'test',
        }
        payload = {
            'as': 'markdown',
            'subject': 'title',
            'content': '# body',
            'tag': '',
            'unknown': 'hmm',
            'attachment': '',
            'garbage': '',
        }

        # Map our fields
        remap_fields(rules, payload)

        # Our field mappings have taken place
        assert payload == {
            'tag': 'test',
            'unknown': 'missing',
            'format': 'markdown',
            'title': 'title',
            'body': '# body',
        }

        #
        # rules defined - test 2
        #
        rules = {
            #
            # map 'content' to 'body'
            'content': 'body',
            # a double mapping to body will trigger an error
            'message': 'body',
            # Swapping fields
            'body': 'another set of data',
        }
        payload = {
            'as': 'markdown',
            'subject': 'title',
            'content': '# content body',
            'message': '# message body',
            'body': 'another set of data',
        }

        # Map our fields
        remap_fields(rules, payload)

        # Our information gets swapped
        assert payload == {
            'as': 'markdown',
            'subject': 'title',
            'body': 'another set of data',
        }

        #
        # swapping fields - test 3
        #
        rules = {
            #
            # map 'content' to 'body'
            'title': 'body',
        }
        payload = {
            'format': 'markdown',
            'title': 'body',
            'body': '# title',
        }

        # Map our fields
        remap_fields(rules, payload)

        # Our information gets swapped
        assert payload == {
            'format': 'markdown',
            'title': '# title',
            'body': 'body',
        }

        #
        # swapping fields - test 4
        #
        rules = {
            #
            # map 'content' to 'body'
            'title': 'body',
        }
        payload = {
            'format': 'markdown',
            'title': 'body',
        }

        # Map our fields
        remap_fields(rules, payload)

        # Our information gets swapped
        assert payload == {
            'format': 'markdown',
            'body': 'body',
        }

        #
        # swapping fields - test 5
        #
        rules = {
            #
            # map 'content' to 'body'
            'content': 'body',
        }
        payload = {
            'format': 'markdown',
            'content': 'the message',
            'body': 'to-be-replaced',
        }

        # Map our fields
        remap_fields(rules, payload)

        # Our information gets swapped
        assert payload == {
            'format': 'markdown',
            'body': 'the message',
        }

        #
        # mapping of fields don't align - test 6
        #
        rules = {
            'payload': 'body',
            'fmt': 'format',
            'extra': 'tag',
        }
        payload = {
            'format': 'markdown',
            'type': 'info',
            'title': '',
            'body': '## test notifiction',
            'attachment': None,
            'tag': 'general',
            'tags': '',
        }

        # Make a copy of our original payload
        payload_orig = payload.copy()

        # Map our fields
        remap_fields(rules, payload)

        # There are no rules applied since nothing aligned
        assert payload == payload_orig
