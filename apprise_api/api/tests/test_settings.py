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

import base64
import importlib.util
import logging
import os
from unittest import mock

from core.settings.env import env_bool, env_choice, env_int, env_optional_bool
from core.utils import parse_bool, parse_log_level
from django.conf import global_settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

# Path to the settings module under test, resolved relative to this file:
#   apprise_api/api/tests/ -> ../../ -> apprise_api/ -> core/settings/__init__.py
_SETTINGS_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "core", "settings", "__init__.py")
)


def _load_settings(extra_env=None):
    """Execute core/settings/__init__.py as a fresh module in a controlled environment.

    Returns the module so callers can inspect settings values (e.g. TIME_ZONE)
    as they would be set for a given environment, independently of Django's
    already-cached settings object.
    """
    env = dict(extra_env or {})
    spec = importlib.util.spec_from_file_location("_settings_under_test", _SETTINGS_PATH)
    assert spec is not None and spec.loader is not None, "Could not load spec from {}".format(_SETTINGS_PATH)
    mod = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, env, clear=True):
        spec.loader.exec_module(mod)
    return mod


class BooleanParsingTests(SimpleTestCase):
    """Test the shared loose boolean convention."""

    def test_true_prefixes(self):
        """Common affirmative prefixes are accepted."""
        # Every value begins with one of the supported truthy characters.
        for value in ("active", "yes", "1", "true", "enable", "+", "  Enabled"):
            self.assertTrue(parse_bool(value))

    def test_false_and_empty_values(self):
        """Negative and empty values use the expected fallback."""
        # Unrecognized prefixes keep the normal false default.
        for value in ("no", "false", "0", "disabled", "", "   ", None):
            self.assertFalse(parse_bool(value))

        # Empty input can also preserve a caller-supplied true default.
        self.assertTrue(parse_bool("", default=True))


class EnvironmentSettingHelperTests(SimpleTestCase):
    """Test the small environment readers used by Django settings."""

    def test_boolean_helpers_preserve_loose_parsing(self):
        """Required and optional booleans keep the existing conventions."""
        with mock.patch.dict(
            os.environ,
            {
                "ENABLED_SETTING": " yes please ",
                "DISABLED_SETTING": "disabled",
                "OPTIONAL_SETTING": "1",
            },
            clear=True,
        ):
            self.assertTrue(env_bool("ENABLED_SETTING"))
            self.assertFalse(env_bool("DISABLED_SETTING", default=True))
            self.assertTrue(env_bool("MISSING_SETTING", default=True))
            self.assertTrue(env_optional_bool("OPTIONAL_SETTING"))
            self.assertIsNone(env_optional_bool("MISSING_SETTING"))

    def test_integer_conversion_and_bounds(self):
        """Whole numbers support defaults, legacy absolute values, and bounds."""
        with mock.patch.dict(
            os.environ,
            {
                "COUNT": " 4 ",
                "SIGNED": "-3",
                "TOO_SMALL": "0",
                "TOO_LARGE": "11",
                "INVALID": "many",
            },
            clear=True,
        ):
            self.assertEqual(env_int("COUNT", 1, minimum=1, maximum=10), 4)
            self.assertEqual(env_int("SIGNED", 1, absolute=True), 3)
            self.assertEqual(env_int("MISSING", 7), 7)
            with self.assertRaisesRegex(ImproperlyConfigured, "TOO_SMALL must be at least 1"):
                env_int("TOO_SMALL", 1, minimum=1)
            with self.assertRaisesRegex(ImproperlyConfigured, "TOO_LARGE must not exceed 10"):
                env_int("TOO_LARGE", 1, maximum=10)
            with self.assertRaisesRegex(ImproperlyConfigured, "INVALID must be a whole number"):
                env_int("INVALID", 1)

    def test_choice_matching_and_errors(self):
        """Choices normalize exact values and documented first-letter forms."""
        with mock.patch.dict(
            os.environ,
            {
                "EXACT": " SIMPLE ",
                "SHORT": "s",
                "TYPO": "simpple",
                "INVALID": "unknown",
                "AMBIGUOUS": "a-value",
            },
            clear=True,
        ):
            choices = ("hash", "simple", "disabled")
            self.assertEqual(env_choice("EXACT", "hash", choices), "simple")
            self.assertEqual(env_choice("SHORT", "hash", choices, first_character=True), "simple")
            self.assertEqual(env_choice("TYPO", "hash", choices, first_character=True), "simple")
            self.assertEqual(env_choice("MISSING", "hash", choices), "hash")

            with self.assertRaisesRegex(ImproperlyConfigured, "INVALID must be one of"):
                env_choice("INVALID", "hash", choices)
            with self.assertRaisesRegex(ImproperlyConfigured, "INVALID must be one of"):
                env_choice("INVALID", "hash", choices, first_character=True)
            with self.assertRaisesRegex(ImproperlyConfigured, "AMBIGUOUS must be one of"):
                env_choice("AMBIGUOUS", "auto", ("auto", "active"), first_character=True)


