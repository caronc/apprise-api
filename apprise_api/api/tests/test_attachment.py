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
from contextlib import suppress
import errno
import io
import os
from os.path import dirname, getsize, join
import socket
from tempfile import TemporaryDirectory
from unittest import mock
from unittest.mock import mock_open, patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.test.utils import override_settings
from django.utils.datastructures import MultiValueDict
import requests

from .. import utils
from ..exceptions import (
    AppriseAPIImproperlyConfigured,
    AppriseAPIStorageError,
)
from ..urlfilter import AppriseURLFilter
from ..utils import Attachment, HTTPAttachment, _build_http_attachment, parse_attachments
from .helpers import LocalFileFixtureMixin

SAMPLE_FILE = join(dirname(dirname(dirname(__file__))), "static", "logo.png")


class _ChunkedUpload:
    """Small controllable stand-in for Django's UploadedFile."""

    name = "attach.bin"
    content_type = "application/octet-stream"

    def __init__(self, chunks, size=None):
        self._chunks = chunks
        self.size = size
        self.requested_chunk_size = None

    def chunks(self, chunk_size=None):
        self.requested_chunk_size = chunk_size
        yield from self._chunks


class AttachmentTests(SimpleTestCase):
    def setUp(self):
        # Prepare a temporary directory
        self.tmp_dir = TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)

        # Keep local hosts on loopback for internal-address tests. Give all
        # other hosts a public test address so the suite never uses real DNS.
        def _fake_getaddrinfo(host, *_args, **_kwargs):
            addr = "127.0.0.1" if host in ("localhost", "localhost.localdomain") else "93.184.215.14"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 0))]

        getaddrinfo_patcher = mock.patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo)
        getaddrinfo_patcher.start()
        self.addCleanup(getaddrinfo_patcher.stop)

    def test_attachment_initialization(self):
        """
        Test attachment handling
        """

        with override_settings(APPRISE_ATTACH_DIR=self.tmp_dir.name):
            with mock.patch("os.makedirs", side_effect=OSError(errno.EACCES, "denied")):
                with self.assertRaises(AppriseAPIStorageError):
                    Attachment("file")
                with self.assertRaises(AppriseAPIStorageError):
                    HTTPAttachment("web")

            with (
                mock.patch("tempfile.mkstemp", side_effect=OSError(errno.ENOSPC, "full")),
                self.assertRaises(AppriseAPIStorageError),
            ):
                Attachment("file")

            with mock.patch("os.remove", side_effect=FileNotFoundError):
                a = Attachment("file")
                # Force __del__ call to throw an exception which we gracefully
                # handle
                del a

                a = HTTPAttachment("web")
                assert a.filename == "web"
                # Force __del__ call to throw an exception which we gracefully
                # handle
                del a

            a = Attachment("file")
            assert a.filename

            # Test with an explicit path already provided (covers the
            # 'if not path:' False branch — mkstemp is skipped)
            import tempfile

            fd, explicit_path = tempfile.mkstemp(dir=self.tmp_dir.name)
            os.close(fd)
            a = Attachment("explicit.txt", path=explicit_path, delete=False)
            assert a._path == explicit_path

    def test_http_session_closes_on_storage_error(self):
        """A failed constructor must release its HTTP session immediately."""
        error = OSError(errno.EACCES, "denied")
        with (
            override_settings(APPRISE_ATTACH_DIR=self.tmp_dir.name),
            mock.patch.object(utils, "HTTPPolicySession") as session_type,
            mock.patch("os.makedirs", side_effect=error),
            self.assertRaises(AppriseAPIStorageError) as caught,
        ):
            HTTPAttachment("web")

        self.assertEqual(caught.exception.errno, error.errno)
        self.assertIs(caught.exception.__cause__, error)
        session_type.return_value.close.assert_called_once_with()

    def test_storage_error_survives_cleanup_failure(self):
        """Session cleanup must not hide the attachment storage error."""
        storage_error = OSError(errno.ENOSPC, "full")
        with (
            override_settings(APPRISE_ATTACH_DIR=self.tmp_dir.name),
            mock.patch.object(utils, "HTTPPolicySession") as session_type,
            mock.patch("os.makedirs", side_effect=storage_error),
            self.assertRaises(AppriseAPIStorageError) as caught,
        ):
            session_type.return_value.close.side_effect = OSError("close failed")
            HTTPAttachment("web")

        self.assertIs(caught.exception.__cause__, storage_error)
        session_type.return_value.close.assert_called_once_with()

    def test_close_failure_removes_partial_file(self):
        """A descriptor close failure must remove its temporary file."""
        descriptor, path = utils.tempfile.mkstemp(dir=self.tmp_dir.name)

        def close_always_fails(fd):
            raise OSError(errno.EIO, "close failed")

        with (
            override_settings(APPRISE_ATTACH_DIR=self.tmp_dir.name),
            mock.patch("tempfile.mkstemp", return_value=(descriptor, path)),
            mock.patch("os.close", side_effect=close_always_fails) as mock_close,
            self.assertRaises(AppriseAPIStorageError) as caught,
        ):
            Attachment("file")

        self.assertEqual(caught.exception.errno, errno.EIO)
        self.assertFalse(os.path.exists(path))

        # Do not retry: another thread may already own this descriptor.
        mock_close.assert_called_once_with(descriptor)
        os.close(descriptor)

    def test_form_file_attachment_parsing(self):
        """
        Test the parsing of file attachments
        """
        # Variation tests without any data
        result = parse_attachments(None, None)
        assert isinstance(result, list)
        assert len(result) == 0

        result = parse_attachments([], [])
        assert isinstance(result, list)
        assert len(result) == 0

        with override_settings(APPRISE_ATTACH_SIZE=0):
            result = parse_attachments(None, None)
            assert isinstance(result, list)
            assert len(result) == 0

            result = parse_attachments([], [])
            assert isinstance(result, list)
            assert len(result) == 0

        # Get ourselves a file to work with
        files_request = {"file1": SimpleUploadedFile("attach.txt", b"content here", content_type="text/plain")}
        result = parse_attachments(None, files_request)
        assert isinstance(result, list)
        assert len(result) == 1

        # Test case where no filename was specified
        files_request = {"file1": SimpleUploadedFile("    ", b"content here", content_type="text/plain")}
        result = parse_attachments(None, files_request)
        assert isinstance(result, list)
        assert len(result) == 1

        # Test our case where we throw an error trying to open/read/write our
        # attachment to disk
        m = mock_open()
        m.side_effect = OSError()
        with patch("builtins.open", m), self.assertRaises(AppriseAPIStorageError):
            parse_attachments(None, files_request)

        # Test a case where our attachment exceeds the maximum size we allow
        # for
        with override_settings(APPRISE_ATTACH_SIZE=1):
            files_request = {
                "file1": SimpleUploadedFile(
                    "attach.txt",
                    # More than the configured one-byte limit.
                    ("content" * 1024 * 1024).encode("utf-8"),
                    content_type="text/plain",
                )
            }
            with self.assertRaises(AppriseAPIImproperlyConfigured):
                parse_attachments(None, files_request)

        # Disabled attachment support rejects uploaded files.
        with override_settings(APPRISE_ATTACH_SIZE=0):
            files_request = {
                "file1": SimpleUploadedFile(
                    "attach.txt",
                    # Content size does not matter while support is disabled.
                    ("content" * 1024 * 1024).encode("utf-8"),
                    content_type="text/plain",
                )
            }
            with self.assertRaises(AppriseAPIImproperlyConfigured):
                parse_attachments(None, files_request)

        # Bad data provided in filename field
        files_request = {"file1": SimpleUploadedFile(None, b"content here", content_type="text/plain")}
        with self.assertRaises(AppriseAPIImproperlyConfigured):
            parse_attachments(None, files_request)

    def test_form_upload_streams_chunks_at_exact_limit(self):
        """Chunked uploads may reach, but never exceed, the byte limit."""
        upload = _ChunkedUpload(
            (b"ab", b"", bytearray(b"cd"), memoryview(b"ef")),
            size=None,
        )
        with (
            override_settings(
                APPRISE_ATTACH_DIR=self.tmp_dir.name,
                APPRISE_ATTACH_SIZE=6,
            ),
        ):
            result = parse_attachments(None, {"file1": upload})

        self.assertEqual(result[0].size, 6)
        self.assertEqual(upload.requested_chunk_size, 7)

    def test_form_upload_rejects_advertised_oversize_before_read(self):
        """Reliable size metadata avoids unnecessary reads and disk writes."""
        upload = _ChunkedUpload((b"unused",), size=5)
        upload.chunks = mock.Mock(side_effect=AssertionError("must not read"))

        with (
            override_settings(
                APPRISE_ATTACH_DIR=self.tmp_dir.name,
                APPRISE_ATTACH_SIZE=4,
            ),
            self.assertRaisesRegex(
                AppriseAPIImproperlyConfigured,
                "file size is too large",
            ),
        ):
            parse_attachments(None, {"file1": upload})

        upload.chunks.assert_not_called()
        self.assertEqual(os.listdir(self.tmp_dir.name), [])

    def test_form_upload_rejects_understated_oversize_during_stream(self):
        """The streamed byte count overrides dishonest size metadata."""
        upload = _ChunkedUpload((b"ab", b"cde"), size=1)

        sizes_before_cleanup = []

        def remove_partial(path):
            sizes_before_cleanup.append(os.path.getsize(path))
            os.unlink(path)

        with (
            override_settings(
                APPRISE_ATTACH_DIR=self.tmp_dir.name,
                APPRISE_ATTACH_SIZE=4,
            ),
            patch("os.remove", side_effect=remove_partial),
            self.assertRaisesRegex(
                AppriseAPIImproperlyConfigured,
                "file size is too large",
            ),
        ):
            parse_attachments(None, {"file1": upload})

        self.assertEqual(sizes_before_cleanup, [2])
        self.assertEqual(os.listdir(self.tmp_dir.name), [])

    def test_form_upload_supports_bounded_file_reads(self):
        """Legacy file-like uploads are read in bounded pieces."""

        class FileUpload(io.BytesIO):
            name = "attach.txt"
            content_type = "text/plain"

        upload = FileUpload(b"content")
        with override_settings(
            APPRISE_ATTACH_DIR=self.tmp_dir.name,
            APPRISE_ATTACH_SIZE=7,
        ):
            result = parse_attachments(None, {"file1": upload})

        self.assertEqual(result[0].size, 7)

    def test_form_upload_rejects_missing_reader(self):
        """Malformed upload wrappers produce a normal validation error."""

        class InvalidUpload:
            name = "attach.txt"
            content_type = "text/plain"

        with (
            override_settings(
                APPRISE_ATTACH_DIR=self.tmp_dir.name,
                APPRISE_ATTACH_SIZE=8,
            ),
            self.assertRaisesRegex(
                AppriseAPIImproperlyConfigured,
                "Invalid file content",
            ),
        ):
            parse_attachments(None, {"file1": InvalidUpload()})

        self.assertEqual(os.listdir(self.tmp_dir.name), [])

    def test_form_upload_rejects_non_binary_chunk(self):
        """Only bytes-like chunks can be written to a binary attachment."""
        upload = _ChunkedUpload(("not bytes",), size=None)

        with (
            override_settings(
                APPRISE_ATTACH_DIR=self.tmp_dir.name,
                APPRISE_ATTACH_SIZE=32,
            ),
            self.assertRaisesRegex(
                AppriseAPIImproperlyConfigured,
                "Invalid file content",
            ),
        ):
            parse_attachments(None, {"file1": upload})

        self.assertEqual(os.listdir(self.tmp_dir.name), [])

    def test_form_upload_normalizes_reader_value_error(self):
        """Invalid reader state is reported as invalid upload content."""

        class InvalidUpload:
            name = "attach.txt"
            content_type = "text/plain"

            def read(self, _size):
                raise ValueError("closed upload")

        with (
            override_settings(
                APPRISE_ATTACH_DIR=self.tmp_dir.name,
                APPRISE_ATTACH_SIZE=32,
            ),
            self.assertRaisesRegex(
                AppriseAPIImproperlyConfigured,
                "Invalid file content",
            ),
        ):
            parse_attachments(None, {"file1": InvalidUpload()})

        self.assertEqual(os.listdir(self.tmp_dir.name), [])

    def test_form_upload_normalizes_reader_io_error(self):
        """Upload read failures use the same safe response as disk failures."""

        class UnreadableUpload:
            name = "attach.txt"
            content_type = "text/plain"

            def read(self, _size):
                raise OSError(errno.EIO, "read failed")

        with (
            override_settings(
                APPRISE_ATTACH_DIR=self.tmp_dir.name,
                APPRISE_ATTACH_SIZE=32,
            ),
            self.assertRaisesRegex(
                AppriseAPIStorageError,
                "Could not read or write",
            ) as caught,
        ):
            parse_attachments(None, {"file1": UnreadableUpload()})

        self.assertEqual(caught.exception.errno, errno.EIO)
        self.assertIsInstance(caught.exception.__cause__, OSError)
        self.assertEqual(os.listdir(self.tmp_dir.name), [])

    def test_form_upload_normalizes_disk_full_and_cleans_up(self):
        """Disk exhaustion returns a safe error and removes the placeholder."""
        upload = _ChunkedUpload((b"content",), size=None)
        disk_full = OSError(errno.ENOSPC, "disk full")

        with (
            override_settings(
                APPRISE_ATTACH_DIR=self.tmp_dir.name,
                APPRISE_ATTACH_SIZE=32,
            ),
            patch.object(utils, "open", create=True, side_effect=disk_full),
            self.assertRaisesRegex(
                AppriseAPIStorageError,
                "Could not read or write",
            ) as caught,
        ):
            parse_attachments(None, {"file1": upload})

        self.assertEqual(caught.exception.errno, errno.ENOSPC)
        self.assertIs(caught.exception.__cause__, disk_full)
        self.assertEqual(os.listdir(self.tmp_dir.name), [])

    def test_form_upload_rejects_short_disk_write(self):
        """A short filesystem write is handled like any other I/O failure."""
        upload = _ChunkedUpload((b"content",), size=None)
        opened = mock_open()
        opened().write.return_value = 1

        with (
            override_settings(
                APPRISE_ATTACH_DIR=self.tmp_dir.name,
                APPRISE_ATTACH_SIZE=32,
            ),
            patch.object(utils, "open", opened, create=True),
            self.assertRaisesRegex(
                AppriseAPIStorageError,
                "Could not read or write",
            ) as caught,
        ):
            parse_attachments(None, {"file1": upload})

        self.assertEqual(caught.exception.errno, errno.EIO)
        self.assertEqual(os.listdir(self.tmp_dir.name), [])

    def test_form_upload_preserves_unexpected_errors_after_cleanup(self):
        """Unexpected server errors propagate only after partial files are removed."""

        def broken_chunks():
            yield b"partial"
            raise RuntimeError("unexpected upload failure")

        upload = _ChunkedUpload(broken_chunks(), size=None)
        with (
            override_settings(
                APPRISE_ATTACH_DIR=self.tmp_dir.name,
                APPRISE_ATTACH_SIZE=32,
            ),
            self.assertRaisesRegex(
                AppriseAPIStorageError,
                "Could not process attachment",
            ) as caught,
        ):
            parse_attachments(None, {"file1": upload})

        self.assertIsInstance(caught.exception.__cause__, RuntimeError)
        self.assertEqual(os.listdir(self.tmp_dir.name), [])

    def test_form_upload_cleanup_error_does_not_hide_disk_error(self):
        """Cleanup failure never replaces the useful upload failure."""
        upload = _ChunkedUpload((b"content",), size=None)

        with (
            override_settings(
                APPRISE_ATTACH_DIR=self.tmp_dir.name,
                APPRISE_ATTACH_SIZE=32,
            ),
            patch.object(
                utils,
                "open",
                create=True,
                side_effect=OSError(errno.EDQUOT, "quota"),
            ),
            patch("os.remove", side_effect=PermissionError("cleanup denied")),
            self.assertRaisesRegex(
                AppriseAPIStorageError,
                "Could not read or write",
            ) as caught,
        ):
            parse_attachments(None, {"file1": upload})

        self.assertEqual(caught.exception.errno, errno.EDQUOT)

    @patch("apprise.utils.http.HTTPPolicySession.get")
    def test_direct_attachment_parsing(self, mock_get):
        """
        Test the parsing of file attachments
        """
        # Test the processing of file attachments
        result = parse_attachments([], {})
        assert isinstance(result, list)
        assert len(result) == 0

        # Response object
        response = mock.Mock()
        response.status_code = requests.codes.ok
        response.raise_for_status.return_value = True
        response.headers = {
            "Content-Length": getsize(SAMPLE_FILE),
        }

        def iter_content(chunk_size=1024, *args, **kwargs):
            # Mirror Requests by yielding every chunk from one response.
            stream = open(SAMPLE_FILE, "rb")  # noqa: SIM115
            try:
                while block := stream.read(chunk_size):
                    yield block
            finally:
                # Also close the file if the caller stops consuming early.
                stream.close()

        response.iter_content = iter_content

        def test(*args, **kwargs):
            return response

        response.__enter__ = test
        response.__exit__ = test
        mock_get.return_value = response

        # Support base64 encoding
        attachment_payload = {"base64": base64.b64encode(b"data to be encoded").decode("utf-8")}
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 1

        # Support multi entries
        attachment_payload = [
            {
                "base64": base64.b64encode(b"data to be encoded 1").decode("utf-8"),
            },
            {
                "base64": base64.b64encode(b"data to be encoded 2").decode("utf-8"),
            },
            {
                "base64": base64.b64encode(b"data to be encoded 3").decode("utf-8"),
            },
        ]
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 3

        # Support multi entries
        attachment_payload = [
            {
                "url": "http://myserver/my.attachment.3",
            },
            {
                "url": "http://myserver/my.attachment.2",
            },
            {
                "url": "http://myserver/my.attachment.1",
            },
        ]
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 3

        with override_settings(APPRISE_ATTACH_DENY_URLS="*"):
            utils.ATTACH_URL_FILTER = AppriseURLFilter(
                settings.APPRISE_ATTACH_ALLOW_URLS,
                settings.APPRISE_ATTACH_DENY_URLS,
            )

            # We will fail to parse our URL based attachment
            with self.assertRaises(AppriseAPIImproperlyConfigured):
                parse_attachments(attachment_payload, {})

        # Reload our configuration to default values
        utils.ATTACH_URL_FILTER = AppriseURLFilter(
            settings.APPRISE_ATTACH_ALLOW_URLS,
            settings.APPRISE_ATTACH_DENY_URLS,
        )

        # Garbage handling (integer, float, object, etc is invalid)
        attachment_payload = 5
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 0
        attachment_payload = 5.5
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 0
        attachment_payload = object()
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 0

        # dict with a valid non-empty filename — exercises the False branch
        # of 'elif not filename:' (filename is provided and non-empty, so the
        # fallback-to-default branch is not taken)
        attachment_payload = {
            "base64": base64.b64encode(b"data to be encoded").decode("utf-8"),
            "filename": "myfile.bin",
        }
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].name == "myfile.bin"

        # filename provided, but its empty (and/or contains whitespace)
        attachment_payload = {
            "base64": base64.b64encode(b"data to be encoded").decode("utf-8"),
            "filename": "   ",
        }
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 1

        # filename too long
        attachment_payload = {
            "base64": base64.b64encode(b"data to be encoded").decode("utf-8"),
            "filename": "a" * 1000,
        }
        with self.assertRaises(AppriseAPIImproperlyConfigured):
            parse_attachments(attachment_payload, {})

        # filename invalid
        attachment_payload = {
            "base64": base64.b64encode(b"data to be encoded").decode("utf-8"),
            "filename": 1,
        }
        with self.assertRaises(AppriseAPIImproperlyConfigured):
            parse_attachments(attachment_payload, {})

        attachment_payload = {
            "base64": base64.b64encode(b"data to be encoded").decode("utf-8"),
            "filename": None,
        }
        with self.assertRaises(AppriseAPIImproperlyConfigured):
            parse_attachments(attachment_payload, {})

        attachment_payload = {
            "base64": base64.b64encode(b"data to be encoded").decode("utf-8"),
            "filename": object(),
        }
        with self.assertRaises(AppriseAPIImproperlyConfigured):
            parse_attachments(attachment_payload, {})

        # List Entry with bad data
        attachment_payload = [
            None,
        ]
        with self.assertRaises(AppriseAPIImproperlyConfigured):
            parse_attachments(attachment_payload, {})

        # We expect at least a 'base64' or something in our dict
        attachment_payload = [
            {},
        ]
        with self.assertRaises(AppriseAPIImproperlyConfigured):
            parse_attachments(attachment_payload, {})

        # We allow empty entries, this is okay; there is just nothing
        # returned at the end of the day
        assert parse_attachments({""}, {}) == []

        # We can't parse entries that are not base64 but specified as
        # though they are
        attachment_payload = {
            "base64": "not-base-64",
        }
        with self.assertRaises(AppriseAPIImproperlyConfigured):
            parse_attachments(attachment_payload, {})

        # Support string; these become web requests
        attachment_payload = "https://avatars.githubusercontent.com/u/850374?v=4"
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 1

        # Local files are not allowed
        attachment_payload = "file:///etc/hosts"
        with self.assertRaises(AppriseAPIImproperlyConfigured):
            parse_attachments(attachment_payload, {})
        attachment_payload = "/etc/hosts"
        with self.assertRaises(AppriseAPIImproperlyConfigured):
            parse_attachments(attachment_payload, {})
        attachment_payload = "simply invalid"
        with self.assertRaises(AppriseAPIImproperlyConfigured):
            parse_attachments(attachment_payload, {})

        # Test our case where we throw an error trying to write our attachment
        # to disk
        m = mock_open()
        m.side_effect = OSError()
        with patch("builtins.open", m), self.assertRaises(AppriseAPIStorageError):
            attachment_payload = b"some data to work with."
            parse_attachments(attachment_payload, {})

        # Test a case where our attachment exceeds the maximum size we allow
        # for
        with override_settings(APPRISE_ATTACH_SIZE=1):
            # More then 1 MB in size causing error to trip
            attachment_payload = ("content" * 1024 * 1024).encode("utf-8")
            with self.assertRaises(AppriseAPIImproperlyConfigured):
                parse_attachments(attachment_payload, {})

        # Support byte data
        attachment_payload = b"some content to pass along as an attachment."
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 1

        attachment_payload = [
            # Request several images
            "https://myserver/myotherfile.png",
            "https://myserver/myfile.png",
        ]
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 2

        # A attachment set where our URLs are blocked
        attachment_payload = [
            # Request several images
            "https://localhost.localdomain/myotherfile.png",
            "http://localhost/myfile.png",
            "http://127.0.0.1/myfile.png",
            "https://127.0.0.3/myfile.png",
        ]
        with self.assertRaises(AppriseAPIImproperlyConfigured):
            # We have hosts that will be blocked
            parse_attachments(attachment_payload, {})

        # Test each
        for ap in attachment_payload:
            with self.assertRaises(AppriseAPIImproperlyConfigured):
                # We have hosts that will be blocked
                parse_attachments([ap], {})

        attachment_payload = [
            {
                # Request several images
                "url": "https://myserver/myotherfile.png",
            },
            {"url": "https://myserver/myfile.png"},
        ]
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 2

        # Test pure binary payload (raw)
        attachment_payload = [
            b"some content to pass along as an attachment.",
            b"some more content to pass along as an attachment.",
        ]
        result = parse_attachments(attachment_payload, {})
        assert isinstance(result, list)
        assert len(result) == 2

        # Support remote URL payload combined with uploaded FILES
        with override_settings(APPRISE_ATTACH_DIR=self.tmp_dir.name):
            attachment_payload = ["https://example.com/logo.png"]

            files_request = MultiValueDict(
                {
                    "attachment": [
                        SimpleUploadedFile("a.txt", b"a", content_type="text/plain"),
                        SimpleUploadedFile("b.txt", b"b", content_type="text/plain"),
                    ]
                }
            )

            result = parse_attachments(attachment_payload, files_request)
            assert isinstance(result, list)
            # 1 remote + 2 local uploads
            assert len(result) == 3

    def test_direct_attachment_parsing_nw(self):
        """
        Test the parsing of file attachments with network availability
        We test web requests that do not work or in accessible to access
        this part of the test cases
        """
        attachment_payload = [
            # While we have a network in place, we're intentionally requesting
            # URLs that do not exist (hopefully they don't anyway) as we want
            # this test to fail.
            "https://myserver/garbage/abcd1.png",
            "https://myserver/garbage/abcd2.png",
        ]
        with self.assertRaises(AppriseAPIImproperlyConfigured):
            parse_attachments(attachment_payload, {})

        # Support url encoding
        attachment_payload = [
            {
                "url": "https://myserver/garbage/abcd1.png",
            },
            {
                "url": "https://myserver/garbage/abcd2.png",
            },
        ]
        with self.assertRaises(AppriseAPIImproperlyConfigured):
            parse_attachments(attachment_payload, {})

    def test_form_file_attachment_parsing_multivalue_single_key(self):
        """
        Regression: MultiValueDict under a single key must not lose files.
        """
        with override_settings(APPRISE_ATTACH_DIR=self.tmp_dir.name):
            files_request = MultiValueDict(
                {
                    "attachment": [
                        SimpleUploadedFile("a.txt", b"a", content_type="text/plain"),
                        SimpleUploadedFile("b.txt", b"b", content_type="text/plain"),
                        SimpleUploadedFile("c.txt", b"c", content_type="text/plain"),
                    ]
                }
            )

            result = parse_attachments(None, files_request)
            assert isinstance(result, list)
            assert len(result) == 3

    def test_form_file_attachment_parsing_unique_keys(self):
        """
        Support curl: -F attach1=@... -F attach2=@...
        """
        with override_settings(APPRISE_ATTACH_DIR=self.tmp_dir.name):
            files_request = {
                "attach1": SimpleUploadedFile("a.txt", b"a", content_type="text/plain"),
                "attach2": SimpleUploadedFile("b.txt", b"b", content_type="text/plain"),
            }

            result = parse_attachments(None, files_request)
            assert isinstance(result, list)
            assert len(result) == 2

    def test_form_file_attachment_parsing_payload_dict_plus_files(self):
        """
        Base64 dict payload should count as 1 attachment, plus all file uploads.
        """
        with override_settings(APPRISE_ATTACH_DIR=self.tmp_dir.name):
            attachment_payload = {
                "base64": base64.b64encode(b"data to be encoded").decode("utf-8"),
            }
            files_request = MultiValueDict(
                {
                    "attachment": [
                        SimpleUploadedFile("a.txt", b"a", content_type="text/plain"),
                        SimpleUploadedFile("b.txt", b"b", content_type="text/plain"),
                    ]
                }
            )

            result = parse_attachments(attachment_payload, files_request)
            assert isinstance(result, list)
            assert len(result) == 3

    def test_form_file_attachment_parsing_max_attachments_payload_plus_files(self):
        """
        Verify APPRISE_MAX_ATTACHMENTS enforcement accounts for payload + all FILES.
        """
        with override_settings(APPRISE_ATTACH_DIR=self.tmp_dir.name, APPRISE_MAX_ATTACHMENTS=3):
            attachment_payload = [
                {"base64": base64.b64encode(b"one").decode("utf-8")},
                {"base64": base64.b64encode(b"two").decode("utf-8")},
            ]
            files_request = MultiValueDict(
                {
                    "attachment": [
                        SimpleUploadedFile("a.txt", b"a", content_type="text/plain"),
                        SimpleUploadedFile("b.txt", b"b", content_type="text/plain"),
                    ]
                }
            )

            # 2 (payload) + 2 (files) = 4 > max=3
            with self.assertRaises(AppriseAPIImproperlyConfigured):
                parse_attachments(attachment_payload, files_request)

    def test_form_file_attachment_parsing_honors_wire_content_type(self):
        """
        Multipart Content-Type should win over filename extension guessing.
        """
        with override_settings(APPRISE_ATTACH_DIR=self.tmp_dir.name):
            files_request = {
                "file1": SimpleUploadedFile(
                    "attachment.001",
                    b"\xff\xd8\xff\xe0",
                    content_type="image/jpeg",
                )
            }
            result = parse_attachments(None, files_request)
            assert isinstance(result, list)
            assert len(result) == 1
            assert result[0].mimetype == "image/jpeg"

    def test_form_file_attachment_parsing_octet_stream_falls_back_to_filename(self):
        """
        Django's "application/octet-stream" default must not suppress filename guessing.
        """
        with override_settings(APPRISE_ATTACH_DIR=self.tmp_dir.name):
            files_request = {
                "file1": SimpleUploadedFile(
                    "poster.jpg",
                    b"\xff\xd8\xff\xe0",
                    content_type="application/octet-stream",
                )
            }
            result = parse_attachments(None, files_request)
            assert isinstance(result, list)
            assert len(result) == 1
            assert result[0].mimetype == "image/jpeg"

    def test_form_file_attachment_parsing_normalizes_mixed_case_content_type(self):
        """
        Mixed-case Content-Type must be lowercased before reaching Apprise.
        """
        with override_settings(APPRISE_ATTACH_DIR=self.tmp_dir.name):
            files_request = {
                "file1": SimpleUploadedFile(
                    "attachment.001",
                    b"\xff\xd8\xff\xe0",
                    content_type="Image/JPEG",
                )
            }
            result = parse_attachments(None, files_request)
            assert isinstance(result, list)
            assert len(result) == 1
            assert result[0].mimetype == "image/jpeg"

    def test_form_file_attachment_parsing_uppercase_octet_stream_falls_back_to_filename(self):
        """
        Uppercase "application/octet-stream" must still defer to filename guessing.
        """
        with override_settings(APPRISE_ATTACH_DIR=self.tmp_dir.name):
            files_request = {
                "file1": SimpleUploadedFile(
                    "poster.jpg",
                    b"\xff\xd8\xff\xe0",
                    content_type="APPLICATION/OCTET-STREAM",
                )
            }
            result = parse_attachments(None, files_request)
            assert isinstance(result, list)
            assert len(result) == 1
            assert result[0].mimetype == "image/jpeg"

    def test_http_attachment_name_priority(self):
        """HTTPAttachment name resolution: explicit > ?name= > None (auto)."""
        with override_settings(APPRISE_ATTACH_DIR=self.tmp_dir.name):
            # ?name= from URL (no explicit filename): no TypeError, name used
            a = HTTPAttachment(
                host="example.com",
                fullpath="/thumbnails/6dba.jpg",
                secure=True,
                name="thumbnail.jpg",
            )
            assert a._name == "thumbnail.jpg"
            assert a.filename == "thumbnail.jpg"

            # Explicit filename beats URL's ?name=
            a = HTTPAttachment(
                "explicit.jpg",
                host="example.com",
                fullpath="/thumbnails/6dba.jpg",
                secure=True,
                name="url_name.jpg",
            )
            assert a._name == "explicit.jpg"
            assert a.filename == "explicit.jpg"

            # No filename, no ?name= -> auto-detect during download
            a = HTTPAttachment(
                host="example.com",
                fullpath="/thumbnails/6dba.jpg",
                secure=True,
            )
            assert a._name is None
            assert a.filename is None

    @patch("apprise.utils.http.HTTPPolicySession.get")
    def test_http_attachment_storage(self, mock_get):
        """Remote content lands in APPRISE_ATTACH_DIR and reports its size."""
        payload = b"data"
        response = mock.Mock()
        response.status_code = requests.codes.ok
        response.raise_for_status.return_value = True
        response.headers = {}
        response.iter_content.return_value = iter([payload])
        response.__enter__ = lambda s, *a, **kw: response
        response.__exit__ = mock.Mock(return_value=False)
        mock_get.return_value = response

        with override_settings(APPRISE_ATTACH_DIR=self.tmp_dir.name):
            attachment = HTTPAttachment(
                host="example.com",
                fullpath="/file.txt",
                secure=True,
            )
            assert attachment

            # Our download lives in the configured attachment directory
            assert os.path.dirname(attachment.path) == self.tmp_dir.name

            # It is the only file there; nothing extra was allocated
            assert len(os.listdir(self.tmp_dir.name)) == 1

            # The reported size matches what we actually retrieved
            assert attachment.size == len(payload)
            assert len(attachment) == len(payload)

    @patch("apprise.utils.http.HTTPPolicySession.get")
    def test_url_attachment_name_resolution(self, mock_get):
        """parse_attachments derives attachment name from URL intelligently."""
        response = mock.Mock()
        response.status_code = requests.codes.ok
        response.raise_for_status.return_value = True
        response.headers = {}
        response.iter_content.return_value = iter([b"data"])
        response.__enter__ = lambda s, *a, **kw: response
        response.__exit__ = mock.Mock(return_value=False)
        mock_get.return_value = response

        # ?name= in URL: name is taken from query param (no TypeError)
        result = parse_attachments(
            ["https://example.com/thumbnails/6dba.jpg?name=thumbnail.jpg"],
            {},
        )
        assert len(result) == 1
        assert result[0]._name == "thumbnail.jpg"

        # URL has a filename in path, no ?name=: _name stays None so
        # AttachHTTP.download() can discover the basename naturally
        result = parse_attachments(
            ["https://example.com/thumbnails/6dba96988a83163dcffe0d75e4a28bc5.jpg"],
            {},
        )
        assert len(result) == 1
        assert result[0]._name is None

        # URL has no path filename: fallback to attachment.NNN
        result = parse_attachments(["https://example.com/"], {})
        assert len(result) == 1
        assert result[0]._name == "attachment.001"

        # ?name= is empty: treated as not specified; name from URL path
        result = parse_attachments(
            ["https://example.com/thumbnails/6dba.jpg?name="],
            {},
        )
        assert len(result) == 1
        assert result[0]._name is None

        # ?name= is whitespace only (%20 is a URL-encoded space): same as empty
        result = parse_attachments(
            ["https://example.com/thumbnails/6dba.jpg?name=%20%20%20"],
            {},
        )
        assert len(result) == 1
        assert result[0]._name is None

        # ?name= has path traversal: only basename is kept
        result = parse_attachments(
            ["https://example.com/thumbnails/6dba.jpg?name=/etc/passwd"],
            {},
        )
        assert len(result) == 1
        assert result[0]._name == "passwd"

        # ?name= has relative path traversal: only basename is kept
        result = parse_attachments(
            ["https://example.com/thumbnails/6dba.jpg?name=../../secret.jpg"],
            {},
        )
        assert len(result) == 1
        assert result[0]._name == "secret.jpg"

        # ?name= empty with no path filename: still falls back to NNN
        result = parse_attachments(
            ["https://example.com/?name="],
            {},
        )
        assert len(result) == 1
        assert result[0]._name == "attachment.001"

    @patch("apprise.utils.http.HTTPPolicySession.get")
    def test_dict_url_attachment_name_resolution(self, mock_get):
        """parse_attachments dict+url form: filename priority order."""
        response = mock.Mock()
        response.status_code = requests.codes.ok
        response.raise_for_status.return_value = True
        response.headers = {}
        response.iter_content.return_value = iter([b"data"])
        response.__enter__ = lambda s, *a, **kw: response
        response.__exit__ = mock.Mock(return_value=False)
        mock_get.return_value = response

        # Dict filename overrides URL ?name= (explicit user choice wins)
        result = parse_attachments(
            [{"url": "https://example.com/img.jpg?name=url_name.jpg", "filename": "custom.jpg"}],
            {},
        )
        assert len(result) == 1
        assert result[0]._name == "custom.jpg"

        # No dict filename + URL ?name=: URL name used (no TypeError)
        result = parse_attachments(
            [{"url": "https://example.com/img.jpg?name=thumbnail.jpg"}],
            {},
        )
        assert len(result) == 1
        assert result[0]._name == "thumbnail.jpg"

        # No dict filename, no ?name=, path has filename: auto-detect
        result = parse_attachments(
            [{"url": "https://example.com/thumbnails/photo.jpg"}],
            {},
        )
        assert len(result) == 1
        assert result[0]._name is None

        # No dict filename, no ?name=, no path filename: attachment.NNN
        result = parse_attachments(
            [{"url": "https://example.com/"}],
            {},
        )
        assert len(result) == 1
        assert result[0]._name == "attachment.001"

        # URL ?name= empty: treated as not specified; path basename used
        result = parse_attachments(
            [{"url": "https://example.com/thumbnails/photo.jpg?name="}],
            {},
        )
        assert len(result) == 1
        assert result[0]._name is None

        # URL ?name= path traversal: only basename kept
        result = parse_attachments(
            [{"url": "https://example.com/img.jpg?name=/etc/passwd"}],
            {},
        )
        assert len(result) == 1
        assert result[0]._name == "passwd"

    @patch("apprise.utils.http.HTTPPolicySession.get")
    def test_dict_url_skips_local_attachment(self, mock_get):
        """A URL dictionary must not create an unused local attachment."""
        response = mock.MagicMock()
        response.status_code = requests.codes.ok
        response.headers = {}
        response.iter_content.return_value = iter([b"data"])
        response.__enter__.return_value = response
        mock_get.return_value = response

        with patch.object(utils, "Attachment") as local_attachment:
            result = parse_attachments([{"url": "https://example.com/file.txt"}], {})

        self.assertEqual(len(result), 1)
        local_attachment.assert_not_called()

    def test_base64_takes_priority_over_url(self):
        """A dictionary containing both fields retains base64 precedence."""
        payload = {
            "base64": base64.b64encode(b"local data").decode(),
            "url": "file:///should-not-be-used",
        }
        result = parse_attachments([payload], {})

        self.assertEqual(len(result), 1)
        with open(result[0].path, "rb") as stream:
            self.assertEqual(stream.read(), b"local data")

    def test_base64_accepts_ascii_whitespace(self):
        """Wrapped base64 remains compatible with common encoders."""
        encoded = base64.b64encode(b"wrapped content").decode()
        wrapped = f"\n {encoded[:8]}\r\n{encoded[8:]}\t"

        result = parse_attachments([{"base64": wrapped}], {})

        with open(result[0].path, "rb") as stream:
            self.assertEqual(stream.read(), b"wrapped content")

    def test_base64_accepts_bytes_like_values(self):
        """Internal callers may supply base64 as any bytes-like value."""
        encoded = base64.b64encode(b"binary content")
        for value in (encoded, bytearray(encoded), memoryview(encoded)):
            with self.subTest(value=type(value).__name__):
                result = parse_attachments([{"base64": value}], {})
                with open(result[0].path, "rb") as stream:
                    self.assertEqual(stream.read(), b"binary content")

    def test_base64_rejects_malformed_values(self):
        """Invalid base64 values always use the API validation error."""
        with override_settings(APPRISE_ATTACH_DIR=self.tmp_dir.name):
            for value in (None, [], {}, "é", "%%%", "abc"):
                with self.subTest(value=value), self.assertRaises(AppriseAPIImproperlyConfigured):
                    parse_attachments([{"base64": value}], {})

            self.assertEqual(os.listdir(self.tmp_dir.name), [])

    def test_base64_rejects_oversize_before_decode(self):
        """Known oversized base64 is rejected before decoding or writing."""
        encoded = base64.b64encode(b"four").decode()
        with (
            override_settings(
                APPRISE_ATTACH_DIR=self.tmp_dir.name,
                APPRISE_ATTACH_SIZE=3,
            ),
            patch.object(utils.base64, "b64decode") as decode,
            self.assertRaisesRegex(
                AppriseAPIImproperlyConfigured,
                "file size is too large",
            ),
        ):
            parse_attachments([{"base64": encoded}], {})

        decode.assert_not_called()
        self.assertEqual(os.listdir(self.tmp_dir.name), [])

    def test_base64_checks_decoded_size(self):
        """Padding-aware validation enforces the exact decoded size."""
        encoded = base64.b64encode(b"ab").decode()
        with (
            override_settings(
                APPRISE_ATTACH_DIR=self.tmp_dir.name,
                APPRISE_ATTACH_SIZE=1,
            ),
            self.assertRaisesRegex(
                AppriseAPIImproperlyConfigured,
                "file size is too large",
            ),
        ):
            parse_attachments([{"base64": encoded}], {})

        self.assertEqual(os.listdir(self.tmp_dir.name), [])

    def test_base64_rejects_short_write(self):
        """A short base64 write is reported as a storage failure."""
        opened = mock_open()
        opened().write.return_value = 1
        encoded = base64.b64encode(b"content").decode()

        with (
            override_settings(APPRISE_ATTACH_DIR=self.tmp_dir.name),
            patch("builtins.open", opened),
            self.assertRaises(AppriseAPIStorageError) as caught,
        ):
            parse_attachments([{"base64": encoded}], {})

        self.assertEqual(caught.exception.errno, errno.EIO)
        self.assertEqual(os.listdir(self.tmp_dir.name), [])

    @patch("apprise.utils.http.HTTPPolicySession.get")
    def test_remote_storage_error_uses_api_error(self, mock_get):
        """Remote download storage errors retain their original I/O code."""
        response = mock.MagicMock()
        response.status_code = requests.codes.ok
        response.headers = {}
        response.iter_content.return_value = iter([b"data"])
        response.__enter__.return_value = response
        mock_get.return_value = response
        storage_error = OSError(errno.ENOSPC, "disk full")

        with (
            patch("apprise.attachment.http.NamedTemporaryFile", side_effect=storage_error),
            self.assertRaises(AppriseAPIStorageError) as caught,
        ):
            parse_attachments(["https://example.com/file.txt"], {})

        self.assertEqual(caught.exception.errno, errno.ENOSPC)
        self.assertIs(caught.exception.__cause__, storage_error)


