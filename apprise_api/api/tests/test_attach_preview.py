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
from tempfile import TemporaryDirectory
from unittest import mock
from unittest.mock import patch

from django.test import SimpleTestCase
from django.test.utils import override_settings
import requests

from ..utils import ConfigCache

_MASTER_TOKEN = base64.b64encode(b"master:pass").decode()


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