class LogLevelParsingTests(SimpleTestCase):
    """Test shared notification log-level parsing."""

    def test_valid_levels(self):
        """Supported names map to their logging values."""
        self.assertEqual(parse_log_level(" info "), logging.INFO)
        self.assertEqual(parse_log_level("TRACE"), logging.DEBUG - 1)

    def test_invalid_levels_use_safe_fallbacks(self):
        """Invalid request and configured values fall back safely."""
        self.assertEqual(parse_log_level("invalid", "ERROR"), logging.ERROR)
        self.assertEqual(parse_log_level("invalid", "invalid"), logging.WARNING)


class LogLevelSettingsTests(SimpleTestCase):
    """Ensure the configured console level is always safe for Django."""

    def test_valid_levels_are_normalized(self):
        """Whitespace and custom TRACE values become known level names."""
        for value, expected in ((" error ", "ERROR"), ("TRACE", "TRACE")):
            with self.subTest(value=value):
                configured = _load_settings({"LOG_LEVEL": value})
                self.assertEqual(configured.APPRISE_LOG_LEVEL, expected)
                self.assertEqual(
                    configured.LOGGING["handlers"]["console"]["level"],
                    expected,
                )

    def test_invalid_level_uses_runtime_default(self):
        """A bad environment value cannot prevent application startup."""
        configured = _load_settings({"LOG_LEVEL": "not-a-level"})

        # Tests load normal production settings, where DEBUG is disabled.
        self.assertEqual(configured.APPRISE_LOG_LEVEL, "INFO")
        self.assertEqual(
            configured.LOGGING["handlers"]["console"]["level"],
            "INFO",
        )


class StreamSizeSettingsTests(SimpleTestCase):
    """Validate live-stream memory and disk size settings."""

    def test_defaults_and_overrides(self):
        """Stream sizes use MB environment values and documented defaults."""
        # Load once without overrides to verify the shipped allowances.
        defaults = _load_settings()
        self.assertEqual(defaults.APPRISE_STREAM_MEMORY_SIZE, 2 * 1048576)
        self.assertEqual(defaults.APPRISE_STREAM_DISK_SIZE, 256 * 1048576)
        self.assertEqual(defaults.APPRISE_STREAM_WORKER_COUNT, 4)

        # Environment values are whole megabytes and become bytes at startup.
        configured = _load_settings(
            {
                "APPRISE_STREAM_MEMORY_SIZE": "4",
                "APPRISE_STREAM_DISK_SIZE": "8",
                "APPRISE_STREAM_WORKER_COUNT": "6",
            }
        )
        self.assertEqual(configured.APPRISE_STREAM_MEMORY_SIZE, 4 * 1048576)
        self.assertEqual(configured.APPRISE_STREAM_DISK_SIZE, 8 * 1048576)
        self.assertEqual(configured.APPRISE_STREAM_WORKER_COUNT, 6)

    def test_zero_is_allowed(self):
        """Zero remains available for each documented fallback mode."""
        # Operators can explicitly disable either buffering layer.
        configured = _load_settings(
            {
                "APPRISE_STREAM_MEMORY_SIZE": "0",
                "APPRISE_STREAM_DISK_SIZE": "0",
            }
        )
        self.assertEqual(configured.APPRISE_STREAM_MEMORY_SIZE, 0)
        self.assertEqual(configured.APPRISE_STREAM_DISK_SIZE, 0)

    def test_invalid_values_fail_at_startup(self):
        """Negative, fractional, and non-numeric sizes are rejected."""
        # Apply every invalid shape to both supported size settings.
        for name in ("APPRISE_STREAM_MEMORY_SIZE", "APPRISE_STREAM_DISK_SIZE"):
            for value in ("-1", "1.5", "many"):
                with self.subTest(name=name, value=value), self.assertRaisesRegex(ImproperlyConfigured, name):
                    _load_settings({name: value})

        for value in ("0", "-1", "1.5", "many"):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(
                    ImproperlyConfigured,
                    "APPRISE_STREAM_WORKER_COUNT",
                ),
            ):
                _load_settings({"APPRISE_STREAM_WORKER_COUNT": value})


