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
from ..utils import AppriseStoreMode
from ..utils import SimpleFileExtension
from apprise import ConfigFormat
from unittest.mock import patch
from unittest.mock import mock_open
import errno


def test_apprise_config_io_hash_mode(tmpdir):
    """
    Test Apprise Config Disk Put/Get using HASH mode
    """
    content = "mailto://test:pass@gmail.com"
    key = "test_apprise_config_io_hash"

    # Create our object to work with
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)

    # Verify that the content doesn't already exist
    assert acc_obj.get(key) == (None, "")

    # Write our content assigned to our key
    assert acc_obj.put(key, content, ConfigFormat.TEXT)

    # Test the handling of underlining disk/write exceptions
    with patch("gzip.open") as mock_gzopen:
        mock_gzopen.side_effect = OSError()
        # We'll fail to write our key now
        assert not acc_obj.put(key, content, ConfigFormat.TEXT)

    # Get path details
    conf_dir, _ = acc_obj.path(key)

    # List content of directory
    contents = os.listdir(conf_dir)

    # There should be just 1 new file in this directory
    assert len(contents) == 1
    assert contents[0].endswith(".{}".format(ConfigFormat.TEXT))

    # Verify that the content is retrievable
    assert acc_obj.get(key) == (content, ConfigFormat.TEXT)

    # Test the handling of underlining disk/read exceptions
    with patch("gzip.open") as mock_gzopen:
        mock_gzopen.side_effect = OSError()
        # We'll fail to read our key now
        assert acc_obj.get(key) == (None, None)

    # Tidy up our content
    assert acc_obj.clear(key) is True

    # But the second time is okay as it no longer exists
    assert acc_obj.clear(key) is None

    with patch("os.remove") as mock_remove:
        mock_remove.side_effect = OSError(errno.EPERM)
        # OSError
        assert acc_obj.clear(key) is False

        # If we try to put the same file, we'll fail since
        # one exists there already
        assert not acc_obj.put(key, content, ConfigFormat.TEXT)

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
    assert contents[0].endswith(".{}".format(ConfigFormat.YAML))

    # Verify that the content is retrievable
    assert acc_obj.get(key) == (content, ConfigFormat.YAML)


def test_apprise_config_list_simple_mode(tmpdir):
    """
    Test Apprise Config Keys List using SIMPLE mode
    """
    # Create our object to work with
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)

    # Add a hidden file to the config directory (which should be ignored)
    hidden_file = os.path.join(str(tmpdir), ".hidden")
    with open(hidden_file, "w") as f:
        f.write("hidden file")

    # Write 5 text configs and 5 yaml configs
    content_text = "mailto://test:pass@gmail.com"
    content_yaml = """
    version: 1
    urls:
         - windows://
    """
    text_key_tpl = "test_apprise_config_list_simple_text_{}"
    yaml_key_tpl = "test_apprise_config_list_simple_yaml_{}"
    text_keys = [text_key_tpl.format(i) for i in range(5)]
    yaml_keys = [yaml_key_tpl.format(i) for i in range(5)]
    key = None
    for key in text_keys:
        assert acc_obj.put(key, content_text, ConfigFormat.TEXT)
    for key in yaml_keys:
        assert acc_obj.put(key, content_yaml, ConfigFormat.YAML)

    # Ensure the 10 configuration files (plus the hidden file) are the only
    # contents of the directory
    conf_dir, _ = acc_obj.path(key)
    contents = os.listdir(conf_dir)
    assert len(contents) == 11

    keys = acc_obj.keys()
    assert len(keys) == 10
    assert sorted(keys) == sorted(text_keys + yaml_keys)


