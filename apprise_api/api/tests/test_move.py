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
            data=dumps({"to_config_id": "move_dst"}),
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
            data=dumps({"to_config_id": "move_dst"}),
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
            data=dumps({"to_config_id": "move_conflict_dst"}),
            content_type="application/json",
            headers={**_GOOD_MASTER, "accept": "application/json"},
        )
        assert response.status_code == 409
        # Neither side was touched by the rejected move.
        assert self._exists("move_conflict_src")
        assert self._exists("move_conflict_dst")

    def test_move_destination_reusable_after_full_delete(self):
        """A destination cleared by a full DEL is available again, not a phantom conflict.

        DelView removes both the configuration and its authentication lock,
        so a key that was fully deleted must not trip the lock-file check in
        AppriseConfigCache.move() for a later, unrelated move onto that ID.
        """
        self._seed("move_reused_dst")
        assert ConfigCache.set_auth("move_reused_dst", "alice", "secret") is True

        response = self.client.post("/del/move_reused_dst", headers=_GOOD_MASTER)
        assert response.status_code == 200
        assert not self._exists("move_reused_dst")
        assert ConfigCache.get_auth("move_reused_dst") is None

        self._seed("move_reused_src", url="mailto://other@yahoo.ca")
        response = self.client.post(
            "/move/move_reused_src",
            data=dumps({"to_config_id": "move_reused_dst"}),
            content_type="application/json",
            headers={**_GOOD_MASTER, "accept": "application/json"},
        )
        assert response.status_code == 200
        assert self._exists("move_reused_dst")

    def test_move_to_config_id_must_differ_from_source(self):
        """Moving a key onto itself is a no-op request, rejected up front."""
        self._seed("move_same")
        response = self.client.post(
            "/move/move_same",
            data=dumps({"to_config_id": "move_same"}),
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
            data={"from_config_id": "move_formsame_src", "to_config_id": "move_formsame_src"},
            headers=_GOOD_MASTER,
        )
        assert response.status_code == 400
        assert self._exists("move_formsame_src")

    def test_move_invalid_to_config_id_rejected(self):
        """A destination key that doesn't match the accepted key format is rejected."""
        self._seed("move_src")
        response = self.client.post(
            "/move/move_src",
            data=dumps({"to_config_id": "not a valid key!"}),
            content_type="application/json",
            headers={**_GOOD_MASTER, "accept": "application/json"},
        )
        assert response.status_code == 400
        assert self._exists("move_src")

    @override_settings(APPRISE_CONFIG_LOCK=True)
    def test_move_with_lock(self):
        """A locked site refuses every move, mirroring /add and /del."""
        response = self.client.post(
            "/move/move_src",
            data=dumps({"to_config_id": "move_dst"}),
            content_type="application/json",
            headers={**_GOOD_MASTER, "accept": "application/json"},
        )
        assert response.status_code == 403

    def test_move_carries_the_authentication_lock(self):
        """The destination inherits the source key's own lock; the source's is gone."""
        self._seed("move_lock_src")
        assert ConfigCache.set_auth("move_lock_src", "alice", "secret") is True

        response = self.client.post(
            "/move/move_lock_src",
            data=dumps({"to_config_id": "move_lock_dst"}),
            content_type="application/json",
            headers={**_GOOD_MASTER, "accept": "application/json"},
        )
        assert response.status_code == 200
        assert ConfigCache.verify_auth("move_lock_dst", "alice", "secret") is True
        assert ConfigCache.get_auth("move_lock_src") is None

    def test_move_falls_back_to_a_locked_copy_when_rename_fails(self):
        """A rename failure (e.g. a filesystem boundary) still completes the move via copy."""
        self._seed("move_copy_src")
        with patch("os.rename", side_effect=OSError("cross-device link")):
            response = self.client.post(
                "/move/move_copy_src",
                data=dumps({"to_config_id": "move_copy_dst"}),
                content_type="application/json",
                headers={**_GOOD_MASTER, "accept": "application/json"},
            )
        assert response.status_code == 200
        assert self._exists("move_copy_dst")
        assert not self._exists("move_copy_src")

    def test_move_reports_failure_and_leaves_the_source_untouched_when_copy_also_fails(self):
        """When both rename and the copy fallback fail, nothing is lost and 500 is reported."""
        self._seed("move_copyfail_src")
        with (
            patch("os.rename", side_effect=OSError("cross-device link")),
            patch("shutil.copy2", side_effect=OSError("disk full")),
        ):
            response = self.client.post(
                "/move/move_copyfail_src",
                data=dumps({"to_config_id": "move_copyfail_dst"}),
                content_type="application/json",
                headers={**_GOOD_MASTER, "accept": "application/json"},
            )
        assert response.status_code == 500
        assert self._exists("move_copyfail_src")
        assert not self._exists("move_copyfail_dst")

    def test_move_fails_early_when_the_configuration_store_is_not_writable(self):
        """A read-only filesystem is caught before any file operation is attempted."""
        self._seed("move_hc_src")
        with patch("api.views.healthcheck", return_value={"can_write_config": False, "details": []}):
            response = self.client.post(
                "/move/move_hc_src",
                data=dumps({"to_config_id": "move_hc_dst"}),
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

        # A JSON payload's source is always the URL/header key itself --
        # there is no from_config_id field to smuggle a different key
        # through.
        response = self.client.post(
            "/move/move_user_src",
            data=dumps({"to_config_id": "move_user_dst"}),
            content_type="application/json",
            headers={**user_creds, "accept": "application/json"},
        )
        assert response.status_code == 200
        assert self._exists("move_user_dst")

    def test_move_restricted_user_cannot_redirect_a_different_source_key_via_the_html_form(self):
        """The HTML form's from_config_id is read-only for a restricted user, enforced server-side too."""
        self._seed("move_user_src")
        self._seed("move_user_other")
        assert ConfigCache.set_auth("move_user_src", "alice", "secret") is True
        user_creds = {"authorization": _basic("alice", "secret")}

        # Authenticated against move_user_src (via the URL), but the form
        # payload claims a different from_config_id -- must be rejected,
        # not silently substituted or honored.
        response = self.client.post(
            "/move/move_user_src",
            data={"from_config_id": "move_user_other", "to_config_id": "move_user_dst"},
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
            data=dumps({"to_config_id": "move_admin_dst"}),
            content_type="application/json",
            headers={**_GOOD_MASTER, "accept": "application/json"},
        )
        assert response.status_code == 200
        assert self._exists("move_admin_dst")

    def test_move_admin_can_redirect_a_different_from_config_id_via_the_html_form(self):
        """An administrator's form may target any key, not just the one in the URL."""
        self._seed("move_admin_src")
        response = self.client.post(
            "/move/some_other_key_in_the_url",
            data={"from_config_id": "move_admin_src", "to_config_id": "move_admin_dst"},
            headers=_GOOD_MASTER,
        )
        assert response.status_code == 200
        assert self._exists("move_admin_dst")
        assert not self._exists("move_admin_src")

    def test_move_missing_key_is_rejected(self):
        """No URL key and no X-Apprise-Config-ID header is a plain bad request."""
        response = self.client.post(
            "/move/",
            data=dumps({"to_config_id": "move_dst"}),
            content_type="application/json",
            headers={**_GOOD_MASTER, "accept": "application/json"},
        )
        assert response.status_code == 400

    def test_move_invalid_header_key_is_rejected(self):
        """An X-Apprise-Config-ID header that fails the key pattern is rejected outright."""
        response = self.client.post(
            "/move/",
            data=dumps({"to_config_id": "move_dst"}),
            content_type="application/json",
            headers={**_GOOD_MASTER, "accept": "application/json", "X-Apprise-Config-ID": "not a valid key!!"},
        )
        assert response.status_code == 400

    def test_move_restricted_user_denied_for_a_key_that_is_not_theirs(self):
        """A per-key credential is rejected outright for a URL key it doesn't own."""
        self._seed("move_user_src")
        self._seed("move_user_other")
        assert ConfigCache.set_auth("move_user_src", "alice", "secret") is True
        user_creds = {"authorization": _basic("alice", "secret")}

        response = self.client.post(
            "/move/move_user_other",
            data=dumps({"to_config_id": "move_user_dst"}),
            content_type="application/json",
            headers={**user_creds, "accept": "application/json"},
        )
        assert response.status_code == 401
        assert self._exists("move_user_other")

    def test_move_form_redirect_denied_when_key_auth_fails(self):
        """A from_config_id override is still checked with key_auth_ok, even
        after the caller already cleared the URL key's own check."""
        self._seed("move_admin_src")
        with mock.patch(
            "api.views.key_auth_ok",
            side_effect=lambda request, key: key != "move_admin_src",
        ):
            response = self.client.post(
                "/move/some_other_key_in_the_url",
                data={"from_config_id": "move_admin_src", "to_config_id": "move_admin_dst"},
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
                data=dumps({"to_config_id": "move_dst"}),
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
