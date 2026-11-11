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
from datetime import UTC, datetime
import errno
import fcntl
import gzip
import json
import os
import shutil
import signal
import socket
import time
from unittest.mock import mock_open, patch

from apprise import ConfigFormat
from django.test import override_settings
import pytest

from .. import utils as utils_module
from ..utils import (
    AppriseConfigCache,
    AppriseStoreMode,
    AuthStorageError,
    MoveResult,
    SimpleFileExtension,
    _configured_datetime,
)


def _backdate(path, seconds_ago):
    """Sets a file's mtime (and atime) `seconds_ago` seconds in the past."""
    backdated = time.time() - seconds_ago
    os.utime(path, (backdated, backdated))


def test_clear_preserving_auth_failure_paths(tmpdir):
    """Authenticated deletion fails closed if content or auth cannot be maintained."""
    disabled = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.DISABLED)
    assert disabled.clear_preserving_auth("key") is False

    store = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)
    with patch.object(store, "_acquire_auth_guard", side_effect=OSError):
        assert store.clear_preserving_auth("key") is False

    assert store.clear_preserving_auth("key") is False
    assert store.set_auth("key", "alice", "secret") is True

    with patch.object(store, "get_auth_record", side_effect=AuthStorageError("damaged")):
        assert store.clear_preserving_auth("key") is False

    with patch.object(store, "clear", return_value=False):
        assert store.clear_preserving_auth("key") is False

    with patch("os.utime", side_effect=OSError):
        assert store.clear_preserving_auth("key") is False


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
    assert acc_obj.put(key, content, ConfigFormat.TEXT.value)

    # Test the handling of underlining disk/write exceptions
    with patch("gzip.open") as mock_gzopen:
        mock_gzopen.side_effect = OSError()
        # We'll fail to write our key now
        assert not acc_obj.put(key, content, ConfigFormat.TEXT.value)

    # Get path details
    conf_dir, _ = acc_obj.path(key)

    # List content of directory
    contents = [name for name in os.listdir(conf_dir) if not name.startswith(".")]

    # There should be just 1 new file in this directory
    assert len(contents) == 1
    assert contents[0].endswith(".{}".format(ConfigFormat.TEXT.value))

    # Verify that the content is retrievable
    assert acc_obj.get(key) == (content, ConfigFormat.TEXT.value)

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
        assert not acc_obj.put(key, content, ConfigFormat.TEXT.value)

    # Now test with YAML file
    content = """
    version: 1

    urls:
       - windows://
    """

    # Write our content assigned to our key
    # This should gracefully clear the TEXT entry that was
    # previously in the spot
    assert acc_obj.put(key, content, ConfigFormat.YAML.value)

    # List content of directory
    contents = [name for name in os.listdir(conf_dir) if not name.startswith(".")]

    # There should STILL be just 1 new file in this directory
    assert len(contents) == 1
    assert contents[0].endswith(".{}".format(ConfigFormat.YAML.value))

    # Verify that the content is retrievable
    assert acc_obj.get(key) == (content, ConfigFormat.YAML.value)


def test_apprise_config_io_hash_mode_corrupt_encoding(tmpdir):
    """Treat invalid stored text as a read failure."""
    content = "mailto://test:pass@gmail.com"
    key = "test_apprise_config_io_hash_corrupt"

    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    assert acc_obj.put(key, content, ConfigFormat.TEXT.value)

    conf_dir, filename = acc_obj.path(key)
    full_path = os.path.join(conf_dir, "{}.{}".format(filename, ConfigFormat.TEXT.value))
    with gzip.open(full_path, "wb") as f:
        f.write(b"\xff\xfe not valid utf-8")

    assert acc_obj.get(key) == (None, None)


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
        assert acc_obj.put(key, content_text, ConfigFormat.TEXT.value)
    for key in yaml_keys:
        assert acc_obj.put(key, content_yaml, ConfigFormat.YAML.value)

    # Hidden bookkeeping files do not affect the ten visible configurations.
    conf_dir, _ = acc_obj.path(key)
    contents = [name for name in os.listdir(conf_dir) if not name.startswith(".")]
    assert len(contents) == 10

    keys = acc_obj.keys()
    assert len(keys) == 10
    assert sorted(keys) == sorted(text_keys + yaml_keys)

    # Add a subdirectory — keys() must skip non-file entries (covers the
    # 'if os.path.isfile(path): False' branch)
    subdir = os.path.join(str(tmpdir), "subdir")
    os.makedirs(subdir)
    keys = acc_obj.keys()
    assert len(keys) == 10


def test_apprise_config_list_includes_lock_only_key(tmpdir):
    """A login-only key is occupied and must appear in the key list."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)

    # Lock-only key: no content was ever put(), only a login assigned
    assert acc_obj.set_auth("lockonly", "user", "pass")

    # A regular key with actual content for comparison
    assert acc_obj.put("withcontent", "mailto://test:pass@gmail.com", ConfigFormat.TEXT.value)

    keys = acc_obj.keys()
    assert sorted(keys) == sorted(["lockonly", "withcontent"])


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
        assert acc_obj.put(key, content_text, ConfigFormat.TEXT.value)
    for key in yaml_keys:
        assert acc_obj.put(key, content_yaml, ConfigFormat.YAML.value)

    # Ensure the 10 configuration files (plus the hidden file) are the only
    # contents of the directory
    conf_dir, _ = acc_obj.path(key)
    contents = [name for name in os.listdir(conf_dir) if not name.startswith(".")]
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
    assert acc_obj.put(key, content, ConfigFormat.TEXT.value)

    m = mock_open()
    m.side_effect = OSError()
    with patch("builtins.open", m):
        # We'll fail to write our key now
        assert not acc_obj.put(key, content, ConfigFormat.TEXT.value)

    # Get path details
    conf_dir, _ = acc_obj.path(key)

    # List content of directory
    contents = [name for name in os.listdir(conf_dir) if not name.startswith(".")]

    # There should be just 1 new file in this directory
    assert len(contents) == 1
    assert contents[0].endswith(".{}".format(SimpleFileExtension.TEXT))

    # Verify that the content is retrievable
    assert acc_obj.get(key) == (content, ConfigFormat.TEXT.value)

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
        assert not acc_obj.put(key, content, ConfigFormat.TEXT.value)

    # Now test with YAML file
    content = """
    version: 1

    urls:
       - windows://
    """

    # Write our content assigned to our key
    # This should gracefully clear the TEXT entry that was
    # previously in the spot
    assert acc_obj.put(key, content, ConfigFormat.YAML.value)

    # List content of directory
    contents = [name for name in os.listdir(conf_dir) if not name.startswith(".")]

    # There should STILL be just 1 new file in this directory
    assert len(contents) == 1
    assert contents[0].endswith(".{}".format(SimpleFileExtension.YAML))

    # Verify that the content is retrievable
    assert acc_obj.get(key) == (content, ConfigFormat.YAML.value)


def test_apprise_config_io_simple_mode_corrupt_encoding(tmpdir):
    """Treat invalid stored text as a read failure."""
    content = "mailto://test:pass@gmail.com"
    key = "test_apprise_config_io_simple_corrupt"

    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)
    assert acc_obj.put(key, content, ConfigFormat.TEXT.value)

    conf_dir, filename = acc_obj.path(key)
    full_path = os.path.join(conf_dir, "{}.{}".format(filename, SimpleFileExtension.TEXT))
    with open(full_path, "wb") as f:
        f.write(b"\xff\xfe not valid utf-8")

    assert acc_obj.get(key) == (None, None)


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
    assert acc_obj.put(key, content, ConfigFormat.TEXT.value) is False

    # Get path details
    conf_dir, _ = acc_obj.path(key)

    # List content of directory
    contents = os.listdir(conf_dir)

    # There should never be an entry
    assert len(contents) == 0

    # Content never exists
    assert acc_obj.clear(key) is None


def test_move_simple_mode(tmpdir):
    """A move relocates content correctly under SIMPLE mode's plain-filename layout."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)
    content = "mailto://test:pass@gmail.com"
    assert acc_obj.put("move_simple_src", content, ConfigFormat.TEXT.value)

    assert acc_obj.move("move_simple_src", "move_simple_dst") == MoveResult.MOVED
    assert acc_obj.get("move_simple_dst") == (content, ConfigFormat.TEXT.value)
    assert acc_obj.get("move_simple_src") == (None, "")


