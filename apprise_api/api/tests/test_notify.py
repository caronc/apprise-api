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
from inspect import cleandoc
import io
import json
import logging
import queue
import threading
from unittest import mock

import apprise
from django.core.exceptions import RequestDataTooBig
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
import requests

from ..forms import NotifyForm
from ..views import (
    _EVENT_SIZE,
    _STREAM_PUT_FAILED,
    _STREAM_PUT_SPOOLED,
    _safe_stream_log,
    _SpooledEventQueue,
    parse_tag_expression,
    render_notify_logs,
    render_notify_response,
    stream_notify_response,
    stream_result_response,
)
from .helpers import notify_result

# Grant access to our Notification Manager Singleton
N_MGR = apprise.manager_plugins.NotificationManager()


class NotifyTests(SimpleTestCase):
    """
    Test notifications
    """

    def test_result_rendering_stays_lazy(self):
        """JSON rendering reads entries only as response chunks are consumed."""
        consumed = []

        def entries():
            """Record when the renderer asks for the one available entry."""
            consumed.append(True)
            yield apprise.NotifyLogEntry(level="INFO", message="ready")

        chunks = render_notify_logs(
            entries(),
            json_response=True,
            content_type="application/json",
        )

        # The opening bracket is available before the first log is decoded.
        assert next(chunks) == "["
        assert consumed == []

        output = "[" + "".join(chunks)
        assert json.loads(output)[0][2] == "ready"
        assert consumed == [True]

    def test_result_rendering_supports_html_and_plain_text(self):
        """Bounded rendering preserves HTML escaping and text separators."""
        first = apprise.NotifyLogEntry(level="INFO", message="<ready>")
        second = apprise.NotifyLogEntry(level="WARNING", message="later")

        html = "".join(
            render_notify_logs(
                iter((first,)),
                json_response=False,
                content_type="text/html",
            )
        )
        assert html.startswith('<ul class="logs">')
        assert "&lt;ready&gt;" in html
        assert html.endswith("</ul>")

        text = "".join(
            render_notify_logs(
                iter((first, second)),
                json_response=False,
                content_type="text/plain",
            )
        )
        assert text == "{}\n{}".format(first, second)

        # A failed plain-text response still explains an empty log result.
        fallback = "".join(
            render_notify_response(
                iter(()),
                json_response=False,
                content_type="text/plain",
                error="delivery failed",
            )
        )
        assert fallback == "delivery failed"

    def test_result_response_closes_owned_storage(self):
        """Closing a standard streamed response also closes its result."""
        result = notify_result(True)
        result.close = mock.Mock(wraps=result.close)

        response = stream_result_response(
            result,
            json_response=True,
            content_type="application/json",
            status=200,
        )

        payload = json.loads(b"".join(response.streaming_content).decode("utf-8"))
        assert payload == {"error": None, "details": []}
        result.close.assert_called_once_with()

    @override_settings(APPRISE_STREAM_MEMORY_SIZE=17, APPRISE_STREAM_DISK_SIZE=29)
    def test_stream_sizes_are_passed_to_notify_assets(self):
        """Both notification endpoints apply stream limits to result logs."""
        captured = {}
        real_asset = apprise.AppriseAsset

        class SpyAsset(real_asset):
            """Capture asset arguments while preserving normal behavior."""

            def __init__(self, **kwargs):
                captured.update(kwargs)
                super().__init__(**kwargs)

        payload = {
            "urls": "json://user:pass@localhost",
            "title": "Test",
            "body": "Body",
        }
        key = "test_stream_sizes_are_passed_to_notify_assets"
        # Save one stateful configuration before exercising that endpoint.
        self.client.post(
            "/add/{}".format(key),
            {"urls": payload["urls"]},
        )

        with (
            mock.patch("apprise.AppriseAsset", SpyAsset),
            mock.patch("apprise.Apprise.notify", return_value=notify_result(True)),
        ):
            self.client.post("/notify/{}".format(key), {"body": "Body"})

        assert captured["result_log_memory_size"] == 17
        assert captured["result_log_disk_size"] == 29
        # Clear the first endpoint's values before checking stateless notify.
        captured.clear()

        with (
            mock.patch("apprise.AppriseAsset", SpyAsset),
            mock.patch("apprise.Apprise.notify", return_value=notify_result(True)),
        ):
            self.client.post(
                "/notify",
                data=json.dumps(payload),
                content_type="application/json",
            )

        assert captured["result_log_memory_size"] == 17
        assert captured["result_log_disk_size"] == 29

    def test_tag_expression_preserves_logic(self):
        """
        Advanced tag tokens should not change existing OR/AND behavior.
        """
        assert parse_tag_expression("family friends") == [("family", "friends")]
        assert parse_tag_expression("family+friends") == [("family", "friends")]
        assert parse_tag_expression("family&friends") == [("family", "friends")]
        assert parse_tag_expression("1:family 3:friends") == [("1:family", "3:friends")]
        assert parse_tag_expression("1:family+3:friends") == [("1:family", "3:friends")]
        assert parse_tag_expression("friends 4:family") == [("friends", "4:family")]
        assert parse_tag_expression("family:2, 3:friends:4") == [
            "family:2",
            "3:friends:4",
        ]
        with self.assertRaises(ValueError):
            parse_tag_expression("family:")
        with self.assertRaises(ValueError):
            parse_tag_expression("9" * 5000 + ":family")

    def test_stream_queue_preserves_spooled_order(self):
        """Slow-reader overflow moves to disk and keeps every event."""
        events = _SpooledEventQueue(memory_bytes=6, disk_bytes=1024)

        # Six bytes hold the first two events; later events must use disk.
        assert events.put("one") is None
        assert events.put("two") is None
        assert events.put("three") == _STREAM_PUT_SPOOLED
        assert events.put("four") is None
        assert events.qsize() == 4

        # Reading crosses the memory/disk boundary without changing order.
        assert [events.get() for _ in range(4)] == [
            "one",
            "two",
            "three",
            "four",
        ]
        assert events.close() == (0, 4, 2, 0)
        assert events.put("closed") is None
        with self.assertRaises(queue.Empty):
            events.get()

        large_event = _SpooledEventQueue(memory_bytes=3, disk_bytes=1024)
        # One event larger than memory goes directly to disk.
        assert large_event.put("four") == _STREAM_PUT_SPOOLED
        assert large_event.get() == "four"
        large_event.close()

    @override_settings(APPRISE_STREAM_MEMORY_SIZE=3, APPRISE_STREAM_DISK_SIZE=1024)
    def test_stream_queue_uses_django_size_settings(self):
        """The queue reads its default limits from Django settings."""
        events = _SpooledEventQueue()

        assert events.put("one") is None
        assert events.put("two") == _STREAM_PUT_SPOOLED
        assert events.get() == "one"
        assert events.get() == "two"
        events.close()

    def test_stream_queue_reuses_disk_after_drain(self):
        """A full disk spool drops overflow but can be reused after draining."""
        events = _SpooledEventQueue(memory_bytes=0, disk_bytes=11)

        assert events.put("one") == _STREAM_PUT_SPOOLED
        with self.assertLogs("django", level="WARNING"):
            assert events.put("two") == _STREAM_PUT_FAILED
        # Repeated overflow stays contained without repeating the warning.
        assert events.put("two") == _STREAM_PUT_FAILED
        assert events.get() == "one"

        # Draining closes the full file, allowing a fresh spool to be used.
        assert events.put("two") is None
        assert events.get() == "two"
        assert events.close() == (0, 1, 2, 2)

    def test_stream_queue_zero_size_modes(self):
        """Zero selects memory-only, disk-only, or no-buffer operation."""
        memory_only = _SpooledEventQueue(memory_bytes=1, disk_bytes=0)
        # A zero disk limit keeps the earlier unbounded-memory behavior.
        assert memory_only.put("one") is None
        assert memory_only.put("two") is None
        assert memory_only.get() == "one"
        assert memory_only.get() == "two"
        assert memory_only.close() == (0, 2, 0, 0)

        disk_only = _SpooledEventQueue(memory_bytes=0, disk_bytes=1024)
        # A zero memory limit sends the first event straight to disk.
        assert disk_only.put("one") == _STREAM_PUT_SPOOLED
        assert disk_only.get() == "one"
        disk_only.close()

        disabled = _SpooledEventQueue(memory_bytes=0, disk_bytes=0)
        # With both limits zero, the event is counted but not retained.
        with self.assertLogs("django", level="WARNING"):
            assert disabled.put("one") == _STREAM_PUT_FAILED
        assert disabled.storage_failed() is True
        assert disabled.close() == (0, 0, 0, 1)

    def test_stream_queue_recovers_from_create_failure(self):
        """A disk failure drops no in-memory data and allows later logging."""
        events = _SpooledEventQueue(memory_bytes=5, disk_bytes=1024)
        assert events.put("one") is None

        with (
            mock.patch("api.views.tempfile.TemporaryFile", side_effect=OSError("disk full")),
            self.assertLogs("django", level="ERROR"),
        ):
            assert events.put("two") == _STREAM_PUT_FAILED

        assert events.storage_failed() is True
        # Full memory remains bounded after disk storage becomes unavailable.
        assert events.put("also unavailable") is None
        assert events.get() == "one"
        # Once memory drains, best-effort in-memory delivery resumes.
        assert events.put("three") is None
        assert events.get() == "three"
        assert events.close() == (0, 1, 0, 2)

    def test_stream_queue_preserves_events_on_write_failure(self):
        """A partial write does not damage events already on disk."""

        class FailingSpool(io.BytesIO):
            """Fail the second event written to this temporary file."""

            def __init__(self):
                super().__init__()
                self.write_count = 0

            def write(self, value):
                self.write_count += 1
                if self.write_count == 2:
                    super().write(value[:1])
                    raise OSError("disk full")
                return super().write(value)

        spool = FailingSpool()
        events = _SpooledEventQueue(memory_bytes=3, disk_bytes=1024)
        with mock.patch("api.views.tempfile.TemporaryFile", return_value=spool):
            events.put("one")
            assert events.put("two") == _STREAM_PUT_SPOOLED
            with self.assertLogs("django", level="ERROR"):
                assert events.put("three") == _STREAM_PUT_FAILED

        assert events.get() == "one"
        assert events.get() == "two"
        assert events.close() == (0, 2, 1, 1)

    def test_stream_queue_contains_write_and_close_failures(self):
        """Short writes and close errors are contained and reported."""

        class ShortWriteSpool(io.BytesIO):
            """Accept only part of the event and then fail cleanup."""

            close_failed = False

            def write(self, value):
                super().write(value[:-1])
                return len(value) - 1

            def close(self):
                if not self.close_failed:
                    self.close_failed = True
                    raise OSError("close failed")
                super().close()

        events = _SpooledEventQueue(memory_bytes=0, disk_bytes=1024)
        with (
            mock.patch("api.views.tempfile.TemporaryFile", return_value=ShortWriteSpool()),
            self.assertLogs("django", level="ERROR"),
        ):
            assert events.put("one") == _STREAM_PUT_FAILED

        assert events.storage_failed() is True
        assert events.close() == (0, 0, 0, 1)

    def test_stream_queue_ignores_truncate_failure(self):
        """A failed partial-write cleanup does not hide the original error."""

        class FailingTruncateSpool(io.BytesIO):
            """Keep one disk event, then fail its next write and truncate."""

            def __init__(self):
                super().__init__()
                self.write_count = 0

            def write(self, value):
                self.write_count += 1
                if self.write_count == 2:
                    raise OSError("write failed")
                return super().write(value)

            def truncate(self, *args, **kwargs):
                raise OSError("truncate failed")

        events = _SpooledEventQueue(memory_bytes=0, disk_bytes=1024)
        spool = FailingTruncateSpool()
        with mock.patch("api.views.tempfile.TemporaryFile", return_value=spool):
            assert events.put("one") == _STREAM_PUT_SPOOLED
            with self.assertLogs("django", level="ERROR"):
                assert events.put("two") == _STREAM_PUT_FAILED

        assert events.get() == "one"
        events.close()

    def test_stream_queue_contains_unreadable_disk_data(self):
        """Unreadable temporary data becomes an alertable storage failure."""
        spool = io.BytesIO()
        events = _SpooledEventQueue(memory_bytes=0, disk_bytes=1024)
        with mock.patch("api.views.tempfile.TemporaryFile", return_value=spool):
            assert events.put("one") == _STREAM_PUT_SPOOLED

        spool.seek(0)
        # Remove nearly all of the length header before reading it back.
        spool.truncate(1)
        with (
            self.assertLogs("django", level="ERROR"),
            self.assertRaises(queue.Empty),
        ):
            events.get()

        assert events.storage_failed() is True
        assert events.close() == (0, 1, 1, 1)

        short_payload = io.BytesIO()
        events = _SpooledEventQueue(memory_bytes=0, disk_bytes=1024)
        with mock.patch("api.views.tempfile.TemporaryFile", return_value=short_payload):
            events.put("payload")
        short_payload.seek(0)
        # Keep a full header but only one byte of its declared payload.
        short_payload.truncate(_EVENT_SIZE.size + 1)
        with (
            self.assertLogs("django", level="ERROR"),
            self.assertRaises(queue.Empty),
        ):
            events.get()
        events.close()

    def test_stream_reports_storage_failure_and_completes(self):
        """Disk failure alerts the client without stopping notification work."""
        fake_service = type("FakeService", (), {"service_name": "JSON"})()
        notify_finished = mock.Mock()

        class FakeApprise:
            """Emit enough entries to require temporary storage."""

            def notify(self, *args, **kwargs):
                kwargs["log_callback"](
                    apprise.NotifyLogEntry(level="INFO", message="one"),
                    fake_service,
                )
                kwargs["log_callback"](
                    apprise.NotifyLogEntry(level="INFO", message="two"),
                    fake_service,
                )
                notify_finished()
                return notify_result(True)

        events = _SpooledEventQueue(memory_bytes=0, disk_bytes=1024)
        with (
            mock.patch.dict(
                stream_notify_response.__globals__,
                {"_SpooledEventQueue": mock.Mock(return_value=events)},
            ),
            mock.patch("api.views.tempfile.TemporaryFile", side_effect=OSError("disk full")),
        ):
            response = stream_notify_response(
                FakeApprise(),
                body="test",
                title="",
                notify_type=apprise.NotifyType.INFO,
                tag=None,
                attach=None,
                log_level=logging.INFO,
            )
            body = b"".join(response.streaming_content).decode("utf-8")

        notify_finished.assert_called_once_with()
        assert "event: result" in body
        assert "notification processing is continuing" in body
        assert "Please contact the server administrator" in body

    def test_stream_contains_logging_failure(self):
        """A broken server log handler cannot stop notification work."""
        with mock.patch.object(
            _safe_stream_log.__globals__["logger"],
            "log",
            side_effect=RuntimeError("handler failed"),
        ):
            _safe_stream_log(logging.ERROR, "test")
            events = _SpooledEventQueue(memory_bytes=0, disk_bytes=1024)
            with mock.patch(
                "api.views.tempfile.TemporaryFile",
                side_effect=OSError("disk full"),
            ):
                assert events.put("one") == _STREAM_PUT_FAILED

        assert events.storage_failed() is True
        assert events.close() == (0, 0, 0, 1)

    def test_stream_queue_contains_cleanup_failure(self):
        """A cleanup failure is reported after the saved event is read."""

        class CloseFailSpool(io.BytesIO):
            """Store events normally but fail when closed."""

            close_failed = False

            def close(self):
                if not self.close_failed:
                    self.close_failed = True
                    raise ValueError("close failed")
                super().close()

        events = _SpooledEventQueue(memory_bytes=0, disk_bytes=1024)
        with mock.patch("api.views.tempfile.TemporaryFile", return_value=CloseFailSpool()):
            assert events.put("one") == _STREAM_PUT_SPOOLED
            with self.assertLogs("django", level="ERROR"):
                assert events.get() == "one"

        assert events.storage_failed() is True
        assert events.close() == (0, 1, 1, 0)

    def test_stream_spools_and_reports_backlog(self):
        """A normal disk-backed stream reports its slow-reader statistics."""
        fake_service = type("FakeService", (), {"service_name": "JSON"})()

        class FakeApprise:
            """Emit one entry and finish successfully."""

            def notify(self, *args, **kwargs):
                kwargs["log_callback"](
                    apprise.NotifyLogEntry(level="INFO", message="one"),
                    fake_service,
                )
                return notify_result(True)

        events = _SpooledEventQueue(memory_bytes=0, disk_bytes=1024)
        with (
            mock.patch.dict(
                stream_notify_response.__globals__,
                {"_SpooledEventQueue": mock.Mock(return_value=events)},
            ),
            self.assertLogs("django", level="INFO") as logs,
        ):
            response = stream_notify_response(
                FakeApprise(),
                body="test",
                title="",
                notify_type=apprise.NotifyType.INFO,
                tag=None,
                attach=None,
                log_level=logging.INFO,
            )
            body = b"".join(response.streaming_content).decode("utf-8")

        assert "event: log" in body
        assert "event: result" in body
        assert any("backlog moved" in message for message in logs.output)
        assert any("spooled 1 event" in message for message in logs.output)

    def test_stream_handles_worker_start_failure(self):
        """A worker startup failure returns a safe error event."""
        with (
            mock.patch.object(threading.Thread, "start", side_effect=RuntimeError("no threads")),
            self.assertLogs("django", level="ERROR") as logs,
        ):
            response = stream_notify_response(
                mock.Mock(),
                body="test",
                title="",
                notify_type=apprise.NotifyType.INFO,
                tag=None,
                attach=None,
                log_level=logging.INFO,
            )
            body = b"".join(response.streaming_content).decode("utf-8")

        assert "event: error" in body
        assert "Notification processing failed" in body
        assert any("could not start" in message for message in logs.output)

    def test_stream_disconnect_does_not_stop_notification(self):
        """Client disconnect cleanup leaves notification work running."""
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        class FakeApprise:
            """Wait for the test while simulating active notification work."""

            def notify(self, *args, **kwargs):
                kwargs["log_callback"](
                    apprise.NotifyLogEntry(level="INFO", message="pending"),
                    None,
                )
                started.set()
                release.wait(2)
                finished.set()
                return notify_result(True)

        response = stream_notify_response(
            FakeApprise(),
            body="test",
            title="",
            notify_type=apprise.NotifyType.INFO,
            tag=None,
            attach=None,
            log_level=logging.INFO,
        )
        iterator = iter(response.streaming_content)
        assert next(iterator) == b": connected\n\n"
        assert started.wait(1)

        with self.assertLogs("django", level="WARNING") as logs:
            response.close()

        release.set()
        assert finished.wait(1)
        assert any("before notification processing finished" in message for message in logs.output)
        assert any("pending event" in message for message in logs.output)

    def test_stream_worker_count_is_bounded_after_disconnect(self):
        """A detached notification retains one slot while new streams queue."""
        started = threading.Event()
        queued_started = threading.Event()
        release = threading.Event()

        class BlockingApprise:
            def notify(self, *args, **kwargs):
                started.set()
                release.wait(2)
                return notify_result(True)

        class QueuedApprise:
            def notify(self, *args, **kwargs):
                queued_started.set()
                return notify_result(True)

        limiter = threading.BoundedSemaphore(1)
        with mock.patch.dict(stream_notify_response.__globals__, {"_STREAM_WORKERS": limiter}):
            first = stream_notify_response(
                BlockingApprise(),
                body="test",
                title="",
                notify_type=apprise.NotifyType.INFO,
                tag=None,
                attach=None,
                log_level=logging.INFO,
            )
            assert started.wait(1)

            queued = stream_notify_response(
                QueuedApprise(),
                body="test",
                title="",
                notify_type=apprise.NotifyType.INFO,
                tag=None,
                attach=None,
                log_level=logging.INFO,
            )
            assert queued.status_code == 200
            queued_iterator = iter(queued.streaming_content)
            assert next(queued_iterator) == b": connected\n\n"
            assert not queued_started.wait(0.05)

            first.close()
            release.set()
            assert queued_started.wait(1)
            # Consume the completed response so its queue-owned result closes.
            b"".join(first.streaming_content)
            b"".join(queued_iterator)

    def test_stream_handles_disk_read_failure(self):
        """A disk read failure alerts the client and still returns a result."""

        class ReadFailSpool(io.BytesIO):
            """Accept the event but fail when the stream reads it back."""

            def read(self, *args, **kwargs):
                raise OSError("read failed")

        class FakeApprise:
            """Emit one entry and finish successfully."""

            def notify(self, *args, **kwargs):
                kwargs["log_callback"](
                    apprise.NotifyLogEntry(level="INFO", message="one"),
                    None,
                )
                return notify_result(True)

        events = _SpooledEventQueue(memory_bytes=0, disk_bytes=1024)
        with (
            mock.patch.dict(
                stream_notify_response.__globals__,
                {"_SpooledEventQueue": mock.Mock(return_value=events)},
            ),
            mock.patch("api.views.tempfile.TemporaryFile", return_value=ReadFailSpool()),
            self.assertLogs("django", level="ERROR"),
        ):
            response = stream_notify_response(
                FakeApprise(),
                body="test",
                title="",
                notify_type=apprise.NotifyType.INFO,
                tag=None,
                attach=None,
                log_level=logging.INFO,
            )
            body = b"".join(response.streaming_content).decode("utf-8")

        assert "event: result" in body
        assert "server storage limit or error" in body

    @mock.patch("apprise.Apprise.notify")
    def test_notify_accepts_advanced_tag_expression(self, mock_notify):
        """
        Stateful notify should pass advanced tag expressions through to Apprise.
        """
        mock_notify.return_value = notify_result(True)
        key = "test_notify_accepts_advanced_tag_expression"

        response = self.client.post(
            "/add/{}".format(key),
            {"urls": "mailto://user:pass@yahoo.ca"},
        )
        assert response.status_code == 200

        response = self.client.post(
            "/notify/{}".format(key),
            {
                "body": "test notification",
                "tag": "1:family 3:friends, friends 4:family",
            },
        )
        assert response.status_code == 200
        assert mock_notify.call_count == 1
        assert mock_notify.call_args.kwargs["tag"] == [
            ("1:family", "3:friends"),
            ("friends", "4:family"),
        ]

    @mock.patch("apprise.Apprise.notify")
    def test_notify_by_loaded_urls(self, mock_notify):
        """
        Test adding a simple notification and notifying it
        """

        # Set our return value
        mock_notify.return_value = notify_result(True)

        # our key to use
        key = "test_notify_by_loaded_urls"

        # Add some content
        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200

        # Preare our form data
        form_data = {
            "body": "test notifiction",
        }

        # At a minimum, just a body is required
        form = NotifyForm(data=form_data)
        assert form.is_valid()

        # we always set a type if one wasn't done so already
        assert form.cleaned_data["type"] == apprise.NotifyType.INFO.value

        # format is entirely optional; it stays unset (None) if the caller
        # never specified one, rather than defaulting to TEXT
        assert form.cleaned_data["format"] is None

        # Required to prevent None from being passed into self.client.post()
        del form.cleaned_data["attachment"]
        if not form.cleaned_data.get("format") and "format" in form.cleaned_data:
            # format is optional; None cannot be encoded as POST data
            del form.cleaned_data["format"]

        # Send our notification
        response = self.client.post("/notify/{}".format(key), form.cleaned_data)
        assert response.status_code == 200
        assert mock_notify.call_count == 1

        # Reset our mock object
        mock_notify.reset_mock()

        # Preare our form data
        form_data = {}
        attach_data = {"attachment": SimpleUploadedFile("attach.txt", b"content here", content_type="text/plain")}

        # At a minimum, just an attachment is required
        form = NotifyForm(form_data, attach_data)
        assert form.is_valid()

        if not form.cleaned_data.get("format") and "format" in form.cleaned_data:
            # format is optional; None cannot be encoded as POST data
            del form.cleaned_data["format"]

        # Send our notification
        response = self.client.post("/notify/{}".format(key), form.cleaned_data)
        assert response.status_code == 200
        assert mock_notify.call_count == 1

        # Reset our mock object
        mock_notify.reset_mock()

        # Preare our form data
        form_data = {
            "body": "test notifiction",
        }
        attach_data = {"attachment": SimpleUploadedFile("attach.txt", b"content here", content_type="text/plain")}

        # At a minimum, just a body is required
        form = NotifyForm(form_data, attach_data)
        assert form.is_valid()

        if not form.cleaned_data.get("format") and "format" in form.cleaned_data:
            # format is optional; None cannot be encoded as POST data
            del form.cleaned_data["format"]

        # Send our notification
        response = self.client.post("/notify/{}".format(key), form.cleaned_data)
        assert response.status_code == 200
        assert mock_notify.call_count == 1

        # Reset our mock object
        mock_notify.reset_mock()

        # Test Headers
        for level in (
            "CRITICAL",
            "ERROR",
            "WARNING",
            "INFO",
            "DEBUG",
            "TRACE",
            "INVALID",
        ):
            # Preare our form data
            form_data = {
                "body": "test notifiction",
            }
            attach_data = {"attachment": SimpleUploadedFile("attach.txt", b"content here", content_type="text/plain")}

            # At a minimum, just a body is required
            form = NotifyForm(form_data, attach_data)
            assert form.is_valid()

            if not form.cleaned_data.get("format") and "format" in form.cleaned_data:
                # format is optional; None cannot be encoded as POST data
                del form.cleaned_data["format"]

            # Prepare our header
            headers = {
                "HTTP_X-APPRISE-LOG-LEVEL": level,
            }

            # Send our notification
            response = self.client.post("/notify/{}".format(key), form.cleaned_data, **headers)
            assert response.status_code == 200
            assert mock_notify.call_count == 1

            # Reset our mock object
            mock_notify.reset_mock()

        # Submit with an invalid format choice — NotifyForm fails validation
        # (covers the False branch of 'if form.is_valid()' at the form
        # parse block, leaving content empty and returning 400)
        response = self.client.post(
            "/notify/{}".format(key),
            {"format": "invalid_format_xyz", "body": "test"},
        )
        assert response.status_code == 400
        assert mock_notify.call_count == 0

        # Long Filename
        attach_data = {
            "attachment": SimpleUploadedFile(
                "{}.txt".format("a" * 2000),
                b"content here",
                content_type="text/plain",
            )
        }

        # At a minimum, just a body is required
        form = NotifyForm(form_data, attach_data)
        assert form.is_valid()

        if not form.cleaned_data.get("format") and "format" in form.cleaned_data:
            # format is optional; None cannot be encoded as POST data
            del form.cleaned_data["format"]

        # Send our notification
        response = self.client.post("/notify/{}".format(key), form.cleaned_data)

        # We fail because the filename is too long
        assert response.status_code == 400
        assert mock_notify.call_count == 0

        # Reset our mock object
        mock_notify.reset_mock()

        # A setting of zero means unlimited attachments are allowed
        with override_settings(APPRISE_MAX_ATTACHMENTS=0):
            # Preare our form data
            form_data = {
                "body": "test notifiction",
            }
            attach_data = {"attachment": SimpleUploadedFile("attach.txt", b"content here", content_type="text/plain")}

            # At a minimum, just a body is required
            form = NotifyForm(form_data, attach_data)
            assert form.is_valid()

            if not form.cleaned_data.get("format") and "format" in form.cleaned_data:
                # format is optional; None cannot be encoded as POST data
                del form.cleaned_data["format"]

            # Send our notification
            response = self.client.post("/notify/{}".format(key), form.cleaned_data)

            # We're good!
            assert response.status_code == 200
            assert mock_notify.call_count == 1

        # Reset our mock object
        mock_notify.reset_mock()

        # Only allow 1 attachment, but we'll attempt to send more...
        with override_settings(APPRISE_MAX_ATTACHMENTS=1):
            # Preare our form data
            form_data = {
                "body": "test notifiction",
            }

            # At a minimum, just a body is required
            form = NotifyForm(form_data)

            assert form.is_valid()
            # Required to prevent None from being passed into self.client.post()
            del form.cleaned_data["attachment"]
            if not form.cleaned_data.get("format") and "format" in form.cleaned_data:
                # format is optional; None cannot be encoded as POST data
                del form.cleaned_data["format"]

            data = {
                **form.cleaned_data,
                "file1": SimpleUploadedFile("attach1.txt", b"content here", content_type="text/plain"),
                "file2": SimpleUploadedFile(
                    "attach2.txt",
                    b"more content here",
                    content_type="text/plain",
                ),
            }

            # Send our notification
            response = self.client.post("/notify/{}".format(key), data, format="multipart")

            # Too many attachments
            assert response.status_code == 400
            assert mock_notify.call_count == 0

        # Reset our mock object
        mock_notify.reset_mock()

        # A setting of zero means unlimited attachments are allowed
        with override_settings(APPRISE_ATTACH_SIZE=0):
            # Preare our form data
            form_data = {
                "body": "test notifiction",
            }
            attach_data = {"attachment": SimpleUploadedFile("attach.txt", b"content here", content_type="text/plain")}

            # At a minimum, just a body is required
            form = NotifyForm(form_data, attach_data)
            assert form.is_valid()

            if not form.cleaned_data.get("format") and "format" in form.cleaned_data:
                # format is optional; None cannot be encoded as POST data
                del form.cleaned_data["format"]

            # Send our notification
            response = self.client.post("/notify/{}".format(key), form.cleaned_data)

            # No attachments allowed
            assert response.status_code == 400
            assert mock_notify.call_count == 0

        # Reset our mock object
        mock_notify.reset_mock()

        # Test Webhooks
        with mock.patch("requests.post") as mock_post:
            # Response object
            response = mock.Mock()
            response.status_code = requests.codes.ok
            mock_post.return_value = response

            with override_settings(APPRISE_WEBHOOK_URL="http://localhost/webhook/"):
                # Preare our form data
                form_data = {
                    "body": "test notifiction",
                }

                # At a minimum, just a body is required
                form = NotifyForm(data=form_data)
                assert form.is_valid()

                # Required to prevent None from being passed into
                # self.client.post()
                del form.cleaned_data["attachment"]
                if not form.cleaned_data.get("format") and "format" in form.cleaned_data:
                    # format is optional; None cannot be encoded as POST data
                    del form.cleaned_data["format"]

                # Send our notification
                response = self.client.post("/notify/{}".format(key), form.cleaned_data)

                # Test our results
                assert response.status_code == 200
                assert mock_notify.call_count == 1
                assert mock_post.call_count == 1

                # Reset our mock object
                mock_notify.reset_mock()

    @mock.patch("requests.request")
    def test_notify_with_tags(self, mock_post):
        """
        Test notification handling when setting tags
        """

        # Disable Throttling to speed testing
        apprise.plugins.NotifyBase.request_rate_per_sec = 0
        # Ensure we're enabled for the purpose of our testing
        N_MGR["json"].enabled = True

        # Prepare our response
        response = requests.Request()
        response.status_code = requests.codes.ok
        mock_post.return_value = response

        # our key to use
        key = "test_notify_with_tags"

        # Valid Yaml Configuration
        config = """
        urls:
          - json://user:pass@localhost:
              tag: home
        """

        # Load our configuration (it will be detected as YAML)
        response = self.client.post("/add/{}".format(key), {"config": config})
        assert response.status_code == 200

        # Preare our form data
        form_data = {
            "body": "test notifiction",
            "type": apprise.NotifyType.INFO.value,
            "format": apprise.NotifyFormat.TEXT.value,
        }

        # Send our notification
        response = self.client.post("/notify/{}".format(key), form_data)

        # Nothing could be notified as there were no tag matches
        assert response.status_code == 424
        assert mock_post.call_count == 0

        # Now let's send our notification by specifying the tag in the
        # parameters
        response = self.client.post("/notify/{}?tag=home".format(key), form_data)

        # Our notification was sent
        assert response.status_code == 200
        assert mock_post.call_count == 1

        # Test our posted data
        response = json.loads(mock_post.call_args_list[0][1]["data"])
        assert response["title"] == ""
        assert response["message"] == form_data["body"]
        assert response["type"] == apprise.NotifyType.INFO.value

        # Preare our form data (body is actually the minimum requirement)
        # All of the rest of the variables can actually be over-ridden
        # by the GET Parameter (ONLY if not otherwise identified in the
        # payload). The Payload contents of the POST request always take
        # priority to eliminate any ambiguity
        form_data = {
            "body": "test notifiction",
        }

        # Reset our mock object
        mock_post.reset_mock()

        # tags keyword is also supported
        response = self.client.post("/notify/{}?tags=home".format(key), form_data)

        # Our notification was sent
        assert response.status_code == 200
        assert mock_post.call_count == 1

        # Test our posted data
        response = json.loads(mock_post.call_args_list[0][1]["data"])
        assert response["title"] == ""
        assert response["message"] == form_data["body"]
        assert response["type"] == apprise.NotifyType.INFO.value

        # Preare our form data (body is actually the minimum requirement)
        # All of the rest of the variables can actually be over-ridden
        # by the GET Parameter (ONLY if not otherwise identified in the
        # payload). The Payload contents of the POST request always take
        # priority to eliminate any ambiguity
        form_data = {
            "body": "test notifiction",
        }

        # Reset our mock object
        mock_post.reset_mock()

        # Send our notification by specifying the tag in the parameters
        response = self.client.post(
            "/notify/{}?tag=home&format={}&type={}&title={}&body=ignored".format(
                key,
                apprise.NotifyFormat.TEXT.value,
                apprise.NotifyType.WARNING.value,
                "Test Title",
            ),
            form_data,
            content_type="application/json",
        )

        # Our notification was sent
        assert response.status_code == 200
        assert mock_post.call_count == 1

        response = json.loads(mock_post.call_args_list[0][1]["data"])
        assert response["title"] == "Test Title"
        assert response["message"] == form_data["body"]
        assert response["type"] == apprise.NotifyType.WARNING.value

    @mock.patch("requests.request")
    def test_notify_with_tags_via_apprise(self, mock_post):
        """
        Test notification handling when setting tags via the Apprise CLI
        """

        # Disable Throttling to speed testing
        apprise.plugins.NotifyBase.request_rate_per_sec = 0
        # Ensure we're enabled for the purpose of our testing
        N_MGR["json"].enabled = True

        # Prepare our response
        response = requests.Request()
        response.status_code = requests.codes.ok
        mock_post.return_value = response

        # our key to use
        key = "test_notify_with_tags_via_apprise"

        # Valid Yaml Configuration
        config = """
        urls:
          - json://user:pass@localhost:
              tag: home
        """

        # Load our configuration (it will be detected as YAML)
        response = self.client.post("/add/{}".format(key), {"config": config})
        assert response.status_code == 200

        # Reset our mock object
        mock_post.reset_mock()

        # Preare our form data
        form_data = {
            "body": "test notifiction",
            "type": apprise.NotifyType.INFO.value,
            "format": apprise.NotifyFormat.TEXT.value,
            # Support Array
            "tag": [("home", "summer-home")],
        }

        # Send our notification
        response = self.client.post(
            "/notify/{}/".format(key),
            content_type="application/json",
            data=form_data,
        )

        # Nothing could be notified as there were no tag matches for 'home'
        # AND 'summer-home'
        assert response.status_code == 424
        assert mock_post.call_count == 0

        # Reset our mock object
        mock_post.reset_mock()

        # Update our tags
        form_data["tag"] = ["home", "summer-home"]

        # Now let's send our notification by specifying the tag in the
        # parameters

        # Send our notification
        response = self.client.post(
            "/notify/{}/".format(key),
            content_type="application/json",
            data=form_data,
        )

        # Our notification was sent (as we matched 'home' OR' 'summer-home')
        assert response.status_code == 200
        assert mock_post.call_count == 1

        # Test our posted data
        response = json.loads(mock_post.call_args_list[0][1]["data"])
        assert response["title"] == ""
        assert response["message"] == form_data["body"]
        assert response["type"] == apprise.NotifyType.INFO.value

        # Reset our mock object
        mock_post.reset_mock()

        # use the `tags` keyword instead which is also supported
        del form_data["tag"]
        form_data["tags"] = ["home", "summer-home"]

        # Now let's send our notification by specifying the tag in the
        # parameters

        # Send our notification
        response = self.client.post(
            "/notify/{}/".format(key),
            content_type="application/json",
            data=form_data,
        )

        # Our notification was sent (as we matched 'home' OR' 'summer-home')
        assert response.status_code == 200
        assert mock_post.call_count == 1

        # Test our posted data
        response = json.loads(mock_post.call_args_list[0][1]["data"])
        assert response["title"] == ""
        assert response["message"] == form_data["body"]
        assert response["type"] == apprise.NotifyType.INFO.value

        # Reset our mock object
        mock_post.reset_mock()

        # use the `tag` and `tags` keyword causes tag to always take priority
        form_data["tag"] = ["invalid"]
        form_data["tags"] = ["home", "summer-home"]

        # Now let's send our notification by specifying the tag in the
        # parameters

        # Send our notification
        response = self.client.post(
            "/notify/{}/".format(key),
            content_type="application/json",
            data=form_data,
        )

        # Our notification failed because 'tag' took priority over 'tags' and
        # it contains an invalid entry
        assert response.status_code == 424
        assert mock_post.call_count == 0

        # Reset our mock object
        mock_post.reset_mock()

        # integers or non string not accepted
        form_data["tag"] = 42
        del form_data["tags"]

        # Now let's send our notification by specifying the tag in the
        # parameters

        # Send our notification
        response = self.client.post(
            "/notify/{}/".format(key),
            content_type="application/json",
            data=form_data,
        )

        # Our notification failed because no tags were loaded
        assert response.status_code == 400
        assert mock_post.call_count == 0

        # Reset our mock object
        mock_post.reset_mock()

        # integers or non string not accepted
        form_data["tag"] = [42, "valid", 5.4]

        # Now let's send our notification by specifying the tag in the
        # parameters

        # Send our notification
        response = self.client.post(
            "/notify/{}/".format(key),
            content_type="application/json",
            data=form_data,
        )

        # Malformed JSON tag members are rejected before reaching Apprise.
        assert response.status_code == 400
        assert mock_post.call_count == 0

        # Reset our mock object
        mock_post.reset_mock()

        # continued to verify the use of the `tag` and `tags` keyword
        # where tag priorities over tags
        form_data["tags"] = ["invalid"]
        form_data["tag"] = ["home", "summer-home"]

        # Now let's send our notification by specifying the tag in the
        # parameters

        # Send our notification
        response = self.client.post(
            "/notify/{}/".format(key),
            content_type="application/json",
            data=form_data,
        )

        # Our notification was sent (as we matched 'home' OR' 'summer-home')
        assert response.status_code == 200
        assert mock_post.call_count == 1

        # Test our posted data
        response = json.loads(mock_post.call_args_list[0][1]["data"])
        assert response["title"] == ""
        assert response["message"] == form_data["body"]
        assert response["type"] == apprise.NotifyType.INFO.value

        # Reset our mock object
        mock_post.reset_mock()

        # Preare our form data (body is actually the minimum requirement)
        # All of the rest of the variables can actually be over-ridden
        # by the GET Parameter (ONLY if not otherwise identified in the
        # payload). The Payload contents of the POST request always take
        # priority to eliminate any ambiguity
        form_data = {
            "body": "test notifiction",
        }

        # Send our notification by specifying the tag in the parameters
        response = self.client.post(
            "/notify/{}?tag=home&format={}&type={}&title={}&body=ignored".format(
                key,
                apprise.NotifyFormat.TEXT.value,
                apprise.NotifyType.WARNING.value,
                "Test Title",
            ),
            form_data,
            content_type="application/json",
        )

        # Our notification was sent
        assert response.status_code == 200
        assert mock_post.call_count == 1

        response = json.loads(mock_post.call_args_list[0][1]["data"])
        assert response["title"] == "Test Title"
        assert response["message"] == form_data["body"]
        assert response["type"] == apprise.NotifyType.WARNING.value

        # Test case where RequestDataTooBig thrown
        # Reset our mock object
        mock_post.reset_mock()

        with mock.patch("json.loads") as mock_loads:
            mock_loads.side_effect = RequestDataTooBig()
            # Send our notification by specifying the tag in the parameters
            response = self.client.post(
                f"/notify/{key}?tag=home&body=test",
                form_data,
                content_type="application/json",
            )

            # Our notification failed
            assert response.status_code == 431
            assert mock_post.call_count == 0

    @mock.patch("requests.request")
    def test_advanced_notify_with_tags(self, mock_post):
        """
        Test advanced notification handling when setting tags
        """

        # Disable Throttling to speed testing
        apprise.plugins.NotifyBase.request_rate_per_sec = 0
        # Ensure we're enabled for the purpose of our testing
        N_MGR["json"].enabled = True

        # Prepare our response
        response = requests.Request()
        response.status_code = requests.codes.ok
        mock_post.return_value = response

        # our key to use
        key = "test_adv_notify_with_tags"

        # Valid Yaml Configuration
        config = cleandoc(
            """
        version: 1
        tag: panic

        urls:
          - json://user:pass@localhost?+url=1:
             tag: devops, notify
          - json://user:pass@localhost?+url=2:
             tag: devops, high
          - json://user:pass@localhost?+url=3:
             tag: cris, emergency
        """
        )

        # Load our configuration (it will be detected as YAML)
        response = self.client.post("/add/{}".format(key), {"config": config})
        assert response.status_code == 200

        # Preare our form data
        form_data = {
            "body": "test notifiction",
            "type": apprise.NotifyType.INFO.value,
            "format": apprise.NotifyFormat.TEXT.value,
        }

        # Send our notification
        response = self.client.post("/notify/{}".format(key), form_data)

        # Nothing could be notified as there were no tag matches
        assert response.status_code == 424
        assert mock_post.call_count == 0

        # Let's identify a tag, but note that it won't match anything
        # parameters
        response = self.client.post("/notify/{}?tag=nomatch".format(key), form_data)

        # Nothing could be notified as there were no tag matches
        assert response.status_code == 424
        assert mock_post.call_count == 0

        # Now let's do devops AND notify
        response = self.client.post("/notify/{}?tag=devops notify".format(key), form_data)

        # Our notification was sent
        assert response.status_code == 200
        assert mock_post.call_count == 1

        # Test our posted data
        response = json.loads(mock_post.call_args_list[0][1]["data"])
        headers = mock_post.call_args_list[0][1]["headers"]
        assert response["title"] == ""
        assert response["message"] == form_data["body"]
        assert response["type"] == apprise.NotifyType.INFO.value
        # Verify we matched the first entry only
        assert headers["url"] == "1"

        # Reset our object
        mock_post.reset_mock()

        # Now let's do panic
        response = self.client.post("/notify/{}?tag=panic".format(key), form_data)

        # Our notification was sent to each match
        assert response.status_code == 200
        assert mock_post.call_count == 3

        # Reset our object
        mock_post.reset_mock()

        # Let's store our tag in our form
        form_data = {
            "body": "test notifiction",
            "type": apprise.NotifyType.INFO.value,
            "format": apprise.NotifyFormat.TEXT.value,
            # (devops AND cris) OR (notify AND high)
            "tag": "devops cris, notify high",
        }

        # Send our notification
        response = self.client.post("/notify/{}".format(key), form_data)

        # Nothing could be notified as there were no tag matches in our
        # form body that matched the anded comnbination
        assert response.status_code == 424
        assert mock_post.call_count == 0

        # Trigger on high OR emergency (some empty garbage at the end to
        # tidy/ignore
        form_data["tag"] = "high, emergency, , ,"

        # Send our notification
        response = self.client.post("/notify/{}".format(key), form_data)

        # Our notification was sent
        assert response.status_code == 200
        # We'll trigger on 2 entries
        assert mock_post.call_count == 2

        # Concurrent notifications may finish in either order.
        matched_urls = set()
        for call in mock_post.call_args_list:
            response = json.loads(call[1]["data"])
            headers = call[1]["headers"]
            assert response["title"] == ""
            assert response["message"] == form_data["body"]
            assert response["type"] == apprise.NotifyType.INFO.value
            matched_urls.add(headers["url"])
        # Verify we matched the second and third entries only
        assert matched_urls == {"2", "3"}

        # Reset our object
        mock_post.reset_mock()

        # Trigger on notify OR cris
        form_data["tag"] = "notify, cris"

        # Send our notification
        response = self.client.post("/notify/{}".format(key), form_data)

        # Our notification was sent
        assert response.status_code == 200
        # We'll trigger on 2 entries
        assert mock_post.call_count == 2

        # Concurrent notifications may finish in either order.
        matched_urls = set()
        for call in mock_post.call_args_list:
            response = json.loads(call[1]["data"])
            headers = call[1]["headers"]
            assert response["title"] == ""
            assert response["message"] == form_data["body"]
            assert response["type"] == apprise.NotifyType.INFO.value
            matched_urls.add(headers["url"])
        # Verify we matched the first and third entries only.
        assert matched_urls == {"1", "3"}

        # Reset our object
        mock_post.reset_mock()

        # Trigger on notify AND cris (should not match anything)
        form_data["tag"] = "notify cris"

        # Send our notification
        response = self.client.post("/notify/{}".format(key), form_data)

        assert response.status_code == 424
        assert mock_post.call_count == 0

        # Reset our object
        mock_post.reset_mock()

        # Invalid characters in our tag
        form_data["tag"] = "$"

        # Send our notification
        response = self.client.post("/notify/{}".format(key), form_data)

        # Our notification was sent
        assert response.status_code == 400
        # We'll trigger on 2 entries
        assert mock_post.call_count == 0

    @mock.patch("apprise.NotifyBase.notify")
    def test_partial_notify_by_loaded_urls(self, mock_notify):
        """
        Test notification handling when one or more of the services
        can not be notified.
        """

        # our key to use
        key = "test_partial_notify_by_loaded_urls"

        # Add some content
        response = self.client.post(
            "/add/{}".format(key),
            {
                "urls": ", ".join(
                    [
                        "mailto://user:pass@hotmail.com",
                        "mailto://user:pass@gmail.com",
                    ]
                ),
            },
        )
        assert response.status_code == 200

        # Preare our form data
        form_data = {
            "body": "test notifiction",
        }

        # At a minimum, just a body is required
        form = NotifyForm(data=form_data)
        assert form.is_valid()

        # we always set a type if one wasn't done so already
        assert form.cleaned_data["type"] == apprise.NotifyType.INFO.value

        # format is entirely optional; it stays unset (None) if the caller
        # never specified one, rather than defaulting to TEXT
        assert form.cleaned_data["format"] is None

        # Required to prevent None from being passed into self.client.post()
        del form.cleaned_data["attachment"]
        if not form.cleaned_data.get("format") and "format" in form.cleaned_data:
            # format is optional; None cannot be encoded as POST data
            del form.cleaned_data["format"]

        # Set our return value; first we return a true, then we fail
        # on the second call
        mock_notify.side_effect = (True, False)

        # Send our notification
        response = self.client.post("/notify/{}".format(key), form.cleaned_data)
        assert response.status_code == 424
        assert mock_notify.call_count == 2

        # One more test but we test our URL fetching
        mock_notify.side_effect = True

        # Reset our mock object
        mock_notify.reset_mock()

        # Preare our form data
        form_data = {
            "body": "test notifiction",
            "attachment": "https://localhost/invalid/path/to/image.png",
        }

        # Send our notification
        response = self.client.post("/notify/{}".format(key), form_data)
        # We fail because we couldn't retrieve our attachment
        assert response.status_code == 400
        assert mock_notify.call_count == 0

        # Reset our mock object
        mock_notify.reset_mock()

        # Preare our form data
        form_data = {
            "body": "test notifiction",
            "attach": "https://localhost/invalid/path/to/image.png",
        }

        # Send our notification
        response = self.client.post("/notify/{}".format(key), form_data)
        # We fail because we couldn't retrieve our attachment
        assert response.status_code == 400
        assert mock_notify.call_count == 0

        # Reset our mock object
        mock_notify.reset_mock()

        # Preare our form data
        form_data = {
            "body": "test notifiction",
            "attachments": "https://localhost/invalid/path/to/image.png",
        }

        # Send our notification
        response = self.client.post("/notify/{}".format(key), form_data)
        # We fail because we couldn't retrieve our attachment
        assert response.status_code == 400
        assert mock_notify.call_count == 0

        # Reset our mock object
        mock_notify.reset_mock()

        # Verify each alias alone (no body) routes to Bad Attachment, not the
        # minimum-requirements gate — confirming attach-only payloads are valid.
        for _alias in ("attach", "attachment", "attachments"):
            form_data = {
                _alias: "https://localhost/invalid/path/to/image.png",
            }
            response = self.client.post("/notify/{}".format(key), form_data)
            assert response.status_code == 400
            assert b"Bad Attachment" in response.content
            assert mock_notify.call_count == 0
            mock_notify.reset_mock()

    @mock.patch("apprise.Apprise.notify")
    def test_notify_by_loaded_urls_with_json(self, mock_notify):
        """
        Test adding a simple notification and notifying it using JSON
        """

        # Set our return value
        mock_notify.return_value = notify_result(True)

        # our key to use
        key = "test_notify_by_loaded_urls_with_json"

        # Add some content
        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200

        # Preare our JSON data
        json_data = {
            "body": "test notification",
            "type": apprise.NotifyType.WARNING.value,
        }

        # Send our notification as a JSON object
        response = self.client.post(
            "/notify/{}".format(key),
            data=json.dumps(json_data),
            content_type="application/json",
        )

        # Still supported
        assert response.status_code == 200
        assert mock_notify.call_count == 1

        # Reset our mock object
        mock_notify.reset_mock()

        # Test referencing a key that doesn't exist
        response = self.client.post(
            "/notify/non-existant-key",
            data=json.dumps(json_data),
            content_type="application/json",
        )

        # Nothing notified
        assert response.status_code == 204
        assert mock_notify.call_count == 0

        # Test sending a garbage JSON object
        response = self.client.post(
            "/notify/{}".format(key),
            data="{",
            content_type="application/json",
        )

        assert response.status_code == 400
        assert mock_notify.call_count == 0

        # Test sending with an invalid content type
        response = self.client.post(
            "/notify/{}".format(key),
            data="{}",
            content_type="application/xml",
        )

        assert response.status_code == 400
        assert mock_notify.call_count == 0

        # Test sending without any content at all
        response = self.client.post(
            "/notify/{}".format(key),
            data="{}",
            content_type="application/json",
        )

        assert response.status_code == 400
        assert mock_notify.call_count == 0

        # Test sending without a body
        json_data = {
            "type": apprise.NotifyType.WARNING.value,
        }

        response = self.client.post(
            "/notify/{}".format(key),
            data=json.dumps(json_data),
            content_type="application/json",
        )

        assert response.status_code == 400
        assert mock_notify.call_count == 0

        # Test inability to prepare writing config to disk
        json_data = {"body": "test message"}

        # Test the handling of underlining disk/write exceptions
        with mock.patch("gzip.open") as mock_open:
            mock_open.side_effect = OSError()
            # We'll fail to write our key now
            response = self.client.post(
                "/notify/{}".format(key),
                data=json.dumps(json_data),
                content_type="application/json",
            )

            # internal errors are correctly identified
            assert response.status_code == 500
            assert mock_notify.call_count == 0

        # Reset our mock object
        mock_notify.reset_mock()

        # Test with invalid format
        json_data = {"body": "test message", "format": "invalid"}

        # Test case with format set to invalid
        response = self.client.post(
            "/notify/{}".format(key),
            data=json.dumps(json_data),
            content_type="application/json",
        )

        assert response.status_code == 400
        assert mock_notify.call_count == 0

        # Reset our mock object
        mock_notify.reset_mock()

        # If an empty format is specified, it is accepted and
        # no imput format is specified
        json_data = {
            "body": "test message",
            "format": None,
        }

        # Test case with format changed
        response = self.client.post(
            "/notify/{}".format(key),
            data=json.dumps(json_data),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert mock_notify.call_count == 1

        # Reset our mock object
        mock_notify.reset_mock()

        # If an empty format is specified, it is accepted and
        # no imput format is specified
        json_data = {
            "body": "test message",
            "format": None,
            "attach": "https://localhost/invalid/path/to/image.png",
        }

        # Test case with format changed
        response = self.client.post(
            "/notify/{}".format(key),
            data=json.dumps(json_data),
            content_type="application/json",
        )

        # We failed to send notification because we couldn't fetch the
        # attachment
        assert response.status_code == 400
        assert mock_notify.call_count == 0

        # Reset our mock object
        mock_notify.reset_mock()

        json_data = {
            "body": "test message",
            "format": None,
            "attachments": "https://localhost/invalid/path/to/image.png",
        }

        response = self.client.post(
            "/notify/{}".format(key),
            data=json.dumps(json_data),
            content_type="application/json",
        )

        # We failed to send notification because we couldn't fetch the
        # attachment
        assert response.status_code == 400
        assert mock_notify.call_count == 0

        # Reset our mock object
        mock_notify.reset_mock()

        # Every alias works without a body and reaches attachment validation.
        for _alias in ("attach", "attachment", "attachments"):
            json_data = {
                _alias: "https://localhost/invalid/path/to/image.png",
            }
            response = self.client.post(
                "/notify/{}".format(key),
                data=json.dumps(json_data),
                content_type="application/json",
            )
            assert response.status_code == 400
            assert b"Bad Attachment" in response.content
            assert mock_notify.call_count == 0
            mock_notify.reset_mock()

        json_data = {
            "body": "test message",
        }

        # Same results for any empty string:
        response = self.client.post(
            "/notify/{}".format(key),
            data=json.dumps(json_data),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert mock_notify.call_count == 1

        # Reset our mock object
        mock_notify.reset_mock()

        headers = {
            "HTTP_X_APPRISE_LOG_LEVEL": "debug",
            # Accept is over-ridden to be that of the content type
            "HTTP_ACCEPT": "text/plain",
        }

        # Test referencing a key that doesn't exist
        response = self.client.post(
            "/notify/{}".format(key),
            data=json.dumps(json_data),
            content_type="application/json",
            **headers,
        )

        assert response.status_code == 200
        assert mock_notify.call_count == 1
        assert response["content-type"] == "text/plain"

        mock_notify.reset_mock()

        headers = {
            "HTTP_X_APPRISE_LOG_LEVEL": "debug",
            # Accept is over-ridden to be that of the content type
            "HTTP_ACCEPT": "text/html",
        }

        # Test referencing a key that doesn't exist
        response = self.client.post(
            "/notify/{}".format(key),
            data=json.dumps(json_data),
            content_type="application/json",
            **headers,
        )

        assert response.status_code == 200
        assert mock_notify.call_count == 1
        assert response["content-type"] == "text/html"

        mock_notify.reset_mock()

        # Test referencing a key that doesn't exist
        response = self.client.post(
            "/notify/{}".format(key),
            data={"body": "test"},
            **headers,
        )

        assert response.status_code == 200
        assert mock_notify.call_count == 1
        assert response["content-type"].startswith("text/html")

        mock_notify.reset_mock()

        headers = {
            "HTTP_X_APPRISE_LOG_LEVEL": "debug",
            "HTTP_ACCEPT": "*/*",
        }

        # Test referencing a key that doesn't exist
        response = self.client.post(
            "/notify/{}".format(key),
            data=json.dumps(json_data),
            content_type="application/json",
            **headers,
        )

        assert response.status_code == 200
        assert mock_notify.call_count == 1
        assert response["content-type"] == "application/json"

        headers = {
            "HTTP_X_APPRISE_LOG_LEVEL": "invalid",
            "HTTP_ACCEPT": "text/*",
        }

        mock_notify.reset_mock()

        # Test referencing a key that doesn't exist
        response = self.client.post(
            "/notify/{}".format(key),
            data=json.dumps(json_data),
            content_type="application/json",
            **headers,
        )

        assert response.status_code == 200
        assert mock_notify.call_count == 1
        assert response["content-type"] == "text/html"

        mock_notify.reset_mock()

        # Test referencing a key that doesn't exist
        response = self.client.post(
            "/notify/{}".format(key),
            data=json_data,
            **headers,
        )

        assert response.status_code == 200
        assert mock_notify.call_count == 1
        assert response["content-type"].startswith("text/html")

    @mock.patch("apprise.plugins.email.NotifyEmail.send")
    def test_notify_with_filters(self, mock_send):
        """
        Test workings of APPRISE_DENY_SERVICES and APPRISE_ALLOW_SERVICES
        """

        # Set our return value
        mock_send.return_value = True

        # our key to use
        key = "test_notify_with_restrictions"

        # Add some content
        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200

        # Preare our JSON data
        json_data = {
            "body": "test notifiction",
            "type": apprise.NotifyType.WARNING.value,
        }

        # Verify by default email is enabled
        assert N_MGR["mailto"].enabled is True

        # Send our service with the `mailto://` denied
        with override_settings(APPRISE_ALLOW_SERVICES=""), override_settings(APPRISE_DENY_SERVICES="mailto"):
            # Send our notification as a JSON object
            response = self.client.post(
                "/notify/{}".format(key),
                data=json.dumps(json_data),
                content_type="application/json",
            )

            # mailto:// is disabled
            assert response.status_code == 424
            assert mock_send.call_count == 0

            # What actually took place behind close doors:
            assert N_MGR["mailto"].enabled is False

            # Reset our flag (for next test)
            N_MGR["mailto"].enabled = True

        # Reset Mock
        mock_send.reset_mock()

        # Send our service with the `mailto://` denied
        with override_settings(APPRISE_ALLOW_SERVICES=""), override_settings(APPRISE_DENY_SERVICES="invalid, syslog"):
            # Send our notification as a JSON object
            response = self.client.post(
                "/notify/{}".format(key),
                data=json.dumps(json_data),
                content_type="application/json",
            )

            # mailto:// is enabled
            assert response.status_code == 200
            assert mock_send.call_count == 1

            # Verify that mailto was never turned off
            assert N_MGR["mailto"].enabled is True

        # Reset Mock
        mock_send.reset_mock()

        # Send our service with the `mailto://` being the only accepted type
        with override_settings(APPRISE_ALLOW_SERVICES="mailto"), override_settings(APPRISE_DENY_SERVICES=""):
            # Send our notification as a JSON object
            response = self.client.post(
                "/notify/{}".format(key),
                data=json.dumps(json_data),
                content_type="application/json",
            )

            # mailto:// is enabled
            assert response.status_code == 200
            assert mock_send.call_count == 1

            # Verify email was never turned off
            assert N_MGR["mailto"].enabled is True

        # Reset Mock
        mock_send.reset_mock()

        # Send our service with the `mailto://` being the only accepted type
        with override_settings(APPRISE_ALLOW_SERVICES="invalid, mailtos"), override_settings(APPRISE_DENY_SERVICES=""):
            # Send our notification as a JSON object
            response = self.client.post(
                "/notify/{}".format(key),
                data=json.dumps(json_data),
                content_type="application/json",
            )

            # mailto:// is enabled
            assert response.status_code == 200
            assert mock_send.call_count == 1

            # Verify email was never turned off
            assert N_MGR["mailto"].enabled is True

        # Reset Mock
        mock_send.reset_mock()

        # Send our service with the `mailto://` being the only accepted type
        with override_settings(APPRISE_ALLOW_SERVICES="syslog"), override_settings(APPRISE_DENY_SERVICES=""):
            # Send our notification as a JSON object
            response = self.client.post(
                "/notify/{}".format(key),
                data=json.dumps(json_data),
                content_type="application/json",
            )

            # mailto:// is disabled
            assert response.status_code == 424
            assert mock_send.call_count == 0

            # What actually took place behind close doors:
            assert N_MGR["mailto"].enabled is False

            # Reset our flag (for next test)
            N_MGR["mailto"].enabled = True

        # Reset Mock
        mock_send.reset_mock()

        # Test case where there is simply no over-rides defined
        with override_settings(APPRISE_ALLOW_SERVICES=""), override_settings(APPRISE_DENY_SERVICES=""):
            # Send our notification as a JSON object
            response = self.client.post(
                "/notify/{}".format(key),
                data=json.dumps(json_data),
                content_type="application/json",
            )

            # json:// is disabled
            assert response.status_code == 200
            assert mock_send.call_count == 1

            # nothing was changed
            assert N_MGR["mailto"].enabled is True

    @override_settings(APPRISE_RECURSION_MAX=1)
    @mock.patch("apprise.Apprise.notify")
    def test_stateful_notify_recursion(self, mock_notify):
        """
        Test recursion an id header details as part of post
        """

        # Set our return value
        mock_notify.return_value = notify_result(True)

        # our key to use
        key = "test_stateful_notify_recursion"

        # Add some content
        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200

        # Form data
        form_data = {
            "body": "test notifiction",
        }

        # Define our headers we plan to pass along with our request
        headers = {
            "HTTP_X-APPRISE-ID": "abc123",
            "HTTP_X-APPRISE-RECURSION-COUNT": str(1),
        }

        # Send our notification
        response = self.client.post("/notify/{}".format(key), data=form_data, **headers)
        assert response.status_code == 200
        assert mock_notify.call_count == 1

        headers = {
            # Header specified but with whitespace
            "HTTP_X-APPRISE-ID": "  ",
            # No Recursion value specified
        }

        # Reset our mock object
        mock_notify.reset_mock()

        # Recursion limit reached
        response = self.client.post("/notify/{}".format(key), data=form_data, **headers)
        assert response.status_code == 200
        assert mock_notify.call_count == 1

        headers = {
            "HTTP_X-APPRISE-ID": "abc123",
            # Recursion Limit hit
            "HTTP_X-APPRISE-RECURSION-COUNT": str(2),
        }

        # Reset our mock object
        mock_notify.reset_mock()

        # Recursion limit reached
        response = self.client.post("/notify/{}".format(key), data=form_data, **headers)
        assert response.status_code == 406
        assert mock_notify.call_count == 0

        headers = {
            "HTTP_X-APPRISE-ID": "abc123",
            # Negative recursion value (bad request)
            "HTTP_X-APPRISE-RECURSION-COUNT": str(-1),
        }

        # Reset our mock object
        mock_notify.reset_mock()

        # invalid recursion specified
        response = self.client.post("/notify/{}".format(key), data=form_data, **headers)
        assert response.status_code == 400
        assert mock_notify.call_count == 0

        headers = {
            "HTTP_X-APPRISE-ID": "abc123",
            # Invalid recursion value (bad request)
            "HTTP_X-APPRISE-RECURSION-COUNT": "invalid",
        }

        # Reset our mock object
        mock_notify.reset_mock()

        # invalid recursion specified
        response = self.client.post("/notify/{}".format(key), data=form_data, **headers)
        assert response.status_code == 400
        assert mock_notify.call_count == 0

    @mock.patch("apprise.Apprise.notify")
    def test_stateful_notify_preserves_mapping_case(self, mock_notify):
        """Rule-based field mapping preserves source key case."""

        mock_notify.return_value = notify_result(True)

        key = "test_notify_rule_mapping_preserves_source_case"

        response = self.client.post(
            "/add/{}".format(key),
            {"urls": "mailto://user:pass@yahoo.ca"},
        )
        assert response.status_code == 200

        #
        # JSON payload using mixed-case source keys
        #
        json_data = {
            "Title": "Test Notification",
            "Description": "Test Notification Description",
        }

        response = self.client.post(
            "/notify/{}?:Title=title&:Description=body".format(key),
            data=json.dumps(json_data),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert mock_notify.call_count == 1

        mock_notify.reset_mock()

        #
        # Case-sensitive verification:
        # lower-case payload should not match mixed-case rule names
        #
        json_data = {
            "title": "Test Notification",
            "description": "Test Notification Description",
        }

        response = self.client.post(
            "/notify/{}?:Title=title&:Description=body".format(key),
            data=json.dumps(json_data),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert mock_notify.call_count == 0

    @mock.patch("apprise.Apprise.notify")
    def test_notify_invalid_default_log_level(self, mock_notify):
        """
        Test that a request proceeds when the configured default log level is
        not one of the recognised values.  The level string then falls through
        every branch in the level-to-int conversion chain, covering the final
        'elif level == "TRACE"' False branch.
        """
        mock_notify.return_value = notify_result(True)

        key = "test_notify_invalid_default_log_level"

        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200

        from django.conf import settings as _settings

        bad_logging = {
            **_settings.LOGGING,
            "loggers": {
                **_settings.LOGGING["loggers"],
                "apprise": {
                    **_settings.LOGGING["loggers"]["apprise"],
                    "level": "NOTSET",
                },
            },
        }
        with override_settings(LOGGING=bad_logging):
            response = self.client.post("/notify/{}".format(key), {"body": "test"})
        assert response.status_code == 200

    @mock.patch("apprise.Apprise.notify")
    def test_notify_subfield_mapping(self, mock_notify):
        """Test stateful nested-field mapping.

        Missing paths warn and return 400 without sending.
        """
        mock_notify.return_value = notify_result(True)

        key = "test_notify_subfield_mapping"

        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200

        # Form posts are flat strings, so nested paths cannot be resolved.
        response = self.client.post(
            f"/notify/{key}/?:event.title=title&:event.body=body",
            {"event": '{"title": "hi", "body": "world"}'},
        )
        assert response.status_code == 400
        assert mock_notify.call_count == 0

        mock_notify.reset_mock()

        # Successful subfield mapping — JSON payload
        with self.assertLogs("django", level="WARNING") as _:
            # event.missing does not exist → mapping fails → 400
            response = self.client.post(
                f"/notify/{key}/?:event.missing=body",
                data=json.dumps({"event": {"title": "hi"}}),
                content_type="application/json",
            )
        assert response.status_code == 400
        assert mock_notify.call_count == 0

        mock_notify.reset_mock()

        # Depth exceeded — JSON payload
        with self.assertLogs("django", level="WARNING") as _, override_settings(APPRISE_WEBHOOK_MAPPING_MAX_DEPTH=1):
            response = self.client.post(
                f"/notify/{key}/?:event.title=body",
                data=json.dumps({"event": {"title": "hi"}}),
                content_type="application/json",
            )
        assert response.status_code == 400
        assert mock_notify.call_count == 0

        mock_notify.reset_mock()

        # Valid nested mapping succeeds — JSON payload
        response = self.client.post(
            f"/notify/{key}/?:event.title=body",
            data=json.dumps({"event": {"title": "hello world"}}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert mock_notify.call_count == 1

    @mock.patch("apprise.Apprise.notify")
    def test_notify_streams_logs_and_result(self, mock_notify):
        """Stateful notifications can stream progress and results live."""
        fake_service = type("FakeService", (), {"service_name": "JSON"})()

        def fake_notify(*args, **kwargs):
            log_callback = kwargs["log_callback"]
            log_callback(
                apprise.NotifyLogEntry(level="INFO", message="Sent JSON POST notification."),
                fake_service,
            )
            return notify_result(True)

        mock_notify.side_effect = fake_notify

        key = "test_notify_stream_emits_log_and_result_events"
        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200

        response = self.client.post(
            "/notify/{}".format(key),
            {"body": "hello"},
            HTTP_ACCEPT="text/event-stream",
        )
        assert response.status_code == 200
        assert response["Content-Type"] == "text/event-stream"

        body = b"".join(response.streaming_content).decode("utf-8")
        assert "event: log" in body
        assert "Sent JSON POST notification." in body
        assert '"service": "JSON"' in body
        # Keep sensitive notification targets out of the stream.
        assert "yahoo.ca" not in body
        assert "event: result" in body
        assert '"status": "SUCCESS"' in body

    @mock.patch("api.views.send_webhook")
    @mock.patch("apprise.Apprise.notify")
    def test_notify_stream_sends_webhook(self, mock_notify, mock_webhook):
        """Stateful streams send their completion webhook."""
        mock_notify.return_value = notify_result(True)
        payload = {}

        # Consume the bounded webhook while its result storage is still open.
        mock_webhook.side_effect = lambda chunks: payload.update(json.loads("".join(chunks)))

        key = "test_notify_stream_sends_webhook"
        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200

        with override_settings(APPRISE_WEBHOOK_URL="https://localhost/webhook"):
            response = self.client.post(
                "/notify/{}".format(key),
                {"body": "hello"},
                HTTP_ACCEPT="text/event-stream",
            )
            b"".join(response.streaming_content)

        mock_webhook.assert_called_once()
        assert payload["status"] == 0
        assert isinstance(payload["output"], list)

    @mock.patch("api.views.send_webhook", side_effect=RuntimeError("boom"))
    @mock.patch("apprise.Apprise.notify")
    def test_notify_stream_contains_webhook_error(self, mock_notify, mock_webhook):
        """A broken completion webhook does not change the stream result."""
        mock_notify.return_value = notify_result(True)
        key = "test_notify_stream_contains_webhook_error"
        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200

        with (
            override_settings(APPRISE_WEBHOOK_URL="https://localhost/webhook"),
            self.assertLogs("django", level="ERROR"),
        ):
            response = self.client.post(
                "/notify/{}".format(key),
                {"body": "hello"},
                HTTP_ACCEPT="text/event-stream",
            )
            body = b"".join(response.streaming_content).decode("utf-8")

        mock_webhook.assert_called_once()
        assert "event: result" in body
        assert '"status": "SUCCESS"' in body

    @mock.patch("apprise.Apprise.notify")
    def test_notify_stream_skips_gzip(self, mock_notify):
        """Stateful event streams remain uncompressed for live delivery."""
        fake_service = type("FakeService", (), {"service_name": "JSON"})()

        def fake_notify(*args, **kwargs):
            kwargs["log_callback"](
                apprise.NotifyLogEntry(level="INFO", message="Sent JSON POST notification."),
                fake_service,
            )
            return notify_result(True)

        mock_notify.side_effect = fake_notify

        key = "test_notify_stream_skips_gzip"
        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200

        response = self.client.post(
            "/notify/{}?stream=yes".format(key),
            {"body": "hello"},
            HTTP_ACCEPT_ENCODING="gzip, deflate",
        )
        assert response.status_code == 200
        assert response["Content-Encoding"] == "identity"

        # Gzip content would not decode directly as UTF-8.
        body = b"".join(response.streaming_content).decode("utf-8")
        assert "event: log" in body
        assert "event: result" in body

    @mock.patch("apprise.Apprise.notify")
    def test_notify_stream_via_query_string(self, mock_notify):
        """The stream query parameter works without an Accept header."""
        mock_notify.return_value = notify_result(True)

        key = "test_notify_stream_via_query_string_fallback"
        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200

        response = self.client.post("/notify/{}?stream=1".format(key), {"body": "hello"})
        assert response.status_code == 200
        assert response["Content-Type"] == "text/event-stream"

        body = b"".join(response.streaming_content).decode("utf-8")
        assert "event: result" in body

    @mock.patch("apprise.Apprise.notify")
    def test_notify_stream_ignores_empty_query_value(self, mock_notify):
        """An empty stream parameter uses the normal response."""
        mock_notify.return_value = notify_result(True)

        key = "test_notify_stream_ignores_empty_query_value"
        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200

        response = self.client.post("/notify/{}?stream=".format(key), {"body": "hello"})
        assert response.status_code == 200
        assert response["Content-Type"] != "text/event-stream"

    @mock.patch("api.views.send_webhook")
    @mock.patch("apprise.Apprise.notify")
    def test_notify_stream_reports_error(self, mock_notify, mock_webhook):
        """Notification exceptions end the stream with an error event."""
        mock_notify.side_effect = ValueError("boom")
        payload = {}

        # Capture the generated failure payload inside the worker thread.
        mock_webhook.side_effect = lambda chunks: payload.update(json.loads("".join(chunks)))

        key = "test_notify_stream_reports_an_unexpected_notify_failure"
        response = self.client.post("/add/{}".format(key), {"urls": "mailto://user:pass@yahoo.ca"})
        assert response.status_code == 200

        with override_settings(APPRISE_WEBHOOK_URL="https://localhost/webhook"):
            response = self.client.post(
                "/notify/{}".format(key),
                {"body": "hello"},
                HTTP_ACCEPT="text/event-stream",
            )
            assert response.status_code == 200
            body = b"".join(response.streaming_content).decode("utf-8")

        assert "event: error" in body
        assert "Notification processing failed." in body
        assert "boom" not in body
        assert payload["status"] == 1