class ChoiceSettingsTests(SimpleTestCase):
    """Validate canonical and first-character mode settings."""

    def test_mode_defaults_and_first_character_forms(self):
        """Short forms and harmless spelling mistakes become canonical modes."""
        defaults = _load_settings()
        self.assertEqual(defaults.APPRISE_DEFAULT_THEME, "light")
        self.assertEqual(defaults.APPRISE_STORAGE_MODE, "auto")
        self.assertEqual(defaults.APPRISE_STATEFUL_MODE, "hash")

        configured = _load_settings(
            {
                "APPRISE_DEFAULT_THEME": "dakr",
                "APPRISE_STORAGE_MODE": "f",
                "APPRISE_STATEFUL_MODE": "simpple",
            }
        )
        self.assertEqual(configured.APPRISE_DEFAULT_THEME, "dark")
        self.assertEqual(configured.APPRISE_STORAGE_MODE, "flush")
        self.assertEqual(configured.APPRISE_STATEFUL_MODE, "simple")

    def test_invalid_modes_fail_with_a_clear_configuration_error(self):
        """Unknown first letters never silently select an operating mode."""
        for name in (
            "APPRISE_DEFAULT_THEME",
            "APPRISE_STORAGE_MODE",
            "APPRISE_STATEFUL_MODE",
        ):
            with self.subTest(name=name), self.assertRaisesRegex(ImproperlyConfigured, name):
                _load_settings({name: "unknown"})

    def test_stateless_mode_keeps_boolean_compatibility(self):
        """The boolean-style stateless mode still accepts legacy spellings."""
        for value in ("yes", "1", "true", "enable", "active", "+"):
            with self.subTest(value=value):
                self.assertEqual(_load_settings({"APPRISE_STATELESS_MODE": value}).APPRISE_STATELESS_MODE, "enabled")

        for value in ("no", "0", "false", "disabled"):
            with self.subTest(value=value):
                self.assertEqual(_load_settings({"APPRISE_STATELESS_MODE": value}).APPRISE_STATELESS_MODE, "disabled")


class NumericSettingsTests(SimpleTestCase):
    """Validate documented bounds on general numeric settings."""

    def test_storage_uid_length_uses_documented_bounds(self):
        """Storage identifiers accept lengths from 2 through 64."""
        self.assertEqual(_load_settings({"APPRISE_STORAGE_UID_LENGTH": "2"}).APPRISE_STORAGE_UID_LENGTH, 2)
        self.assertEqual(_load_settings({"APPRISE_STORAGE_UID_LENGTH": "64"}).APPRISE_STORAGE_UID_LENGTH, 64)

        for value in ("1", "65", "invalid"):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(
                    ImproperlyConfigured,
                    "APPRISE_STORAGE_UID_LENGTH",
                ),
            ):
                _load_settings({"APPRISE_STORAGE_UID_LENGTH": value})


class BaseUrlParsingTests(SimpleTestCase):
    """
    Test the BASE_URL environment variable parsing logic to ensure it correctly
    normalizes prefixes, strips trailing slashes, and falls back properly.
    """

    def _get_base_url(self, apprise_base=None, base=None):
        """
        Helper to simulate the exact logic found in settings/__init__.py
        """
        env = {}
        if apprise_base is not None:
            env["APPRISE_BASE_URL"] = apprise_base
        if base is not None:
            env["BASE_URL"] = base

        with mock.patch.dict(os.environ, env, clear=True):
            # Simulate the exact logic from settings/__init__.py
            _raw_base = os.environ.get("APPRISE_BASE_URL", os.environ.get("BASE_URL", "")).strip().strip("/")

            return f"/{_raw_base}" if _raw_base else ""

    def test_base_url_normalization(self):
        """
        Test that priority, fallback, and slash-stripping behave correctly
        """
        # 1. Prioritize APPRISE_BASE_URL over legacy BASE_URL
        self.assertEqual(self._get_base_url(apprise_base="/apprise", base="/wrong"), "/apprise")

        # 2. Fallback to BASE_URL if APPRISE_BASE_URL is not set
        self.assertEqual(self._get_base_url(apprise_base=None, base="/apprise"), "/apprise")

        # 3. Strip trailing/leading slashes and whitespace aggressively
        self.assertEqual(self._get_base_url(apprise_base="  /apprise/  "), "/apprise")
        self.assertEqual(self._get_base_url(apprise_base="apprise/"), "/apprise")
        self.assertEqual(self._get_base_url(apprise_base="/apprise"), "/apprise")
        self.assertEqual(self._get_base_url(apprise_base="apprise"), "/apprise")

        # 3b. Strip tabs and newlines (not just spaces)
        self.assertEqual(self._get_base_url(apprise_base="\t/apprise\n"), "/apprise")
        self.assertEqual(self._get_base_url(apprise_base="\n/apprise\n"), "/apprise")

        # 3c. Normalize multiple leading slashes to a single slash
        self.assertEqual(self._get_base_url(apprise_base="///apprise"), "/apprise")
        self.assertEqual(self._get_base_url(apprise_base="///apprise///"), "/apprise")

        # 4. Handle empty/root paths safely (must result in an empty string)
        self.assertEqual(self._get_base_url(apprise_base="/"), "")
        self.assertEqual(self._get_base_url(apprise_base="   "), "")
        self.assertEqual(self._get_base_url(apprise_base=None, base=None), "")


