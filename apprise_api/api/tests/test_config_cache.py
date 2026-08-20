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


def test_move_reports_failure_when_the_destination_directory_cannot_be_created(tmpdir):
    """A destination directory that can't be created fails the move cleanly; the source is untouched."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    content = "mailto://test:pass@gmail.com"
    assert acc_obj.put("move_makedirs_src", content, ConfigFormat.TEXT.value)

    with patch("os.makedirs", side_effect=OSError("permission denied")):
        assert acc_obj.move("move_makedirs_src", "move_makedirs_dst") == MoveResult.FAILED

    assert acc_obj.get("move_makedirs_src") == (content, ConfigFormat.TEXT.value)
    assert acc_obj.get("move_makedirs_dst") == (None, "")


def test_move_reports_a_stray_original_when_the_content_cannot_be_removed_after_a_locked_copy(tmpdir):
    """A locked-copy fallback that succeeds, but can't remove the original afterward, still
    reports a successful move -- the content is already safely at the destination, and a
    stray original left behind is a cleanup nuisance, not a failed move."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    content = "mailto://test:pass@gmail.com"
    assert acc_obj.put("move_strayorig_src", content, ConfigFormat.TEXT.value)

    with (
        patch("os.rename", side_effect=OSError("cross-device link")),
        patch("os.remove", side_effect=OSError("permission denied")),
    ):
        assert acc_obj.move("move_strayorig_src", "move_strayorig_dst") == MoveResult.MOVED

    assert acc_obj.get("move_strayorig_dst") == (content, ConfigFormat.TEXT.value)
    assert acc_obj.get("move_strayorig_src") == (content, ConfigFormat.TEXT.value)


def test_move_carries_the_authentication_lock_via_a_locked_copy_when_its_own_rename_fails(tmpdir):
    """The lock's own rename-fails fallback still carries it forward via a locked copy,
    independently of the configuration content's own (successful) rename."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    assert acc_obj.put("move_lockfallback_src", "mailto://test:pass@gmail.com", ConfigFormat.TEXT.value)
    assert acc_obj.set_auth("move_lockfallback_src", "alice", "secret")

    real_rename = os.rename
    real_remove = os.remove

    def rename_side_effect(src, dst):
        if src.endswith(".lock"):
            raise OSError("cross-device link")
        return real_rename(src, dst)

    def remove_side_effect(path):
        if path.endswith(".lock"):
            raise OSError("permission denied")
        return real_remove(path)

    with patch("os.rename", side_effect=rename_side_effect), patch("os.remove", side_effect=remove_side_effect):
        assert acc_obj.move("move_lockfallback_src", "move_lockfallback_dst") == MoveResult.MOVED

    assert acc_obj.verify_auth("move_lockfallback_dst", "alice", "secret") is True


def test_move_reports_the_authentication_lock_failure_when_its_own_locked_copy_also_fails(tmpdir):
    """If the lock's own rename AND its locked-copy fallback both fail, the content move
    still succeeds and the lock failure is only logged -- the source key keeps its lock
    rather than the move silently discarding it."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    assert acc_obj.put("move_lockcopyfail_src", "mailto://test:pass@gmail.com", ConfigFormat.TEXT.value)
    assert acc_obj.set_auth("move_lockcopyfail_src", "alice", "secret")

    real_rename = os.rename
    real_copy2 = shutil.copy2

    def rename_side_effect(src, dst):
        if src.endswith(".lock"):
            raise OSError("cross-device link")
        return real_rename(src, dst)

    def copy2_side_effect(src, dst):
        if src.endswith(".lock"):
            raise OSError("disk full")
        return real_copy2(src, dst)

    with patch("os.rename", side_effect=rename_side_effect), patch("shutil.copy2", side_effect=copy2_side_effect):
        assert acc_obj.move("move_lockcopyfail_src", "move_lockcopyfail_dst") == MoveResult.MOVED

    assert acc_obj.get_auth("move_lockcopyfail_dst") is None
    assert acc_obj.verify_auth("move_lockcopyfail_src", "alice", "secret") is True


def test_move_reports_failure_when_the_locked_copy_guard_file_cannot_be_created(tmpdir):
    """If the locked-copy fallback can't even create its own guard-lock file, the move fails cleanly."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    content = "mailto://test:pass@gmail.com"
    assert acc_obj.put("move_openfail_src", content, ConfigFormat.TEXT.value)

    real_open = os.open

    def open_side_effect(path, flags, *args, **kwargs):
        if path.endswith(".movelock"):
            raise OSError("too many open files")
        return real_open(path, flags, *args, **kwargs)

    with (
        patch("os.rename", side_effect=OSError("cross-device link")),
        patch("os.open", side_effect=open_side_effect),
    ):
        assert acc_obj.move("move_openfail_src", "move_openfail_dst") == MoveResult.FAILED

    assert acc_obj.get("move_openfail_src") == (content, ConfigFormat.TEXT.value)
    assert acc_obj.get("move_openfail_dst") == (None, "")


def test_move_reports_failure_when_the_locked_copy_cannot_acquire_its_guard_lock(tmpdir):
    """If the locked-copy fallback can't acquire its own guard lock, the move fails cleanly."""
    acc_obj = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    content = "mailto://test:pass@gmail.com"
    assert acc_obj.put("move_flockfail_src", content, ConfigFormat.TEXT.value)

    with (
        patch("os.rename", side_effect=OSError("cross-device link")),
        patch("fcntl.flock", side_effect=OSError("resource temporarily unavailable")),
    ):
        assert acc_obj.move("move_flockfail_src", "move_flockfail_dst") == MoveResult.FAILED

    assert acc_obj.get("move_flockfail_src") == (content, ConfigFormat.TEXT.value)
    assert acc_obj.get("move_flockfail_dst") == (None, "")


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
