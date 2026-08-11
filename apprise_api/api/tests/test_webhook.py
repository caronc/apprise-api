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
import io
from json import loads
from unittest import mock

from django.test import SimpleTestCase
from django.test.utils import override_settings
import requests

from ..utils import send_webhook


class WebhookTests(SimpleTestCase):
    @mock.patch("requests.post")
    def test_webhook_spools_large_payload(self, mock_post):
        """Chunked webhook JSON is prepared on disk and read by requests."""
        captured = {}

        def post(_url, **kwargs):
            """Read the file while send_webhook still owns it."""
            captured["payload"] = loads(kwargs["data"].read())
            response = mock.Mock()
            response.status_code = requests.codes.ok
            return response

        mock_post.side_effect = post

        chunks = iter(("{", '"status":0,', '"output":[]', "}"))
        with override_settings(APPRISE_WEBHOOK_URL="https://localhost/webhook"):
            send_webhook(chunks)

        assert captured["payload"] == {"status": 0, "output": []}

    @mock.patch("requests.post")
    def test_webhook_contains_spool_failures(self, mock_post):
        """Webhook buffering failures are logged without making a request."""
        with (
            mock.patch(
                "apprise_api.api.utils.tempfile.TemporaryFile",
                side_effect=OSError("disk unavailable"),
            ),
            override_settings(APPRISE_WEBHOOK_URL="https://localhost/webhook"),
            self.assertLogs("django", level="WARNING"),
        ):
            send_webhook(iter(("{}",)))

        mock_post.assert_not_called()

        class ShortWriteFile(io.BytesIO):
            """Report that only part of the first chunk was written."""

            def write(self, value):
                super().write(value[:-1])
                return len(value) - 1

        with (
            mock.patch(
                "apprise_api.api.utils.tempfile.TemporaryFile",
                return_value=ShortWriteFile(),
            ),
            override_settings(APPRISE_WEBHOOK_URL="https://localhost/webhook"),
            self.assertLogs("django", level="WARNING"),
        ):
            send_webhook(iter(("{}",)))

        mock_post.assert_not_called()

    @mock.patch("requests.post")
    def test_webhook_contains_mapping_serialization_failure(self, mock_post):
        """Invalid mapping content is reported without making a request."""
        payload = {"unsupported": object()}

        with (
            override_settings(APPRISE_WEBHOOK_URL="https://localhost/webhook"),
            self.assertLogs("django", level="WARNING"),
        ):
            # JSON preparation may fail before any network work begins.
            send_webhook(payload)

        mock_post.assert_not_called()

    @mock.patch("requests.post")
    def test_webhook_contains_cleanup_failure(self, mock_post):
        """A close error is reported after a buffered webhook is sent."""

        class CloseFailFile(io.BytesIO):
            """Store normally but fail the first cleanup request."""

            failed = False

            def close(self):
                if not self.failed:
                    self.failed = True
                    raise OSError("close failed")

                super().close()

        spool = CloseFailFile()

        def post(_url, **kwargs):
            """Read the complete request while the temporary file is open."""
            assert loads(kwargs["data"].read()) == {}
            response = mock.Mock()
            response.status_code = requests.codes.ok
            return response

        mock_post.side_effect = post

        with (
            mock.patch(
                "apprise_api.api.utils.tempfile.TemporaryFile",
                return_value=spool,
            ),
            override_settings(APPRISE_WEBHOOK_URL="https://localhost/webhook"),
            self.assertLogs("django", level="WARNING"),
        ):
            send_webhook(iter(("{}",)))

        mock_post.assert_called_once()
        # Finish cleanup after exercising the contained first failure.
        spool.close()

    @mock.patch("requests.post")
    def test_webhook_testing(self, mock_post):
        """
        Test webhook handling
        """

        # Response object
        response = mock.Mock()
        response.status_code = requests.codes.ok
        mock_post.return_value = response

        with override_settings(APPRISE_WEBHOOK_URL="https://user:pass@localhost/webhook"):
            send_webhook({})
            assert mock_post.call_count == 1

            details = mock_post.call_args_list[0]
            assert details[0][0] == "https://localhost/webhook"
            assert loads(details[1]["data"]) == {}
            assert "User-Agent" in details[1]["headers"]
            assert "Content-Type" in details[1]["headers"]
            assert details[1]["headers"]["User-Agent"] == "Apprise-API"
            assert details[1]["headers"]["Content-Type"] == "application/json"
            assert details[1]["auth"] == ("user", "pass")
            assert details[1]["verify"] is True
            assert details[1]["params"] == {}
            assert details[1]["timeout"] == (4.0, 4.0)

        mock_post.reset_mock()

        with override_settings(
            APPRISE_WEBHOOK_URL="http://user@localhost/webhook/here?verify=False&key=value&cto=2.0&rto=1.0"
        ):
            send_webhook({})
            assert mock_post.call_count == 1

            details = mock_post.call_args_list[0]
            assert details[0][0] == "http://localhost/webhook/here"
            assert loads(details[1]["data"]) == {}
            assert "User-Agent" in details[1]["headers"]
            assert "Content-Type" in details[1]["headers"]
            assert details[1]["headers"]["User-Agent"] == "Apprise-API"
            assert details[1]["headers"]["Content-Type"] == "application/json"
            assert details[1]["auth"] == ("user", None)
            assert details[1]["verify"] is False
            assert details[1]["params"] == {"key": "value"}
            assert details[1]["timeout"] == (2.0, 1.0)

        mock_post.reset_mock()

        with override_settings(APPRISE_WEBHOOK_URL="invalid"):
            # Invalid webhook defined
            send_webhook({})
            assert mock_post.call_count == 0

        mock_post.reset_mock()

        with override_settings(APPRISE_WEBHOOK_URL=None):
            # Invalid webhook defined
            send_webhook({})
            assert mock_post.call_count == 0

        mock_post.reset_mock()

        with override_settings(APPRISE_WEBHOOK_URL="http://$#@"):
            # Invalid hostname defined
            send_webhook({})
            assert mock_post.call_count == 0

        mock_post.reset_mock()

        with (
            mock.patch("apprise_api.api.utils.apprise.URLBase.parse_url", return_value=None),
            override_settings(APPRISE_WEBHOOK_URL="https://localhost/webhook"),
        ):
            # parse_url returns None (unparseable despite valid schema)
            send_webhook({})
            assert mock_post.call_count == 0

        mock_post.reset_mock()

        with override_settings(APPRISE_WEBHOOK_URL="invalid://hostname"):
            # Invalid webhook defined
            send_webhook({})
            assert mock_post.call_count == 0

        mock_post.reset_mock()

        # A valid URL with a bad server response:
        response.status_code = requests.codes.internal_server_error
        mock_post.return_value = response
        with override_settings(APPRISE_WEBHOOK_URL="http://localhost"):
            with self.assertLogs("django", level="WARNING"):
                send_webhook({})
            assert mock_post.call_count == 1

        mock_post.reset_mock()

        # A valid URL with a bad server response:
        mock_post.return_value = None
        mock_post.side_effect = requests.Timeout("timed out")
        with override_settings(APPRISE_WEBHOOK_URL="http://localhost"):
            with self.assertLogs("django", level="WARNING"):
                send_webhook({})
            assert mock_post.call_count == 1