class TimezoneSettingsTests(SimpleTestCase):
    """
    Ensure TIME_ZONE follows the container TZ environment variable.
    """

    def test_time_zone_from_tz_env(self):
        """TIME_ZONE must equal TZ when the env variable is present."""
        mod = _load_settings({"TZ": "America/Toronto"})
        self.assertEqual(mod.TIME_ZONE, "America/Toronto")

    def test_time_zone_various_zones(self):
        """TIME_ZONE must follow TZ across a representative set of zones."""
        for tz in ("Europe/Madrid", "Asia/Tokyo", "America/New_York", "Etc/UTC"):
            mod = _load_settings({"TZ": tz})
            self.assertEqual(mod.TIME_ZONE, tz)

    def test_time_zone_default(self):
        """Without TZ, TIME_ZONE defaults to Etc/UTC"""
        mod = _load_settings()  # TZ intentionally absent from environment
        self.assertEqual(mod.TIME_ZONE, "Etc/UTC")
        self.assertNotEqual(
            mod.TIME_ZONE,
            global_settings.TIME_ZONE,
            "TIME_ZONE must not fall back to Django's built-in default "
            "({!r}). Define TIME_ZONE = os.environ.get('TZ', 'Etc/UTC') "
            "in core/settings/__init__.py.".format(global_settings.TIME_ZONE),
        )