def test_move_never_replaces_a_concurrent_destination(tmpdir):
    """A destination created during a move wins and the source remains intact."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)
    source = "mailto://source:pass@gmail.com"
    competing = "mailto://other:pass@gmail.com"
    assert acc_obj.put("move_race_src", source, ConfigFormat.TEXT.value)
    assert acc_obj.set_auth("move_race_src", "alice", "secret")

    real_link = os.link
    published = False

    def publish_competitor(src, dst):
        nonlocal published
        if not published and dst.endswith(".cfg"):
            published = True
            # Simulate an add that lands after move() checked the destination.
            with open(dst, "wb") as destination:
                destination.write(competing.encode())
        return real_link(src, dst)

    with patch("os.link", side_effect=publish_competitor):
        assert acc_obj.move("move_race_src", "move_race_dst") == MoveResult.CONFLICT

    assert acc_obj.get("move_race_src") == (source, ConfigFormat.TEXT.value)
    assert acc_obj.get("move_race_dst") == (competing, ConfigFormat.TEXT.value)


def test_unprotected_move_never_replaces_concurrent_destination(tmpdir):
    """The no-login move path also preserves a concurrently created target."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)
    source = "mailto://source:pass@gmail.com"
    competing = "mailto://other:pass@gmail.com"
    assert acc_obj.put("move_openrace_src", source, ConfigFormat.TEXT.value)

    real_link = os.link

    def publish_competitor(src, dst):
        # Another writer wins immediately before the hard-link publish step.
        with open(dst, "wb") as destination:
            destination.write(competing.encode())
        return real_link(src, dst)

    with patch("os.link", side_effect=publish_competitor):
        assert acc_obj.move("move_openrace_src", "move_openrace_dst") == MoveResult.CONFLICT

    assert acc_obj.get("move_openrace_src") == (source, ConfigFormat.TEXT.value)
    assert acc_obj.get("move_openrace_dst") == (competing, ConfigFormat.TEXT.value)


def test_move_disabled_mode(tmpdir):
    """A disabled store can never move anything, matching put()/get() being no-ops."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.DISABLED)
    assert acc_obj.move("move_disabled_src", "move_disabled_dst") == MoveResult.FAILED


def test_move_yaml_source(tmpdir):
    """A YAML-stored configuration is found and moved just as a TEXT one is."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    content = "urls:\n  - mailto://test:pass@gmail.com\n"
    assert acc_obj.put("move_yaml_src", content, ConfigFormat.YAML.value)

    assert acc_obj.move("move_yaml_src", "move_yaml_dst") == MoveResult.MOVED
    assert acc_obj.get("move_yaml_dst") == (content, ConfigFormat.YAML.value)


def test_move_fails_when_destination_directory_unavailable(tmpdir):
    """A destination directory that can't be created fails the move cleanly; the source is untouched."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    content = "mailto://test:pass@gmail.com"
    assert acc_obj.put("move_makedirs_src", content, ConfigFormat.TEXT.value)

    with patch("os.makedirs", side_effect=OSError("permission denied")):
        assert acc_obj.move("move_makedirs_src", "move_makedirs_dst") == MoveResult.FAILED

    assert acc_obj.get("move_makedirs_src") == (content, ConfigFormat.TEXT.value)
    assert acc_obj.get("move_makedirs_dst") == (None, "")


def test_move_handles_destination_directory_failure_after_guard(tmpdir):
    """A protected guard does not hide a later destination directory error."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    content = "mailto://test:pass@gmail.com"
    assert acc_obj.put("move_latedir_src", content, ConfigFormat.TEXT.value)

    with (
        patch.object(acc_obj, "_acquire_auth_guard", return_value=10),
        patch.object(acc_obj, "_release_auth_guard"),
        patch("os.makedirs", side_effect=OSError("permission denied")),
    ):
        assert acc_obj.move("move_latedir_src", "move_latedir_dst") == MoveResult.FAILED

    assert acc_obj.get("move_latedir_src") == (content, ConfigFormat.TEXT.value)


def test_move_cleanup_failure_never_restores_old_id(tmpdir):
    """Staging cleanup cannot leave the old Config ID usable."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    content = "mailto://test:pass@gmail.com"
    assert acc_obj.put("move_strayorig_src", content, ConfigFormat.TEXT.value)
    assert acc_obj.set_auth("move_strayorig_src", "alice", "secret")

    with (
        patch("os.remove", side_effect=OSError("permission denied")),
    ):
        assert acc_obj.move("move_strayorig_src", "move_strayorig_dst") == MoveResult.MOVED

    assert acc_obj.get("move_strayorig_dst") == (content, ConfigFormat.TEXT.value)
    assert acc_obj.get("move_strayorig_src") == (None, "")
    assert acc_obj.verify_auth("move_strayorig_dst", "alice", "secret") is True
    assert acc_obj.get_auth("move_strayorig_src") is None


def test_move_keeps_destination_on_lock_cleanup_failure(tmpdir):
    """A destination remains protected when its old lock cannot be removed."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    assert acc_obj.put("move_lockfallback_src", "mailto://test:pass@gmail.com", ConfigFormat.TEXT.value)
    assert acc_obj.set_auth("move_lockfallback_src", "alice", "secret")

    real_remove = os.remove

    def remove_side_effect(path):
        if path.endswith(".lock"):
            raise OSError("permission denied")
        return real_remove(path)

    with patch("os.remove", side_effect=remove_side_effect):
        assert acc_obj.move("move_lockfallback_src", "move_lockfallback_dst") == MoveResult.MOVED

    assert acc_obj.verify_auth("move_lockfallback_dst", "alice", "secret") is True


def test_move_stops_when_auth_lock_copy_fails(tmpdir):
    """Do not move content unless its destination lock is ready."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    assert acc_obj.put("move_lockcopyfail_src", "mailto://test:pass@gmail.com", ConfigFormat.TEXT.value)
    assert acc_obj.set_auth("move_lockcopyfail_src", "alice", "secret")

    real_link = os.link

    def link_side_effect(src, dst):
        if dst.endswith(".lock"):
            raise OSError("cross-device link")
        return real_link(src, dst)

    def copy_side_effect(_src, dst):
        return not dst.endswith(".lock")

    with (
        patch("os.link", side_effect=link_side_effect),
        patch.object(acc_obj, "_exclusive_copy", side_effect=copy_side_effect),
    ):
        assert acc_obj.move("move_lockcopyfail_src", "move_lockcopyfail_dst") == MoveResult.FAILED

    assert acc_obj.get_auth("move_lockcopyfail_dst") is None
    assert acc_obj.verify_auth("move_lockcopyfail_src", "alice", "secret") is True
    assert acc_obj.get("move_lockcopyfail_src")[0] is not None
    assert acc_obj.get("move_lockcopyfail_dst") == (None, "")


def test_move_rolls_back_staged_lock_when_content_copy_fails(tmpdir):
    """A failed content move removes the staged destination lock."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    content = "mailto://test:pass@gmail.com"
    assert acc_obj.put("move_contentfail_src", content, ConfigFormat.TEXT.value)
    assert acc_obj.set_auth("move_contentfail_src", "alice", "secret")

    real_copy2 = shutil.copy2
    real_link = os.link

    def copy2_side_effect(src, dst):
        if not src.endswith(".lock"):
            raise OSError("disk full")
        return real_copy2(src, dst)

    def link_side_effect(src, dst):
        if dst.endswith(".lock"):
            return real_link(src, dst)
        raise OSError("cross-device link")

    with (
        patch("os.link", side_effect=link_side_effect),
        patch("shutil.copy2", side_effect=copy2_side_effect),
    ):
        assert acc_obj.move("move_contentfail_src", "move_contentfail_dst") == MoveResult.FAILED

    assert acc_obj.get("move_contentfail_src") == (content, ConfigFormat.TEXT.value)
    assert acc_obj.get("move_contentfail_dst") == (None, "")
    assert acc_obj.verify_auth("move_contentfail_src", "alice", "secret") is True
    assert acc_obj.get_auth("move_contentfail_dst") is None


