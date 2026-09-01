#
# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
# All rights reserved.
#
# This code is licensed under the MIT License.
#
"""Test the isolated cache used for repeated configuration authentication."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from ..auth import ConfigCredentialVerifier


class _ReadOnlyRequest:
    """Act like a request wrapper that does not permit extra attributes."""

    __slots__ = ()


class _UnreadableCache(dict):
    """Raise when code asks whether a request decision exists."""

    def __contains__(self, _key):
        """Simulate a damaged custom request cache."""
        raise RuntimeError("cache cannot be read")


class _UnwritableCache(dict):
    """Raise when code tries to remember a request decision."""

    def __setitem__(self, _key, _value):
        """Simulate a read-only custom request cache."""
        raise RuntimeError("cache cannot be written")


class ConfigCredentialVerifierTests(SimpleTestCase):
    """Cover successful, failed, expired, malformed, and degraded checks."""

    def setUp(self):
        """Build a deterministic verifier so timing never makes tests flaky."""
        self.now = 100
        self.checker = Mock(return_value=True)
        self.verifier = ConfigCredentialVerifier(
            max_entries=2,
            ttl=300,
            secret=b"t" * 32,
            clock=lambda: self.now,
            password_checker=self.checker,
        )

    def verify(self, **kwargs):
        """Call the verifier with readable defaults for each focused test."""
        values = {
            "key": "config-a",
            "username": "alice",
            "password": "secret",
            "stored_username": "alice",
            "digest": "digest-a",
        }
        values.update(kwargs)
        return self.verifier.verify(**values)

    def test_constructor_rejects_unsafe_limits(self):
        """A cache must always have a real size and a positive lifetime."""
        with self.assertRaises(ValueError):
            ConfigCredentialVerifier(max_entries=0)
        with self.assertRaises(ValueError):
            ConfigCredentialVerifier(ttl=0)
        with self.assertRaises(ValueError):
            ConfigCredentialVerifier(secret=b"too-short")
        with self.assertRaises(ValueError):
            ConfigCredentialVerifier(clock="not callable")
        with self.assertRaises(ValueError):
            ConfigCredentialVerifier(password_checker="not callable")
        with patch("api.auth.os.urandom", side_effect=OSError("random failed")), self.assertRaises(RuntimeError):
            ConfigCredentialVerifier()

    def test_success_is_cached_without_storing_plain_credentials(self):
        """Later requests use the private fingerprint instead of PBKDF2."""
        self.assertTrue(self.verify())
        self.assertTrue(self.verify())
        self.checker.assert_called_once_with("alice:secret", "digest-a")
        self.assertEqual(len(self.verifier), 1)

        fingerprint = next(iter(self.verifier._cache))
        self.assertIsInstance(fingerprint, bytes)
        self.assertNotIn(b"alice", fingerprint)
        self.assertNotIn(b"secret", fingerprint)

        self.verifier.clear()
        self.assertEqual(len(self.verifier), 0)

    def test_request_reuses_success_and_failure_once(self):
        """Middleware and its view share one decision on the request object."""
        request = SimpleNamespace()
        self.assertTrue(self.verify(request=request))
        self.verifier.clear()
        self.assertTrue(self.verify(request=request))
        self.checker.assert_called_once()

        failed_request = SimpleNamespace()
        self.checker.return_value = False
        self.assertFalse(self.verify(password="wrong", request=failed_request))
        self.assertFalse(self.verify(password="wrong", request=failed_request))
        self.assertEqual(self.checker.call_count, 2)
        self.assertEqual(len(self.verifier), 0)

    def test_username_mismatch_hashes_but_malformed_values_do_not(self):
        """A wrong username has no timing shortcut; malformed data stays cheap."""
        self.assertFalse(self.verify(username="bob"))
        self.checker.assert_called_once_with("bob:secret", "digest-a")
        self.assertFalse(self.verify(key=None))
        self.assertFalse(self.verify(stored_username=object()))
        self.checker.assert_called_once()

        # The low-level verifier also supports callers without a username label.
        self.assertTrue(self.verify(stored_username=None))

    def test_digest_and_config_changes_cannot_reuse_success(self):
        """Moving a key or changing a password requires a fresh verification."""
        self.assertTrue(self.verify())
        self.assertTrue(self.verify(key="config-b"))
        self.assertTrue(self.verify(digest="digest-b"))
        self.assertEqual(self.checker.call_count, 3)

    def test_expiry_is_absolute_and_capacity_is_bounded(self):
        """Activity does not extend expiry and the oldest entry is evicted."""
        self.assertTrue(self.verify())
        self.now = 399
        self.assertTrue(self.verify())
        self.checker.assert_called_once()

        self.now = 400
        self.assertTrue(self.verify())
        self.assertEqual(self.checker.call_count, 2)

        self.assertTrue(self.verify(key="config-b"))
        self.assertTrue(self.verify(key="config-c"))
        self.assertEqual(len(self.verifier), 2)

        # Config A was the oldest, so it needs another full check.
        self.assertTrue(self.verify())
        self.assertEqual(self.checker.call_count, 5)

    def test_cache_and_fingerprint_failures_fall_back_safely(self):
        """Optimization failures never grant access or cause HTTP 500 errors."""
        with patch.object(self.verifier, "_fingerprint", side_effect=ValueError("bad fingerprint")):
            self.assertTrue(self.verify())

        with patch.object(self.verifier, "_cached_success", side_effect=RuntimeError("cache read failed")):
            self.assertTrue(self.verify(key="config-b"))

        with patch.object(self.verifier, "_remember_success", side_effect=RuntimeError("cache write failed")):
            self.assertTrue(self.verify(key="config-c"))

        self.checker.side_effect = OSError("password backend failed")
        self.assertFalse(self.verify(key="config-d"))

    def test_unusable_request_cache_falls_back_to_process_cache(self):
        """Odd request wrappers only lose the one-request shortcut."""
        malformed = SimpleNamespace(_apprise_auth_verification_results=[])
        self.assertTrue(self.verify(request=malformed))

        self.assertTrue(self.verify(key="config-b", request=_ReadOnlyRequest()))

        unreadable = SimpleNamespace(_apprise_auth_verification_results=_UnreadableCache())
        self.assertTrue(self.verify(key="config-c", request=unreadable))

        unwritable = SimpleNamespace(_apprise_auth_verification_results=_UnwritableCache())
        self.assertTrue(self.verify(key="config-d", request=unwritable))
