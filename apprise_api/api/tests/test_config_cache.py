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
import errno
import gzip
import os
import shutil
import time
from unittest.mock import mock_open, patch

from apprise import ConfigFormat
import pytest

from ..utils import AppriseConfigCache, AppriseStoreMode, MoveResult, SimpleFileExtension


def _backdate(path, seconds_ago):
    """Sets a file's mtime (and atime) `seconds_ago` seconds in the past."""
    backdated = time.time() - seconds_ago
    os.utime(path, (backdated, backdated))


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
    contents = os.listdir(conf_dir)

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
    contents = os.listdir(conf_dir)

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

    # Ensure the 10 configuration files (plus the hidden file) are the only
    # contents of the directory
    conf_dir, _ = acc_obj.path(key)
    contents = os.listdir(conf_dir)
    assert len(contents) == 11

    keys = acc_obj.keys()
    assert len(keys) == 10
    assert sorted(keys) == sorted(text_keys + yaml_keys)

    # Add a subdirectory — keys() must skip non-file entries (covers the
    # 'if os.path.isfile(path): False' branch)
    subdir = os.path.join(str(tmpdir), "subdir")
    os.makedirs(subdir)
    keys = acc_obj.keys()
    assert len(keys) == 10


def test_apprise_config_list_simple_mode_lock_only_key(tmpdir):
    """
    A key that has been assigned a login but was never given any
    configuration content still occupies that key (move()'s conflict
    check treats it as taken), so it must be visible via keys() too.
    """
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
    assert acc_obj.put(key, content, ConfigFormat.TEXT.value)

    m = mock_open()
    m.side_effect = OSError()
    with patch("builtins.open", m):
        # We'll fail to write our key now
        assert not acc_obj.put(key, content, ConfigFormat.TEXT.value)

    # Get path details
    conf_dir, _ = acc_obj.path(key)

    # List content of directory
    contents = os.listdir(conf_dir)

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
    contents = os.listdir(conf_dir)

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


def test_move_retains_destination_when_source_lock_cleanup_fails(tmpdir):
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
    # Same age as old_key's lock -- only survives because it has content.
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

    # An unrelated, unlocked config -- pruning must never touch it.
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