def test_move_reports_source_restore_failure(tmpdir):
    """A rollback error is contained after the destination is removed."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    content = "mailto://test:pass@gmail.com"
    source = "move_restorefail_src"
    destination = "move_restorefail_dst"
    assert acc_obj.put(source, content, ConfigFormat.TEXT.value)
    assert acc_obj.set_auth(source, "alice", "secret")

    src_text, _src_yaml = acc_obj._content_paths(source)
    real_replace = os.replace
    real_link = os.link

    def replace_side_effect(from_path, to_path):
        if os.path.basename(from_path).startswith(".move-source-") and to_path == src_text:
            raise OSError("source directory became read-only")
        return real_replace(from_path, to_path)

    def link_side_effect(from_path, to_path):
        if not to_path.endswith(".lock"):
            raise OSError("publish failed")
        return real_link(from_path, to_path)

    with (
        patch("os.replace", side_effect=replace_side_effect),
        patch("os.link", side_effect=link_side_effect),
        patch.object(acc_obj, "_exclusive_copy", return_value=False),
    ):
        assert acc_obj.move(source, destination) == MoveResult.FAILED

    assert acc_obj.get(destination) == (None, "")
    assert acc_obj.get_auth(destination) is None


def test_move_skips_missing_staging_file_during_rollback(tmpdir):
    """Rollback ignores a staging file removed outside the move."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)
    source = "move_missingstage_src"
    destination = "move_missingstage_dst"
    content = "mailto://test:pass@gmail.com"
    assert acc_obj.put(source, content, ConfigFormat.TEXT.value)

    real_exists = os.path.exists

    def exists_side_effect(path):
        if os.path.basename(path).startswith(".move-source-"):
            return False
        return real_exists(path)

    with (
        patch("os.link", side_effect=OSError("publish failed")),
        patch.object(acc_obj, "_exclusive_copy", return_value=False),
        patch("os.path.exists", side_effect=exists_side_effect),
    ):
        assert acc_obj.move(source, destination) == MoveResult.FAILED

    assert acc_obj.get(destination) == (None, "")


def test_move_rolls_back_lock_when_content_directory_fails(tmpdir):
    """A staged lock is removed if the content directory becomes unavailable."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    content = "mailto://test:pass@gmail.com"
    assert acc_obj.put("move_dirfail_src", content, ConfigFormat.TEXT.value)
    assert acc_obj.set_auth("move_dirfail_src", "alice", "secret")

    real_makedirs = os.makedirs
    calls = 0

    def makedirs_side_effect(path, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("permission denied")
        return real_makedirs(path, *args, **kwargs)

    with patch("os.makedirs", side_effect=makedirs_side_effect):
        assert acc_obj.move("move_dirfail_src", "move_dirfail_dst") == MoveResult.FAILED

    assert acc_obj.get("move_dirfail_src") == (content, ConfigFormat.TEXT.value)
    assert acc_obj.get("move_dirfail_dst") == (None, "")
    assert acc_obj.verify_auth("move_dirfail_src", "alice", "secret") is True
    assert acc_obj.get_auth("move_dirfail_dst") is None


def test_move_fails_when_auth_directory_is_unavailable(tmpdir):
    """Keep the source untouched when its destination cannot be protected."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    content = "mailto://test:pass@gmail.com"
    assert acc_obj.put("move_authdir_src", content, ConfigFormat.TEXT.value)
    assert acc_obj.set_auth("move_authdir_src", "alice", "secret")

    with patch("os.makedirs", side_effect=OSError("permission denied")):
        assert acc_obj.move("move_authdir_src", "move_authdir_dst") == MoveResult.FAILED

    assert acc_obj.get("move_authdir_src") == (content, ConfigFormat.TEXT.value)
    assert acc_obj.get("move_authdir_dst") == (None, "")
    assert acc_obj.verify_auth("move_authdir_src", "alice", "secret") is True


def test_move_handles_auth_directory_failure_after_guard(tmpdir):
    """A login-protected source stays intact if its target lock directory fails."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    content = "mailto://test:pass@gmail.com"
    assert acc_obj.put("move_authlate_src", content, ConfigFormat.TEXT.value)
    assert acc_obj.set_auth("move_authlate_src", "alice", "secret")

    with (
        patch.object(acc_obj, "_acquire_auth_guard", return_value=10),
        patch.object(acc_obj, "_release_auth_guard"),
        patch("os.makedirs", side_effect=OSError("permission denied")),
    ):
        assert acc_obj.move("move_authlate_src", "move_authlate_dst") == MoveResult.FAILED

    assert acc_obj.get("move_authlate_src") == (content, ConfigFormat.TEXT.value)
    assert acc_obj.verify_auth("move_authlate_src", "alice", "secret") is True


def test_move_rolls_back_when_source_staging_fails(tmpdir):
    """A failed source rename restores every file staged before it."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)
    content = "mailto://test:pass@gmail.com"
    assert acc_obj.put("move_lockcleanup_src", content, ConfigFormat.TEXT.value)
    assert acc_obj.set_auth("move_lockcleanup_src", "alice", "secret")
    src_path, src_name = acc_obj.auth_path("move_lockcleanup_src")
    src_file = os.path.join(src_path, src_name)
    real_replace = os.replace

    def replace_side_effect(source, destination):
        if source == src_file:
            raise OSError("permission denied")
        return real_replace(source, destination)

    with patch("os.replace", side_effect=replace_side_effect):
        assert acc_obj.move("move_lockcleanup_src", "move_lockcleanup_dst") == MoveResult.FAILED

    assert acc_obj.get("move_lockcleanup_src") == (content, ConfigFormat.TEXT.value)
    assert acc_obj.verify_auth("move_lockcleanup_src", "alice", "secret") is True
    assert acc_obj.get_auth("move_lockcleanup_dst") is None


def test_move_lock_only_source_relocates_the_lock(tmpdir):
    """A lock-only key moves by relocating its login lock."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)
    assert acc_obj.set_auth("move_lockonly_src", "alice", "secret")

    assert acc_obj.move("move_lockonly_src", "move_lockonly_dst") == MoveResult.MOVED
    assert acc_obj.verify_auth("move_lockonly_dst", "alice", "secret") is True
    assert acc_obj.get_auth("move_lockonly_src") is None


def test_move_lock_only_fails_when_lock_cannot_move(tmpdir):
    """A lock-only move fails when its protected copy cannot be written."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)
    assert acc_obj.set_auth("move_lockonlyfail_src", "alice", "secret")

    with (
        patch("os.link", side_effect=OSError("cross-device link")),
        patch.object(acc_obj, "_exclusive_copy", return_value=False),
    ):
        assert acc_obj.move("move_lockonlyfail_src", "move_lockonlyfail_dst") == MoveResult.FAILED

    assert acc_obj.verify_auth("move_lockonlyfail_src", "alice", "secret") is True


def test_move_fails_when_guard_file_creation_fails(tmpdir):
    """A move fails cleanly when its per-key guard cannot be opened."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    content = "mailto://test:pass@gmail.com"
    assert acc_obj.put("move_openfail_src", content, ConfigFormat.TEXT.value)

    real_open = os.open

    def open_side_effect(path, flags, *args, **kwargs):
        if path.endswith(".guard"):
            raise OSError("too many open files")
        return real_open(path, flags, *args, **kwargs)

    with (
        patch("os.open", side_effect=open_side_effect),
    ):
        assert acc_obj.move("move_openfail_src", "move_openfail_dst") == MoveResult.FAILED

    assert acc_obj.get("move_openfail_src") == (content, ConfigFormat.TEXT.value)
    assert acc_obj.get("move_openfail_dst") == (None, "")


def test_move_fails_when_guard_lock_unavailable(tmpdir):
    """A move fails cleanly when its per-key guard cannot be locked."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    content = "mailto://test:pass@gmail.com"
    assert acc_obj.put("move_flockfail_src", content, ConfigFormat.TEXT.value)

    with (
        patch("fcntl.flock", side_effect=OSError("resource temporarily unavailable")),
    ):
        assert acc_obj.move("move_flockfail_src", "move_flockfail_dst") == MoveResult.FAILED

    assert acc_obj.get("move_flockfail_src") == (content, ConfigFormat.TEXT.value)
    assert acc_obj.get("move_flockfail_dst") == (None, "")


def test_auth_guard_never_blocks_on_a_busy_writer(tmpdir):
    """A second credential writer fails promptly instead of deadlocking."""
    first = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    second = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    descriptor = first._acquire_auth_guard("first")
    try:
        with pytest.raises(BlockingIOError):
            second._acquire_auth_guard("second")
    finally:
        first._release_auth_guard(descriptor)

    # The failed attempt closed its descriptor, so the guard is reusable.
    descriptor = second._acquire_auth_guard("retry")
    second._release_auth_guard(descriptor)


