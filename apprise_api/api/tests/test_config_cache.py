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
import os
from ..utils import AppriseConfigCache
from apprise import ConfigFormat
from unittest.mock import patch
import errno


def test_apprise_config_io(tmpdir):
    """
    Test Apprise Config Disk Put/Get
    """
    content = 'mailto://test:pass@gmail.com'
    key = 'test_apprise_config_io'

    # Create our object to work with
    acc_obj = AppriseConfigCache(str(tmpdir))

    # Verify that the content doesn't already exist
    assert acc_obj.get(key) == (None, '')

    # Write our content assigned to our key
    assert acc_obj.put(key, content, ConfigFormat.TEXT)

    # Test the handling of underlining disk/write exceptions
    with patch('gzip.open') as mock_open:
        mock_open.side_effect = OSError()
        # We'll fail to write our key now
        assert not acc_obj.put(key, content, ConfigFormat.TEXT)

    # Get path details
    conf_dir, _ = acc_obj.path(key)

    # List content of directory
    contents = os.listdir(conf_dir)

    # There should be just 1 new file in this directory
    assert len(contents) == 1
    assert contents[0].endswith('.{}'.format(ConfigFormat.TEXT))

    # Verify that the content is retrievable
    assert acc_obj.get(key) == (content, ConfigFormat.TEXT)

    # Test the handling of underlining disk/read exceptions
    with patch('gzip.open') as mock_open:
        mock_open.side_effect = OSError()
        # We'll fail to read our key now
        assert acc_obj.get(key) == (None, None)

    # Tidy up our content
    assert acc_obj.clear(key) is True

    # But the second time is okay as it no longer exists
    assert acc_obj.clear(key) is None

    with patch('os.remove') as mock_remove:
        mock_remove.side_effect = OSError(errno.EPERM)
        # OSError
        assert acc_obj.clear(key) is False

    # Now test with YAML file
    content = """
    version: 1

    urls:
       - windows://
    """

    # Write our content assigned to our key
    # This should gracefully clear the TEXT entry that was
    # previously in the spot
    assert acc_obj.put(key, content, ConfigFormat.YAML)

    # List content of directory
    contents = os.listdir(conf_dir)

    # There should STILL be just 1 new file in this directory
    assert len(contents) == 1
    assert contents[0].endswith('.{}'.format(ConfigFormat.YAML))

    # Verify that the content is retrievable
    assert acc_obj.get(key) == (content, ConfigFormat.YAML)