class BasicAuthSettingsTests(SimpleTestCase):
    """Test the global Basic Auth environment settings."""

    def test_unset_by_default(self):
        """Authentication is off unless explicitly enabled."""
        with self.assertLogs(level="INFO") as cm:
            mod = _load_settings()
        self.assertFalse(mod.APPRISE_AUTH_REQUIRED)
        self.assertIsNone(mod.APPRISE_USER)
        self.assertIsNone(mod.APPRISE_PASSWORD)
        self.assertIsNone(mod.APPRISE_BASIC_AUTH_TOKEN)
        self.assertEqual(mod.APPRISE_BASIC_AUTH_REALM, "Apprise API")
        self.assertTrue(any("Authentication Mode: Disabled" in message for message in cm.output))

    def test_credentials_are_ignored_while_disabled(self):
        """Credentials alone do not turn authentication on."""
        mod = _load_settings({"APPRISE_USER": "alice", "APPRISE_PASSWORD": "secret"})
        self.assertFalse(mod.APPRISE_AUTH_REQUIRED)
        self.assertIsNone(mod.APPRISE_USER)
        self.assertIsNone(mod.APPRISE_PASSWORD)
        self.assertIsNone(mod.APPRISE_BASIC_AUTH_TOKEN)

    def test_custom_realm(self):
        """The login prompt label can identify a specific instance."""
        mod = _load_settings({"APPRISE_BASIC_AUTH_REALM": "Home Alerts"})
        self.assertEqual(mod.APPRISE_BASIC_AUTH_REALM, "Home Alerts")

    def test_web_secret_uses_its_own_default(self):
        """Browser signing has a default independent of Django's key."""
        mod = _load_settings({"APPRISE_AUTH_REQUIRED": "yes"})
        self.assertEqual(mod.APPRISE_WEB_AUTH_SECRET, mod.DEFAULT_WEB_AUTH_SECRET)
        self.assertEqual(mod.APPRISE_WEB_AUTH_MAX_AGE, 24 * 60 * 60)
        self.assertNotEqual(mod.APPRISE_WEB_AUTH_SECRET, mod.SECRET_KEY)

    def test_django_key_does_not_change_web_secret(self):
        """Changing Django's key does not change browser signing."""
        mod = _load_settings(
            {
                "APPRISE_AUTH_REQUIRED": "yes",
                "SECRET_KEY": "private-django-key",
            }
        )
        self.assertEqual(mod.APPRISE_WEB_AUTH_SECRET, mod.DEFAULT_WEB_AUTH_SECRET)

    def test_web_secret_is_independent(self):
        """A separate web secret does not replace the configuration hash salt."""
        mod = _load_settings(
            {
                "APPRISE_AUTH_REQUIRED": "yes",
                "SECRET_KEY": "configuration-key",
                "APPRISE_WEB_AUTH_SECRET": "browser-key",
            }
        )
        self.assertEqual(mod.SECRET_KEY, "configuration-key")
        self.assertEqual(mod.APPRISE_WEB_AUTH_SECRET, "browser-key")

    def test_both_set(self):
        """Both set: the token is base64("user:pass")."""
        with self.assertLogs(level="INFO") as cm:
            mod = _load_settings(
                {
                    "APPRISE_AUTH_REQUIRED": "yes",
                    "APPRISE_USER": "alice",
                    "APPRISE_PASSWORD": "secret",
                }
            )
        self.assertTrue(mod.APPRISE_AUTH_REQUIRED)
        self.assertEqual(mod.APPRISE_BASIC_AUTH_TOKEN, base64.b64encode(b"alice:secret").decode())
        self.assertTrue(any("Administration Account Enabled" in message for message in cm.output))

    def test_username_only_disables_auth(self):
        """A username without a password disables auth and logs a warning."""
        with self.assertLogs(level="WARNING") as cm:
            mod = _load_settings({"APPRISE_AUTH_REQUIRED": "yes", "APPRISE_USER": "alice"})
        self.assertTrue(mod.APPRISE_AUTH_REQUIRED)
        self.assertIsNone(mod.APPRISE_BASIC_AUTH_TOKEN)
        self.assertIsNone(mod.APPRISE_USER)
        self.assertTrue(any("APPRISE_PASSWORD" in message for message in cm.output))

    def test_password_only(self):
        """A password without a username is valid."""
        mod = _load_settings({"APPRISE_AUTH_REQUIRED": "yes", "APPRISE_PASSWORD": "secret"})
        self.assertEqual(mod.APPRISE_BASIC_AUTH_TOKEN, base64.b64encode(b":secret").decode())

    def test_colon_in_username_stops_startup(self):
        """A colon in the global username stops startup."""
        with self.assertRaises(ImproperlyConfigured):
            _load_settings(
                {
                    "APPRISE_AUTH_REQUIRED": "yes",
                    "APPRISE_USER": "ali:ce",
                    "APPRISE_PASSWORD": "secret",
                }
            )

    def test_blank_credentials_enable_users_only_mode(self):
        """Blank credentials leave the administrator account disabled."""
        with self.assertLogs(level="INFO") as cm:
            mod = _load_settings(
                {
                    "APPRISE_AUTH_REQUIRED": "yes",
                    "APPRISE_USER": "",
                    "APPRISE_PASSWORD": "",
                }
            )
        self.assertTrue(mod.APPRISE_AUTH_REQUIRED)
        self.assertIsNone(mod.APPRISE_BASIC_AUTH_TOKEN)
        self.assertTrue(any("Administration Account Disabled" in message for message in cm.output))

    def test_empty_username_without_password_uses_users_only_mode(self):
        """An empty username does not create a blank administrator."""
        mod = _load_settings({"APPRISE_AUTH_REQUIRED": "yes", "APPRISE_USER": ""})
        self.assertIsNone(mod.APPRISE_BASIC_AUTH_TOKEN)

    def test_password_only_with_empty_username_is_valid(self):
        """An empty username is valid when a password is supplied."""
        mod = _load_settings(
            {
                "APPRISE_AUTH_REQUIRED": "yes",
                "APPRISE_USER": "",
                "APPRISE_PASSWORD": "secret",
            }
        )
        self.assertEqual(mod.APPRISE_BASIC_AUTH_TOKEN, base64.b64encode(b":secret").decode())
