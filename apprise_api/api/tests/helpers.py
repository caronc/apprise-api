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
import errno
import os
from os.path import join
from tempfile import TemporaryDirectory
from unittest import mock

import apprise
from django.test.utils import override_settings

LOCAL_FILE_MARKER = "TOP-SECRET-4f8f9c9b-do-not-leak"


def notify_result(success=True):
    """Build a minimal notification result for mocked Apprise calls."""
    status = apprise.AppriseResultStatus.SUCCESS if success else apprise.AppriseResultStatus.FAILURE
    return apprise.AppriseResult(status=status, results=[])


class LocalFileFixtureMixin:
    """Provide a real server file for attachment disclosure tests."""

    def setUp(self):
        super().setUp()
        self.tmp_dir = TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.secret_marker = LOCAL_FILE_MARKER
        self.secret_path = join(self.tmp_dir.name, "secret.txt")
        with open(self.secret_path, "w") as stream:
            stream.write(self.secret_marker)

    def local_file_variants(self):
        """Return common spellings of a local file reference."""
        unrooted = self.secret_path.lstrip("/")
        return {
            "bare path": self.secret_path,
            "file: path": f"file:{self.secret_path}",
            "file:// (two slash)": f"file://{unrooted}",
            "file:/// (three slash)": f"file://{self.secret_path}",
            "FILE:/// uppercase scheme": f"FILE://{self.secret_path}",
        }

    def assert_marker_not_leaked(self, *values):
        """Check that returned values do not contain the server-file marker."""
        for value in values:
            if value is not None:
                self.assertNotIn(self.secret_marker, value if isinstance(value, str) else str(value))

    def assert_attach_dir_clean(self, attach_dir):
        """Check that no file in an attachment directory contains the marker."""
        marker = self.secret_marker.encode()
        for root, _dirs, files in os.walk(attach_dir):
            for name in files:
                with open(join(root, name), "rb") as stream:
                    self.assertNotIn(marker, stream.read())


class NotifyAttachmentSecurityMixin(LocalFileFixtureMixin):
    """Apply unsafe attachment checks to a notification endpoint."""

    def assert_rejected(self, response, mock_notify):
        """Check the common response and ensure delivery never started."""
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Bad Attachment", response.content)
        self.assertNotIn(self.secret_marker.encode(), response.content)
        mock_notify.assert_not_called()
        mock_notify.reset_mock()

    @mock.patch("apprise.Apprise.notify")
    def test_form_rejects_local_file(self, mock_notify):
        """Reject local attachment paths submitted as form data."""
        for label, candidate in self.local_file_variants().items():
            with self.subTest(style=label):
                self.assert_rejected(self.post_attachment(candidate), mock_notify)

    @mock.patch("apprise.Apprise.notify")
    def test_json_string_rejects_local_file(self, mock_notify):
        """Reject a local path in the JSON string form."""
        for label, candidate in self.local_file_variants().items():
            with self.subTest(style=label):
                self.assert_rejected(self.post_attachment(candidate, as_json=True), mock_notify)

    @mock.patch("apprise.Apprise.notify")
    def test_json_url_rejects_local_file(self, mock_notify):
        """Reject a local path in the JSON URL form."""
        for label, candidate in self.local_file_variants().items():
            with self.subTest(style=label):
                self.assert_rejected(self.post_attachment([{"url": candidate}], as_json=True), mock_notify)

    @mock.patch("apprise.Apprise.notify")
    def test_local_files_are_not_copied(self, mock_notify):
        """Ensure rejected requests leave no copy in attachment storage."""
        with TemporaryDirectory() as attach_dir, override_settings(APPRISE_ATTACH_DIR=attach_dir):
            for candidate in self.local_file_variants().values():
                self.post_attachment(candidate, as_json=True)
                self.post_attachment([{"url": candidate}], as_json=True)

            self.assert_attach_dir_clean(attach_dir)

        mock_notify.assert_not_called()

    @mock.patch("apprise.Apprise.notify")
    def test_json_rejects_invalid_base64(self, mock_notify):
        """Malformed base64 must return a safe attachment response."""
        for value in (None, [], {}, "é", "%%%"):
            with self.subTest(value=value):
                response = self.post_attachment({"base64": value}, as_json=True)
                self.assert_rejected(response, mock_notify)

    @mock.patch("apprise.Apprise.notify")
    def test_remote_storage_error_is_caught(self, mock_notify):
        """A remote download storage failure must not escape the endpoint."""
        response = mock.MagicMock()
        response.status_code = 200
        response.headers = {}
        response.iter_content.return_value = iter([b"data"])
        response.__enter__.return_value = response
        storage_error = OSError(errno.ENOSPC, "disk full")

        with (
            mock.patch("apprise.utils.http.HTTPPolicySession.get", return_value=response),
            mock.patch("apprise.attachment.http.NamedTemporaryFile", side_effect=storage_error),
        ):
            result = self.post_attachment("https://example.com/file.txt", as_json=True)

        self.assert_rejected(result, mock_notify)
        self.assertNotIn(b"disk full", result.content)
