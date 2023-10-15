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
from unittest import mock
from ..utils import Attachment
from django.test.utils import override_settings
from tempfile import TemporaryDirectory
from shutil import rmtree


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

            with mock.patch('tempfile.mkstemp', side_effect=FileNotFoundError):
                with self.assertRaises(ValueError):
                    Attachment('file')

            a = Attachment('file')
            assert a.filename