def test_set_auth_rejects_colon_in_username(tmpdir):
    """Reject usernames that conflict with Basic Auth's colon separator."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)
    assert not acc_obj.set_auth("colon_username_key", "ali:ce", "secret")
    assert not acc_obj.has_auth("colon_username_key")


def test_prune_unused_locks_hash_mode(tmpdir):
    """Prune only old HASH-mode locks without configuration content."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)

    old_key = "test_prune_hash_old"
    young_key = "test_prune_hash_young"
    configured_key = "test_prune_hash_configured"

    assert acc_obj.set_auth(old_key, "alice", "secret")
    assert acc_obj.set_auth(young_key, "alice", "secret")
    assert acc_obj.set_auth(configured_key, "alice", "secret")
    assert acc_obj.put(configured_key, "mailto://test:pass@gmail.com", ConfigFormat.TEXT.value)

    old_path, old_filename = acc_obj.auth_path(old_key)
    _backdate(os.path.join(old_path, old_filename), seconds_ago=1000)
    configured_path, configured_filename = acc_obj.auth_path(configured_key)
    _backdate(os.path.join(configured_path, configured_filename), seconds_ago=1000)

    pruned = acc_obj.prune_unused_locks(older_than_seconds=500)

    assert pruned == 1
    assert not acc_obj.has_auth(old_key)
    assert acc_obj.has_auth(young_key)
    # Configuration content protects an old lock from pruning.
    assert acc_obj.has_auth(configured_key)
    assert acc_obj.get(configured_key) == ("mailto://test:pass@gmail.com", ConfigFormat.TEXT.value)


def test_hash_prune_removes_empty_prefix_directories(tmpdir):
    """Maintenance removes empty hash buckets but never the store root."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    empty_prefix = os.path.join(str(tmpdir), "ab")
    os.makedirs(empty_prefix)

    assert acc_obj.prune_unused_locks(older_than_seconds=500) == 0
    assert not os.path.exists(empty_prefix)
    assert os.path.isdir(str(tmpdir))


def test_hash_prune_tolerates_empty_directory_cleanup_failure(tmpdir):
    """A denied empty-directory cleanup does not fail the prune operation."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    empty_prefix = os.path.join(str(tmpdir), "ab")
    os.makedirs(empty_prefix)

    with patch("os.rmdir", side_effect=OSError(errno.EPERM, "denied")):
        assert acc_obj.prune_unused_locks(older_than_seconds=500) == 0

    assert os.path.isdir(empty_prefix)


def test_prune_releases_guard_after_unexpected_failure(tmpdir):
    """An unexpected scan failure cannot leave the maintenance guard locked."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)

    with (
        patch.object(acc_obj, "_acquire_auth_guard", return_value=10),
        patch.object(acc_obj, "_prune_unused_locks", side_effect=RuntimeError("scan failed")),
        patch.object(acc_obj, "_release_auth_guard") as release,
        pytest.raises(RuntimeError),
    ):
        acc_obj.prune_unused_locks(older_than_seconds=500)

    release.assert_called_once_with(10)


def test_prune_skips_when_guard_is_unavailable(tmpdir):
    """Maintenance leaves every lock untouched if its write guard fails."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)

    with patch.object(acc_obj, "_acquire_auth_guard", side_effect=OSError("busy")):
        assert acc_obj.prune_unused_locks(older_than_seconds=500) == 0


def test_prune_unused_locks_simple_mode(tmpdir):
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)

    old_key = "test_prune_simple_old"
    young_key = "test_prune_simple_young"
    configured_key = "test_prune_simple_configured"

    assert acc_obj.set_auth(old_key, "alice", "secret")
    assert acc_obj.set_auth(young_key, "alice", "secret")
    assert acc_obj.set_auth(configured_key, "alice", "secret")
    assert acc_obj.put(configured_key, "mailto://test:pass@gmail.com", ConfigFormat.TEXT.value)

    old_path, old_filename = acc_obj.auth_path(old_key)
    _backdate(os.path.join(old_path, old_filename), seconds_ago=1000)
    configured_path, configured_filename = acc_obj.auth_path(configured_key)
    _backdate(os.path.join(configured_path, configured_filename), seconds_ago=1000)

    pruned = acc_obj.prune_unused_locks(older_than_seconds=500)

    assert pruned == 1
    assert not acc_obj.has_auth(old_key)
    assert acc_obj.has_auth(young_key)
    # This old lock remains because its configuration still exists.
    assert acc_obj.has_auth(configured_key)
    assert acc_obj.get(configured_key) == ("mailto://test:pass@gmail.com", ConfigFormat.TEXT.value)


def test_prune_removes_all_eligible(tmpdir):
    """Prune every eligible lock without changing unlocked configurations."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)

    keys = ["test_prune_multi_a", "test_prune_multi_b", "test_prune_multi_c"]
    for key in keys:
        assert acc_obj.set_auth(key, "alice", "secret")
        path, filename = acc_obj.auth_path(key)
        _backdate(os.path.join(path, filename), seconds_ago=1000)

    # Pruning must not touch an unrelated, unlocked configuration.
    unlocked_key = "test_prune_multi_unlocked"
    assert acc_obj.put(unlocked_key, "mailto://test:pass@gmail.com", ConfigFormat.TEXT.value)

    pruned = acc_obj.prune_unused_locks(older_than_seconds=500)

    assert pruned == 3
    for key in keys:
        assert not acc_obj.has_auth(key)
    assert acc_obj.get(unlocked_key) == ("mailto://test:pass@gmail.com", ConfigFormat.TEXT.value)


def test_prune_skips_remove_errors(tmpdir):
    """A removal error does not stop the prune run."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)

    key = "test_prune_remove_failure"
    assert acc_obj.set_auth(key, "alice", "secret")
    path, filename = acc_obj.auth_path(key)
    _backdate(os.path.join(path, filename), seconds_ago=1000)

    with patch("os.remove") as mock_remove:
        mock_remove.side_effect = OSError(errno.EPERM)
        pruned = acc_obj.prune_unused_locks(older_than_seconds=500)

    assert pruned == 0
    assert acc_obj.has_auth(key)


def test_prune_skips_mtime_errors(tmpdir):
    """Skip locks whose modification time cannot be read."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)

    key = "test_prune_getmtime_failure"
    assert acc_obj.set_auth(key, "alice", "secret")
    path, filename = acc_obj.auth_path(key)
    _backdate(os.path.join(path, filename), seconds_ago=1000)

    with patch("os.path.getmtime") as mock_getmtime:
        mock_getmtime.side_effect = OSError(errno.ENOENT)
        pruned = acc_obj.prune_unused_locks(older_than_seconds=500)

    assert pruned == 0
    assert acc_obj.has_auth(key)


def test_hash_prune_ignores_foreign_dirs(tmpdir):
    """HASH-mode pruning only scans its two-character prefix directories."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)

    foreign_dir = os.path.join(str(tmpdir), "store")
    os.makedirs(foreign_dir)
    foreign_lock = os.path.join(foreign_dir, ".notavalidhash.lock")
    with open(foreign_lock, "w") as f:
        f.write("not an auth lock")
    _backdate(foreign_lock, seconds_ago=1000)

    assert acc_obj.prune_unused_locks(older_than_seconds=500) == 0
    assert os.path.isfile(foreign_lock)


def test_hash_prune_skips_symlink_dirs(tmpdir):
    """Do not follow prefix-directory links outside the configuration root."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)

    outside_dir = os.path.join(str(tmpdir), "outside-the-config-root")
    os.makedirs(outside_dir)
    outside_lock = os.path.join(outside_dir, "." + ("a" * 54) + ".lock")
    with open(outside_lock, "w") as f:
        f.write("not an auth lock")
    _backdate(outside_lock, seconds_ago=1000)

    symlinked_prefix = os.path.join(str(tmpdir), "aa")
    os.symlink(outside_dir, symlinked_prefix, target_is_directory=True)

    assert acc_obj.prune_unused_locks(older_than_seconds=500) == 0
    assert os.path.isfile(outside_lock)


def test_simple_prune_ignores_bad_names(tmpdir):
    """SIMPLE-mode pruning ignores invalid configuration keys."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)
    os.makedirs(str(tmpdir), exist_ok=True)

    malformed_lock = os.path.join(str(tmpdir), ". not valid!.lock")
    with open(malformed_lock, "w") as f:
        f.write("not an auth lock")
    _backdate(malformed_lock, seconds_ago=1000)

    assert acc_obj.prune_unused_locks(older_than_seconds=500) == 0
    assert os.path.isfile(malformed_lock)