class AttachmentSSRFPinningTests(SimpleTestCase):
    """Verify that API attachments use Apprise's safe HTTP transport."""

    def test_attachment_policy_follows_url_rules(self):
        """The session applies full allow and deny rules to every URL."""
        original_filter = utils.ATTACH_URL_FILTER
        utils.ATTACH_URL_FILTER = AppriseURLFilter(
            "https://good.example/*",
            "https://good.example/private internal",
        )
        try:
            with TemporaryDirectory() as tmp_dir, override_settings(APPRISE_ATTACH_DIR=tmp_dir):
                attachment = HTTPAttachment(
                    host="good.example",
                    secure=True,
                    fullpath="/file",
                )
                attachment.http_session.policy.validate_url("https://good.example/file")
                with self.assertRaises(requests.exceptions.InvalidURL):
                    attachment.http_session.policy.validate_url("https://good.example/private/secret")

        finally:
            utils.ATTACH_URL_FILTER = original_filter

        # The attachment keeps one coherent policy after global restoration.
        attachment.http_session.policy.validate_url("https://good.example/file")
        with self.assertRaises(requests.exceptions.InvalidURL):
            attachment.http_session.policy.validate_url("https://good.example/private/secret")
        self.assertFalse(attachment.http_session.policy.address_filter("10.0.0.5"))

    def test_internal_address_filter_is_opt_in(self):
        """Private DNS answers are blocked only when ``internal`` is denied."""
        self.assertTrue(AppriseURLFilter("*", "").is_address_allowed("10.0.0.5"))
        self.assertFalse(AppriseURLFilter("*", "internal").is_address_allowed("10.0.0.5"))

    def test_global_dns_is_unchanged(self):
        """Importing API utilities never replaces the process DNS function."""
        self.assertEqual(socket.getaddrinfo.__module__, "socket")

    def test_attachment_closes_session_once(self):
        """Attachment cleanup closes and clears its owned HTTP session."""
        attachment = object.__new__(HTTPAttachment)
        attachment.delete = False
        attachment.http_session = mock.Mock()
        session = attachment.http_session

        attachment.__del__()
        attachment.__del__()

        session.close.assert_called_once_with()
        self.assertIsNone(attachment.http_session)

    def test_attachment_suppresses_session_close_error(self):
        """A cleanup failure never escapes from the destructor."""
        attachment = object.__new__(HTTPAttachment)
        attachment.delete = False
        attachment.http_session = mock.Mock()
        attachment.http_session.close.side_effect = RuntimeError("close failed")

        attachment.__del__()

        self.assertIsNone(attachment.http_session)

    def test_attachment_runs_apprise_cleanup(self):
        """API cleanup also releases the file Apprise downloaded for us."""
        attachment = object.__new__(HTTPAttachment)
        attachment.delete = True
        attachment.http_session = mock.Mock()
        cleaned = []

        with mock.patch.object(
            HTTPAttachment.__mro__[1],
            "__del__",
            new=lambda target: cleaned.append(target),
        ):
            attachment.__del__()

        self.assertIn(attachment, cleaned)

    def test_cleanup_error_still_closes_session(self):
        """A parent cleanup failure cannot leave pooled sockets open."""
        attachment = object.__new__(HTTPAttachment)
        attachment.delete = True
        attachment.http_session = mock.Mock()
        session = attachment.http_session

        with mock.patch.object(
            HTTPAttachment.__mro__[1],
            "__del__",
            side_effect=RuntimeError("cleanup failed"),
        ):
            attachment.__del__()

        session.close.assert_called_once_with()
        self.assertIsNone(attachment.http_session)

    def test_retained_attachment_skips_apprise_cleanup(self):
        """A caller asking to keep the download still gets its session back."""
        attachment = object.__new__(HTTPAttachment)
        attachment.delete = False
        attachment.http_session = mock.Mock()
        session = attachment.http_session
        cleaned = []

        with mock.patch.object(
            HTTPAttachment.__mro__[1],
            "__del__",
            new=lambda target: cleaned.append(target),
        ):
            attachment.__del__()

        self.assertEqual(cleaned, [])
        session.close.assert_called_once_with()


