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
"""Test template values supplied to saved configurations.

URL variables are intentionally unrestricted and may select a host; named
YAML settings affect only their option. The API preserves these placement
rules for every access level. Configuration authors decide which placement
is appropriate for the callers who can reach their Config ID.
"""

from json import dumps
import logging
import os
from pathlib import Path
from unittest.mock import Mock, patch

import apprise
from django.conf import settings
from django.test import SimpleTestCase
from django.test.utils import override_settings
import requests

from ..utils import ConfigCache

CONFIG = """
version: 2
template:
  - token
  - host_name: localhost
urls:
  - json://user:${TOKEN}@${HOST_NAME}/:
    - tag: work
  - json://plain-host/:
    - tag: work
"""


class TemplateTests(SimpleTestCase):
    """Supplying template values when sending to a stored configuration."""

    def setUp(self):
        """Store a configuration that is waiting on a value."""
        self.key = "test_template"
        ConfigCache.put(self.key, CONFIG, "yaml")

    def tearDown(self):
        ConfigCache.clear(self.key)

    def test_editor_highlights_template_variables(self):
        """The config editor recognizes variables inside and outside URLs."""
        response = self.client.get(f"/cfg/{self.key}", headers={"accept": "text/html"})

        assert response.status_code == 200
        content = response.content.decode()
        assert "className: 'template-variable'" in content
        assert "contains: [TEMPLATE_VARIABLE]" in content
        assert "selectedFormat === 'yaml'" in content
        assert "declaredNames.has(match[1].toLowerCase())" in content
        assert "setReviewUrlTextWithBreaks(code, entry.url, entry.template)" in content

    def test_editor_highlight_supports_both_themes(self):
        """Light and dark themes give template variables distinct colors."""
        css_path = Path(settings.BASE_DIR) / "static" / "css"

        for theme in ("theme-light.css", "theme-dark.css"):
            content = (css_path / theme).read_text(encoding="utf-8")
            assert (".apprise-config-yaml .apprise-config-highlight .hljs-template-variable") in content

    @patch("apprise.Apprise.notify")
    def test_json_value(self, mock_notify):
        """A JSON payload carries an object of name/value pairs."""
        mock_notify.return_value = True

        response = self.client.post(
            f"/notify/{self.key}",
            data=dumps({"body": "test", "template": {"token": "abc123"}}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert mock_notify.call_args.kwargs["template"] == {"token": "abc123"}

    @patch("apprise.Apprise.notify")
    def test_form_value(self, mock_notify):
        """A form posts each value as template[name]."""
        mock_notify.return_value = True

        response = self.client.post(
            f"/notify/{self.key}",
            {"body": "test", "template[token]": "abc123"},
        )

        assert response.status_code == 200
        assert mock_notify.call_args.kwargs["template"] == {"token": "abc123"}

    @patch("apprise.Apprise.notify")
    def test_name_case_insensitive(self, mock_notify):
        """TOKEN and token mean the same variable."""
        mock_notify.return_value = True

        response = self.client.post(
            f"/notify/{self.key}",
            {"body": "test", "template[TOKEN]": "abc123"},
        )

        assert response.status_code == 200
        assert mock_notify.call_args.kwargs["template"] == {"token": "abc123"}

    @patch("apprise.Apprise.notify")
    def test_json_name_case_insensitive(self, mock_notify):
        """JSON template names use the same case-insensitive lookup."""
        mock_notify.return_value = True

        response = self.client.post(
            f"/notify/{self.key}",
            data=dumps({"body": "test", "template": {"ToKeN": "abc123"}}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert mock_notify.call_args.kwargs["template"] == {"token": "abc123"}

    @patch("apprise.Apprise.notify")
    def test_form_case_collision(self, mock_notify):
        """Two spellings of one form name are ambiguous."""
        response = self.client.post(
            f"/notify/{self.key}",
            {
                "body": "test",
                "template[token]": "first",
                "template[TOKEN]": "second",
            },
        )

        assert response.status_code == 400
        assert mock_notify.call_count == 0

    @patch("apprise.Apprise.notify")
    def test_json_case_collision(self, mock_notify):
        """Two spellings of one JSON name are also ambiguous."""
        response = self.client.post(
            f"/notify/{self.key}",
            data=dumps(
                {
                    "body": "test",
                    "template": {"token": "first", "TOKEN": "second"},
                }
            ),
            content_type="application/json",
        )

        assert response.status_code == 400
        assert mock_notify.call_count == 0

    @patch("apprise.Apprise.notify")
    def test_no_values(self, mock_notify):
        """Nothing supplied is not itself an error."""
        mock_notify.return_value = True

        response = self.client.post(f"/notify/{self.key}", {"body": "test"})

        assert response.status_code == 200
        assert mock_notify.call_args.kwargs["template"] is None

    @patch("requests.request")
    def test_undeclared_name_is_accepted(self, mock_request):
        """Extra valid names are passed through and harmlessly ignored."""
        result = Mock()
        result.status_code = requests.codes.ok
        result.content = ""
        result.headers = {}
        mock_request.return_value = result

        response = self.client.post(
            f"/notify/{self.key}",
            data=dumps(
                {
                    "body": "test",
                    "tag": "work",
                    "template": {"token": "a", "sneaky": "b"},
                }
            ),
            content_type="application/json",
            headers={"accept": "application/json"},
        )

        assert response.status_code == 200
        assert mock_request.call_count == 2

    @patch("apprise.Apprise.notify")
    def test_invalid_field_shape(self, mock_notify):
        """The field has to be an object of name/value pairs."""
        for payload in ("a string", ["a", "list"], 42):
            response = self.client.post(
                f"/notify/{self.key}",
                data=dumps({"body": "test", "template": payload}),
                content_type="application/json",
                headers={"accept": "application/json"},
            )

            assert response.status_code == 400
            assert "template" in response.json().get("field", "")

        assert mock_notify.call_count == 0

    def test_control_character(self):
        """A value must not be able to break out of a log line."""
        response = self.client.post(
            f"/notify/{self.key}",
            data=dumps({"body": "test", "template": {"token": "a\nb"}}),
            content_type="application/json",
            headers={"accept": "application/json"},
        )

        assert response.status_code == 400

    def test_value_length_limit(self):
        """Values are capped so a caller can not flood the parser."""
        response = self.client.post(
            f"/notify/{self.key}",
            data=dumps({"body": "test", "template": {"token": "x" * 2000}}),
            content_type="application/json",
            headers={"accept": "application/json"},
        )

        assert response.status_code == 400

    @patch("apprise.Apprise.notify")
    def test_many_values(self, mock_notify):
        """There is no cap on how many values a request may carry."""
        mock_notify.return_value = True

        names = [f"name{i}" for i in range(200)]
        declared = "\n".join(f"  - {name}" for name in names)
        key = "test_template_many"
        ConfigCache.put(
            key,
            f"template:\n{declared}\nurls:\n  - json://localhost/\n",
            "yaml",
        )

        response = self.client.post(
            f"/notify/{key}",
            data=dumps(
                {
                    "body": "test",
                    "template": dict.fromkeys(names, "x"),
                }
            ),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert len(mock_notify.call_args.kwargs["template"]) == 200
        ConfigCache.clear(key)

    @patch("requests.request")
    def test_missing_name_not_disclosed(self, mock_request):
        """Do not reveal missing variable names to the caller."""
        response = Mock()
        response.status_code = requests.codes.ok
        response.content = ""
        response.headers = {}
        mock_request.return_value = response

        result = self.client.post(
            f"/notify/{self.key}",
            {"body": "test", "tag": "work"},
            headers={"accept": "application/json"},
        )

        if result.streaming:
            # Check the caller-controlled live log response too.
            body = b"".join(result.streaming_content).decode("utf-8")

        else:
            body = result.content.decode("utf-8")

        body = body.lower()
        assert "token" not in body
        assert "${" not in body
        assert "host_name" not in body

    @patch("requests.request")
    def test_pending_entry_skipped(self, mock_request):
        """The rest of the configuration is still notified."""
        response = Mock()
        response.status_code = requests.codes.ok
        response.content = ""
        response.headers = {}
        mock_request.return_value = response

        result = self.client.post(f"/notify/{self.key}", {"body": "x", "tag": "work"})

        # Only the entry that needed nothing was reached
        assert mock_request.call_count == 1

        # ...and the caller is told it was not a clean run
        assert result.status_code == 424

    @patch("requests.request")
    def test_supplied_value_sends_all(self, mock_request):
        """With the value to hand, nothing is held back."""
        response = Mock()
        response.status_code = requests.codes.ok
        response.content = ""
        response.headers = {}
        mock_request.return_value = response

        result = self.client.post(
            f"/notify/{self.key}",
            {"body": "x", "tag": "work", "template[token]": "abc123"},
        )

        assert result.status_code == 200
        assert mock_request.call_count == 2
        assert "localhost" in dumps([str(call) for call in mock_request.call_args_list])

    @patch("requests.request")
    def test_review_quick_test_sends_only_selected_entry(self, mock_request):
        """A card test does not include another entry with the same tag."""
        upstream = Mock()
        upstream.status_code = requests.codes.ok
        upstream.content = ""
        upstream.headers = {}
        mock_request.return_value = upstream

        result = self.client.post(
            f"/notify/{self.key}",
            {"body": "test", "tag": "work:0", "template[token]": "abc123"},
            headers={"X-Apprise-Notification-Index": "0"},
        )

        assert result.status_code == 200
        assert mock_request.call_count == 1

    @patch("requests.request")
    def test_review_quick_test_rejects_invalid_entry_index(self, mock_request):
        """Malformed or stale card indexes never fall back to a broad send."""
        for value in ("-1", "01", "invalid", "999"):
            with self.subTest(value=value):
                result = self.client.post(
                    f"/notify/{self.key}",
                    {"body": "test", "tag": "work:0", "template[token]": "abc123"},
                    headers={"X-Apprise-Notification-Index": value},
                )
                assert result.status_code == 400

        assert mock_request.call_count == 0

    @patch("requests.request")
    def test_environment_value_used(self, mock_request):
        """A value need not be sent with every notification."""
        response = Mock()
        response.status_code = requests.codes.ok
        response.content = ""
        response.headers = {}
        mock_request.return_value = response

        with patch.dict(os.environ, {"APPRISE_TEMPLATE_TOKEN": "from-env"}):
            result = self.client.post(f"/notify/{self.key}", {"body": "x", "tag": "work"})

        assert result.status_code == 200
        assert mock_request.call_count == 2

    @patch("requests.request")
    def test_templates_disabled_reads_config_verbatim(self, mock_request):
        """Reads the configuration as it was read before templates existed.

        The ${NAME} markers stay as written, so every URL loads straight
        away and nothing is held back waiting on a value.
        """
        response = Mock()
        response.status_code = requests.codes.ok
        response.content = ""
        response.headers = {}
        mock_request.return_value = response

        with override_settings(APPRISE_ALLOW_TEMPLATES=False):
            result = self.client.post(f"/notify/{self.key}", {"body": "x", "tag": "work"})

            # Both URLs were used, even though neither value was supplied
            assert result.status_code == 200
            assert mock_request.call_count == 2

            # ...and nothing is reported as waiting on a value
            listing = self.client.get(f"/json/urls/{self.key}").json()
            assert all(entry["template"] == {} for entry in listing["urls"])

    @patch("apprise.Apprise.notify")
    def test_templates_disabled_accepts_unused_value(self, mock_notify):
        """The disabled parser may harmlessly ignore supplied values."""
        mock_notify.return_value = True
        with override_settings(APPRISE_ALLOW_TEMPLATES=False):
            response = self.client.post(
                f"/notify/{self.key}",
                data=dumps({"body": "test", "template": {"token": "abc"}}),
                content_type="application/json",
                headers={"accept": "application/json"},
            )

        assert response.status_code == 200
        assert mock_notify.call_args.kwargs["template"] == {"token": "abc"}

    def test_templates_disabled_ignores_table_when_saving(self):
        """The server toggle also controls validation during a save."""
        key = "test_disabled_template_save"
        config = "version: 2\ntemplate: not-a-table\nurls:\n  - json://localhost/\n"

        assert self.client.post(f"/add/{key}", {"config": config, "format": "yaml"}).status_code == 400

        with override_settings(APPRISE_ALLOW_TEMPLATES=False):
            response = self.client.post(f"/add/{key}", {"config": config, "format": "yaml"})

        assert response.status_code == 200
        ConfigCache.clear(key)

    @patch("requests.request")
    def test_live_log_name_not_disclosed(self, mock_request):
        """Keep missing variable names out of caller-controlled live logs."""
        response = Mock()
        response.status_code = requests.codes.ok
        response.content = ""
        response.headers = {}
        mock_request.return_value = response

        result = self.client.post(
            f"/notify/{self.key}",
            {"body": "x", "tag": "work"},
            headers={"X-Apprise-Log-Level": "debug"},
        )

        body = b"".join(result.streaming_content).decode("utf-8").lower()

        # Something clearly went unsent...
        assert "skipped" in body

        # ...but never what it was waiting for
        assert "token" not in body
        assert "host_name" not in body
        assert "${" not in body

    def test_url_listing_variables(self):
        """The review screen needs to know what to ask for."""
        result = self.client.get(f"/json/urls/{self.key}")
        assert result.status_code == 200

        payload = result.json()

        # Reported against the entry that is waiting on them; a name maps
        # to the default the configuration offers, or to None when none
        # can be offered
        waiting = [entry for entry in payload["urls"] if entry["template"]]
        assert len(waiting) == 1
        assert waiting[0]["template"] == {
            "host_name": "localhost",
            "token": None,
        }

        # It has no identifier yet; it is not a service until it loads
        assert waiting[0]["id"] is None

        # ...and it reads back as it was written
        assert "${TOKEN}" in waiting[0]["url"]

    def test_private_url_listing_hides_defaults_but_not_names(self):
        """Privacy withholds values while still saying what to ask for.

        A caller has to know which names to prompt for and which of them
        are mandatory.  A default is a different matter: it can hold a
        secret, so it is left out.
        """
        result = self.client.get(f"/json/urls/{self.key}?privacy=1")
        assert result.status_code == 200
        payload = result.json()

        # No default value comes through, not even for the optional name
        assert "localhost" not in dumps(payload)

        waiting = [entry for entry in payload["urls"] if entry["template"]]
        assert len(waiting) == 1

        # Both names are still listed; neither carries a value, which is
        # all a caller needs in order to know what to prompt for
        assert waiting[0]["template"] == {"host_name": None, "token": None}

        # Markers stay readable so a caller can still see where a value goes
        assert "${TOKEN}" in waiting[0]["url"]

    def test_url_listing_never_exposes_environment_values(self):
        """Environment fallbacks stay server-side in both privacy modes."""
        with patch.dict(
            os.environ,
            {
                "APPRISE_TEMPLATE_HOST_NAME": "environment.example",
                "APPRISE_TEMPLATE_TOKEN": "environment-secret",
            },
        ):
            visible = self.client.get(f"/json/urls/{self.key}")
            private = self.client.get(f"/json/urls/{self.key}?privacy=1")
            availability = self.client.get(f"/json/urls/{self.key}?privacy=0&fallbacks=1")

        assert visible.status_code == 200
        assert private.status_code == 200
        assert availability.status_code == 200
        visible_payload = visible.json()
        private_payload = private.json()
        availability_payload = availability.json()
        visible_waiting = [entry for entry in visible_payload["urls"] if entry["template"]]
        private_waiting = [entry for entry in private_payload["urls"] if entry["template"]]
        availability_waiting = [entry for entry in availability_payload["urls"] if entry["template"]]
        assert visible_waiting[0]["template"] == {
            "host_name": "localhost",
            "token": None,
        }
        assert private_waiting[0]["template"] == {
            "host_name": None,
            "token": None,
        }
        # The editor learns that a fallback exists, but never receives it.
        assert availability_waiting[0]["template"] == {
            "host_name": "localhost",
            "token": "",
        }
        for payload in (visible_payload, private_payload, availability_payload):
            assert "environment.example" not in dumps(payload)
            assert "environment-secret" not in dumps(payload)

    @override_settings(APPRISE_CONFIG_LOCK=True)
    @patch("api.views.Authentication.key_ok", return_value=True)
    @patch("api.views.Authentication.config_lock_allows", return_value=False)
    def test_config_lock_reveals_nothing_at_all(self, _mock_allows, _mock_key_ok):
        """A caller the lock turns away learns nothing, names included."""
        for query in (
            "",
            "?privacy=1",
        ):
            result = self.client.get(f"/json/urls/{self.key}{query}")
            assert result.status_code == 403
            assert "TOKEN" not in result.content.decode("utf-8")
            assert "HOST_NAME" not in result.content.decode("utf-8")

    @override_settings(APPRISE_ALLOW_TEMPLATES=False)
    def test_templates_disabled_reports_no_names(self):
        """With templates switched off there is nothing to report."""
        result = self.client.get(f"/json/urls/{self.key}?privacy=1")
        assert result.status_code == 200
        payload = result.json()
        assert all(entry["template"] == {} for entry in payload["urls"])
        assert "TOKEN" not in dumps(payload)

    @override_settings(APPRISE_CONFIG_LOCK=True)
    @patch("api.views.Authentication.key_ok", return_value=True)
    @patch("api.views.Authentication.config_lock_allows", return_value=True)
    def test_admin_status_is_not_locked_out(self, _mock_allows, _mock_key_ok):
        """An administrator may bypass the lock, so nothing is locked."""
        result = self.client.get("/status", headers={"accept": "application/json"})
        assert result.status_code == 200
        payload = result.json()
        assert payload["config_lock"] is False

    def test_web_template_controls(self):
        """The page provides concealed template controls for both send paths."""
        result = self.client.get(f"/cfg/{self.key}")
        assert result.status_code == 200
        page = result.content.decode("utf-8")
        assert "privacy=1" in page
        assert "privacy=0" in page
        assert "selectedNotifyTemplateDetails" in page
        assert "template-prompt-" in page
        assert 'id="notify-template-rows"' in page
        assert 'id="notify-template-add"' in page
        assert 'autocomplete="off"' in page
        assert 'class="notify-form-section notify-delivery-options"' in page
        assert 'class="notify-form-section notify-message-fields"' in page
        assert "url-template-summary" in page
        assert "url-template-row" not in page
        assert "collectNotifyTemplateValues" in page
        assert "sendReviewTestNotification(tags, entryIndex" in page
        assert "'X-Apprise-Notification-Index': String(entryIndex)" in page
        assert "data-notify-copy-logs" in page
        assert "--- BEGIN APPRISE NOTIFICATION LOG ---" in page
        assert "--- END APPRISE NOTIFICATION LOG ---" in page
        assert "disableTemplateAutofill" in page
        assert "input.autocomplete = 'off'" in page
        assert "input.dataset.concealedText = 'true'" in page
        assert "input.setAttribute('type', 'password')" not in page
        assert "valueInput.type = 'password'" not in page
        assert "wrapper.addEventListener('keydown'" in page
        assert "Swal.clickConfirm()" in page
        assert "data-1p-ignore" in page
        assert "data-bwignore" in page
        assert "data-lpignore" in page
        assert "data-protonpass-ignore" in page
        assert "data-form-type" in page
        assert "Show Value" in page

    @patch("apprise.Apprise.notify")
    def test_stateless_values_are_accepted_and_unused(self, mock_notify):
        """Extra template values do not affect a stateless URL."""
        mock_notify.return_value = True
        response = self.client.post(
            "/notify",
            data=dumps(
                {
                    "body": "test",
                    "urls": "json://localhost",
                    "template": {"token": "abc"},
                }
            ),
            content_type="application/json",
            headers={"accept": "application/json"},
        )

        assert response.status_code == 200
        assert mock_notify.call_args.kwargs["template"] == {"token": "abc"}

    def test_template_only_config_saves(self):
        """Held-back entries still count as loaded configuration."""
        key = "test_template_only"
        response = self.client.post(
            f"/add/{key}",
            {"config": ("version: 2\ntemplate:\n  - token\nurls:\n  - json://user:${TOKEN}@localhost/\n")},
        )

        assert response.status_code == 200
        ConfigCache.clear(key)

    @override_settings(APPRISE_CONFIG_LOCK=True)
    @patch("api.views.Authentication.key_ok", return_value=True)
    @patch("api.views.Authentication.config_lock_allows", return_value=True)
    def test_admin_still_sees_template_names(self, _mock_allows, _mock_key_ok):
        """An administrator past the lock can still be told what to ask for."""
        result = self.client.get(f"/json/urls/{self.key}?privacy=1")
        assert result.status_code == 200
        payload = result.json()

        waiting = [entry for entry in payload["urls"] if entry["template"]]
        assert len(waiting) == 1
        assert waiting[0]["template"]["token"] is None

        # ...and the marker is readable, not concealed behind stars
        assert "${TOKEN}" in waiting[0]["url"]
        assert "****" not in waiting[0]["url"]

    def test_web_dialog_omits_blank_values(self):
        """The quick-test dialog trims values and leaves blanks out."""
        result = self.client.get(f"/cfg/{self.key}")
        assert result.status_code == 200
        page = result.content.decode("utf-8")

        assert "const value = (el.value || '').trim();" in page
        assert "input.dataset.templateRequired = 'true';" in page
        assert "auth-login-error review-template-summary-error" in page
        assert "summary.hidden = false;" in page
        assert "Provide the required template values before sending." in page
        assert "privacy=0&fallbacks=1" in page

        # A blank field is simply not sent, so the saved default or the
        # server environment can still supply it.
        assert "required.indexOf" not in page

    @patch("requests.request")
    def test_host_variable(self, mock_request):
        """A URL variable may choose the host for every access level."""
        result = Mock()
        result.status_code = requests.codes.ok
        result.content = ""
        result.headers = {}
        mock_request.return_value = result

        response = self.client.post(
            f"/notify/{self.key}",
            data=dumps(
                {
                    "body": "test",
                    "tag": "work",
                    "template": {"token": "a", "host_name": "elsewhere"},
                }
            ),
            content_type="application/json",
            headers={"accept": "application/json"},
        )

        # Both entries send, and the supplied host is the one used
        assert response.status_code == 200
        assert mock_request.call_count == 2
        assert "elsewhere" in dumps([str(c) for c in mock_request.call_args_list])

    @patch("apprise.Apprise.notify")
    def test_form_empty_value(self, mock_notify):
        """A field left empty reads the same as not sending the name.

        The value is dropped, so a default in the configuration or a value
        the server holds in its own environment still applies.
        """
        mock_notify.return_value = True

        response = self.client.post(
            f"/notify/{self.key}",
            {"body": "test", "template[host_name]": "   "},
        )

        assert response.status_code == 200
        assert mock_notify.call_args.kwargs["template"] is None

    @patch("apprise.Apprise.notify")
    def test_json_empty_value(self, mock_notify):
        """A JSON payload drops an empty value the same way."""
        mock_notify.return_value = True

        response = self.client.post(
            f"/notify/{self.key}",
            data=dumps({"body": "test", "template": {"host_name": ""}}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert mock_notify.call_args.kwargs["template"] is None

    @patch("apprise.Apprise.notify")
    def test_values_are_trimmed(self, mock_notify):
        """Surrounding whitespace never reaches a URL."""
        mock_notify.return_value = True

        response = self.client.post(
            f"/notify/{self.key}",
            data=dumps({"body": "test", "template": {"token": "  abc  "}}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert mock_notify.call_args.kwargs["template"] == {"token": "abc"}

    @patch("apprise.Apprise.notify")
    def test_payload_without_template_is_accepted(self, mock_notify):
        """A caller that knows nothing about template values still works.

        Leaving the field out, sending an empty set, or sending only blank
        values all mean the same thing, which keeps the payload usable by
        clients written before template values existed.
        """
        mock_notify.return_value = True

        for payload in (
            {"body": "test"},
            {"body": "test", "template": {}},
            {"body": "test", "template": None},
            {"body": "test", "template": {"token": "   "}},
        ):
            response = self.client.post(
                f"/notify/{self.key}",
                data=dumps(payload),
                content_type="application/json",
            )

            assert response.status_code == 200, payload
            assert mock_notify.call_args.kwargs["template"] is None, payload

    def test_review_separates_redacted_urls_from_declared_defaults(self):
        """The review displays private URLs and reads YAML defaults separately."""
        result = self.client.get(f"/cfg/{self.key}")
        assert result.status_code == 200
        page = result.content.decode("utf-8")
        assert "/json/urls/" in page
        assert "?privacy=1" in page
        assert "?privacy=0" in page
        assert "entry.template = source.template;" in page

    def test_notification_values_skip_blank_and_duplicate_names(self):
        """The Notification tab trims values and sends each name once."""
        result = self.client.get(f"/cfg/{self.key}")
        assert result.status_code == 200
        page = result.content.decode("utf-8")
        assert "valueInput.value.trim()" in page
        assert "seen.has(normalized)" in page
        assert "if (!normalized || !value" in page
        assert "syncNotifyTemplateRows(true)" in page
        assert "add.disabled = !canAdd" in page
        assert "'is-included', Boolean(name && value && !duplicate)" in page
        assert "const declared = card.templateDetails || {};" in page
        assert "valueInput.value = value || '';" in page

        css = (Path(settings.BASE_DIR) / "static" / "css" / "base.css").read_text()
        assert ".notify-template-row.is-included" in css
        assert 'input[data-concealed-text="true"]:not(.value-is-visible)' in css
        assert ".notify-form-section" in css
        assert "--review-control-size: 1.95rem;" in css
        assert "#url-list .url-template-summary" in css
        assert "#url-list .url-template-var," in css
        assert ".review-template-field label {" in css
        assert "background: var(--mobile-beta-accent-soft);" in css
        assert "#notify .notify-form-section .row>.input-field.col" in css
        assert "#notify .notify-form-section .input-field>label.active" in css
        assert "color: var(--mobile-beta-accent);" in css
        assert "grid-row: 1;" in css
        assert "transform: translateY(-50%);" in css

        light_css = (Path(settings.BASE_DIR) / "static" / "css" / "theme-light.css").read_text()
        dark_css = (Path(settings.BASE_DIR) / "static" / "css" / "theme-dark.css").read_text()
        assert "--select-bg: #f8fafb;" in light_css
        assert "--select-bg: #1c1f26;" in dark_css
        assert "color: var(--mobile-beta-accent) !important;" in light_css
        assert "color: var(--mobile-beta-accent) !important;" in dark_css
        assert "background: var(--select-bg);" in css

    def test_review_dialog_does_not_track_blank_overrides(self):
        """The quick-test dialog never sends a blank override."""
        result = self.client.get(f"/cfg/{self.key}")
        assert result.status_code == 200
        page = result.content.decode("utf-8")
        assert "templateEdited" not in page
        assert "if (name.trim() && value)" in page

    @patch("apprise.Apprise.notify")
    def test_form_repeated_field(self, mock_notify):
        """The same form field sent twice is ambiguous."""
        response = self.client.post(
            f"/notify/{self.key}",
            "body=test&template[token]=first&template[token]=second",
            content_type="application/x-www-form-urlencoded",
            headers={"accept": "application/json"},
        )

        assert response.status_code == 400
        assert mock_notify.call_count == 0

    @override_settings(APPRISE_AUTH_REQUIRED=True)
    @patch("requests.request")
    @patch("api.views.Authentication.key_ok", return_value=True)
    def test_public_caller_may_supply_values(self, _mock_key_ok, mock_request):
        """Every access level may fill in the variables it knows about.

        Reaching a configuration at all means holding its Config ID, and
        for `user` and `locked` its credentials too. The author decides
        what is templated, so no access level is singled out here.
        """
        result = Mock()
        result.status_code = requests.codes.ok
        result.content = ""
        result.headers = {}
        mock_request.return_value = result

        state = Mock(public=True, disabled=False, access="public", config_locked=True)
        with patch("api.views.Authentication.config_state", return_value=state):
            response = self.client.post(
                f"/notify/{self.key}",
                data=dumps(
                    {
                        "body": "test",
                        "tag": "work",
                        "template": {"token": "abc", "host_name": "chosen.example"},
                    }
                ),
                content_type="application/json",
                headers={"accept": "application/json"},
            )

        assert response.status_code == 200
        assert "chosen.example" in str(mock_request.call_args_list)

    @override_settings(APPRISE_AUTH_REQUIRED=True)
    @patch("apprise.Apprise.notify")
    @patch("api.views.Authentication.key_ok", return_value=True)
    def test_locked_caller_may_supply_values(self, _mock_key_ok, mock_notify):
        """A locked caller proved its credentials, so it may fill in too."""
        mock_notify.return_value = True

        state = Mock(public=False, disabled=False, access="locked", config_locked=True)
        with patch("api.views.Authentication.config_state", return_value=state):
            response = self.client.post(
                f"/notify/{self.key}",
                data=dumps({"body": "test", "tag": "work", "template": {"token": "abc"}}),
                content_type="application/json",
                headers={"accept": "application/json"},
            )

        assert response.status_code == 200
        assert mock_notify.call_args.kwargs["template"] == {"token": "abc"}

    @override_settings(APPRISE_AUTH_REQUIRED=True)
    @patch("apprise.Apprise.notify")
    @patch("api.views.Authentication.key_ok", return_value=True)
    def test_values_accepted_through_the_header(self, _mock_key_ok, mock_notify):
        """A stateless call naming a saved configuration works the same."""
        mock_notify.return_value = True

        state = Mock(public=True, disabled=False, access="public", config_locked=True)
        with patch("api.views.Authentication.config_state", return_value=state):
            response = self.client.post(
                "/notify",
                data=dumps({"body": "test", "tag": "work", "template": {"token": "abc"}}),
                content_type="application/json",
                headers={"accept": "application/json", "X-Apprise-Config-ID": self.key},
            )

        assert response.status_code == 200
        assert mock_notify.call_args.kwargs["template"] == {"token": "abc"}

    @patch("apprise.Apprise.notify")
    def test_administrator_may_supply_values(self, mock_notify):
        """An administrator can already read and edit the configuration."""
        mock_notify.return_value = True

        response = self.client.post(
            f"/notify/{self.key}",
            data=dumps({"body": "test", "template": {"token": "abc"}}),
            content_type="application/json",
            headers={"accept": "application/json"},
        )

        assert response.status_code == 200
        assert mock_notify.call_args.kwargs["template"] == {"token": "abc"}

    @patch("requests.request")
    def test_placement_in_a_named_setting(self, mock_request):
        """A variable in a named YAML setting reaches that option only."""
        result = Mock()
        result.status_code = requests.codes.ok
        result.content = ""
        result.headers = {}
        mock_request.return_value = result

        key = "test_template_setting"
        ConfigCache.put(
            key,
            "version: 2\ntemplate:\n  - target\n"
            "urls:\n  - json://user:pass@fixed.example/:\n"
            "      - to: ${TARGET}\n        tag: work\n",
            "yaml",
        )

        response = self.client.post(
            f"/notify/{key}",
            data=dumps(
                {
                    "body": "test",
                    "tag": "work",
                    "template": {"target": "elsewhere.example/hook"},
                }
            ),
            content_type="application/json",
            headers={"accept": "application/json"},
        )

        assert response.status_code == 200

        # The request still went to the host the configuration names
        assert mock_request.call_count == 1
        assert "fixed.example" in str(mock_request.call_args)
        assert "elsewhere.example" not in str(mock_request.call_args[0])
        ConfigCache.clear(key)

    def test_placement_in_a_named_setting_is_listed(self):
        """A variable in a setting is reported like any other."""
        key = "test_template_setting_list"
        ConfigCache.put(
            key,
            "version: 2\ntemplate:\n  - target\nurls:\n  - mailto://user:pass@gmail.com:\n      - to: ${TARGET}\n",
            "yaml",
        )

        payload = self.client.get(f"/json/urls/{key}?privacy=1").json()
        entry = payload["urls"][0]
        assert entry["template"] == {"target": None}

        # The marker shows where the value belongs, as a query parameter
        assert "${TARGET}" in entry["url"]
        ConfigCache.clear(key)

    def test_listing_names_unsupported_setting(self):
        """List a needed value even when the service has no URL option for it.

        The marker cannot appear in the URL because this service ignores
        that setting. The template name still tells callers what was declared.
        """
        key = "test_template_inert_setting"
        ConfigCache.put(
            key,
            "version: 2\ntemplate:\n  - target\nurls:\n  - json://user:pass@fixed.example/:\n      to: ${TARGET}\n",
            "yaml",
        )

        entry = self.client.get(f"/json/urls/{key}").json()["urls"][0]
        assert entry["template"] == {"target": None}
        assert "to=" not in entry["url"]
        ConfigCache.clear(key)

    def test_listing_shows_each_marker(self):
        """A caller can find and replace every value the entry needs.

        Review and mobile clients read this listing, find URL markers, and
        fill them in before sending. A missing marker would silently drop a
        setting, so check each supported shape:

        - a setting the service reads as a list, such as email recipients
        - a setting stored under a different name, such as mailto's smtp
        - a header, which arrives grouped with the other headers
        - a port
        - one name used by two different settings
        """
        cases = {
            "list_setting": (
                "urls:\n  - mailto://user:pass@gmail.com:\n      to: ${TARGET}\n",
                ["to=${TARGET}"],
            ),
            "renamed_setting": (
                "urls:\n  - mailto://user:pass@gmail.com:\n      smtp: ${TARGET}\n",
                ["smtp=${TARGET}"],
            ),
            "grouped_setting": (
                "urls:\n  - json://localhost:\n      '+X-Token': ${TARGET}\n",
                ["${TARGET}"],
            ),
            "port_setting": (
                "urls:\n  - mailto://user:pass@gmail.com:\n      port: ${TARGET}\n",
                [":${TARGET}"],
            ),
            "two_settings": (
                "urls:\n  - mailto://user:pass@gmail.com:\n      smtp: ${TARGET}\n      cc: ${TARGET}\n",
                ["smtp=${TARGET}", "cc=${TARGET}"],
            ),
        }

        for name, (urls, expected) in cases.items():
            key = f"test_template_shape_{name}"
            ConfigCache.put(key, "version: 2\ntemplate:\n  - target\n" + urls, "yaml")

            for query in ("", "?privacy=1"):
                entry = self.client.get(f"/json/urls/{key}{query}").json()["urls"][0]
                assert entry["template"] == {"target": None}
                for marker in expected:
                    assert marker in entry["url"], (name, query, entry["url"])

                # A marker is not a value yet; leaving it escaped would give
                # a caller nothing to search for
                assert "%24%7B" not in entry["url"]

            ConfigCache.clear(key)

    def test_listed_url_reloads_once_its_values_are_filled_in(self):
        """A filled-in URL reaches the same setting the configuration did."""
        key = "test_template_reload"
        ConfigCache.put(
            key,
            "version: 2\ntemplate:\n  - target\nurls:\n  - mailto://user:pass@gmail.com:\n      smtp: ${TARGET}\n",
            "yaml",
        )

        entry = self.client.get(f"/json/urls/{key}").json()["urls"][0]
        ConfigCache.clear(key)

        filled = entry["url"].replace("${TARGET}", "mail.example.com")
        service = apprise.Apprise.instantiate(filled)
        assert service.smtp_host == "mail.example.com"

    @override_settings(APPRISE_AUTH_REQUIRED=True)
    def test_public_caller_cannot_discover_variables(self):
        """A public caller may send but cannot list template names.

        The configuration author must share the names separately.
        """
        state = Mock(public=True, disabled=False, access="public", config_locked=True)
        with patch("api.views.Authentication.config_state", return_value=state):
            listing = self.client.get(
                f"/json/urls/{self.key}",
                headers={"accept": "application/json"},
            )
            default_listing = self.client.get(
                f"/json/urls/{self.key}?privacy=1",
                headers={"accept": "application/json"},
            )
            stored = self.client.post(
                f"/get/{self.key}",
                headers={"accept": "application/json"},
            )

        for response in (listing, default_listing, stored):
            assert response.status_code == 401
            body = response.content.decode("utf-8")
            assert "TOKEN" not in body
            assert "HOST_NAME" not in body


class NotifyLogLevelTests(SimpleTestCase):
    """How much detail a caller is allowed to see back."""

    def setUp(self):
        self.key = "test_log_level"
        ConfigCache.clear_auth(self.key)
        ConfigCache.put(self.key, "urls:\n  - json://localhost/\n", "yaml")

    def tearDown(self):
        ConfigCache.clear(self.key)
        ConfigCache.clear_auth(self.key)

    def levels(self, **headers):
        """Return the log level chosen for a request."""
        from django.test import RequestFactory

        from ..views import _notify_log_level

        request = RequestFactory().post("/notify/" + self.key, headers=headers)
        return request, _notify_log_level

    def test_debug_without_auth(self):
        """A simple single-user server behaves as it always did."""
        request, resolve = self.levels(**{"X-Apprise-Log-Level": "debug"})
        with override_settings(APPRISE_AUTH_REQUIRED=False):
            assert resolve(request, self.key) == logging.DEBUG

    def test_admin_debug(self):
        """An admin can already read the configuration."""
        request, resolve = self.levels(**{"X-Apprise-Log-Level": "debug"})
        request.apprise_auth_permission = "admin"
        with override_settings(APPRISE_AUTH_REQUIRED=True):
            assert resolve(request, self.key) == logging.DEBUG

    def test_owner_debug(self):
        """A configuration with ``user`` access may return detailed logs."""
        request, resolve = self.levels(**{"X-Apprise-Log-Level": "debug"})
        request.apprise_auth_permission = "user"
        with override_settings(APPRISE_AUTH_REQUIRED=True):
            assert resolve(request, self.key) == logging.DEBUG

    def test_locked_owner_debug_limited(self):
        """Credentials do not grant detailed logs to ``locked`` access."""
        request, resolve = self.levels(**{"X-Apprise-Log-Level": "debug"})
        request.apprise_auth_permission = "user"
        ConfigCache.set_auth(self.key, "owner", "secret", access="locked")
        with override_settings(APPRISE_AUTH_REQUIRED=True):
            assert resolve(request, self.key) == logging.INFO

    def test_public_owner_trace_limited(self):
        """Credentials do not grant detailed logs to ``public`` access."""
        request, resolve = self.levels(**{"X-Apprise-Log-Level": "trace"})
        request.apprise_auth_permission = "user"
        ConfigCache.set_auth(self.key, "owner", "secret", access="public")
        with override_settings(APPRISE_AUTH_REQUIRED=True):
            assert resolve(request, self.key) == logging.INFO

    def test_public_debug_limited(self):
        """Limit public callers to logs that do not expose configuration."""
        request, resolve = self.levels(**{"X-Apprise-Log-Level": "debug"})
        with override_settings(APPRISE_AUTH_REQUIRED=True):
            assert resolve(request, self.key) == logging.INFO

    def test_public_trace_limited(self):
        """Trace is more revealing than debug, not less."""
        request, resolve = self.levels(**{"X-Apprise-Log-Level": "trace"})
        with override_settings(APPRISE_AUTH_REQUIRED=True):
            assert resolve(request, self.key) == logging.INFO

    def test_ordinary_level(self):
        """Nothing detailed was asked for, so nothing is changed."""
        request, resolve = self.levels(**{"X-Apprise-Log-Level": "warning"})
        with override_settings(APPRISE_AUTH_REQUIRED=True):
            assert resolve(request, self.key) == logging.WARNING

    def test_stateless_debug(self):
        """Without a stored configuration there is nothing to protect."""
        request, resolve = self.levels(**{"X-Apprise-Log-Level": "debug"})
        with override_settings(APPRISE_AUTH_REQUIRED=True):
            assert resolve(request, None) == logging.DEBUG

    @patch("apprise.Apprise.notify")
    def test_json_null_template(self, mock_notify):
        """An explicit null means no values were supplied."""
        mock_notify.return_value = True

        response = self.client.post(
            f"/notify/{self.key}",
            data=dumps({"body": "test", "template": None}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert mock_notify.call_args.kwargs["template"] is None

    @patch("apprise.Apprise.notify")
    def test_json_invalid_name(self, mock_notify):
        """A name that could never be declared is refused outright."""
        for name in ("bad-name", "", "x" * 33, "has space", "tok\n"):
            response = self.client.post(
                f"/notify/{self.key}",
                data=dumps({"body": "test", "template": {name: "v"}}),
                content_type="application/json",
                headers={"accept": "application/json"},
            )

            assert response.status_code == 400, name

        assert mock_notify.call_count == 0

    @patch("apprise.Apprise.notify")
    def test_form_invalid_field_name(self, mock_notify):
        """A form reports a bad name rather than passing over it.

        A JSON payload already refuses one, so the two behave alike.
        """
        for field in ("template[bad-name]", "template[]", f"template[{'x' * 33}]"):
            response = self.client.post(
                f"/notify/{self.key}",
                {"body": "test", field: "value"},
                headers={"accept": "application/json"},
            )

            assert response.status_code == 400, field

        assert mock_notify.call_count == 0

    @patch("apprise.Apprise.notify")
    def test_form_invalid_value(self, mock_notify):
        """A form value is checked the same way a JSON one is."""
        for value in ("a\nb", "x" * 2000):
            response = self.client.post(
                f"/notify/{self.key}",
                {"body": "test", "template[token]": value},
                headers={"accept": "application/json"},
            )

            assert response.status_code == 400

        assert mock_notify.call_count == 0
