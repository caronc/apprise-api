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
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from django.urls import Resolver404, resolve

from ..auth import Authentication, ConfigAuthRecord, ConfigAuthState
from ..utils import (
    CONFIG_KEY_MAX_LENGTH,
    AppriseStoreMode,
    ConfigCache,
)


class ManagerPageTests(SimpleTestCase):
    """
    Manager Webpage testing
    """

    def test_manage_status_code(self):
        """
        General testing of management page
        """
        # No permission to get keys
        response = self.client.get("/cfg/")
        assert response.status_code == 403

        with override_settings(APPRISE_ADMIN=True, APPRISE_STATEFUL_MODE="hash"):
            response = self.client.get("/cfg/")
            assert response.status_code == 403

        with override_settings(APPRISE_ADMIN=False, APPRISE_STATEFUL_MODE="simple"):
            response = self.client.get("/cfg/")
            assert response.status_code == 403

        with override_settings(APPRISE_ADMIN=False, APPRISE_STATEFUL_MODE="disabled"):
            response = self.client.get("/cfg/")
            assert response.status_code == 403

        with override_settings(APPRISE_ADMIN=True, APPRISE_STATEFUL_MODE="disabled"):
            response = self.client.get("/cfg/")
            assert response.status_code == 403

        # But only when the setting is enabled
        with override_settings(APPRISE_ADMIN=True, APPRISE_STATEFUL_MODE="simple"):
            response = self.client.get("/cfg/")
            assert response.status_code == 200

        # A locked deployment without authentication does not expose IDs.
        with override_settings(
            APPRISE_ADMIN=True,
            APPRISE_STATEFUL_MODE="simple",
            APPRISE_CONFIG_LOCK=True,
            APPRISE_AUTH_REQUIRED=False,
        ):
            response = self.client.get("/cfg/")
            assert response.status_code == 403

        # An invalid key was specified
        response = self.client.get("/cfg/**invalid-key**")
        assert response.status_code == 404

        # An invalid key was specified
        response = self.client.get("/cfg/valid-key")
        assert response.status_code == 200

    @override_settings(
        APPRISE_ADMIN=True,
        APPRISE_STATEFUL_MODE="simple",
        APPRISE_CONFIG_LOCK=True,
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=base64.b64encode(b"master:pass").decode(),
    )
    def test_locked_admin_can_list_configurations(self):
        """CONFIG_LOCK still permits the authenticated administrator's list."""
        response = self.client.get(
            "/cfg/",
            headers={
                "authorization": "Basic " + base64.b64encode(b"master:pass").decode(),
                "accept": "application/json",
            },
        )
        assert response.status_code == 200

    @override_settings(APPRISE_STATEFUL_MODE="disabled")
    def test_disabled_stateful_mode_rejects_persistent_api_routes(self):
        """Every persistent API route stops before touching the store."""
        json_headers = {"accept": "application/json"}
        requests = (
            ("post", "/add/state_off", {"data": {"urls": "json://localhost"}}),
            ("post", "/get/state_off", {}),
            ("post", "/del/state_off", {}),
            (
                "post",
                "/move/state_off",
                {"data": json.dumps({"to": "state_on"}), "content_type": "application/json"},
            ),
            (
                "post",
                "/auth/state_off",
                {"data": json.dumps({"username": "alice", "password": "secret"}), "content_type": "application/json"},
            ),
            ("delete", "/auth/state_off", {}),
            ("post", "/notify/state_off", {"data": {"body": "test", "tag": "all"}}),
            ("get", "/json/urls/state_off", {}),
            ("get", "/auth/state_off", {}),
        )

        for method, path, kwargs in requests:
            response = getattr(self.client, method)(path, headers=json_headers, **kwargs)
            assert response.status_code == 403, path
            assert response.json()["error"] == "Persistent configuration storage is disabled"

        # Stateless /notify remains separate and reaches its normal validation.
        response = self.client.post("/notify", headers=json_headers)
        assert response.status_code == 400

        # The shared template policy also fails closed in stateless mode.
        assert Authentication.can_move_or_delete(SimpleNamespace()) is False

    def test_stateful_mode_is_normalized_before_access_checks(self):
        """Uppercase and unknown modes fail closed like the configured store."""
        for mode in (" DISABLED ", "unknown"):
            with override_settings(APPRISE_STATEFUL_MODE=mode):
                response = self.client.post("/get/state_off", headers={"accept": "application/json"})
            assert response.status_code == 403

    def test_config_id_selection_and_generator_controls(self):
        """Config IDs stay concealed and are submitted outside the URL."""
        response = self.client.get("/cfg/valid-key")
        assert response.status_code == 200

        content = response.content.decode("utf-8")
        assert content.index("config-auth-status") < content.index("config-id-label")
        assert 'id="config-id-select-form"' in content
        assert 'action="/cfg/@"' in content
        assert 'class="config-id-input"' in content
        assert 'type="password"' in content
        assert 'data-current-config-id="valid-key"' in content
        assert 'name="next"' in content
        assert 'value="config_current"' in content
        assert 'data-explicit-config-base="/cfg/"' in content
        assert 'maxlength="{}"'.format(CONFIG_KEY_MAX_LENGTH) in content
        assert "return /^[-\\w]{{1,{}}}$/u.test(configId);".format(CONFIG_KEY_MAX_LENGTH) in content
        assert 'class="btn-flat btn-small config-id-apply"' in content
        assert 'class="btn-flat btn-small config-id-revert"' in content
        assert "check_circle" in content
        assert "undo" in content
        assert 'id="config-id-mismatch-notice"' in content
        assert 'class="config-id-mismatch-icon"' in content
        assert "Changed Config ID Not Applied" in content
        assert "not the configuration currently being managed" in content
        assert "data-config-id-notice-apply" in content
        assert "data-config-id-notice-revert" in content
        assert "configIdApply.click()" in content
        assert "configIdRevert.click()" in content
        assert "restoreConcealment" in content
        assert "visibilityExplicit" in content
        assert "mismatchShown" in content
        assert "configIdInput.addEventListener('blur'" in content
        assert 'data-config-id-copy="valid-key"' in content
        assert "button.dataset.configIdCopy" in content
        assert "apprisePostConfigId(configId, nextName)" in content
        assert "appriseConfirmConfigSelection(configId, nextName, explicitBase, useCookieAlias)" in content
        assert "window.location.assign(explicitBase + encodeURIComponent(configId))" in content
        assert "Any unsaved changes on this page will be lost." in content
        assert 'id="cfggen-config-id"' in content
        assert 'aria-controls="cfggen-config-id"' in content
        assert 'id="cfggen-randomize"' in content
        assert "window.crypto.getRandomValues(bytes)" in content
        assert "appriseCopyToClipboard(" in content
        assert "Config ID copied to clipboard" in content
        assert "snippet-config-id is-concealed" in content
        assert "snippet-visibility-btn" in content
        assert "visibility_off</i>" in content
        assert "show ? 'visibility' : 'visibility_off'" in content
        assert "NodeFilter.SHOW_TEXT" in content
        assert "data-snippet-config-id" in content
        assert "nodeValue.includes(markedConfigId)" in content
        assert "node.nodeValue.split(markedConfigId)" in content
        assert "data-copy-text='valid-key'" in content
        assert "const copyText = snippet.dataset.copyText" in content

    def test_config_id_matching_command_word_is_not_concealed(self):
        """Only marked Config ID tokens are hidden when the ID is apprise."""
        response = self.client.get("/cfg/apprise")
        assert response.status_code == 200

        content = response.content.decode()
        assert 'data-copy-text=\'apprise --body="Test Message"' in content
        assert "nodeValue.includes(markedConfigId)" in content
        assert "nodeValue.includes(configId)" not in content

    def test_open_config_switch_uses_private_cookie(self):
        """An open deployment switches Config IDs without a keyed URL."""
        response = self.client.post(
            "/cfg/@",
            {"key": "selected-key"},
            headers={"accept": "text/html"},
        )

        assert response.status_code == 302
        assert response.url == "/cfg/@"
        assert response.cookies["key"].value == "selected-key"

        page = self.client.get("/cfg/@", headers={"accept": "text/html"})
        assert page.status_code == 200
        assert 'value="selected-key"' in page.content.decode()

        invalid = self.client.post(
            "/cfg/@",
            {"key": "invalid key"},
            headers={"accept": "text/html"},
        )
        assert invalid.status_code == 400

        auth_destination = self.client.post(
            "/cfg/@",
            {"key": "auth-key", "next": "auth_current"},
            headers={"accept": "text/html"},
        )
        assert auth_destination.status_code == 302
        assert auth_destination.url == "/auth/@"

    def test_unapplied_config_id_cannot_retarget_save(self):
        """An extra selector value cannot change the URL-bound save target."""
        managed_key = "managed-config"
        unapplied_key = "unapplied-config"
        try:
            page = self.client.get("/cfg/{}".format(managed_key))
            content = page.content.decode()
            assert 'id="config-id-select-form"' in content
            assert 'id="addconfig" action="/add/{}"'.format(managed_key) in content

            response = self.client.post(
                "/add/{}".format(managed_key),
                {
                    "key": unapplied_key,
                    "urls": "json://localhost",
                },
            )
            assert response.status_code == 200
            assert ConfigCache.get(managed_key)[0].startswith("json://localhost/")
            assert ConfigCache.get(unapplied_key)[0] is None
        finally:
            ConfigCache.clear(managed_key)
            ConfigCache.clear(unapplied_key)

    def test_configuration_fetch_requests_json(self):
        """The configuration editor explicitly requests a JSON response."""
        response = self.client.get("/cfg/valid-key")
        assert response.status_code == 200

        content = response.content.decode("utf-8")
        request = content.split("let response = await appriseFetch('/get/valid-key'", 1)[1].split("});", 1)[0]
        assert "'Accept': 'application/json'" in request
        assert "'Content-Type'" not in request
        assert "headers.set('X-Apprise-Web-Auth', '1')" in content

    def test_get_config(self):
        """
        Test retrieving configuration
        """

        # our key to use
        key = "test_cfg_config_"

        # No content saved to the location yet
        response = self.client.post("/cfg/{}".format(key))
        self.assertEqual(response.status_code, 204)

        # Add some content
        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200

        # Handle case when we try to retrieve our content but we have no idea
        # what the format is in. Essentially there had to have been disk
        # corruption here or someone meddling with the backend.
        with patch("gzip.open", side_effect=OSError):
            response = self.client.post("/cfg/{}".format(key))
            assert response.status_code == 500

        # Now we should be able to see our content
        response = self.client.post("/cfg/{}".format(key))
        assert response.status_code == 200

        # Add a YAML file
        response = self.client.post(
            "/add/{}".format(key),
            {
                "format": "yaml",
                "config": """
                urls:
                   - dbus://""",
            },
        )
        assert response.status_code == 200

        # Now retrieve our YAML configuration
        response = self.client.post("/cfg/{}".format(key))
        assert response.status_code == 200

        # Verify that the correct Content-Type is set in the header of the
        # response
        assert "Content-Type" in response
        assert response["Content-Type"].startswith("text/yaml")

        # The configuration editor requests the same content as JSON.
        response = self.client.post("/cfg/{}".format(key), HTTP_ACCEPT="application/json")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/json")
        assert response.json()["format"] == "yaml"

    def test_get_config_json_content_type_without_explicit_accept(self):
        """
        The web UI's config tab loads existing configuration via a
        fetch() call carrying an explicit JSON Content-Type but no
        Accept override (so Accept defaults to */*). That must still be
        honored as a request for a JSON response.
        """

        key = "test_cfg_config_json_"
        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200

        response = self.client.post(
            "/cfg/{}".format(key),
            content_type="application/json",
            HTTP_ACCEPT="*/*",
        )
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/json")
        payload = response.json()
        assert "config" in payload
        assert "format" in payload

    def test_manage_cfg_list_content_type_defaults_to_html(self):
        """
        /cfg/ should render HTML by default when allowed.
        """
        mod = resolve("/cfg/").func.__module__
        with (
            override_settings(APPRISE_ADMIN=True, APPRISE_STATEFUL_MODE="simple"),
            patch(f"{mod}.ConfigCache.keys", return_value=["open"]),
        ):
            response = self.client.get("/cfg/")
            assert response.status_code == 200
            assert response["Content-Type"].startswith("text/html")
            assert "config-auth-status is-disabled" in response.content.decode()

    @override_settings(
        APPRISE_ADMIN=True,
        APPRISE_STATEFUL_MODE="simple",
        APPRISE_USER="master",
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=base64.b64encode(b"master:pass").decode(),
    )
    def test_manage_cfg_list_html_shows_user_state(self):
        """The HTML list shows assigned and unassigned lock states."""
        self.client.post(
            "/login",
            {"username": "master", "password": "pass"},
            headers={"accept": "text/html"},
        )
        mod = resolve("/cfg/").func.__module__
        with (
            patch(f"{mod}.ConfigCache.keys", return_value=["open", "shared"]),
            patch(
                f"{mod}.ConfigCache.get_auth_record",
                side_effect=[None, ConfigAuthRecord("user", "alice", "digest")],
            ),
        ):
            response = self.client.get("/cfg/", headers={"accept": "text/html"})

        content = response.content.decode()
        assert response.status_code == 200
        assert "config-auth-status is-unassigned" in content
        assert "config-auth-status is-assigned" in content
        assert content.count("data-config-id-toggle") >= 2
        assert 'data-config-id-copy="open"' in content
        assert 'data-config-id-copy="shared"' in content
        assert 'class="config-list-user"' in content
        assert 'title="alice"' in content
        assert "No Username" in content
        open_item = content.split('class="collection-item config-list-item"', 1)[1]
        assert open_item.index("config-auth-status") < open_item.index("config-list-user")
        assert open_item.index("config-list-user") < open_item.index("config-list-link")

    def test_manage_cfg_list_json_when_requested(self):
        """
        /cfg/ should return JSON list when requested via Accept header.
        """
        with override_settings(APPRISE_ADMIN=True, APPRISE_STATEFUL_MODE="simple"):
            response = self.client.get("/cfg/", HTTP_ACCEPT="application/json")
            assert response.status_code == 200
            assert response["Content-Type"].startswith("application/json")
            payload = json.loads(response.content.decode("utf-8"))
            assert isinstance(payload, list)
            assert all(isinstance(entry, str) for entry in payload)

    def test_manage_cfg_list_denied_content_type_plain_text(self):
        """
        /cfg/ denied case should be plain text when JSON is not requested.
        """
        response = self.client.get("/cfg/")
        assert response.status_code == 403
        assert response["Content-Type"].startswith("text/plain")

    def test_manage_cfg_list_denied_content_type_json(self):
        """
        /cfg/ denied case should return JSON when requested.
        """
        response = self.client.get("/cfg/", HTTP_ACCEPT="application/json")
        assert response.status_code == 403
        assert response["Content-Type"].startswith("application/json")
        payload = json.loads(response.content.decode("utf-8"))
        assert "error" in payload

    def test_manage_cfg_list_json_returns_store_keys(self):
        with override_settings(APPRISE_ADMIN=True, APPRISE_STATEFUL_MODE="simple"):
            mod = resolve("/cfg/").func.__module__
            with patch(f"{mod}.ConfigCache.keys", return_value=["abc", "def"]) as m:
                response = self.client.get("/cfg/", HTTP_ACCEPT="application/json")
                assert response.status_code == 200
                assert response["Content-Type"].startswith("application/json")

                payload = json.loads(response.content.decode("utf-8"))
                assert payload == ["abc", "def"]
                m.assert_called_once_with()

    @override_settings(
        APPRISE_ADMIN=True,
        APPRISE_STATEFUL_MODE="simple",
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=base64.b64encode(b"master:pass").decode(),
    )
    def test_manage_cfg_list_reports_users(self):
        """Each API entry reports its assigned configuration username."""
        mod = resolve("/cfg/").func.__module__
        with (
            patch(
                f"{mod}.ConfigCache.keys",
                return_value=["open", "shared", "password-only", "damaged"],
            ),
            patch(
                "api.auth.Authentication.config_state",
                side_effect=[
                    ConfigAuthState(Authentication.MODE_GLOBAL),
                    ConfigAuthState(Authentication.MODE_ASSIGNED, username="alice", digest="digest"),
                    ConfigAuthState(Authentication.MODE_ASSIGNED, username="", digest="digest"),
                    ConfigAuthState(Authentication.MODE_ASSIGNED, unreadable=True),
                ],
            ),
        ):
            response = self.client.get(
                "/cfg/",
                HTTP_ACCEPT="application/json",
                headers={"authorization": "Basic " + base64.b64encode(b"master:pass").decode()},
            )

        assert response.status_code == 200
        assert response.json() == [
            {"key": "open", "user": None, "access": "user"},
            {"key": "shared", "user": "alice", "access": "user"},
            {"key": "password-only", "user": "", "access": "user"},
            {"key": "damaged", "user": None, "access": "user"},
        ]

    @override_settings(APPRISE_API_ONLY=True)
    def test_api_only_blocks_config_list_if_present(self) -> None:
        """
        Test our inability to access /cfg if APPRISE_API_ONLY set to true
        """
        paths = ("/", "/cfg", "/cfg/key", "/details")
        for path in paths:
            try:
                resolve(path)
            except Resolver404:
                self.skipTest(f"Path not present in URLconf: {path}")

            response = self.client.get(path)
            assert response.status_code == 421

    @override_settings(APPRISE_API_ONLY=True, APPRISE_ADMIN=True, APPRISE_STATEFUL_MODE=AppriseStoreMode.SIMPLE)
    def test_api_only_still_serves_json_for_details_and_config_list(self) -> None:
        """API-only mode keeps JSON available while hiding HTML pages."""
        json_capable_paths = ("/cfg", "/details")
        for path in json_capable_paths:
            response = self.client.get(path, **{"HTTP_ACCEPT": "application/json"})
            self.assertEqual(response.status_code, 200, path)
            assert response["Content-Type"].startswith("application/json")

        # These pages have no JSON form at all - API-only mode always
        # blocks them, Accept header or not.
        html_only_paths = ("/", "/cfg/key")
        for path in html_only_paths:
            response = self.client.get(path, **{"HTTP_ACCEPT": "application/json"})
            self.assertEqual(response.status_code, 421, path)