class LocalFileDisclosureTests(LocalFileFixtureMixin, SimpleTestCase):
    """Ensure URL attachments cannot read files from the API server."""

    def test_string_rejects_local_files(self):
        """A plain string attachment entry can't reference a local path."""
        for label, candidate in self.local_file_variants().items():
            with self.subTest(style=label):
                with self.assertRaises(AppriseAPIImproperlyConfigured) as caught:
                    parse_attachments([candidate], {})
                self.assert_marker_not_leaked(None, str(caught.exception))

    def test_dict_url_rejects_local_files(self):
        """A ``{"url": ...}`` attachment entry can't reference a local path."""
        for label, candidate in self.local_file_variants().items():
            with self.subTest(style=label):
                with self.assertRaises(AppriseAPIImproperlyConfigured) as caught:
                    parse_attachments([{"url": candidate}], {})
                self.assert_marker_not_leaked(str(caught.exception))

    def test_local_files_are_not_copied(self):
        """Even a rejected attempt must not leave the content on disk."""
        with TemporaryDirectory() as attach_dir, override_settings(APPRISE_ATTACH_DIR=attach_dir):
            for candidate in self.local_file_variants().values():
                for payload in (candidate, {"url": candidate}):
                    with suppress(AppriseAPIImproperlyConfigured):
                        parse_attachments([payload], {})

            self.assert_attach_dir_clean(attach_dir)

    def test_url_errors_hide_input(self):
        """Attachment errors must not repeat credentials or control characters."""
        url = "https://user:secret@example.com/attachment\nforged-log-entry"
        with (
            patch.object(utils.ATTACH_URL_FILTER, "is_allowed", return_value=False),
            self.assertRaises(AppriseAPIImproperlyConfigured) as caught,
        ):
            _build_http_attachment(url, 1, "attachment.001")

        self.assertNotIn("secret", str(caught.exception))
        self.assertNotIn("forged-log-entry", str(caught.exception))

    def test_invalid_parsed_url_uses_api_error(self):
        """A parser disagreement must still produce a controlled API error."""
        with (
            patch.object(utils.ATTACH_URL_FILTER, "is_allowed", return_value=True),
            patch.object(utils.A_MGR["http"], "parse_url", return_value=None),
            self.assertRaises(AppriseAPIImproperlyConfigured),
        ):
            _build_http_attachment("https://example.com/file", 1, "attachment.001")

    def test_http_storage_exception_uses_api_error(self):
        """A raised HTTP storage error is converted to the API contract."""
        storage_error = OSError(errno.EIO, "write failed")
        attachment = mock.MagicMock()
        attachment.__bool__.side_effect = storage_error
        with (
            patch.object(utils.ATTACH_URL_FILTER, "is_allowed", return_value=True),
            patch.object(utils, "HTTPAttachment", return_value=attachment),
            self.assertRaises(AppriseAPIStorageError) as caught,
        ):
            _build_http_attachment("https://example.com/file", 1, "attachment.001")

        self.assertEqual(caught.exception.errno, errno.EIO)
        self.assertIs(caught.exception.__cause__, storage_error)

    def test_rejects_non_hosted_http_attachment(self):
        """Reject an attachment if its source is not classified as hosted."""
        attachment = mock.MagicMock()
        attachment.location = utils.apprise.ContentLocation.LOCAL
        with (
            patch.object(utils.ATTACH_URL_FILTER, "is_allowed", return_value=True),
            patch.object(utils.A_MGR["http"], "parse_url", return_value={"host": "example.com"}),
            patch.object(utils, "HTTPAttachment", return_value=attachment),
            self.assertRaises(AppriseAPIImproperlyConfigured),
        ):
            _build_http_attachment("https://example.com/file", 1, "attachment.001")