def test_apprise_config_list_hash_mode(tmpdir):
    """
    Test Apprise Config Keys List using HASH mode
    """
    # Create our object to work with
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)

    # Add a hidden file to the config directory (which should be ignored)
    hidden_file = os.path.join(str(tmpdir), ".hidden")
    with open(hidden_file, "w") as f:
        f.write("hidden file")

    # Write 5 text configs and 5 yaml configs
    content_text = "mailto://test:pass@gmail.com"
    content_yaml = """
    version: 1
    urls:
         - windows://
    """
    text_key_tpl = "test_apprise_config_list_simple_text_{}"
    yaml_key_tpl = "test_apprise_config_list_simple_yaml_{}"
    text_keys = [text_key_tpl.format(i) for i in range(5)]
    yaml_keys = [yaml_key_tpl.format(i) for i in range(5)]
    key = None
    for key in text_keys:
        assert acc_obj.put(key, content_text, ConfigFormat.TEXT)
    for key in yaml_keys:
        assert acc_obj.put(key, content_yaml, ConfigFormat.YAML)

    # Ensure the 10 configuration files (plus the hidden file) are the only
    # contents of the directory
    conf_dir, _ = acc_obj.path(key)
    contents = os.listdir(conf_dir)
    assert len(contents) == 1

    # does not search on hash mode
    keys = acc_obj.keys()
    assert len(keys) == 0


def test_apprise_config_io_simple_mode(tmpdir):
    """
    Test Apprise Config Disk Put/Get using SIMPLE mode
    """
    content = "mailto://test:pass@gmail.com"
    key = "test_apprise_config_io_simple"

    # Create our object to work with
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)

    # Verify that the content doesn't already exist
    assert acc_obj.get(key) == (None, "")

    # Write our content assigned to our key
    assert acc_obj.put(key, content, ConfigFormat.TEXT)

    m = mock_open()
    m.side_effect = OSError()
    with patch("builtins.open", m):
        # We'll fail to write our key now
        assert not acc_obj.put(key, content, ConfigFormat.TEXT)

    # Get path details
    conf_dir, _ = acc_obj.path(key)

    # List content of directory
    contents = os.listdir(conf_dir)

    # There should be just 1 new file in this directory
    assert len(contents) == 1
    assert contents[0].endswith(".{}".format(SimpleFileExtension.TEXT))

    # Verify that the content is retrievable
    assert acc_obj.get(key) == (content, ConfigFormat.TEXT)

    # Test the handling of underlining disk/read exceptions
    with patch("builtins.open", m) as mock__open:
        mock__open.side_effect = OSError()
        # We'll fail to read our key now
        assert acc_obj.get(key) == (None, None)

    # Tidy up our content
    assert acc_obj.clear(key) is True

    # But the second time is okay as it no longer exists
    assert acc_obj.clear(key) is None

    with patch("os.remove") as mock_remove:
        mock_remove.side_effect = OSError(errno.EPERM)
        # OSError
        assert acc_obj.clear(key) is False

        # If we try to put the same file, we'll fail since
        # one exists there already
        assert not acc_obj.put(key, content, ConfigFormat.TEXT)

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
    assert contents[0].endswith(".{}".format(SimpleFileExtension.YAML))

    # Verify that the content is retrievable
    assert acc_obj.get(key) == (content, ConfigFormat.YAML)


def test_apprise_config_io_disabled_mode(tmpdir):
    """
    Test Apprise Config Disk Put/Get using DISABLED mode
    """
    content = "mailto://test:pass@gmail.com"
    key = "test_apprise_config_io_disabled"

    # Create our object to work with using an invalid mode
    acc_obj = AppriseConfigCache(str(tmpdir), mode="invalid")

    # We always fall back to disabled if we can't interpret the mode
    assert acc_obj.mode is AppriseStoreMode.DISABLED

    # Create our object to work with
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.DISABLED)

    # Verify that the content doesn't already exist
    assert acc_obj.get(key) == (None, "")

    # Write our content assigned to our key
    # This isn't allowed
    assert acc_obj.put(key, content, ConfigFormat.TEXT) is False

    # Get path details
    conf_dir, _ = acc_obj.path(key)

    # List content of directory
    contents = os.listdir(conf_dir)

    # There should never be an entry
    assert len(contents) == 0

    # Content never exists
    assert acc_obj.clear(key) is None