def test_prune_skips_list_errors(tmpdir):
    """A directory listing error does not stop the prune run."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)

    key = "test_prune_listdir_failure"
    assert acc_obj.set_auth(key, "alice", "secret")
    path, filename = acc_obj.auth_path(key)
    _backdate(os.path.join(path, filename), seconds_ago=1000)

    with patch("os.listdir") as mock_listdir:
        mock_listdir.side_effect = OSError(errno.EACCES)
        pruned = acc_obj.prune_unused_locks(older_than_seconds=500)

    assert pruned == 0
    assert acc_obj.has_auth(key)


def test_prune_unused_locks_disabled_mode_is_a_noop(tmpdir):
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.DISABLED)
    assert acc_obj.prune_unused_locks(older_than_seconds=0) == 0


def test_prune_missing_root(tmpdir):
    """A missing configuration root has nothing to prune."""
    acc_obj = AppriseConfigCache(os.path.join(str(tmpdir), "does-not-exist"), mode=AppriseStoreMode.HASH)
    assert acc_obj.prune_unused_locks(older_than_seconds=0) == 0

    acc_obj = AppriseConfigCache(os.path.join(str(tmpdir), "also-missing"), mode=AppriseStoreMode.SIMPLE)
    assert acc_obj.prune_unused_locks(older_than_seconds=0) == 0

    # The guarded helper remains safe if the root disappears after locking.
    missing = os.path.join(str(tmpdir), "removed-after-guard")
    acc_obj = AppriseConfigCache(missing, mode=AppriseStoreMode.HASH)
    assert acc_obj._prune_unused_locks(older_than_seconds=0) == 0


@pytest.mark.parametrize("mode", (AppriseStoreMode.HASH, AppriseStoreMode.SIMPLE))
def test_get_file_times_for_stored_config(tmpdir, mode):
    """Stored configurations expose stable creation and modification times."""
    store = AppriseConfigCache(str(tmpdir), mode=mode)
    assert store.get_file_times("time-key") == (None, None)

    assert store.put("time-key", "json://localhost", ConfigFormat.TEXT.value)
    created, mtime = store.get_file_times("time-key")

    assert isinstance(created, datetime)
    assert isinstance(mtime, datetime)

    original_timestamp = datetime(2020, 1, 2, 3, 4, 5).timestamp()
    os.utime(store._creation_path("time-key"), (original_timestamp, original_timestamp))
    assert store.put("time-key", "json://example.com", ConfigFormat.TEXT.value)

    updated_created, updated_mtime = store.get_file_times("time-key")
    assert updated_created == datetime.fromtimestamp(original_timestamp)
    assert updated_mtime >= mtime

    assert store.move("time-key", "moved-time-key") == MoveResult.MOVED
    moved_created, _ = store.get_file_times("moved-time-key")
    assert moved_created == updated_created
    assert store.clear("moved-time-key") is True
    assert not os.path.exists(store._creation_path("moved-time-key"))


@override_settings(TIME_ZONE="America/New_York")
def test_get_file_times_use_configured_timezone(tmpdir):
    """Configuration times follow the configured zone across DST changes."""
    store = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)
    assert store.put("zoned-time-key", "json://localhost", ConfigFormat.TEXT.value)

    created_timestamp = datetime(2026, 1, 15, 12, tzinfo=UTC).timestamp()
    modified_timestamp = datetime(2026, 7, 15, 12, tzinfo=UTC).timestamp()
    os.utime(store._creation_path("zoned-time-key"), (created_timestamp, created_timestamp))
    content_path = next(path for path in store._content_paths("zoned-time-key") if os.path.isfile(path))
    os.utime(content_path, (modified_timestamp, modified_timestamp))

    created, modified = store.get_file_times("zoned-time-key")
    assert created == datetime(2026, 1, 15, 7)
    assert modified == datetime(2026, 7, 15, 8)


@override_settings(TIME_ZONE="Not/A-Timezone")
def test_configured_datetime_handles_unknown_timezone():
    """An invalid embedded setting falls back without hiding file details."""
    assert isinstance(_configured_datetime(0), datetime)


def test_get_file_times_is_empty_when_storage_is_disabled(tmpdir):
    """Disabled storage has no configuration timestamps."""
    store = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.DISABLED)
    assert store.get_file_times("time-key") == (None, None)


def test_clear_reports_creation_marker_removal_failure(tmpdir):
    """A marker we cannot delete makes the clear report a failure."""
    store = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    assert store.put("marker-key", "json://localhost", ConfigFormat.TEXT.value)

    marker = store._creation_path("marker-key")
    real_remove = os.remove

    def remove(path, *args, **kwargs):
        if path == marker:
            raise OSError(errno.EACCES, "denied")

        return real_remove(path, *args, **kwargs)

    with patch("os.remove", side_effect=remove):
        assert store.clear("marker-key") is False


def test_creation_marker_open_failure_is_not_fatal(tmpdir):
    """A marker we cannot create is logged and skipped, not raised."""
    store = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)

    with patch("os.open", side_effect=OSError(errno.EACCES, "denied")):
        assert store._record_creation_time("open-fail-key") is None

    assert not os.path.exists(store._creation_path("open-fail-key"))


def test_creation_marker_timestamp_failure_is_not_fatal(tmpdir):
    """A marker that cannot be backdated keeps its own creation time."""
    store = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    assert store.put("utime-fail-key", "json://localhost", ConfigFormat.TEXT.value)

    # Drop the marker put() wrote so a fresh one gets created below
    marker = store._creation_path("utime-fail-key")
    os.remove(marker)

    original_timestamp = datetime(2020, 1, 2, 3, 4, 5).timestamp()
    with patch("os.utime", side_effect=OSError(errno.EACCES, "denied")):
        assert store._record_creation_time("utime-fail-key", original_timestamp) is None

    # The marker still exists; only its timestamp could not be restored
    assert os.path.exists(marker)
    assert os.path.getmtime(marker) != original_timestamp


def test_get_file_times_is_empty_when_content_is_unreadable(tmpdir):
    """Content we cannot stat reports no timestamps at all."""
    store = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    assert store.put("unreadable-key", "json://localhost", ConfigFormat.TEXT.value)

    with patch("os.stat", side_effect=OSError(errno.EACCES, "denied")):
        assert store.get_file_times("unreadable-key") == (None, None)


@pytest.mark.parametrize("mode", [AppriseStoreMode.HASH, AppriseStoreMode.SIMPLE])
def test_reminders_round_trip(tmpdir, mode):
    """Reminders are saved, listed, read back and removed in either mode."""
    store = AppriseConfigCache(str(tmpdir), mode=mode)
    assert store.put("reminder-key", "json://localhost", ConfigFormat.TEXT.value)
    reminder_file = store.reminder_path("reminder-key")

    # Nothing is stored until the first event is saved
    assert store.read_reminders(reminder_file) == []
    assert store.reminder_files() == []

    with store.reminder_guard():
        assert store.write_reminders(reminder_file, [{"id": "a"}, {"id": "b"}]) is True

    assert store.read_reminders(reminder_file) == [{"id": "a"}, {"id": "b"}]
    assert store.reminder_files() == [reminder_file]
    # The scheduler finds the configuration through the reminder file alone
    assert store.reminder_config(reminder_file) == ("json://localhost", ConfigFormat.TEXT.value)

    # Hidden reminder files are never listed as keys
    if mode == AppriseStoreMode.SIMPLE:
        assert store.keys() == ["reminder-key"]

    # Saving no events removes the file, and removing it again is harmless
    with store.reminder_guard():
        assert store.write_reminders(reminder_file, []) is True
        assert store.write_reminders(reminder_file, []) is True

    assert not os.path.exists(reminder_file)


def test_reminder_files_skips_unrelated_entries(tmpdir):
    """Only real reminder files are listed."""
    store = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    assert store.reminder_files() == []

    prefix = tmpdir.mkdir("ab")
    prefix.join(".ignored.tmp").write("{}")
    prefix.join(".reminders").write("{}")
    prefix.join("visible.reminders").write("{}")
    prefix.join(".real.reminders").write("{}")
    # Directories that are not hash prefixes are left alone
    tmpdir.mkdir("not-a-prefix").join(".other.reminders").write("{}")
    assert store.reminder_files() == [str(prefix.join(".real.reminders"))]

    with patch("os.listdir", side_effect=OSError(errno.EACCES, "denied")):
        assert store.reminder_files() == []

    disabled = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.DISABLED)
    assert disabled.reminder_files() == []

    missing = AppriseConfigCache(str(tmpdir.join("missing")), mode=AppriseStoreMode.HASH)
    assert missing.reminder_files() == []


def test_read_reminders_rejects_damaged_files(tmpdir):
    """A damaged reminders reads as None instead of raising."""
    path = tmpdir.join(".key.reminders")

    path.write("not json")
    assert AppriseConfigCache.read_reminders(str(path)) is None

    path.write('["no", "events", "key"]')
    assert AppriseConfigCache.read_reminders(str(path)) is None

    path.write('{"events": "not a list"}')
    assert AppriseConfigCache.read_reminders(str(path)) is None

    # Entries that are not events are dropped
    path.write('{"events": [{"id": "a"}, 7]}')
    assert AppriseConfigCache.read_reminders(str(path)) == [{"id": "a"}]


def test_write_reminders_failures(tmpdir):
    """A reminder file that cannot be written leaves no temporary file behind."""
    store = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)
    reminder_file = store.reminder_path("key")

    with patch("tempfile.mkstemp", side_effect=OSError(errno.EACCES, "denied")):
        assert store.write_reminders(reminder_file, [{"id": "a"}]) is False

    with patch("os.replace", side_effect=OSError(errno.EACCES, "denied")):
        assert store.write_reminders(reminder_file, [{"id": "a"}]) is False

    assert os.listdir(str(tmpdir)) == []

    assert store.write_reminders(reminder_file, [{"id": "a"}]) is True
    with patch("os.remove", side_effect=OSError(errno.EACCES, "denied")):
        assert store.write_reminders(reminder_file, []) is False

    # A folder that cannot be put on disk, as on some network shares, is fine
    with patch("os.fsync", side_effect=[None, OSError(errno.EINVAL, "not supported")]):
        assert store.write_reminders(reminder_file, [{"id": "b"}]) is True

    # So is a file that cannot be, but a real write error still fails
    with patch("os.fsync", side_effect=OSError(errno.ENOTSUP, "not supported")):
        assert store.write_reminders(reminder_file, [{"id": "b"}]) is True

    with patch("os.fsync", side_effect=OSError(errno.EIO, "disk error")):
        assert store.write_reminders(reminder_file, [{"id": "c"}]) is False

    assert store.read_reminders(reminder_file) == [{"id": "b"}]


def test_clear_removes_reminders(tmpdir):
    """Replacing a configuration keeps its reminders; deleting it removes them."""
    for mode in (AppriseStoreMode.HASH, AppriseStoreMode.SIMPLE):
        store = AppriseConfigCache(str(tmpdir.mkdir(str(mode))), mode=mode)
        assert store.put("key", "json://localhost", ConfigFormat.TEXT.value)
        source = tmpdir.join(f"{mode}.txt")
        source.write_binary(str(mode).encode())
        digest, _size = store.store_reminder_file(str(source))
        reminder_file = store.reminder_path("key")
        event = {"id": "a", "enabled": False, "attach": [{"file": digest}]}
        assert store.write_reminders(reminder_file, [event]) is True
        assert store.reminder_has_config(reminder_file) is True

        # Replacing the configuration keeps its reminders
        assert store.put("key", "urls:\n  - json://localhost", ConfigFormat.YAML.value)
        assert store.read_reminders(reminder_file) == [event]

        # Reminders that cannot be moved aside leave everything in place
        with patch("os.replace", side_effect=OSError(errno.EACCES, "denied")):
            assert store.clear("key") is False

        assert store.get("key")[0] is not None
        assert store.read_reminders(reminder_file) == [event]

        # A configuration that could not be removed gets its reminders back
        with patch.object(store, "_clear", return_value=False):
            assert store.clear("key") is False

        assert store.read_reminders(reminder_file) == [event]

        # If they cannot be put back, they wait aside for recovery
        replace = os.replace

        def move_aside_only(source, target, reminder_file=reminder_file, replace=replace):
            if target == reminder_file:
                raise OSError(errno.EACCES, "denied")

            return replace(source, target)

        with patch.object(store, "_clear", return_value=False), patch("os.replace", side_effect=move_aside_only):
            assert store.clear("key") is False

        assert os.path.exists(reminder_file + ".removing")
        with patch.object(store, "wake_scheduler"):
            assert store.recover_removed_reminders() == 1

        assert store.read_reminders(reminder_file) == [event]

        # Left aside again, then deleted: the leftover goes with this delete
        os.replace(reminder_file, reminder_file + ".removing")
        assert store.write_reminders(reminder_file, [{"id": "b"}])
        with patch.object(store, "wake_scheduler"):
            assert store.clear("key") is True

        assert not os.path.exists(reminder_file + ".removing")
        assert not os.path.exists(reminder_file)
        assert store.put("key", "json://localhost", ConfigFormat.TEXT.value)

        # Removed reminders that cannot be deleted stay marked finished, so
        # saving the Config ID again never brings them back
        assert store.write_reminders(reminder_file, [event])
        remove = store._remove_file

        def keep_finished(path, remove=remove):
            return False if path.endswith(".removed") else remove(path)

        with patch.object(store, "_remove_file", side_effect=keep_finished), patch.object(store, "wake_scheduler"):
            assert store.clear("key") is True

        assert os.path.exists(reminder_file + ".removed")
        assert store.put("key", "json://localhost", ConfigFormat.TEXT.value)
        assert store.recover_removed_reminders() == 1
        assert store.read_reminders(reminder_file) == []

        # Reminders that cannot be marked finished are still deleted
        assert store.write_reminders(reminder_file, [event])

        def no_marking(source, target, replace=replace):
            if target.endswith(".removed"):
                raise OSError(errno.EACCES, "denied")

            return replace(source, target)

        with patch("os.replace", side_effect=no_marking), patch.object(store, "wake_scheduler"):
            assert store.clear("key") is True

        assert not os.path.exists(reminder_file + ".removing")
        assert store.put("key", "json://localhost", ConfigFormat.TEXT.value)
        assert store.write_reminders(reminder_file, [event])

        # Deleting it removes them, and the files nothing else uses, then wakes the scheduler
        with patch.object(store, "wake_scheduler") as wake:
            assert store.clear("key") is True

        wake.assert_called_once_with()
        assert not os.path.exists(reminder_file)
        assert store.reminder_file_stored(digest) is False

        # Once the configuration itself is gone, its reminders go even if the rest failed
        assert store.put("key", "json://localhost", ConfigFormat.TEXT.value)
        assert store.write_reminders(reminder_file, [event]) is True
        seen = store.seen_path("key")
        remove = store._remove_file

        def remove_all_but_seen(path, seen=seen, remove=remove):
            return path != seen and remove(path)

        with patch.object(store, "_remove_file", side_effect=remove_all_but_seen):
            assert store.clear("key") is False

        assert store.get("key") == (None, "")
        assert store.read_reminders(reminder_file) == []


def test_recover_removed_reminders(tmpdir):
    """Reminders a crash left aside go back, or finish being removed."""
    store = AppriseConfigCache(str(tmpdir.mkdir("cfg")), mode=AppriseStoreMode.SIMPLE)
    source = tmpdir.join("photo.jpg")
    source.write_binary(b"picture")
    digest, _size = store.store_reminder_file(str(source))

    def leave_aside(key, events):
        # As if a removal stopped right after moving the reminders aside
        assert store.write_reminders(store.reminder_path(key), events)
        os.replace(store.reminder_path(key), store.reminder_path(key) + ".removing")

    # Still configured: they go back, joined with any saved since
    assert store.put("kept", "json://localhost", ConfigFormat.TEXT.value)
    leave_aside("kept", [{"id": "a"}, {"id": "b"}])
    assert store.write_reminders(store.reminder_path("kept"), [{"id": "b", "name": "newer"}])

    # No longer configured: they are removed, with files nothing else uses
    leave_aside("gone", [{"id": "c", "attach": [{"file": digest}]}])

    # Files of reminders left aside count as used until then
    with store.reminder_guard():
        store.drop_unused_reminder_files([digest])

    assert store.reminder_file_stored(digest) is True

    with patch.object(store, "wake_scheduler"):
        assert store.recover_removed_reminders() == 2

    assert store.read_reminders(store.reminder_path("kept")) == [{"id": "b", "name": "newer"}, {"id": "a"}]
    assert store.reminder_files(".reminders.removing") == []
    assert store.reminder_file_stored(digest) is False

    # Ones that cannot be dealt with stay aside for later
    leave_aside("kept", [{"id": "d"}])
    with patch.object(store, "write_reminders", return_value=False):
        assert store.recover_removed_reminders() == 0

    with open(store.reminder_path("kept"), "w") as f:
        f.write("damaged")

    assert store.recover_removed_reminders() == 0
    os.remove(store.reminder_path("kept"))

    with patch.object(store, "_remove_file", return_value=False):
        assert store.recover_removed_reminders() == 0

    with open(store.reminder_path("kept") + ".removing", "w") as f:
        f.write("damaged")

    assert store.recover_removed_reminders() == 0
    assert len(store.reminder_files(".reminders.removing")) == 1
    os.remove(store.reminder_path("kept") + ".removing")

    # A Config ID saved or moved to again finishes the stopped removal first,
    # so the old ones never come back
    leave_aside("again", [{"id": "f"}])
    with patch("os.replace", side_effect=OSError("denied")):
        assert store.put("again", "json://localhost", ConfigFormat.TEXT.value) is False

    assert store.put("again", "json://localhost", ConfigFormat.TEXT.value)
    assert store.reminder_files(".reminders.removing") == []
    assert store.recover_removed_reminders() == 0
    assert store.read_reminders(store.reminder_path("again")) == []

    assert store.put("moving", "json://localhost", ConfigFormat.TEXT.value)
    leave_aside("target", [{"id": "t"}])
    with patch("os.replace", side_effect=OSError("denied")):
        assert store.move("moving", "target") == MoveResult.FAILED

    assert store.move("moving", "target") == MoveResult.MOVED
    assert store.reminder_files(".reminders.removing") == []
    assert store.read_reminders(store.reminder_path("target")) == []

    # Saving a configuration that is still there puts its reminders back
    leave_aside("target", [{"id": "u"}])
    assert store.put("target", "json://localhost", ConfigFormat.TEXT.value)
    assert store.read_reminders(store.reminder_path("target")) == [{"id": "u"}]

    # It still saves when they cannot go back yet; recovery tries again later
    leave_aside("target", [{"id": "v"}])
    with patch.object(store, "write_reminders", return_value=False):
        assert store.put("target", "json://localhost", ConfigFormat.TEXT.value)

    assert len(store.reminder_files(".reminders.removing")) == 1
    os.remove(store.reminder_path("target") + ".removing")

    # A removal stops when reminders left aside earlier cannot be settled
    with open(store.reminder_path("again") + ".removing", "w") as f:
        f.write("damaged")

    assert store.write_reminders(store.reminder_path("again"), [{"id": "g"}])
    assert store.clear("again") is False
    assert store.get("again")[0] is not None
    assert store.read_reminders(store.reminder_path("again")) == [{"id": "g"}]
    os.remove(store.reminder_path("again") + ".removing")

    # Ones marked finished are only ever removed, even though "kept" exists
    assert store.write_reminders(store.reminder_path("kept"), [{"id": "e"}])
    os.replace(store.reminder_path("kept"), store.reminder_path("kept") + ".removed")
    assert store.recover_removed_reminders() == 1
    assert store.read_reminders(store.reminder_path("kept")) == []
    assert store.reminder_files(".reminders.removed") == []


def test_unused_reminder_files_go_right_away(tmpdir):
    """Stored files nothing uses are removed at once, unless a reminder file cannot be read."""
    store = AppriseConfigCache(str(tmpdir.mkdir("cfg")), mode=AppriseStoreMode.SIMPLE)
    assert store.reminder_store_size() == 0
    source = tmpdir.join("photo.jpg")
    source.write_binary(b"picture")
    digest, _size = store.store_reminder_file(str(source))
    assert store.reminder_store_size() == 7
    with patch("os.scandir", side_effect=PermissionError("denied")):
        assert store.reminder_store_size() is None

    # Still used by a reminder
    assert store.write_reminders(store.reminder_path("a"), [{"id": "a", "attach": [{"file": digest}]}])
    with store.reminder_guard():
        store.drop_unused_reminder_files([])
        store.drop_unused_reminder_files([digest])

    assert store.reminder_file_stored(digest) is True

    # A damaged reminder file might use it, so it is kept
    assert store.write_reminders(store.reminder_path("a"), [])
    with open(store.reminder_path("b"), "w") as f:
        f.write("{")

    with store.reminder_guard():
        store.drop_unused_reminder_files([digest])

    assert store.reminder_file_stored(digest) is True

    os.remove(store.reminder_path("b"))
    with store.reminder_guard():
        store.drop_unused_reminder_files([digest])

    assert store.reminder_file_stored(digest) is False


def test_move_carries_reminders(tmpdir):
    """A moved configuration keeps its reminders and wakes the scheduler."""
    store = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    assert store.put("old", "json://localhost", ConfigFormat.TEXT.value)
    assert store.write_reminders(store.reminder_path("old"), [{"id": "a"}]) is True

    with patch.object(store, "wake_scheduler") as wake:
        assert store.move("old", "new") == MoveResult.MOVED

    wake.assert_called_once_with()
    assert not os.path.exists(store.reminder_path("old"))
    assert store.read_reminders(store.reminder_path("new")) == [{"id": "a"}]

    # A configuration without reminders moves without waking anything
    with patch.object(store, "wake_scheduler") as wake:
        assert store.move("new", "newer") == MoveResult.MOVED
        assert store.move("newer", "plain") == MoveResult.MOVED

    assert wake.call_count == 2
    os.remove(store.reminder_path("plain"))
    with patch.object(store, "wake_scheduler") as wake:
        assert store.move("plain", "last") == MoveResult.MOVED

    wake.assert_not_called()


def test_move_refuses_existing_destination_reminders(tmpdir):
    """Reminders already at the destination blocks the move."""
    store = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)
    assert store.put("old", "json://localhost", ConfigFormat.TEXT.value)
    assert store.write_reminders(store.reminder_path("new"), [{"id": "a"}]) is True

    assert store.move("old", "new") == MoveResult.CONFLICT


def test_scheduler_lock_allows_one_holder(tmpdir):
    """Only one scheduler can hold the lock, and it records its process ID."""
    store = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)
    with store.scheduler_lock() as held:
        assert held is True
        with store.scheduler_lock() as second:
            assert second is False

        assert str(os.getpid()) in tmpdir.join(".scheduler.pid").read()

    # The pid file is not mistaken for a key
    assert store.keys() == []

    with store.scheduler_lock() as held:
        assert held is True


def test_scheduler_status_and_change_stamp(tmpdir):
    """Callers can tell whether a scheduler runs, and when reminders last changed."""
    store = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)
    assert store.scheduler_running() is False
    assert store.reminders_changed() == 0

    with store.scheduler_lock():
        assert store.scheduler_running() is True

    # Waking marks reminders as changed, even with no scheduler to signal
    assert store.wake_scheduler() is False
    assert store.reminders_changed() > 0

    # A stamp that cannot be written is only logged
    with patch.object(utils_module, "touch", return_value=False):
        assert store.wake_scheduler() is False


def test_wake_scheduler_signals_only_a_running_scheduler(tmpdir):
    """The wake signal goes only to a scheduler that holds the lock on this host."""
    store = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)
    pid_file = tmpdir.join(".scheduler.pid")

    # No scheduler has ever run
    assert store.wake_scheduler() is False

    # A stale record left behind by a stopped scheduler is ignored
    pid_file.write(json.dumps({"pid": 4242, "host": socket.gethostname()}))
    with patch("os.kill") as kill:
        assert store.wake_scheduler() is False

    kill.assert_not_called()

    holder = os.open(str(pid_file), os.O_RDWR)
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        with patch("os.kill") as kill:
            assert store.wake_scheduler() is True

        kill.assert_called_once_with(4242, signal.SIGUSR1)

        with patch("os.kill", side_effect=ProcessLookupError):
            assert store.wake_scheduler() is False

        for record in ("not json", '{"pid": 4242}', '{"pid": 4242, "host": "elsewhere"}'):
            pid_file.write(record)
            with patch("os.kill") as kill:
                assert store.wake_scheduler() is False

            kill.assert_not_called()

        # The scheduler never signals itself
        pid_file.write(json.dumps({"pid": os.getpid(), "host": socket.gethostname()}))
        assert store.wake_scheduler() is False

    finally:
        os.close(holder)

    with patch("fcntl.flock", side_effect=OSError(errno.ENOLCK, "no locks")):
        assert store.wake_scheduler() is False


def test_limits_round_trip(tmpdir):
    """Limits are saved, read back, and removed once none are left."""
    store = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    assert store.get_limits("key") == {}
    assert store.set_limits("key", {"attach_size": 10}) is True
    assert store.get_limits("key") == {"attach_size": 10}

    assert store.set_limits("key", {}) is True
    assert not os.path.exists(store.limits_path("key"))
    assert store.set_limits("key", {}) is True

    # A damaged file cannot be read
    os.makedirs(os.path.dirname(store.limits_path("key")), exist_ok=True)
    for content in ("not json", "[1]"):
        with open(store.limits_path("key"), "w") as f:
            f.write(content)
        assert store.get_limits("key") is None


@override_settings(APPRISE_REMINDER_IDLE_DAYS=30)
def test_note_use_records_at_most_once_a_day(tmpdir):
    """Use is recorded only for a key with reminders, and at most daily."""
    store = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)
    seen = store.seen_path("key")
    store.note_use("key")
    assert not os.path.exists(seen)

    assert store.write_reminders(store.reminder_path("key"), [{"id": "a"}])
    store.note_use("key")
    assert os.path.exists(seen)

    # A recent record is left alone, and an old one is renewed
    os.utime(seen, (1000, 1000))
    with patch("time.time", return_value=1000 + 3600):
        store.note_use("key")
    assert os.path.getmtime(seen) == 1000

    store.note_use("key")
    assert os.path.getmtime(seen) > 1000

    os.utime(seen, (1000, 1000))
    with patch("builtins.open", side_effect=OSError(errno.EACCES, "denied")):
        store.note_use("key")
    assert os.path.getmtime(seen) == 1000

    with override_settings(APPRISE_REMINDER_IDLE_DAYS=0):
        store.note_use("key")
    assert os.path.getmtime(seen) == 1000


def test_reminder_seen_starts_counting(tmpdir):
    """A key never recorded starts counting the first time it is checked."""
    store = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)
    reminder_file = store.reminder_path("key")
    assert store.reminder_seen(reminder_file) > 0
    assert os.path.exists(store.seen_path("key"))

    with patch("builtins.open", side_effect=OSError(errno.EACCES, "denied")):
        assert store.reminder_seen(reminder_file) is None


def test_store_reminder_file_keeps_one_copy(tmpdir):
    """The same content is stored once and renewed when stored again."""
    store = AppriseConfigCache(str(tmpdir.mkdir("cfg")), mode=AppriseStoreMode.HASH)
    source = tmpdir.join("photo.jpg")
    source.write_binary(b"picture")

    digest, size = store.store_reminder_file(str(source))
    assert size == 7
    with open(store.reminder_file_path(digest), "rb") as f:
        assert f.read() == b"picture"
    os.utime(store.reminder_file_path(digest), (1000, 1000))

    assert store.store_reminder_file(str(source)) == (digest, 7)
    assert os.path.getmtime(store.reminder_file_path(digest)) > 1000
    assert os.listdir(os.path.dirname(store.reminder_file_path(digest))) == [digest]

    assert store.store_reminder_file(str(tmpdir.join("missing"))) is None
    assert os.listdir(os.path.dirname(store.reminder_file_path(digest))) == [digest]

    # Hashing alone gives the same name and size without storing anything
    other = tmpdir.join("other.jpg")
    other.write_binary(b"another")
    name, size = store.reminder_file_digest(str(other))
    assert size == 7
    assert store.reminder_file_stored(digest) is True
    assert store.reminder_file_stored(name) is False
    assert store.reminder_file_digest(str(tmpdir.join("missing"))) is None


def test_prune_reminder_files_removes_only_unused_old_files(tmpdir):
    """Stored files no reminder uses are pruned once they are old enough."""
    store = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    assert store.prune_reminder_files() == 0

    directory = os.path.dirname(store.reminder_file_path("x"))
    os.makedirs(directory)
    for name in ("used", "unused", "fresh"):
        with open(os.path.join(directory, name), "w") as f:
            f.write(name)
        if name != "fresh":
            os.utime(os.path.join(directory, name), (1000, 1000))

    assert store.put("key", "json://localhost", ConfigFormat.TEXT.value)
    reminder_file = store.reminder_path("key")
    assert store.write_reminders(reminder_file, [{"id": "a", "attach": [{"file": "used"}, {"url": "http://x"}]}])
    assert store.prune_reminder_files() == 1
    assert sorted(os.listdir(directory)) == ["fresh", "used"]

    # Nothing is removed while a reminder file cannot be read
    os.utime(os.path.join(directory, "fresh"), (1000, 1000))
    with open(reminder_file, "w") as f:
        f.write("damaged")
    assert store.prune_reminder_files() == 0
    assert store.write_reminders(reminder_file, [{"id": "a"}])

    with patch("os.listdir", side_effect=OSError(errno.EACCES, "denied")):
        assert store.prune_reminder_files() == 0

    with patch("os.remove", side_effect=OSError(errno.EACCES, "denied")):
        assert store.prune_reminder_files() == 0

    assert store.prune_reminder_files() == 2
    assert AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.DISABLED).prune_reminder_files() == 0


def test_prune_finished_reminders(tmpdir):
    """Reminders with nothing left to send are removed once old enough, freeing their files."""
    store = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    assert store.prune_finished_reminders(0) == 0

    now = int(time.time())
    old = now - 8 * 86400
    events = [
        {"id": "sent", "next": None, "updated": old, "last": {"at": old, "ok": True}, "attach": [{"file": "f"}]},
        {"id": "recent", "next": None, "updated": old, "last": {"at": now, "ok": True}},
        {"id": "ended", "next": None, "updated": old},
        {"id": "paused", "enabled": False, "next": None, "updated": old},
        {"id": "due", "next": now + 60, "updated": old},
    ]
    assert store.put("key", "json://localhost", ConfigFormat.TEXT.value)
    reminder_file = store.reminder_path("key")
    assert store.write_reminders(reminder_file, events)
    directory = os.path.dirname(store.reminder_file_path("f"))
    os.makedirs(directory)
    with open(os.path.join(directory, "f"), "w") as f:
        f.write("f")
    os.utime(os.path.join(directory, "f"), (old, old))

    # A reminders file with nothing in it is passed over
    assert store.put("other", "json://localhost", ConfigFormat.TEXT.value)
    with open(store.reminder_path("other"), "w") as f:
        f.write('{"version": 1, "events": []}')

    # A failed save removes nothing
    with patch.object(store, "write_reminders", return_value=False):
        assert store.prune_finished_reminders(7 * 86400) == 0

    assert store.prune_finished_reminders(7 * 86400) == 2
    assert [event["id"] for event in store.read_reminders(reminder_file)] == ["recent", "paused", "due"]
    # The attachment only the sent reminder used goes with it
    assert store.prune_reminder_files() == 1

    # With no wait, every finished reminder goes; an emptied file is removed
    assert store.write_reminders(reminder_file, [events[1]])
    assert store.prune_finished_reminders(0) == 1
    assert not os.path.exists(reminder_file)
    assert AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.DISABLED).prune_finished_reminders(0) == 0


def test_move_and_clear_handle_limits_and_use(tmpdir):
    """Limits and the use record move with a configuration; clearing keeps the limits."""
    store = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    assert store.put("old", "json://localhost", ConfigFormat.TEXT.value)
    assert store.set_limits("old", {"reminders": 3})
    store.reminder_seen(store.reminder_path("old"))

    assert store.move("old", "new") == MoveResult.MOVED
    assert store.get_limits("new") == {"reminders": 3}
    assert os.path.exists(store.seen_path("new"))
    assert not os.path.exists(store.seen_path("old"))

    assert store.clear("new") is True
    assert not os.path.exists(store.seen_path("new"))
    # Limits are the administrator's, so a user's delete does not drop them
    assert store.get_limits("new") == {"reminders": 3}

    # The use record that cannot be removed fails the delete
    assert store.put("new", "json://localhost", ConfigFormat.TEXT.value)
    with patch.object(store, "_remove_file", return_value=False):
        assert store.clear("new") is False


def test_prune_unused_locks_removes_their_limits(tmpdir):
    """Limits go with the unused lock of a configuration that was deleted."""
    store = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)
    assert store.set_auth("gone", "alice", "secret")
    assert store.set_limits("gone", {"reminders": 3})
    assert store.prune_unused_locks(0) == 1
    assert not os.path.exists(store.limits_path("gone"))


def test_release_user_reminders(tmpdir):
    """A removed user's reminders are deleted, or handed to the administrator."""
    store = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.SIMPLE)
    reminder_file = store.reminder_path("key")
    assert store.release_user_reminders("key", keep=False) == ([], [])

    # Both the reminders as they were and as they are now come back
    events = [{"id": "a", "owner": "admin"}, {"id": "b", "owner": "user"}]
    assert store.write_reminders(reminder_file, events)
    before, after = store.release_user_reminders("key", keep=True)
    assert before == events
    assert [event["owner"] for event in after] == ["admin", "admin"]
    assert store.read_reminders(reminder_file) == after

    assert store.write_reminders(reminder_file, events)
    assert store.release_user_reminders("key", keep=False) == (events, [events[0]])
    assert store.read_reminders(reminder_file) == [events[0]]

    # A file that cannot be saved or read is reported
    assert store.write_reminders(reminder_file, events)
    with patch.object(store, "write_reminders", return_value=False):
        assert store.release_user_reminders("key", keep=True) is None
        assert store.release_user_reminders("key", keep=False) is None

    with open(reminder_file, "w") as f:
        f.write("damaged")
    assert store.release_user_reminders("key", keep=False) is None
