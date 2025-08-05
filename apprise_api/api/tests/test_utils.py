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
import os
import tempfile
from unittest import mock

from django.test import SimpleTestCase

from .. import utils


class UtilsTests(SimpleTestCase):
    def test_touchdir(self):
        """
        Test touchdir()
        """

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch("os.makedirs", side_effect=OSError()):
                assert utils.touchdir(os.path.join(tmpdir, "tmp-file")) is False

            with mock.patch("os.makedirs", side_effect=FileExistsError()):
                # Dir doesn't exist
                assert utils.touchdir(os.path.join(tmpdir, "tmp-file")) is False

            assert utils.touchdir(os.path.join(tmpdir, "tmp-file")) is True

            # Date is updated
            assert utils.touchdir(os.path.join(tmpdir, "tmp-file")) is True

            with mock.patch("os.utime", side_effect=OSError()):
                # Fails to update file
                assert utils.touchdir(os.path.join(tmpdir, "tmp-file")) is False

    def test_touch(self):
        """
        Test touch()
        """

        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("os.fdopen", side_effect=OSError()):
            assert utils.touch(os.path.join(tmpdir, "tmp-file")) is False
