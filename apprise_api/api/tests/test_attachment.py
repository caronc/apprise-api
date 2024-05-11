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
import requests
from django.test import SimpleTestCase
from unittest import mock
from unittest.mock import mock_open
from ..utils import Attachment, HTTPAttachment
from ..utils import parse_attachments
from django.test.utils import override_settings
from tempfile import TemporaryDirectory
from shutil import rmtree
import base64
from unittest.mock import patch
from django.core.files.uploadedfile import SimpleUploadedFile
from os.path import dirname, join, getsize

SAMPLE_FILE = join(dirname(dirname(dirname(__file__))), 'static', 'logo.png')


class AttachmentTests(SimpleTestCase):
    def setUp(self):
        # Prepare a temporary directory
        self.tmp_dir = TemporaryDirectory()

    def tearDown(self):
        # Clear directory
        try:
            rmtree(self.tmp_dir.name)
        except FileNotFoundError:
            # no worries
            pass

        self.tmp_dir = None

    def test_attachment_initialization(self):
        """
        Test attachment handling
        """

        with override_settings(APPRISE_ATTACH_DIR=self.tmp_dir.name):
            with mock.patch('os.makedirs', side_effect=OSError):
                with self.assertRaises(ValueError):
                    Attachment('file')
                with self.assertRaises(ValueError):
                    HTTPAttachment('web')

            with mock.patch('tempfile.mkstemp', side_effect=FileNotFoundError):
                with self.assertRaises(ValueError):
                    Attachment('file')
                with self.assertRaises(ValueError):
                    HTTPAttachment('web')

            with mock.patch('os.remove', side_effect=FileNotFoundError):
                a = Attachment('file')
                # Force __del__ call to throw an exception which we gracefully
                # handle
                del a

                a = HTTPAttachment('web')
                a._path = 'abcd'
                assert a.filename == 'web'
                # Force __del__ call to throw an exception which we gracefully
                # handle
                del a

            a = Attachment('file')
            assert a.filename

    def test_form_file_attachment_parsing(self):
        """
        Test the parsing of file attachments
        """
        # Get ourselves a file to work with

        files_request = {
            'file1': SimpleUploadedFile(
                "attach.txt", b"content here", content_type="text/plain")
        }
        result = parse_attachments(None, files_request)
        assert isinstance(result, list)
        assert len(result) == 1

        # Test case where no filename was specified
        files_request = {
            'file1': SimpleUploadedFile(
                "    ", b"content here", content_type="text/plain")
        }
        result = parse_attachments(None, files_request)
        assert isinstance(result, list)
        assert len(result) == 1

        # Test our case where we throw an error trying to open/read/write our
        # attachment to disk
        m = mock_open()
        m.side_effect = OSError()
        with patch('builtins.open', m):
            with self.assertRaises(ValueError):
                parse_attachments(None, files_request)

        # Test a case where our attachment exceeds the maximum size we allow
        # for
        with override_settings(APPRISE_ATTACH_SIZE=1):
            files_request = {
                'file1': SimpleUploadedFile(
                    "attach.txt",
                    # More then 1 MB in size causing error to trip
                    ("content" * 1024 * 1024).encode('utf-8'),
                    content_type="text/plain")
            }
            with self.assertRaises(ValueError):
                parse_attachments(None, files_request)

        # Test Attachment Size seto t zer0
        with override_settings(APPRISE_ATTACH_SIZE=0):
            files_request = {
                'file1': SimpleUploadedFile(
                    "attach.txt",
                    # More then 1 MB in size causing error to trip
                    ("content" * 1024 * 1024).encode('utf-8'),
                    content_type="text/plain")
            }
            with self.assertRaises(ValueError):
                parse_attachments(None, files_request)

        # Bad data provided in filename field
        files_request = {
            'file1': SimpleUploadedFile(
                None, b"content here", content_type="text/plain")
        }
        with self.assertRaises(ValueError):
            parse_attachments(None, files_request)

    @patch('requests.get')
    def test_direct_attachment_parsing(self, mock_get):
        """
        Test the parsing of file attachments
        """
        # Test the processing of file attachments
        result = parse_attachments([], {})
        assert isinstance(result, list)
        assert len(result) == 0

        # Response object
        response = mock.Mock()
        response.status_code = requests.codes.ok
        response.raise_for_status.return_value = True
        response.headers = {
            'Content-Length': getsize(SAMPLE_FILE),
        }
        ref = {
            'io': None,
        }

        def iter_content(chunk_size=1024, *args, **kwargs):
            if not ref['io']:
                ref['io'] = open(SAMPLE_FILE, 'rb')
            block = ref['io'].read(chunk_size)
            if not block:
                # Close for re-use
                ref['io'].close()
                ref['io'] = None
            yield block
        response.iter_content = iter_content

        def test(*args, **kwargs):
            return response
        response.__enter__ = test
        response.__exit__ = test
        mock_get.return_value = response

        # Support base64 encoding
        attachment_payload = {
            'base64': base64.b64encode(b'data to be encoded').decode('utf-8')
        }
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 1

        # Support multi entries
        attachment_payload = [
            {
                'base64': base64.b64encode(
                    b'data to be encoded 1').decode('utf-8'),
            }, {
                'base64': base64.b64encode(
                    b'data to be encoded 2').decode('utf-8'),
            }, {
                'base64': base64.b64encode(
                    b'data to be encoded 3').decode('utf-8'),
            }
        ]
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 3

        # Support multi entries
        attachment_payload = [
            {
                'url': 'http://localhost/my.attachment.3',
            }, {
                'url': 'http://localhost/my.attachment.2',
            }, {
                'url': 'http://localhost/my.attachment.1',
            }
        ]
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 3

        # Garbage handling (integer, float, object, etc is invalid)
        attachment_payload = 5
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 0
        attachment_payload = 5.5
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 0
        attachment_payload = object()
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 0

        # filename provided, but its empty (and/or contains whitespace)
        attachment_payload = {
            'base64': base64.b64encode(b'data to be encoded').decode('utf-8'),
            'filename': '   '
        }
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 1

        # filename too long
        attachment_payload = {
            'base64': base64.b64encode(b'data to be encoded').decode('utf-8'),
            'filename': 'a' * 1000,
        }
        with self.assertRaises(ValueError):
            parse_attachments(attachment_payload, {})

        # filename invalid
        attachment_payload = {
            'base64': base64.b64encode(b'data to be encoded').decode('utf-8'),
            'filename': 1,
        }
        with self.assertRaises(ValueError):
            parse_attachments(attachment_payload, {})

        attachment_payload = {
            'base64': base64.b64encode(b'data to be encoded').decode('utf-8'),
            'filename': None,
        }
        with self.assertRaises(ValueError):
            parse_attachments(attachment_payload, {})

        attachment_payload = {
            'base64': base64.b64encode(b'data to be encoded').decode('utf-8'),
            'filename': object(),
        }
        with self.assertRaises(ValueError):
            parse_attachments(attachment_payload, {})

        # List Entry with bad data
        attachment_payload = [
            None,
        ]
        with self.assertRaises(ValueError):
            parse_attachments(attachment_payload, {})

        # We expect at least a 'base64' or something in our dict
        attachment_payload = [
            {},
        ]
        with self.assertRaises(ValueError):
            parse_attachments(attachment_payload, {})

        # We allow empty entries, this is okay; there is just nothing
        # returned at the end of the day
        assert parse_attachments({''}, {}) == []

        # We can't parse entries that are not base64 but specified as
        # though they are
        attachment_payload = {
            'base64': 'not-base-64',
        }
        with self.assertRaises(ValueError):
            parse_attachments(attachment_payload, {})

        # Support string; these become web requests
        attachment_payload = \
            "https://avatars.githubusercontent.com/u/850374?v=4"
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 1

        # Local files are not allowed
        attachment_payload = "file:///etc/hosts"
        with self.assertRaises(ValueError):
            parse_attachments(attachment_payload, {})
        attachment_payload = "/etc/hosts"
        with self.assertRaises(ValueError):
            parse_attachments(attachment_payload, {})
        attachment_payload = "simply invalid"
        with self.assertRaises(ValueError):
            parse_attachments(attachment_payload, {})

        # Test our case where we throw an error trying to write our attachment
        # to disk
        m = mock_open()
        m.side_effect = OSError()
        with patch('builtins.open', m):
            with self.assertRaises(ValueError):
                attachment_payload = b"some data to work with."
                parse_attachments(attachment_payload, {})

        # Test a case where our attachment exceeds the maximum size we allow
        # for
        with override_settings(APPRISE_ATTACH_SIZE=1):
            # More then 1 MB in size causing error to trip
            attachment_payload = \
                ("content" * 1024 * 1024).encode('utf-8')
            with self.assertRaises(ValueError):
                parse_attachments(attachment_payload, {})

        # Support byte data
        attachment_payload = b"some content to pass along as an attachment."
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 1

        attachment_payload = [
            # Request several images
            "https://localhost/myotherfile.png",
            "https://localhost/myfile.png"
        ]
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 2

        attachment_payload = [{
            # Request several images
            'url': "https://localhost/myotherfile.png",
        }, {
            'url': "https://localhost/myfile.png"
        }]
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 2

        # Test pure binary payload (raw)
        attachment_payload = [
            b"some content to pass along as an attachment.",
            b"some more content to pass along as an attachment.",
        ]
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 2

    def test_direct_attachment_parsing_nw(self):
        """
        Test the parsing of file attachments with network availability
        We test web requests that do not work or in accessible to access
        this part of the test cases
        """
        attachment_payload = [
            # While we have a network in place, we're intentionally requesting
            # URLs that do not exist (hopefully they don't anyway) as we want
            # this test to fail.
            "https://localhost/garbage/abcd1.png",
            "https://localhost/garbage/abcd2.png",
        ]
        with self.assertRaises(ValueError):
            parse_attachments(attachment_payload, {})

        # Support url encoding
        attachment_payload = [{
            'url': "https://localhost/garbage/abcd1.png",
        }, {
            'url': "https://localhost/garbage/abcd2.png",
        }]
        with self.assertRaises(ValueError):
            parse_attachments(attachment_payload, {})
