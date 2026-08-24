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
from unittest.mock import patch

from django.test import SimpleTestCase
from django.test.utils import override_settings

from ..utils import CONFIG_KEY_MAX_LENGTH, ConfigCache


def _basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


_MASTER_TOKEN = base64.b64encode(b"master:pass").decode()
_GOOD_MASTER = {"authorization": _basic("master", "pass")}


class DelTests(SimpleTestCase):
    def test_del_get_invalid_key_status_code(self):
        """
        Test GET requests to invalid key
        """
        response = self.client.get("/del/**invalid-key**")
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

        # remove a key that is too long
        response = self.client.post("/del/{}".format(key + "x"))
        assert response.status_code == 404

        # remove the key
        response = self.client.post("/del/{}".format(key))
        assert response.status_code == 200

        # Test again; key is gone
        response = self.client.post("/del/{}".format(key))
        assert response.status_code == 204

    @override_settings(APPRISE_CONFIG_LOCK=True)
    def test_del_with_lock(self):
        """
        Test deleting a configuration by URLs with lock set won't work
        """
        # our key to use
        key = "test_delete_with_lock"

        # We simply do not have permission to do so
        response = self.client.post("/del/{}".format(key))
        assert response.status_code == 403

    @override_settings(
        APPRISE_CONFIG_LOCK=True,
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
    )
    def test_locked_admin_can_delete(self):
        """An authenticated administrator may delete a locked entry."""
        key = "test_delete_locked_admin"
        with override_settings(APPRISE_CONFIG_LOCK=False):
            response = self.client.post(
                "/add/{}".format(key),
                {"urls": "mailto://user:pass@yahoo.ca"},
                headers=_GOOD_MASTER,
            )
        assert response.status_code == 200

        response = self.client.post("/del/{}".format(key), headers=_GOOD_MASTER)
        assert response.status_code == 200

    def test_del_post(self):
        """
        Test DEL POST
        """
        # our key to use
        key = "test_delete"

        # Invalid Key
        response = self.client.post("/del/**invalid-key**")
        assert response.status_code == 404

        # A key that just simply isn't present
        response = self.client.post("/del/{}".format(key))
        assert response.status_code == 204

        # Add our key
        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200

        # Test removing key when the OS just can't do it:
        with patch("os.remove", side_effect=OSError):
            # We can now remove the key
            response = self.client.post("/del/{}".format(key))
            assert response.status_code == 500

        # We can now remove the key
        response = self.client.post("/del/{}".format(key))
        assert response.status_code == 200

        # Key has already been removed
        response = self.client.post("/del/{}".format(key))
        assert response.status_code == 204

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_del_restricted_user_denied_for_own_key(self):
        """A configuration user may move their key but cannot delete it."""
        key = "test_delete_restricted_user"
        response = self.client.post(
            "/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"}, headers=_GOOD_MASTER
        )
        assert response.status_code == 200
        assert ConfigCache.set_auth(key, "alice", "secret") is True
        user_creds = {"authorization": _basic("alice", "secret")}

        response = self.client.post("/del/{}".format(key), headers={**user_creds, "accept": "application/json"})
        assert response.status_code == 403
        # Keep error responses consistent with the other API endpoints.
        assert set(response.json()) == {"error"}

        # The configuration is still there afterward.
        response = self.client.post("/get/{}".format(key), headers=user_creds)
        assert response.status_code == 200

        ConfigCache.clear(key)
        ConfigCache.clear_auth(key)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_del_admin_can_delete_any_key(self):
        """The global administrator may still delete any key, restricted-user lock or not."""
        key = "test_delete_admin"
        response = self.client.post(
            "/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"}, headers=_GOOD_MASTER
        )
        assert response.status_code == 200
        assert ConfigCache.set_auth(key, "alice", "secret") is True

        response = self.client.post("/del/{}".format(key), headers=_GOOD_MASTER)
        assert response.status_code == 200

        ConfigCache.clear(key)
        ConfigCache.clear_auth(key)

    def test_del_works_when_no_auth_is_configured(self):
        """No authentication configured at all is treated the same as an administrator, not a restricted user."""
        key = "test_delete_no_auth"
        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200

        response = self.client.post("/del/{}".format(key))
        assert response.status_code == 200
