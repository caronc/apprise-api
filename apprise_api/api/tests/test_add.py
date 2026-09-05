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
import hashlib
import json
from unittest import mock
from unittest.mock import patch

from apprise import ConfigFormat
from django.core.exceptions import RequestDataTooBig
from django.test import SimpleTestCase
from django.test.utils import override_settings

from ..auth import Authentication
from ..forms import AUTO_DETECT_CONFIG_KEYWORD
from ..utils import CONFIG_KEY_MAX_LENGTH, ConfigCache


def _basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


_MASTER_TOKEN = base64.b64encode(b"master:pass").decode()
_GOOD_MASTER = {"authorization": _basic("master", "pass")}


class AddTests(SimpleTestCase):
    def test_add_invalid_key_status_code(self):
        """
        Test GET requests to invalid key
        """
        response = self.client.get("/add/**invalid-key**")
        assert response.status_code == 404

    def test_key_lengths(self):
        """
        Test our key lengths
        """

        # our key to use
        h = hashlib.sha512()
        h.update(b"string")
        key = h.hexdigest()

        # Our limit
        assert len(key) == CONFIG_KEY_MAX_LENGTH

        # Add our URL
        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200

        # However adding just 1 more character exceeds our limit and the save
        # will fail
        response = self.client.post(
            "/add/{}".format(key + "x"),
            {"urls": "mailto://user:pass@yahoo.ca"},
        )
        assert response.status_code == 404

    @override_settings(APPRISE_CONFIG_LOCK=True)
    def test_save_config_by_urls_with_lock(self):
        """
        Test adding a configuration by URLs with lock set won't work
        """
        # our key to use
        key = "test_save_config_by_urls_with_lock"

        # We simply do not have permission to do so
        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 403

    def test_save_config_by_urls(self):
        """
        Test adding an configuration by URLs
        """

        # our key to use
        key = "test_save_config_by_urls"

        # GET returns 405 (not allowed)
        response = self.client.get("/add/{}".format(key))
        assert response.status_code == 405

        # no data
        response = self.client.post("/add/{}".format(key))
        assert response.status_code == 400

        # No entries specified
        response = self.client.post("/add/{}".format(key), {"urls": ""})
        assert response.status_code == 400

        # Added successfully
        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200

        # No URLs loaded
        response = self.client.post(
            "/add/{}".format(key),
            {"config": "invalid content", "format": "text"},
        )
        assert response.status_code == 400

        # Test a case where we fail to load a valid configuration file
        with patch("apprise.AppriseConfig.add", return_value=False):
            response = self.client.post(
                "/add/{}".format(key),
                {"config": "garbage://", "format": "text"},
            )
        assert response.status_code == 400

        with patch("os.remove", side_effect=OSError):
            # We will fail to remove the device first prior to placing a new
            # one;  This will result in a 500 error
            response = self.client.post(
                "/add/{}".format(key),
                {"urls": "mailto://user:newpass@gmail.com"},
            )
            assert response.status_code == 500

        # URL is actually not a valid one (invalid Slack tokens specified
        # below)
        response = self.client.post("/add/{}".format(key), {"urls": "slack://-/-/-"})
        assert response.status_code == 400

        # Submit with an invalid config format choice — AddByConfigForm
        # fails validation (False branch of 'if form.is_valid()') while
        # AddByUrlForm still succeeds via the valid 'urls' field.
        response = self.client.post(
            "/add/{}".format(key),
            {"format": "invalid_format_xyz", "urls": "mailto://user:pass@yahoo.ca"},
        )
        assert response.status_code == 200

        # Test with JSON
        response = self.client.post(
            "/add/{}".format(key),
            data=json.dumps({"urls": "mailto://user:pass@yahoo.ca"}),
            content_type="application/json",
        )
        assert response.status_code == 200

        with mock.patch("json.loads") as mock_loads:
            mock_loads.side_effect = RequestDataTooBig()
            # Send our notification by specifying the tag in the parameters
            response = self.client.post(
                "/add/{}".format(key),
                data=json.dumps({"urls": "mailto://user:pass@yahoo.ca"}),
                content_type="application/json",
            )

            # Our notification failed
            assert response.status_code == 431

        # Test with JSON (and no payload provided)
        response = self.client.post(
            "/add/{}".format(key),
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == 400

        # Test with XML which simply isn't supported
        response = self.client.post(
            "/add/{}".format(key),
            data="<urls><url>mailto://user:pass@yahoo.ca</url></urls>",
            content_type="application/xml",
        )
        assert response.status_code == 400

        # Invalid JSON
        response = self.client.post(
            "/add/{}".format(key),
            data="{",
            content_type="application/json",
        )
        assert response.status_code == 400

        # JSON endpoints require an object at the root and reject malformed
        # field types without passing them into Apprise.
        for payload in ([], "urls", 42, True, None):
            response = self.client.post(
                "/add/{}".format(key),
                data=json.dumps(payload),
                content_type="application/json",
            )
            assert response.status_code == 400
            assert response.json()["error"] == "The JSON payload must be an object"

        malformed_fields = (
            ({"urls": {}}, "urls"),
            ({"urls": ["json://localhost", 42]}, "urls"),
            ({"config": []}, "config"),
            ({"format": None, "config": "json://localhost"}, "format"),
        )
        for payload, field in malformed_fields:
            response = self.client.post(
                "/add/{}".format(key),
                data=json.dumps(payload),
                content_type="application/json",
            )
            assert response.status_code == 400
            assert response.json()["field"] == field

        # Test the handling of underlining disk/write exceptions
        with patch("os.makedirs") as mock_mkdirs:
            mock_mkdirs.side_effect = OSError()
            # We'll fail to write our key now
            response = self.client.post(
                "/add/{}".format(key),
                data=json.dumps({"urls": "mailto://user:pass@yahoo.ca"}),
                content_type="application/json",
            )

            # internal errors are correctly identified
            assert response.status_code == 500

        # Test the handling of underlining disk/write exceptions
        with patch("gzip.open") as mock_open:
            mock_open.side_effect = OSError()
            # We'll fail to write our key now
            response = self.client.post(
                "/add/{}".format(key),
                data=json.dumps({"urls": "mailto://user:pass@yahoo.ca"}),
                content_type="application/json",
            )

            # internal errors are correctly identified
            assert response.status_code == 500

    def test_save_config_by_config(self):
        """
        Test adding an configuration by a config file
        """

        # our key to use
        key = "test_save_config_by_config"

        # Empty Text Configuration
        config = """

        """
        response = self.client.post(
            "/add/{}".format(key),
            {"format": ConfigFormat.TEXT.value, "config": config},
        )
        assert response.status_code == 400

        # Valid Text Configuration
        config = """
        browser,media=notica://VTokenC
        home=mailto://user:pass@hotmail.com
        """
        response = self.client.post(
            "/add/{}".format(key),
            {"format": ConfigFormat.TEXT.value, "config": config},
        )
        assert response.status_code == 200

        # Test with JSON
        response = self.client.post(
            "/add/{}".format(key),
            data=json.dumps({"format": ConfigFormat.TEXT.value, "config": config}),
            content_type="application/json",
        )
        assert response.status_code == 200

        # Valid Yaml Configuration
        config = """
        urls:
          - notica://VTokenD:
              tag: browser,media
          - mailto://user:pass@hotmail.com:
              tag: home
        """
        response = self.client.post(
            "/add/{}".format(key),
            {"format": ConfigFormat.YAML.value, "config": config},
        )
        assert response.status_code == 200

        # Test with JSON
        response = self.client.post(
            "/add/{}".format(key),
            data=json.dumps({"format": ConfigFormat.YAML.value, "config": config}),
            content_type="application/json",
        )
        assert response.status_code == 200

        # Test invalid config format
        response = self.client.post(
            "/add/{}".format(key),
            data=json.dumps({"format": "INVALID", "config": config}),
            content_type="application/json",
        )
        assert response.status_code == 400

        # Test the handling of underlining disk/write exceptions
        with patch("gzip.open") as mock_open:
            mock_open.side_effect = OSError()
            # We'll fail to write our key now
            response = self.client.post(
                "/add/{}".format(key),
                data=json.dumps({"format": ConfigFormat.YAML.value, "config": config}),
                content_type="application/json",
            )

            # internal errors are correctly identified
            assert response.status_code == 500

    def test_save_auto_detect_config_format(self):
        """
        Test adding an configuration and using the autodetect feature
        """

        # our key to use
        key = "test_save_auto_detect_config_format"

        # Empty Text Configuration
        config = """

        """
        response = self.client.post(
            "/add/{}".format(key),
            {"format": AUTO_DETECT_CONFIG_KEYWORD, "config": config},
        )
        assert response.status_code == 400

        # Valid Text Configuration
        config = """
        browser,media=notica://VTokenA
        home=mailto://user:pass@hotmail.com
        """
        response = self.client.post(
            "/add/{}".format(key),
            {"format": AUTO_DETECT_CONFIG_KEYWORD, "config": config},
        )
        assert response.status_code == 200

        # Test with JSON
        response = self.client.post(
            "/add/{}".format(key),
            data=json.dumps({"format": ConfigFormat.TEXT.value, "config": config}),
            content_type="application/json",
        )
        assert response.status_code == 200

        # Valid Yaml Configuration
        config = """
        urls:
          - notica://VTokenB:
              tag: browser,media

          - mailto://user:pass@hotmail.com:
              tag: home
        """
        response = self.client.post(
            "/add/{}".format(key),
            {"format": AUTO_DETECT_CONFIG_KEYWORD, "config": config},
        )
        assert response.status_code == 200

        # Test with JSON
        response = self.client.post(
            "/add/{}".format(key),
            data=json.dumps({"format": AUTO_DETECT_CONFIG_KEYWORD, "config": config}),
            content_type="application/json",
        )
        assert response.status_code == 200

        # Test invalid config format that can not be auto-detected
        config = """
        42
        """
        response = self.client.post(
            "/add/{}".format(key),
            {"format": AUTO_DETECT_CONFIG_KEYWORD, "config": config},
        )
        assert response.status_code == 400

        # Test with JSON
        response = self.client.post(
            "/add/{}".format(key),
            data=json.dumps({"format": AUTO_DETECT_CONFIG_KEYWORD, "config": config}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_save_with_bad_input(self):
        """
        Test adding with bad input in general
        """

        # our key to use
        key = "test_save_with_bad_input"
        # Test with JSON
        response = self.client.post(
            "/add/{}".format(key),
            data=json.dumps({"garbage": "input"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    @override_settings(APPRISE_CONFIG_MAX_LENGTH=10)
    def test_config_max_length_enforced_json(self):
        """
        Test that JSON config submissions exceeding APPRISE_CONFIG_MAX_LENGTH are rejected
        """
        key = "test_config_max_length_enforced_json"

        # A config that exceeds the 10-byte limit is rejected with 400
        oversized_config = "x" * 11
        response = self.client.post(
            "/add/{}".format(key),
            data=json.dumps({"format": ConfigFormat.TEXT.value, "config": oversized_config}),
            content_type="application/json",
        )
        assert response.status_code == 400

        # A config at exactly the limit (10 bytes) is not rejected by the length check
        # (it may still fail for other reasons such as no valid URLs)
        at_limit_config = "x" * 10
        response = self.client.post(
            "/add/{}".format(key),
            data=json.dumps({"format": ConfigFormat.TEXT.value, "config": at_limit_config}),
            content_type="application/json",
        )
        # Length check passes; config is invalid apprise content so still 400, not 431
        assert response.status_code == 400

    def test_config_max_length_enforced_form(self):
        """
        Test that form config submissions exceeding APPRISE_CONFIG_MAX_LENGTH are rejected.
        The AddByConfigForm enforces max_length at the Django form-validation level.
        """
        from django.conf import settings

        key = "test_config_max_length_enforced_form"

        # A config one byte over the limit causes form validation to fail, so
        # the content dict stays empty and the view returns 400.
        oversized_config = "x" * (settings.APPRISE_CONFIG_MAX_LENGTH + 1)
        response = self.client.post(
            "/add/{}".format(key),
            {"format": ConfigFormat.TEXT.value, "config": oversized_config},
        )
        assert response.status_code == 400

        # A config within the limit passes form validation (though the content
        # itself is not valid apprise config, so the save still returns 400).
        within_limit_config = "x" * 10
        response = self.client.post(
            "/add/{}".format(key),
            {"format": ConfigFormat.TEXT.value, "config": within_limit_config},
        )
        # Passes the length check; invalid apprise content → 400 (not 413/431)
        assert response.status_code == 400

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_add_user_can_replace_own_key_by_url_or_header(self):
        """A configuration user controls the content assigned to their Config ID."""
        key = "test_add_user_owned"
        assert (
            self.client.post(
                "/add/{}".format(key),
                {"urls": "mailto://user:pass@yahoo.ca"},
                headers=_GOOD_MASTER,
            ).status_code
            == 200
        )
        assert ConfigCache.set_auth(key, "alice", "secret", access=Authentication.ACCESS_USER) is True
        user_creds = {"authorization": _basic("alice", "secret")}

        by_url = self.client.post(
            "/add/{}".format(key),
            {"urls": "mailto://other:pass@yahoo.ca"},
            headers={**user_creds, "accept": "application/json"},
        )
        assert by_url.status_code == 200
        assert by_url.json() == {"error": None}
        assert "other:pass@yahoo.ca" in ConfigCache.get(key)[0]

        by_header = self.client.post(
            "/add/",
            {"urls": "mailto://header:pass@yahoo.ca"},
            headers={**user_creds, "X-Apprise-Config-ID": key, "accept": "application/json"},
        )
        assert by_header.status_code == 200
        assert by_header.json() == {"error": None}
        assert "header:pass@yahoo.ca" in ConfigCache.get(key)[0]
        assert ConfigCache.verify_auth(key, "alice", "secret") is True

        ConfigCache.clear(key)
        ConfigCache.clear_auth(key)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_add_user_cannot_write_other_locked_keys(self):
        """Successful authentication never broadens one user's Config ID scope."""
        owned = "test_add_user_scope"
        other = "test_add_other_scope"
        locked = "test_add_locked_scope"
        public = "test_add_public_scope"
        disabled = "test_add_disabled_scope"
        keys = (owned, other, locked, public, disabled)
        for key in keys:
            assert ConfigCache.put(key, "json://localhost", ConfigFormat.TEXT.value) is True

        assert ConfigCache.set_auth(owned, "alice", "secret", access=Authentication.ACCESS_USER) is True
        assert ConfigCache.set_auth(other, "bob", "different", access=Authentication.ACCESS_USER) is True
        assert ConfigCache.set_auth(locked, "alice", "secret", access=Authentication.ACCESS_LOCK) is True
        assert ConfigCache.set_access(public, Authentication.ACCESS_PUBLIC) is True
        assert ConfigCache.set_auth(disabled, "alice", "secret", access=Authentication.ACCESS_DISABLED) is True
        user_headers = {"authorization": _basic("alice", "secret"), "accept": "application/json"}
        payload = {"urls": "mailto://changed:pass@yahoo.ca"}

        mismatched = self.client.post("/add/{}".format(other), payload, headers=user_headers)
        locked_response = self.client.post("/add/{}".format(locked), payload, headers=user_headers)
        public_response = self.client.post(
            "/add/{}".format(public),
            payload,
            headers={"accept": "application/json"},
        )
        disabled_response = self.client.post("/add/{}".format(disabled), payload, headers=user_headers)

        assert mismatched.status_code == 401
        assert locked_response.status_code == 403
        assert public_response.status_code == 401
        assert disabled_response.status_code == 403
        assert all(ConfigCache.get(key)[0] == "json://localhost" for key in (other, locked, public, disabled))

        for key in keys:
            ConfigCache.clear(key)
            ConfigCache.clear_auth(key)

    @override_settings(
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_CONFIG_LOCK=True,
    )
    def test_add_global_lock_still_denies_an_assigned_user(self):
        """The site-wide lock remains stronger than a saved user policy."""
        key = "test_add_globally_locked_user"
        assert ConfigCache.put(key, "json://localhost", ConfigFormat.TEXT.value) is True
        assert ConfigCache.set_auth(key, "alice", "secret", access=Authentication.ACCESS_USER) is True

        response = self.client.post(
            "/add/{}".format(key),
            {"urls": "mailto://changed:pass@yahoo.ca"},
            headers={"authorization": _basic("alice", "secret"), "accept": "application/json"},
        )

        assert response.status_code == 403
        assert ConfigCache.get(key)[0] == "json://localhost"
        ConfigCache.clear(key)
        ConfigCache.clear_auth(key)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_add_admin_can_overwrite_any_key(self):
        """An administrator may create or replace any Config ID."""
        key = "test_add_admin"
        response = self.client.post(
            "/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"}, headers=_GOOD_MASTER
        )
        assert response.status_code == 200
        assert ConfigCache.set_auth(key, "alice", "secret") is True

        response = self.client.post(
            "/add/{}".format(key), {"urls": "mailto://other:pass@yahoo.ca"}, headers=_GOOD_MASTER
        )
        assert response.status_code == 200

        ConfigCache.clear(key)
        ConfigCache.clear_auth(key)

    def test_add_works_when_no_auth_is_configured(self):
        """Open mode keeps its original unrestricted behavior."""
        key = "test_add_no_auth"
        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200
