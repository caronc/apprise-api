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
from json import dumps, loads
import logging
import socket
from tempfile import TemporaryDirectory
import threading
from unittest import mock
from unittest.mock import patch

import apprise
from django.test import SimpleTestCase
from django.test.utils import override_settings
import requests

from ..urlfilter import AppriseURLFilter
from ..utils import ConfigCache
from ..views import own_logs

_MASTER_TOKEN = base64.b64encode(b"master:pass").decode()


def _resolves_to(address):
    """Make a DNS lookup answer with one IPv4 address."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]


def _download(mock_get, payload=b"", headers=None, error=None):
    """Make the mocked web request return ``payload``, or raise ``error``."""
    response = mock.Mock()
    response.status_code = requests.codes.ok
    response.raise_for_status.side_effect = error
    response.headers = headers or {}
    response.iter_content.return_value = iter([payload])
    response.__enter__ = lambda s, *a, **kw: response
    response.__exit__ = mock.Mock(return_value=False)
    mock_get.return_value = response


class AttachPreviewTests(SimpleTestCase):
    """Preview an attachment web address the way a send would download it."""

    key = "attach-preview"

    def setUp(self):
        """Save a configuration and keep downloads in a temporary directory."""
        self.assertTrue(ConfigCache.put(self.key, "json://localhost", "text"))
        self.tmp_dir = TemporaryDirectory()
        self.attach_dir = override_settings(APPRISE_ATTACH_DIR=self.tmp_dir.name)
        self.attach_dir.enable()

    def tearDown(self):
        """Remove the configuration and the downloads."""
        self.attach_dir.disable()
        self.tmp_dir.cleanup()
        ConfigCache.clear(self.key)
        ConfigCache.clear_auth(self.key)

    def preview(self, payload, **kwargs):
        """Ask for a preview, returning the response."""
        return self.client.post(f"/attach/{self.key}", data=dumps(payload), content_type="application/json", **kwargs)

    @patch("apprise.utils.http.HTTPPolicySession.get")
    def test_preview_returns_the_downloaded_file(self, mock_get):
        """The file comes back with its own type and name in headers, never as a page."""
        headers = {"Content-Type": "image/png", "Content-Disposition": 'attachment; filename="cam.png"'}
        _download(mock_get, b"PNGDATA", headers)
        response = self.preview({"url": "https://example.com/camera/live?image=png"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"PNGDATA")
        self.assertEqual(response["Content-Type"], "application/octet-stream")
        self.assertEqual(response["X-Apprise-Content-Type"], "image/png")
        self.assertEqual(response["X-Apprise-Filename"], "cam.png")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Content-Security-Policy"], "sandbox")

        # The web address itself is requested, not a stored copy
        self.assertEqual(mock_get.call_args.args[0], "https://example.com/camera/live")

    @patch("apprise.utils.http.HTTPPolicySession.get")
    def test_preview_passes_on_only_a_plain_file_type(self, mock_get):
        """A file type that is not one plain type is passed on as plain bytes."""
        for given, shown in (
            ("Video/MP4; codecs=avc1", "video/mp4"),
            ("video/mp4, text/html", "application/octet-stream"),
            ("text/html video/mp4", "application/octet-stream"),
            ("", "application/octet-stream"),
        ):
            _download(mock_get, b"DATA", {"Content-Type": given})
            response = self.preview({"url": "https://example.com/clip"})
            response.close()
            self.assertEqual(response["X-Apprise-Content-Type"], shown, given)

    def test_preview_page_only_plays_known_sound_and_video_types(self):
        """The page plays only sound and video types it names; others are not given to the browser."""
        html = self.client.get(f"/cfg/{self.key}", headers={"Accept": "text/html"}).content.decode()
        self.assertIn("NOTIFY_PREVIEW_MEDIA.test(type)", html)
        self.assertNotIn("type.startsWith('video/')", html)
        # Without a saved configuration the preview buttons are hidden
        self.assertIn("attachRows.classList.add('notify-attach-no-preview')", html)
        # Attachment addresses show their passwords hidden until clicked into
        self.assertIn("input.value = notifyAttachMasked(notifyAttachFull.get(input));", html)
        # The copy message keeps its quotes inside the attribute
        self.assertIn('data-copy-label="Apprise configuration &#x27;include&#x27; entry copied to clipboard"', html)

    @patch("apprise.utils.http.HTTPPolicySession.get")
    def test_preview_reports_why_a_download_failed(self, mock_get):
        """A failed download says so, with what the download reported."""
        _download(mock_get, error=requests.HTTPError("404 Client Error: Not Found for url: https://example.com/a.png"))
        response = self.preview({"url": "https://example.com/a.png"})
        self.assertEqual(response.status_code, 424)
        content = loads(response.content)
        self.assertTrue(content["error"])
        self.assertTrue(any("404" in line for line in content["logs"]))

    def test_preview_refuses_what_a_send_would_refuse(self):
        """Local files, refused addresses and unusable requests are turned away."""
        for url in ("/etc/passwd", "file:///etc/passwd", "http://localhost/x.png", "http://127.0.0.1/x.png", None):
            self.assertEqual(self.preview({"url": url}).status_code, 424, url)

        with override_settings(APPRISE_ATTACH_SIZE=0):
            self.assertEqual(self.preview({"url": "https://example.com/a.png"}).status_code, 424)

        # Only JSON is read
        path = f"/attach/{self.key}"
        self.assertEqual(self.client.post(path, data={"url": "https://example.com/a.png"}).status_code, 400)
        self.assertEqual(self.client.post(path, data="[]", content_type="application/json").status_code, 400)

        with override_settings(APPRISE_STATEFUL_MODE="disabled"):
            self.assertEqual(self.preview({"url": "https://example.com/a.png"}).status_code, 403)

    def test_preview_explains_a_local_network_address(self):
        """A local address is refused, naming the setting that would allow it and where to read more."""
        with patch("socket.getaddrinfo", return_value=_resolves_to("192.168.1.20")):
            response = self.preview({"url": "http://camera.example/snapshot.jpg"})

        self.assertEqual(response.status_code, 424)
        content = loads(response.content)
        self.assertEqual(content["reason"], "internal")
        self.assertEqual(content["setting"], "APPRISE_ATTACH_PREVIEW_INTERNAL")
        self.assertEqual(content["help"], "https://appriseit.com/api/deployment/#blocked-attachment-addresses")
        self.assertIn("logs", content)

        # When the reject list itself blocks the local network, that is the setting named
        blocked = AppriseURLFilter("*", "internal")
        with (
            patch("api.utils.ATTACH_URL_FILTER", blocked),
            patch("api.utils.ATTACH_PREVIEW_FILTER", blocked),
            patch("socket.getaddrinfo", return_value=_resolves_to("10.0.0.5")),
        ):
            content = loads(self.preview({"url": "http://camera.example/snapshot.jpg"}).content)

        self.assertEqual((content["reason"], content["setting"]), ("internal", "APPRISE_ATTACH_REJECT_URL"))

    def test_preview_explains_an_address_the_rules_refuse(self):
        """An address the reject or allow list refuses names the list to change."""
        for rules, setting in (
            (AppriseURLFilter("*", "example.com"), "APPRISE_ATTACH_REJECT_URL"),
            (AppriseURLFilter("other.example", ""), "APPRISE_ATTACH_ALLOW_URL"),
        ):
            with patch("api.utils.ATTACH_PREVIEW_FILTER", rules):
                content = loads(self.preview({"url": "https://example.com/a.png"}).content)

            self.assertEqual((content["reason"], content["setting"]), ("rules", setting))

    @patch("apprise.utils.http.HTTPPolicySession.get")
    def test_preview_of_a_host_that_cannot_be_found_tries_the_download(self, mock_get):
        """A host with no address is not explained as a rule; the download reports it."""
        _download(mock_get, b"DATA")
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("name not known")):
            response = self.preview({"url": "https://example.com/a.png"})

        response.close()
        self.assertEqual(response.status_code, 200)

    def test_preview_logs_hold_only_this_request(self):
        """Log lines written by another request at the same time are left out."""

        def download(*args, **kwargs):
            # Another request logs while this one downloads.
            other = threading.Thread(target=lambda: apprise.logger.error("OTHER REQUEST SECRET"))
            other.start()
            other.join()
            apprise.logger.error("This request failed")
            raise requests.ConnectionError("refused")

        with (
            patch("apprise.utils.http.HTTPPolicySession.get", side_effect=download),
            patch("socket.getaddrinfo", return_value=_resolves_to("93.184.215.14")),
        ):
            content = loads(self.preview({"url": "https://example.com/a.png"}).content)

        logs = "\n".join(content["logs"])
        self.assertIn("This request failed", logs)
        self.assertNotIn("OTHER REQUEST SECRET", logs)

    def test_overlapping_log_captures_put_the_level_back(self):
        """Two captures that overlap leave the Apprise log level as it was."""
        before = apprise.logger.level
        apprise.logger.setLevel(logging.WARNING)
        try:
            # The first lowers the level; the second starts before it ends
            first, second = own_logs(logging.DEBUG), own_logs(logging.ERROR)
            first.__enter__()
            second.__enter__()
            first.__exit__(None, None, None)
            second.__exit__(None, None, None)
            self.assertEqual(apprise.logger.level, logging.WARNING)

            # A capture still running when another ends keeps all of its lines
            first, second = own_logs(logging.DEBUG), own_logs(logging.DEBUG)
            first.__enter__()
            kept = second.__enter__()
            first.__exit__(None, None, None)
            apprise.logger.debug("Still wanted")
            second.__exit__(None, None, None)
            self.assertIn("Still wanted", kept.getvalue())
            self.assertEqual(apprise.logger.level, logging.WARNING)

        finally:
            apprise.logger.setLevel(before)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_preview_needs_access_to_the_configuration(self):
        """Callers without access to the configuration are refused."""
        self.assertTrue(ConfigCache.set_auth(self.key, "alice", "secret", access="user"))
        self.assertEqual(self.preview({"url": "https://example.com/a.png"}).status_code, 401)

    def test_preview_needs_a_saved_configuration(self):
        """A key with nothing saved under it cannot download anything."""
        path = "/attach/attach-preview-unknown"
        payload = dumps({"url": "https://example.com/a.png"})
        self.assertEqual(self.client.post(path, data=payload, content_type="application/json").status_code, 404)

        # A configuration that cannot be read is an error
        with patch("api.views.ConfigCache.get", return_value=(None, None)):
            self.assertEqual(self.preview({"url": "https://example.com/a.png"}).status_code, 500)
