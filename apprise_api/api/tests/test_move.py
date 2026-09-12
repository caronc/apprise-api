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
from json import dumps
import os
from unittest import mock
from unittest.mock import patch

from django.core.exceptions import RequestDataTooBig
from django.test import SimpleTestCase
from django.test.utils import override_settings

from ..utils import ConfigCache


def _basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


_MASTER_TOKEN = base64.b64encode(b"master:pass").decode()
_GOOD_MASTER = {"authorization": _basic("master", "pass")}


@override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
class MoveTests(SimpleTestCase):
    """Test the /move endpoint: relocating a configuration from one key to another."""

    def tearDown(self):
        # These tests write real files under APPRISE_CONFIG_DIR; clean up
        # after each so keys never leak state into a later test.
        for key in (
            "move_src",
            "move_dst",
            "move_missing",
            "move_conflict_src",
            "move_conflict_dst",
            "move_lock_src",
            "move_lock_dst",
            "move_copy_src",
            "move_copy_dst",
            "move_copyfail_src",
            "move_copyfail_dst",
            "move_hc_src",
            "move_hc_dst",
            "move_user_src",
            "move_user_dst",
            "move_user_other",
            "move_admin_src",
            "move_admin_dst",
            "move_same",
            "move_reused_src",
            "move_reused_dst",
            "move_formsame_src",
            "move_lockonly_src",
            "move_lockonly_dst",
        ):
            ConfigCache.clear(key)
            ConfigCache.clear_auth(key)

    def _seed(self, key, url="mailto://user:pass@yahoo.ca"):
        response = self.client.post("/add/{}".format(key), {"urls": url}, headers=_GOOD_MASTER)
        assert response.status_code == 200

    def _exists(self, key):
        response = self.client.post("/get/{}".format(key), headers=_GOOD_MASTER)
        return response.status_code == 200

    @override_settings(APPRISE_AUTH_REQUIRED=False)
    def test_move_get_invalid_key_status_code(self):
        """An unresolvable key never reaches the view at all."""
        response = self.client.post("/move/**invalid-key**")
        assert response.status_code == 404

    def test_move_basic_rename(self):
        """A same-filesystem move relocates the configuration and clears the source."""
        self._seed("move_src")
        response = self.client.post(
            "/move/move_src",
            data=dumps({"to": "move_dst"}),
            content_type="application/json",
            headers={**_GOOD_MASTER, "accept": "application/json"},
        )
        assert response.status_code == 200
        assert self._exists("move_dst")
        assert not self._exists("move_src")

    def test_move_no_configuration_to_move(self):
        """Moving a key with nothing stored reports 404, not a false success."""
        response = self.client.post(
            "/move/move_missing",
            data=dumps({"to": "move_dst"}),
            content_type="application/json",
            headers={**_GOOD_MASTER, "accept": "application/json"},
        )
        assert response.status_code == 404
        assert not self._exists("move_dst")

    def test_move_conflict_when_destination_already_has_content(self):
        """Moving onto an already-occupied destination is rejected, not silently overwritten."""
        self._seed("move_conflict_src")
        self._seed("move_conflict_dst", url="mailto://other@yahoo.ca")
        response = self.client.post(
            "/move/move_conflict_src",
            data=dumps({"to": "move_conflict_dst"}),
            content_type="application/json",
            headers={**_GOOD_MASTER, "accept": "application/json"},
        )
        assert response.status_code == 409
        # Neither side was touched by the rejected move.
        assert self._exists("move_conflict_src")
        assert self._exists("move_conflict_dst")

    def test_move_destination_reusable_after_full_delete(self):
        """A deleted Config ID can be reused as a move destination."""
        self._seed("move_reused_dst")
        assert ConfigCache.set_auth("move_reused_dst", "alice", "secret") is True

        response = self.client.post("/del/move_reused_dst", headers=_GOOD_MASTER)
        assert response.status_code == 200
        assert not self._exists("move_reused_dst")
        assert ConfigCache.get_auth("move_reused_dst") is None

        self._seed("move_reused_src", url="mailto://other@yahoo.ca")
        response = self.client.post(
            "/move/move_reused_src",
            data=dumps({"to": "move_reused_dst"}),
            content_type="application/json",
            headers={**_GOOD_MASTER, "accept": "application/json"},
        )
        assert response.status_code == 200
        assert self._exists("move_reused_dst")

    def test_move_to_must_differ_from_source(self):
        """Moving a key onto itself is a no-op request, rejected up front."""
        self._seed("move_same")
        response = self.client.post(
            "/move/move_same",
            data=dumps({"to": "move_same"}),
            content_type="application/json",
            headers={**_GOOD_MASTER, "accept": "application/json"},
        )
        assert response.status_code == 400
        assert self._exists("move_same")

    def test_move_form_rejects_a_destination_equal_to_the_source(self):
        """The HTML form's own same-source-destination check fires too, not just the JSON payload's."""
        self._seed("move_formsame_src")
        response = self.client.post(
            "/move/move_formsame_src",
            data={"from": "move_formsame_src", "to": "move_formsame_src"},
            headers=_GOOD_MASTER,
        )
        assert response.status_code == 400
        assert self._exists("move_formsame_src")

    def test_move_invalid_to_rejected(self):
        """A destination key that doesn't match the accepted key format is rejected."""
        self._seed("move_src")
        response = self.client.post(
            "/move/move_src",
            data=dumps({"to": "not a valid key!"}),
            content_type="application/json",
            headers={**_GOOD_MASTER, "accept": "application/json"},
        )
        assert response.status_code == 400
        assert self._exists("move_src")

    def test_move_requires_to(self):
        """The JSON payload must name its destination with ``to``."""
        self._seed("move_src")
        response = self.client.post(
            "/move/move_src",
            data=dumps({}),
            content_type="application/json",
            headers={**_GOOD_MASTER, "accept": "application/json"},
        )
        assert response.status_code == 400
        assert response.json()["field"] == "to"
        assert self._exists("move_src")

    @override_settings(APPRISE_CONFIG_LOCK=True)
    def test_move_with_lock(self):
        """An authenticated administrator may move entries under CONFIG_LOCK."""
        with override_settings(APPRISE_CONFIG_LOCK=False):
            self._seed("move_src")

        response = self.client.post(
            "/move/move_src",
            data=dumps({"to": "move_dst"}),
            content_type="application/json",
            headers={**_GOOD_MASTER, "accept": "application/json"},
        )
        assert response.status_code == 200
        with override_settings(APPRISE_CONFIG_LOCK=False):
            assert self._exists("move_dst")

    @override_settings(APPRISE_CONFIG_LOCK=True, APPRISE_AUTH_REQUIRED=False)
    def test_open_locked_site_denies_move(self):
        """CONFIG_LOCK remains private when authentication is disabled."""
        response = self.client.post(
            "/move/move_src",
            data=dumps({"to": "move_dst"}),
            content_type="application/json",
            headers={"accept": "application/json"},
        )
        assert response.status_code == 403

    @override_settings(APPRISE_CONFIG_LOCK=True)
    def test_locked_shared_user_cannot_move(self):
        """A configuration login cannot reorganize locked storage."""
        with override_settings(APPRISE_CONFIG_LOCK=False):
            self._seed("move_user_src")
        assert ConfigCache.set_auth("move_user_src", "alice", "secret") is True

        response = self.client.post(
            "/move/move_user_src",
            data=dumps({"to": "move_user_dst"}),
            content_type="application/json",
            headers={"authorization": _basic("alice", "secret"), "accept": "application/json"},
        )
        assert response.status_code == 403

    def test_move_carries_the_authentication_lock(self):
        """The destination inherits the source key's own lock; the source's is gone."""
        self._seed("move_lock_src")
        assert ConfigCache.set_auth("move_lock_src", "alice", "secret") is True

        response = self.client.post(
            "/move/move_lock_src",
            data=dumps({"to": "move_lock_dst"}),
            content_type="application/json",
            headers={**_GOOD_MASTER, "accept": "application/json"},
        )
        assert response.status_code == 200
        assert ConfigCache.verify_auth("move_lock_dst", "alice", "secret") is True
        assert ConfigCache.get_auth("move_lock_src") is None

    def test_move_lock_only_source_succeeds(self):
        """A key with an assigned login but no saved configuration is listed via /cfg/ (a
        lock-only entry, see AppriseConfigCache.keys()), so moving it must not report the
        same 404 as a genuinely nonexistent key -- it relocates the lock alone."""
        assert ConfigCache.set_auth("move_lockonly_src", "alice", "secret") is True

        response = self.client.post(
            "/move/move_lockonly_src",
            data=dumps({"to": "move_lockonly_dst"}),
            content_type="application/json",
            headers={**_GOOD_MASTER, "accept": "application/json"},
        )
        assert response.status_code == 200
        assert ConfigCache.verify_auth("move_lockonly_dst", "alice", "secret") is True
        assert ConfigCache.get_auth("move_lockonly_src") is None
        assert not self._exists("move_lockonly_dst")

    def test_move_falls_back_to_a_locked_copy_when_link_fails(self):
        """A hard-link failure still completes the move through a guarded copy."""
        self._seed("move_copy_src")
        real_link = os.link
        calls = 0

        def first_link_fails(src, dst):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("cross-device link")
            return real_link(src, dst)

        with patch("os.link", side_effect=first_link_fails):
            response = self.client.post(
                "/move/move_copy_src",
                data=dumps({"to": "move_copy_dst"}),
                content_type="application/json",
                headers={**_GOOD_MASTER, "accept": "application/json"},
            )
        assert response.status_code == 200
        assert self._exists("move_copy_dst")
        assert not self._exists("move_copy_src")

    def test_move_copy_failure_preserves_source(self):
        """When both link and copy fail, nothing is lost and 500 is reported."""
        self._seed("move_copyfail_src")
        with (
            patch("os.link", side_effect=OSError("cross-device link")),
            patch("shutil.copy2", side_effect=OSError("disk full")),
        ):
            response = self.client.post(
                "/move/move_copyfail_src",
                data=dumps({"to": "move_copyfail_dst"}),
                content_type="application/json",
                headers={**_GOOD_MASTER, "accept": "application/json"},
            )
        assert response.status_code == 500
        assert self._exists("move_copyfail_src")
        assert not self._exists("move_copyfail_dst")

    def test_move_fails_when_config_store_is_read_only(self):
        """A read-only filesystem is caught before any file operation is attempted."""
        self._seed("move_hc_src")
        with patch("api.views.healthcheck", return_value={"can_write_config": False, "details": []}):
            response = self.client.post(
                "/move/move_hc_src",
                data=dumps({"to": "move_hc_dst"}),
                content_type="application/json",
                headers={**_GOOD_MASTER, "accept": "application/json"},
            )
        assert response.status_code == 424
        assert self._exists("move_hc_src")
        assert not self._exists("move_hc_dst")

    def test_move_restricted_user_can_move_only_their_own_key(self):
        """A per-key (shared) credential may move the one key it authenticates against."""
        self._seed("move_user_src")
        assert ConfigCache.set_auth("move_user_src", "alice", "secret") is True
        user_creds = {"authorization": _basic("alice", "secret")}

        # JSON always uses the URL or header key as its source.
        response = self.client.post(
            "/move/move_user_src",
            data=dumps({"to": "move_user_dst"}),
            content_type="application/json",
            headers={**user_creds, "accept": "application/json"},
        )
        assert response.status_code == 200
        assert self._exists("move_user_dst")

    def test_shared_user_cannot_override_form_source(self):
        """The HTML form's from is read-only for a restricted user, enforced server-side too."""
        self._seed("move_user_src")
        self._seed("move_user_other")
        assert ConfigCache.set_auth("move_user_src", "alice", "secret") is True
        user_creds = {"authorization": _basic("alice", "secret")}

        # The submitted source must match the key used to authenticate.
        response = self.client.post(
            "/move/move_user_src",
            data={"from": "move_user_other", "to": "move_user_dst"},
            headers=user_creds,
        )
        assert response.status_code in (400, 401, 403)
        assert self._exists("move_user_other")
        assert not self._exists("move_user_dst")

    def test_move_admin_can_move_any_key_regardless_of_ownership(self):
        """The global administrator is never restricted to a single key."""
        self._seed("move_admin_src")
        response = self.client.post(
            "/move/move_admin_src",
            data=dumps({"to": "move_admin_dst"}),
            content_type="application/json",
            headers={**_GOOD_MASTER, "accept": "application/json"},
        )
        assert response.status_code == 200
        assert self._exists("move_admin_dst")

    def test_admin_can_override_form_source(self):
        """An administrator's form may target any key, not just the one in the URL."""
        self._seed("move_admin_src")
        response = self.client.post(
            "/move/some_other_key_in_the_url",
            data={"from": "move_admin_src", "to": "move_admin_dst"},
            headers=_GOOD_MASTER,
        )
        assert response.status_code == 200
        assert self._exists("move_admin_dst")
        assert not self._exists("move_admin_src")

    def test_move_missing_key_is_rejected(self):
        """No URL key and no X-Apprise-Config-ID header is a plain bad request."""
        response = self.client.post(
            "/move/",
            data=dumps({"to": "move_dst"}),
            content_type="application/json",
            headers={**_GOOD_MASTER, "accept": "application/json"},
        )
        assert response.status_code == 400

    def test_move_invalid_header_key_is_rejected(self):
        """An X-Apprise-Config-ID header that fails the key pattern is rejected outright."""
        response = self.client.post(
            "/move/",
            data=dumps({"to": "move_dst"}),
            content_type="application/json",
            headers={**_GOOD_MASTER, "accept": "application/json", "X-Apprise-Config-ID": "not a valid key!!"},
        )
        assert response.status_code == 400

    def test_move_denies_restricted_user_for_other_key(self):
        """A per-key credential is rejected outright for a URL key it doesn't own."""
        self._seed("move_user_src")
        self._seed("move_user_other")
        assert ConfigCache.set_auth("move_user_src", "alice", "secret") is True
        user_creds = {"authorization": _basic("alice", "secret")}

        response = self.client.post(
            "/move/move_user_other",
            data=dumps({"to": "move_user_dst"}),
            content_type="application/json",
            headers={**user_creds, "accept": "application/json"},
        )
        assert response.status_code == 401
        assert self._exists("move_user_other")

    def test_move_form_redirect_denied_when_key_auth_fails(self):
        """A source override still requires access to that Config ID, even
        after the caller already cleared the URL key's own check."""
        self._seed("move_admin_src")
        with mock.patch(
            "api.auth.Authentication.key_ok",
            side_effect=lambda request, key: key != "move_admin_src",
        ):
            response = self.client.post(
                "/move/some_other_key_in_the_url",
                data={"from": "move_admin_src", "to": "move_admin_dst"},
                headers=_GOOD_MASTER,
            )
        assert response.status_code == 401
        assert self._exists("move_admin_src")
        assert not self._exists("move_admin_dst")

    def test_move_json_payload_too_large(self):
        """A JSON body exceeding APPRISE_UPLOAD_MAX_MEMORY_SIZE is reported, not crashed."""
        self._seed("move_src")
        with mock.patch("json.loads", side_effect=RequestDataTooBig()):
            response = self.client.post(
                "/move/move_src",
                data=dumps({"to": "move_dst"}),
                content_type="application/json",
                headers={**_GOOD_MASTER, "accept": "application/json"},
            )
        assert response.status_code == 431
        assert self._exists("move_src")

    def test_move_invalid_json_payload(self):
        """A body that isn't valid JSON is rejected, not crashed."""
        self._seed("move_src")
        response = self.client.post(
            "/move/move_src",
            data="{not valid json",
            content_type="application/json",
            headers={**_GOOD_MASTER, "accept": "application/json"},
        )
        assert response.status_code == 400
        assert self._exists("move_src")

    def test_move_json_payload_must_be_an_object(self):
        """A JSON body that parses but isn't an object is rejected."""
        self._seed("move_src")
        response = self.client.post(
            "/move/move_src",
            data=dumps(["move_dst"]),
            content_type="application/json",
            headers={**_GOOD_MASTER, "accept": "application/json"},
        )
        assert response.status_code == 400
        assert self._exists("move_src")
