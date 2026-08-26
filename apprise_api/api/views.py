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
from collections import deque
import json
import logging
import queue
import re
import struct
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import apprise
from core.utils import parse_bool, parse_log_level
from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.urls import Resolver404, resolve, reverse
from django.utils.decorators import method_decorator
from django.utils.html import escape
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.gzip import gzip_page
from error.views import Error421View

from .forms import (
    AUTO_DETECT_CONFIG_KEYWORD,
    CONFIG_FORMATS,
    AddByConfigForm,
    AddByUrlForm,
    AuthForm,
    BrowserLoginForm,
    ConfigKeyForm,
    MoveConfigForm,
    NotifyByUrlForm,
    NotifyForm,
)
from .payload_mapper import remap_fields
from .responses import error_response
from .utils import (
    AUTH_ROLE_ADMIN,
    AUTH_ROLE_DISABLED,
    AUTH_ROLE_USER,
    CONFIG_KEY_HEADER,
    CONFIG_KEY_PATTERN,
    MIME_IS_JSON,
    WEB_AUTH_COOKIE,
    WEB_AUTH_HEADER,
    ConfigCache,
    MoveResult,
    apply_global_filters,
    can_list_configurations,
    can_move_or_delete_configuration,
    clear_web_auth_cookie,
    config_auth_state,
    config_key_header_present_but_invalid,
    global_credentials_ok,
    healthcheck,
    is_html_response,
    is_json_response,
    key_auth_ok,
    key_credentials_ok,
    parse_attachments,
    resolve_config_key,
    send_webhook,
    set_web_auth_cookie,
    stateful_store_enabled,
)

# Get an instance of a logger
logger = logging.getLogger("django")

# Content-Type Parsing
# application/x-www-form-urlencoded
# multipart/form-data
MIME_IS_FORM = re.compile(r"(multipart|application)/(x-www-)?form-(data|urlencoded)", re.I)

# Each disk entry starts with its byte length.
_EVENT_SIZE = struct.Struct("!Q")

# Report the first event moved to disk.
_STREAM_PUT_SPOOLED = "spooled"

# Report an event that could not be retained.
_STREAM_PUT_FAILED = "failed"

# This safe message is shown to a client after temporary storage is lost.
_STREAM_STORAGE_WARNING = (
    "Live log delivery reached a server storage limit or error. Some log "
    "entries may be unavailable, but notification processing is continuing. "
    "Please contact the server administrator."
)


def _safe_stream_log(level, message, *args, exc_info=None):
    """Write an operational log without affecting notification delivery."""
    try:  # noqa: SIM105 - keep the reason for suppressing handler failures clear.
        logger.log(level, message, *args, exc_info=exc_info)
    except Exception:
        # A custom or broken logging handler must not stop notification work.
        pass


class _SpooledEventQueue:
    """Keep a FIFO event stream in memory, then an anonymous temp file."""

    def __init__(
        self,
        memory_bytes=None,
        disk_bytes=None,
    ):
        # Keep this much waiting log data in memory before using disk.
        # Tests may provide a value instead of using the server setting.
        self._memory_byte_limit = settings.APPRISE_STREAM_MEMORY_SIZE if memory_bytes is None else memory_bytes

        # Limit temporary disk use for this response.
        self._disk_byte_limit = settings.APPRISE_STREAM_DISK_SIZE if disk_bytes is None else disk_bytes

        # Memory holds the oldest events until its allowance is filled.
        self._memory = deque()

        # Count encoded bytes so non-ASCII text uses the correct allowance.
        self._memory_bytes = 0

        # Producers wake the response thread whenever a new event is ready.
        self._condition = threading.Condition()

        # Open the temporary file only when memory is full.
        self._spool = None

        # Point to the next disk event to read.
        self._read_offset = 0

        # Point to where the next complete disk event will be written.
        self._write_offset = 0

        # Number of complete events currently waiting on disk.
        self._disk_count = 0

        # Number of retained events still waiting for the client.
        self._backlog = 0

        # Largest backlog observed during this response.
        self._max_backlog = 0

        # Total number of events moved to temporary disk storage.
        self._spooled_count = 0

        # Total number of events that could not be retained.
        self._unavailable_count = 0

        # Report the first move to disk only once.
        self._reported_slow = False

        # Tell the response generator to send one visible storage warning.
        self._storage_failed = False

        # Avoid repeated disk work after temporary storage itself fails.
        self._disk_unavailable = False

        # Closed queues ignore entries produced after a client disconnects.
        self._closed = False

    def put(self, event):
        """Queue an event and report the first spill or storage failure."""
        with self._condition:
            # A disconnected response no longer accepts log events.
            if self._closed:
                return None

            # Limits are measured in encoded bytes, matching disk usage.
            payload = event.encode("utf-8")

            # A zero disk limit intentionally retains all events in memory.
            memory_only = self._disk_byte_limit == 0 and self._memory_byte_limit > 0

            if self._spool is None and (
                memory_only
                or (self._memory_byte_limit > 0 and self._memory_bytes + len(payload) <= self._memory_byte_limit)
            ):
                # Keep early events in memory while space remains.
                self._memory.append((event, len(payload)))
                self._memory_bytes += len(payload)

            else:
                if not self._disk_byte_limit:
                    # Both limits are zero, so backlog retention is disabled.
                    self._record_storage_limit("Notification stream buffering is disabled.")
                    self._unavailable_count += 1

                    return _STREAM_PUT_FAILED

                if self._disk_unavailable:
                    # Keep notification work moving after storage is lost.
                    self._unavailable_count += 1

                    return None

                # Remember where valid disk content ends before writing.
                previous_offset = self._write_offset

                # Prefix the event with its size so it can be read safely.
                record = _EVENT_SIZE.pack(len(payload)) + payload

                if previous_offset + len(record) > self._disk_byte_limit:
                    self._record_storage_limit("Notification stream reached its configured temporary storage limit.")
                    self._unavailable_count += 1

                    return _STREAM_PUT_FAILED

                if self._spool is None:
                    # TemporaryFile is unlinked automatically when closed.
                    # It stays open until the stream drains or disconnects.
                    try:
                        self._spool = tempfile.TemporaryFile(  # noqa: SIM115
                            mode="w+b", buffering=0
                        )
                    except OSError as error:
                        # Creation failure leaves existing memory untouched.
                        self._record_storage_failure("creation", error)
                        self._unavailable_count += 1

                        return _STREAM_PUT_FAILED

                try:
                    # Write after the last complete event.
                    self._spool.seek(previous_offset)
                    if self._spool.write(record) != len(record):
                        raise OSError("Temporary storage accepted an incomplete event.")
                except (OSError, ValueError) as error:
                    # Remove a partial tail while preserving earlier events.
                    try:
                        # Discard only the incomplete record at the file end.
                        self._spool.seek(previous_offset)
                        self._spool.truncate()

                    except (OSError, ValueError):
                        # The original write error remains the useful failure.
                        pass

                    self._record_storage_failure("write", error)
                    self._unavailable_count += 1

                    if not self._disk_count:
                        # No valid disk events remain for a reader to collect.
                        self._close_spool()

                    return _STREAM_PUT_FAILED

                # Advance the valid end only after the full record is written.
                self._write_offset = previous_offset + len(record)

                # This complete event is now available to the reader.
                self._disk_count += 1

                # Keep a lifetime total even after this event is read.
                self._spooled_count += 1

            # Count only events that can still be delivered to the client.
            self._backlog += 1

            # Preserve the highest point for the final operational log.
            self._max_backlog = max(self._max_backlog, self._backlog)

            # Wake one response reader waiting for an event.
            self._condition.notify()

            if self._spool is not None and not self._reported_slow:
                # Tell the caller only about the first move to disk.
                self._reported_slow = True

                return _STREAM_PUT_SPOOLED

            return None

    def get(self, timeout=None):
        """Return the next event, waiting up to timeout when provided."""
        # Use a fixed deadline so wakeups do not extend the requested wait.
        deadline = time.monotonic() + timeout if timeout is not None else None

        with self._condition:
            while not self._backlog and not self._closed:
                # Recalculate only the time left before the fixed deadline.
                remaining = None if deadline is None else deadline - time.monotonic()

                if remaining is not None and remaining <= 0:
                    raise queue.Empty

                # Sleep until a producer adds work, closes, or time expires.
                self._condition.wait(remaining)

            if not self._backlog:
                # Closed and empty queues use the same signal as a timeout.
                raise queue.Empty

            if self._memory:
                # Memory is older than disk because it was filled first.
                event, size = self._memory.popleft()

                # Return these bytes to the in-memory allowance.
                self._memory_bytes -= size

            else:
                try:
                    # Read exactly one length-prefixed event from disk.
                    self._spool.seek(self._read_offset)

                    # The first fixed-size value tells us how much text follows.
                    size_data = self._spool.read(_EVENT_SIZE.size)

                    if len(size_data) != _EVENT_SIZE.size:
                        raise OSError("Incomplete event size in temporary storage.")

                    size = _EVENT_SIZE.unpack(size_data)[0]

                    # Read exactly the event size declared above.
                    payload = self._spool.read(size)

                    if len(payload) != size:
                        raise OSError("Incomplete event in temporary storage.")

                    # Events were stored as UTF-8 text by put().
                    event = payload.decode("utf-8")

                    # Point the next read just beyond this complete record.
                    self._read_offset += _EVENT_SIZE.size + size

                except (OSError, UnicodeError, ValueError) as error:
                    # Drop unreadable disk entries but keep delivery running.
                    self._record_storage_failure("read", error)

                    # Every remaining disk event is now unavailable.
                    self._unavailable_count += self._disk_count
                    self._backlog -= self._disk_count
                    self._disk_count = 0

                    self._close_spool()

                    raise queue.Empty from None

                # One complete disk event was successfully restored.
                self._disk_count -= 1

                if not self._disk_count:
                    # Release disk space as soon as the final event is read.
                    self._close_spool()

            # This event is no longer waiting for the client.
            self._backlog -= 1

            return event

    def qsize(self):
        """Return the number of events awaiting delivery."""
        with self._condition:
            return self._backlog

    def storage_failed(self):
        """Return whether temporary storage failed during this stream."""
        with self._condition:
            return self._storage_failed

    def _record_storage_failure(self, operation, error):
        """Record and report the first temporary-storage failure."""
        if not self._disk_unavailable:
            # Set this before logging because a custom handler may also fail.
            self._storage_failed = True

            # Future events can still use free memory, but no more disk writes.
            self._disk_unavailable = True

            _safe_stream_log(
                logging.ERROR,
                "Notification stream temporary storage %s failed; notification processing will continue.",
                operation,
                exc_info=(type(error), error, error.__traceback__),
            )

    def _record_storage_limit(self, message):
        """Report the first configured storage limit reached by a stream."""
        if not self._storage_failed:
            # The response generator turns this into one client-facing warning.
            self._storage_failed = True

            _safe_stream_log(logging.WARNING, message)

    def _close_spool(self):
        """Close temporary storage without allowing cleanup to escape."""
        # Detach first so another cleanup attempt cannot reuse this handle.
        spool, self._spool = self._spool, None

        # A future spool starts from the beginning of a new temporary file.
        self._read_offset = 0
        self._write_offset = 0

        if spool is not None:
            try:
                # TemporaryFile removes its anonymous content when closed.
                spool.close()

            except (OSError, ValueError) as error:
                self._record_storage_failure("cleanup", error)

    def close(self):
        """Release temporary storage and return backlog statistics."""
        with self._condition:
            # Save counts before clearing the queue for the caller's logs.
            self._closed = True
            stats = (
                self._backlog,
                self._max_backlog,
                self._spooled_count,
                self._unavailable_count,
            )

            # Release all retained in-memory event text.
            self._memory.clear()
            self._memory_bytes = 0

            # Closing the queue also removes its anonymous temporary file.
            self._close_spool()

            # The closed queue has no remaining deliverable events.
            self._backlog = 0
            self._disk_count = 0

            # Wake readers so they can observe the closed, empty state.
            self._condition.notify_all()

            return stats


# Tags separated by space, &, or + are and'ed together
# Tags separated by commas (even commas wrapped in spaces) are "or'ed" together
# We start with a regular expression used to clean up provided tag expressions.
TAG_VALIDATION_RE = re.compile(r"^[a-z0-9\s| ,_:+&-]+$", re.IGNORECASE)

# Split OR groups only on commas or pipes.
TAG_OR_DELIM_RE = re.compile(r"\s*[|,]\s*")

# Break apart our objects anded together.
TAG_AND_DELIM_RE = re.compile(r"[\s&+]+")

# A single Apprise tag token. Supports [priority:]name[:retry].
TAG_TOKEN_RE = re.compile(
    r"^(?:[0-9]+:)?[a-z0-9][a-z0-9_-]*(?::[0-9]+)?$",
    re.IGNORECASE,
)


def parse_tag_expression(tag):
    """
    Convert a user-provided tag expression into Apprise's OR/AND structure.

    Commas and pipes are OR separators. Whitespace, ampersands, and plus signs are AND
    separators. Individual tokens may use Apprise's advanced tag syntax:
    [priority:]tag[:retry].
    """
    if not isinstance(tag, str) or not TAG_VALIDATION_RE.match(tag):
        raise ValueError("Unsupported characters found in tag definition")

    tags = []
    for group in TAG_OR_DELIM_RE.split(tag):
        group = group.strip()
        if not group:
            continue

        tokens = [token for token in TAG_AND_DELIM_RE.split(group) if token]
        if not tokens or any(not TAG_TOKEN_RE.match(token) for token in tokens):
            raise ValueError("Unsupported characters found in tag definition")

        tags.append(tuple(tokens) if len(tokens) > 1 else tokens[0])

    return tags


def tag_detail(tag):
    """
    Return JSON-safe tag metadata for an Apprise tag object or plain string.
    """
    raw_tag = str(tag)
    match = TAG_TOKEN_RE.match(raw_tag)
    if match and getattr(tag, "priority", None) is None:
        parts = raw_tag.split(":")
        if len(parts) > 1 and parts[0].isdigit():
            priority = int(parts[0])
            tag_name = parts[1]
            has_priority = True
        else:
            priority = 0
            tag_name = parts[0]
            has_priority = False
    else:
        tag_name = raw_tag
        priority = getattr(tag, "priority", 0)
        has_priority = getattr(tag, "has_priority", False)
    token = f"{priority}:{tag_name}" if has_priority or priority else tag_name

    return {
        "name": tag_name,
        "priority": priority,
        "token": token,
        "exact": f"{priority}:{tag_name}",
    }


def tag_names(tags):
    """
    Return only bare tag names for UI autocomplete and legacy API consumers.
    """
    return {tag_detail(tag)["name"] for tag in tags}


def service_retry(notification, url):
    """
    Return the configured retry count for a rendered notification URL.
    """
    retry = getattr(notification, "retry", 0) or 0
    if retry:
        return retry

    try:
        value = parse_qs(urlsplit(url).query).get("retry", [0])[0]
        return int(value)

    except (TypeError, ValueError):
        return 0


def service_optional(notification, url):
    """
    Return whether a rendered notification URL is marked optional.
    """
    optional = getattr(notification, "optional", None)
    if optional is True:
        return True

    try:
        values = parse_qs(urlsplit(url).query).get("optional")
        if values:
            return str(values[0]).lower() in {"1", "yes", "true", "on"}

    except (TypeError, ValueError):
        pass

    return bool(optional)


def _notify_log_asctime(entry):
    """Format a notification log time as ``YYYY-MM-DD HH:MM:SS,mmm``."""
    return "{},{:03d}".format(
        entry.time.strftime("%Y-%m-%d %H:%M:%S"),
        entry.time.microsecond // 1000,
    )


def render_notify_logs(entries, json_response, content_type):
    """Yield logs as JSON, HTML, or plain text.

    Entries are read only as the response needs them, including logs stored on
    disk.
    """
    if json_response:
        # Open the details array before reading its first log entry.
        yield "["

        separator = ""
        for entry in entries:
            # Encode one entry at a time.
            yield separator
            yield json.dumps(
                [entry.level, _notify_log_asctime(entry), entry.message],
                cls=JSONEncoder,
                separators=(",", ":"),
            )
            separator = ","

        yield "]"
        return

    if content_type == "text/html":
        # The surrounding list is sent even when no log entries were captured.
        yield '<ul class="logs">'

        for entry in entries:
            # Escape plugin messages before placing them into HTML.
            yield (
                '<li class="log_{level}">'
                '<div class="log_time">{time}</div>'
                '<div class="log_level">{level}</div>'
                '<div class="log_msg">{message}</div></li>'
            ).format(
                level=entry.level,
                time=_notify_log_asctime(entry),
                message=escape(entry.message),
            )

        yield "</ul>"
        return

    # Plain text uses separators before later entries to avoid a trailing line.
    separator = ""
    for entry in entries:
        yield separator
        yield str(entry)
        separator = "\n"


def render_notify_response(
    entries,
    json_response,
    content_type,
    error=None,
):
    """Yield the complete HTTP response while keeping log entries lazy."""
    if json_response:
        # Preserve the documented response envelope around streamed details.
        yield '{"error":'
        yield json.dumps(error, cls=JSONEncoder, separators=(",", ":"))
        yield ',"details":'
        yield from render_notify_logs(entries, True, content_type)
        yield "}"
        return

    # Track whether plain text produced anything before using an error fallback.
    rendered = False
    for chunk in render_notify_logs(entries, False, content_type):
        rendered = True
        yield chunk

    if not rendered and error is not None:
        # Match the earlier behavior when a failed call captured no text logs.
        yield str(error)


class _ResultLogResponse:
    """Stream one result and close its temporary log storage."""

    def __init__(self, result, json_response, content_type, error=None):
        # The result remains open until Django finishes or closes this stream.
        self._result = result

        # JSON responses include an envelope around the streamed log details.
        self._json_response = json_response

        # HTML and plain-text renderers use this to choose their output shape.
        self._content_type = content_type

        # Failed notifications include this message in the response envelope.
        self._error = error

        # Closing is safe to request more than once.
        self._closed = False

    def __iter__(self):
        """Yield response chunks and release storage after delivery."""
        try:
            yield from render_notify_response(
                self._result.logs(),
                self._json_response,
                self._content_type,
                error=self._error,
            )

        finally:
            # Normal completion and interrupted iteration share cleanup.
            self.close()

    def close(self):
        """Release disk-backed result logs when Django closes the response."""
        if not self._closed:
            self._closed = True

            # AppriseResult.close() safely handles memory-only results too.
            self._result.close()


def stream_result_response(
    result,
    *,
    json_response,
    content_type,
    status,
    error=None,
):
    """Stream a response and release result storage when it closes."""
    content = _ResultLogResponse(
        result,
        json_response,
        content_type,
        error=error,
    )

    response = StreamingHttpResponse(
        content,
        status=status,
        content_type=content_type,
    )

    # Compression middleware may stream-compress this response safely.
    return response


def iter_notify_webhook(source, result):
    """Yield a completion webhook without joining all result logs."""
    yield '{"source":'
    yield json.dumps(source, cls=JSONEncoder, separators=(",", ":"))

    yield ',"status":'
    yield "0" if result else "1"

    yield ',"output":'
    if result is None:
        # Unexpected notification failures have no structured result to read.
        yield json.dumps(
            "Notification processing failed.",
            cls=JSONEncoder,
            separators=(",", ":"),
        )

    else:
        # Webhook output keeps its established JSON log-list shape.
        yield from render_notify_logs(
            result.logs(),
            True,
            "application/json",
        )

    yield "}"


def send_notify_webhook(source, result):
    """Send the completion webhook without changing notification results."""
    if settings.APPRISE_WEBHOOK_URL:
        try:
            # The utility spools these chunks to disk before making the request.
            send_webhook(iter_notify_webhook(source, result))
        except Exception:
            # Expected URL and network failures are logged by send_webhook.
            # This final guard covers an unexpected library or handler error.
            _safe_stream_log(
                logging.ERROR,
                "Unexpected error while sending the completion webhook.",
                exc_info=True,
            )


def _stream_requested(request):
    """
    Return whether the caller requested a live event stream.

    Use ``Accept: text/event-stream`` or a truthy ``?stream=`` value.
    """
    media_types = (value.partition(";")[0].strip().lower() for value in request.headers.get("accept", "").split(","))
    if "text/event-stream" in media_types:
        return True
    return parse_bool(request.GET.get("stream"), default=False)


def _sse_event(event, data):
    """Format one Server-Sent Event: a named event plus a JSON data line."""
    return "event: {}\ndata: {}\n\n".format(event, json.dumps(data, cls=JSONEncoder))


def stream_notify_response(
    a_obj,
    *,
    body,
    title,
    notify_type,
    tag,
    attach,
    log_level,
    webhook_source=None,
    match_always=True,
):
    """
    Stream notification progress while ``notify()`` runs in the background.

    Log entries are sent as they occur. The stream ends with a ``result``
    event, or an ``error`` event if notification processing raises.
    """
    events = _SpooledEventQueue()
    finished = threading.Event()
    final_event = [_sse_event("error", {"message": "Notification processing failed."})]

    def log_callback(entry, service):
        # Apprise requires a synchronous callback; the queue safely passes
        # each entry to the response generator.
        event = _sse_event(
            "log",
            {
                "level": entry.level,
                "asctime": _notify_log_asctime(entry),
                "message": entry.message,
                # Include the service type, never its sensitive target URL.
                "service": getattr(service, "service_name", None),
            },
        )
        queue_status = events.put(event)
        if queue_status == _STREAM_PUT_SPOOLED:
            # Record slow readers without delaying notification delivery.
            _safe_stream_log(
                logging.WARNING,
                "Notification stream backlog moved to temporary storage.",
            )

    def run():
        # The worker owns notification processing and prepares one final event.
        result = None

        try:
            result = a_obj.notify(
                body,
                title=title,
                notify_type=notify_type,
                tag=tag,
                match_always=match_always,
                attach=attach,
                log_level=log_level,
                log_callback=log_callback,
            )
            final_event[0] = _sse_event(
                "result",
                {"status": result.status.name if result.status is not None else None},
            )

            # Webhook rendering walks logs in bounded chunks when configured.
            send_notify_webhook(webhook_source, result)

        except Exception:
            # Log internal details without exposing them to the caller.
            _safe_stream_log(
                logging.ERROR,
                "Notification streaming failed.",
                exc_info=True,
            )
            send_notify_webhook(webhook_source, None)

        finally:
            if result is not None:
                # Live events are already queued, so retained result storage
                # can be released after optional webhook delivery finishes.
                result.close()

            # Wake the response loop even when notification work failed.
            finished.set()

    def generator():
        storage_warning_sent = False
        try:
            # Start work when Django begins sending the response.
            try:
                threading.Thread(target=run, daemon=True).start()
            except RuntimeError:
                _safe_stream_log(
                    logging.ERROR,
                    "Notification streaming could not start.",
                    exc_info=True,
                )
                finished.set()

            # Send an ignored SSE comment so headers arrive immediately.
            yield ": connected\n\n"
            while True:
                if events.storage_failed() and not storage_warning_sent:
                    # This alert bypasses the failed temporary storage.
                    storage_warning_sent = True
                    entry = apprise.NotifyLogEntry(level="ERROR", message=_STREAM_STORAGE_WARNING)
                    yield _sse_event(
                        "log",
                        {
                            "level": entry.level,
                            "asctime": _notify_log_asctime(entry),
                            "message": entry.message,
                            "service": None,
                        },
                    )
                    continue

                if events.qsize():
                    try:
                        yield events.get(timeout=0.25)
                    except queue.Empty:
                        continue
                    continue

                if finished.is_set():
                    # All queued logs were sent, so the final event can follow.
                    break

                try:
                    yield events.get(timeout=0.25)
                except queue.Empty:
                    continue

            yield final_event[0]
        finally:
            # This also runs when the client disconnects mid-stream.
            pending, max_backlog, spooled, unavailable = events.close()
            if not finished.is_set():
                # The client is gone, but notification work may still finish.
                _safe_stream_log(
                    logging.WARNING,
                    "Notification stream closed before notification processing finished.",
                )
            if pending:
                # The client is gone, so only normal server logging remains.
                _safe_stream_log(
                    logging.WARNING,
                    "Notification stream closed with %d pending event(s).",
                    pending,
                )
            if spooled:
                _safe_stream_log(
                    logging.INFO,
                    "Notification stream spooled %d event(s); peak backlog was %d.",
                    spooled,
                    max_backlog,
                )
            if unavailable:
                _safe_stream_log(
                    logging.WARNING,
                    "Notification stream could not retain %d log event(s).",
                    unavailable,
                )

    response = StreamingHttpResponse(generator(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    # Prevent proxies such as nginx from batching streamed events.
    response["X-Accel-Buffering"] = "no"
    # Compression can buffer events, so keep this response uncompressed.
    response["Content-Encoding"] = "identity"
    return response


class JSONEncoder(DjangoJSONEncoder):
    """
    A wrapper to the DjangoJSONEncoder to support
    sets() (converting them to lists).
    """

    def default(self, obj):
        if isinstance(obj, set | frozenset):
            return list(obj)

        elif isinstance(obj, apprise.locale.LazyTranslation):
            return str(obj)

        return super().default(obj)


class ResponseCode:
    """
    These codes are based on those provided by the requests object
    """

    okay = 200
    no_content = 204
    bad_request = 400
    unauthorized = 401
    no_access = 403
    not_found = 404
    method_not_allowed = 405
    method_not_accepted = 406
    conflict = 409
    expectation_failed = 417
    misdirected_request = 421
    failed_dependency = 424
    fields_too_large = 431
    too_many_requests = 429
    internal_server_error = 500


def _key_access_denied_response(request, key):
    """Return the standard Basic Auth challenge for a protected key."""
    logger.warning(
        "AUTH - %s - Per-key Authentication Failed - Request Denied for KEY: %s",
        request.META["REMOTE_ADDR"],
        key,
    )
    return error_response(
        request,
        _("Access Denied"),
        ResponseCode.unauthorized,
        template="401.html",
        headers={
            "WWW-Authenticate": 'Basic realm="{}: {}"'.format(
                settings.APPRISE_BASIC_AUTH_REALM,
                key,
            )
        },
    )


def _per_key_auth_unavailable_response(request):
    """Return 403 when authentication mode is disabled."""
    return error_response(
        request,
        _("Authentication mode is disabled (set APPRISE_AUTH_REQUIRED to enable it)"),
        ResponseCode.no_access,
    )


def _stateful_mode_unavailable_response(request):
    """Return 403 when persistent configuration storage is disabled."""
    return error_response(
        request,
        _("Persistent configuration storage is disabled"),
        ResponseCode.no_access,
    )


def _missing_key_response(request):
    """Return the standard response when no configuration key was supplied."""
    return error_response(
        request,
        _("A configuration ID is required (URL path or X-Apprise-Config-ID header)"),
        ResponseCode.bad_request,
    )


def _invalid_key_response(request):
    """Return the standard response for an invalid config ID header.

    Invalid values are rejected so routes such as /notify are not silently
    reinterpreted as keyless requests.
    """
    return error_response(
        request,
        _("The X-Apprise-Config-ID header provided is invalid"),
        ResponseCode.bad_request,
    )


def _get_config_response(request, key):
    """Return stored configuration for ``/get`` and ``/cfg``.

    Bare ``/get`` requests read the key from ``X-Apprise-Config-ID``.
    """

    if not stateful_store_enabled():
        return _stateful_mode_unavailable_response(request)

    # Detect the format our response should be in.
    json_response = is_json_response(request)

    key = resolve_config_key(request, key)
    if not key:
        if config_key_header_present_but_invalid(request):
            return _invalid_key_response(request)
        return _missing_key_response(request)

    if not key_auth_ok(request, key):
        return _key_access_denied_response(request, key)

    if settings.APPRISE_CONFIG_LOCK:
        # General Access Control
        logger.warning(
            "VIEW - %s - Config Lock Active - Request Denied",
            request.META["REMOTE_ADDR"],
        )

        msg = _("The site has been configured to deny this request")
        status = ResponseCode.no_access
        return (
            HttpResponse(msg, status=status, content_type="text/plain")
            if not json_response
            else JsonResponse(
                {"error": msg},
                encoder=JSONEncoder,
                safe=False,
                status=status,
            )
        )

    config, format = ConfigCache.get(key)
    if config is None:
        # The returned value of config and format tell a rather cryptic
        # story; this portion could probably be updated in the future.
        # but for now it reads like this:
        #   config == None and format == None: We had an internal error
        #   config == None and format != None: we simply have no data
        #   config != None: we simply have no data
        if format is not None:
            # no content to return
            logger.warning(
                "VIEW - %s - No configuration associated using KEY: %s",
                request.META["REMOTE_ADDR"],
                key,
            )
            msg = _("There was no configuration found")
            status = ResponseCode.no_content
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse(
                    {"error": msg},
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )
            )

        # Something went very wrong; return 500
        logger.error(
            "VIEW - %s - Configuration could not be accessed associated using KEY: %s",
            request.META["REMOTE_ADDR"],
            key,
        )
        msg = _("An error occurred accessing configuration")
        status = ResponseCode.internal_server_error
        return (
            HttpResponse(msg, status=status, content_type="text/plain")
            if not json_response
            else JsonResponse(
                {
                    "error": msg,
                },
                encoder=JSONEncoder,
                safe=False,
                status=status,
            )
        )

    # Our configuration was retrieved; now our response varies on whether
    # we are a YAML configuration or a TEXT based one.  This allows us to
    # be compatible with those using the AppriseConfig() library or the
    # reference to it through the --config (-c) option in the CLI.
    content_type = (
        "text/yaml; charset=utf-8" if format == apprise.ConfigFormat.YAML.value else "text/plain; charset=utf-8"
    )

    # Return our retrieved content
    logger.info(
        "VIEW - %s - Retrieved configuration associated using KEY: %s",
        request.META["REMOTE_ADDR"],
        key,
    )
    return (
        HttpResponse(
            config,
            content_type=content_type,
            status=ResponseCode.okay,
        )
        if not json_response
        else JsonResponse(
            {"format": format, "config": config},
            encoder=JSONEncoder,
            safe=False,
            status=ResponseCode.okay,
        )
    )


class WelcomeView(View):
    """
    A simple welcome/index page
    """

    template_name = "welcome.html"

    def get(self, request):
        default_key = "KEY"
        key = request.GET.get("key", default_key).strip()

        if settings.APPRISE_API_ONLY:
            # API Mode only - Nothing further to parse
            return Error421View.as_view()(request)

        if request.apprise_auth_permission == AUTH_ROLE_USER:
            # A shared browser login cannot inspect another key through examples.
            key = request.apprise_web_auth_key

        # Examples use the key's username when one is saved. The password is
        # always shown as a placeholder and is never read from storage.
        example_username = settings.APPRISE_USER
        if settings.APPRISE_AUTH_REQUIRED and CONFIG_KEY_PATTERN.match(key):
            example_username = config_auth_state(key, request).username or example_username

        return render(
            request,
            self.template_name,
            {
                "secure": request.scheme[-1].lower() == "s",
                "key": key if key else default_key,
                "AUTH_USERNAME": example_username,
            },
        )


def _login_config_key(request, next_url):
    """Return the Config ID named by a safe local login destination."""
    if not next_url or not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return ""

    path = urlsplit(next_url).path
    if settings.BASE_URL and path.startswith("{}/".format(settings.BASE_URL)):
        path = path[len(settings.BASE_URL) :]

    try:
        key = resolve(path).kwargs.get("key", "")
    except Resolver404:
        return ""
    return key if isinstance(key, str) and CONFIG_KEY_PATTERN.match(key) else ""


@method_decorator(never_cache, name="dispatch")
class LoginView(View):
    """Create a signed login used only by the browser interface."""

    template_name = "login.html"

    def get(self, request):
        """Show the browser login form."""
        if settings.APPRISE_API_ONLY:
            return Error421View.as_view()(request)
        if not settings.APPRISE_AUTH_REQUIRED:
            return redirect("welcome")

        next_url = request.GET.get("next", "")
        key = request.GET.get("key", "").strip()
        if not CONFIG_KEY_PATTERN.match(key):
            key = _login_config_key(request, next_url)
        if not key:
            # A Config ID switch stores the destination in the private key
            # cookie so it does not need to appear in the login URL.
            remembered_key = request.COOKIES.get("key", "").strip()
            key = remembered_key if CONFIG_KEY_PATTERN.match(remembered_key) else ""

        response = render(
            request,
            self.template_name,
            {
                "AUTH_ENABLED": False,
                "form_login": BrowserLoginForm(
                    initial={
                        "next": next_url,
                        "key": key,
                    }
                ),
            },
        )
        # Opening Login starts a clean browser-authentication attempt. Theme
        # and support-banner preferences are separate and remain untouched.
        request.clear_config_cookie = True
        clear_web_auth_cookie(response)
        return response

    def post(self, request):
        """Validate credentials and start the browser login."""
        if settings.APPRISE_API_ONLY:
            return Error421View.as_view()(request)
        if not settings.APPRISE_AUTH_REQUIRED:
            return redirect("welcome")
        if not is_html_response(request):
            return HttpResponse(
                _("The login form is only available to the web interface"),
                status=ResponseCode.method_not_accepted,
                content_type="text/plain",
            )

        form = BrowserLoginForm(request.POST)
        status = ResponseCode.unauthorized
        if form.is_valid():
            username = form.cleaned_data["username"]
            password = form.cleaned_data["password"]
            key = form.cleaned_data["key"]

            mode = None
            # A supplied Config ID may belong to a shared user. Test it first
            # so a successful shared login never consumes the administrator's
            # failed-attempt allowance.
            if CONFIG_KEY_PATTERN.match(key) and key_credentials_ok(request, key, username, password):
                mode = AUTH_ROLE_USER
            elif global_credentials_ok(username, password):
                mode = AUTH_ROLE_ADMIN
                key = None

            if mode:
                next_url = form.cleaned_data["next"] or (
                    reverse("config", kwargs={"key": key}) if mode == AUTH_ROLE_USER else reverse("welcome")
                )
                if not url_has_allowed_host_and_scheme(
                    next_url,
                    allowed_hosts={request.get_host()},
                    require_https=request.is_secure(),
                ):
                    next_url = reverse("config", kwargs={"key": key}) if key else reverse("welcome")

                if mode == AUTH_ROLE_USER:
                    # Store the selected key and shorten matching browser URLs.
                    # Older keyed URLs remain valid when cookies are unavailable.
                    request.default_config_id = key
                    destination = urlsplit(next_url).path.rstrip("/")
                    if destination == reverse("config", kwargs={"key": key}).rstrip("/"):
                        next_url = reverse("config_current")
                    elif destination == reverse("auth", kwargs={"key": key}).rstrip("/"):
                        next_url = reverse("auth_current")

                else:
                    # A new administrator login must not inherit a Config ID
                    # remembered by a previous browser user.
                    request.default_config_id = settings.APPRISE_DEFAULT_CONFIG_ID

                response = redirect(next_url)
                set_web_auth_cookie(response, request, mode, username, key)
                response["Cache-Control"] = "no-store"
                return response

            form.add_error(None, _("The username, password, or Config ID was not accepted."))
        else:
            status = ResponseCode.bad_request

        response = render(
            request,
            self.template_name,
            {"AUTH_ENABLED": False, "form_login": form},
            status=status,
        )
        # Failed credentials must not leave a previous user's signed session
        # active behind the visible login error.
        request.clear_config_cookie = True
        clear_web_auth_cookie(response)
        return response


class LogoutView(View):
    """End the signed browser login."""

    template_name = "logout.html"

    def get(self, request):
        """Delete the browser login and show its confirmation page."""
        if settings.APPRISE_API_ONLY:
            return Error421View.as_view()(request)
        if not settings.APPRISE_AUTH_REQUIRED:
            return redirect("welcome")

        # DetectConfigMiddleware sees this marker while the response unwinds.
        request.clear_config_cookie = True
        response = render(request, self.template_name, {"AUTH_ENABLED": False})
        clear_web_auth_cookie(response)
        response["Cache-Control"] = "no-store"
        return response


# Describe this caller's access: either one key ("user") or unrestricted
# access ("admin"). Open deployments are also unrestricted.
_PRIVILEGE_LABELS = {
    AUTH_ROLE_ADMIN: "admin",
    AUTH_ROLE_USER: "user",
    AUTH_ROLE_DISABLED: "admin",
}


def _health_check_response(request):
    """Return the shared status payload for keyed and keyless requests.

    A key changes access control, not the response content.
    """
    # Detect the format our response should be in
    json_response = is_json_response(request)

    # Normalize modes once so status matches the endpoint guards.
    stateful_enabled = stateful_store_enabled()
    stateless_enabled = str(settings.APPRISE_STATELESS_MODE).strip().lower() != "disabled"

    # Run our healthcheck; allow ?force which will cause the check to run each time
    response = healthcheck(lazy="force" not in request.GET)

    # Prepare our response
    status = ResponseCode.okay if "OK" in response["details"] else ResponseCode.expectation_failed
    if not json_response:
        response = ",".join(response["details"])

    return (
        HttpResponse(response, status=status, content_type="text/plain")
        if not json_response
        else JsonResponse(
            {
                "config_lock": settings.APPRISE_CONFIG_LOCK,
                "attach_lock": settings.APPRISE_ATTACH_SIZE <= 0,
                "stateful_enabled": stateful_enabled,
                "stateless_enabled": stateless_enabled,
                "degraded": not stateful_enabled and not stateless_enabled,
                "max_attachments": settings.APPRISE_MAX_ATTACHMENTS,
                "attach_size": settings.APPRISE_ATTACH_SIZE,
                "status": response,
                "privilege": _PRIVILEGE_LABELS[request.apprise_auth_permission],
            },
            encoder=JSONEncoder,
            safe=False,
            status=status,
        )
    )


@method_decorator((gzip_page, never_cache), name="dispatch")
class HealthCheckView(View):
    """
    A Django view used to return a simple status/healthcheck
    """

    def get(self, request):
        """Return status, applying per-key auth when the header supplies a key."""
        raw_config_key = request.headers.get(CONFIG_KEY_HEADER, "").strip()
        if raw_config_key:
            config_key = resolve_config_key(request, "")
            if not config_key:
                return _invalid_key_response(request)
            if not key_auth_ok(request, config_key):
                return _key_access_denied_response(request, config_key)

        return _health_check_response(request)


@method_decorator((gzip_page, never_cache), name="dispatch")
class KeyedHealthCheckView(View):
    """Return server status after applying authentication for a key."""

    def get(self, request, key):
        """Return status after resolving and authenticating the key.

        ``X-Apprise-Config-ID`` takes precedence over the URL key.
        """
        key = resolve_config_key(request, key)
        if not key:
            return _invalid_key_response(request)

        if not key_auth_ok(request, key):
            return _key_access_denied_response(request, key)

        return _health_check_response(request)


@method_decorator((gzip_page, never_cache), name="dispatch")
class CurrentHealthCheckView(View):
    """Return status for the configuration in the signed browser login."""

    def get(self, request):
        """Use the browser's current key without exposing it in the URL."""
        if not is_html_response(request):
            return _missing_key_response(request)

        key = _current_browser_config_key(request)
        return KeyedHealthCheckView.as_view()(request, key=key) if key else _missing_key_response(request)


@method_decorator((gzip_page, never_cache), name="dispatch")
class DetailsView(View):
    """
    A Django view used to list all supported endpoints
    """

    template_name = "details.html"

    def get(self, request):
        """
        Handle a GET request, applying per-key auth when the header supplies a key.
        """

        raw_config_key = request.headers.get(CONFIG_KEY_HEADER, "").strip()
        if raw_config_key:
            config_key = resolve_config_key(request, "")
            if not config_key:
                return _invalid_key_response(request)
            if not key_auth_ok(request, config_key):
                return _key_access_denied_response(request, config_key)

        # Detect the format our response should be in.
        json_response = is_json_response(request)

        if settings.APPRISE_API_ONLY and not json_response:
            # API Mode only disables the browsable HTML page.
            return Error421View.as_view()(request)

        # Show All flag
        # Support 'yes', '1', 'true', 'enable', 'active', and +
        show_all = parse_bool(request.GET.get("all"), default=False)

        # Our status
        status = ResponseCode.okay

        #
        # Apply Any Global Filters (if identified)
        #
        apply_global_filters()

        # Create an Apprise Object
        a_obj = apprise.Apprise()

        # Load our details
        details = a_obj.details(show_disabled=show_all)

        # Sort our result set
        details["schemas"] = sorted(details["schemas"], key=lambda i: str(i["service_name"]).upper())

        # Return our content
        return (
            render(
                request,
                self.template_name,
                {
                    "show_all": show_all,
                    "details": details,
                },
                status=status,
            )
            if not json_response
            else JsonResponse(details, encoder=JSONEncoder, safe=False, status=status)
        )


@method_decorator(never_cache, name="dispatch")
class ConfigView(View):
    """
    A Django view used to manage configuration
    """

    template_name = "config.html"

    def get(self, request, key):
        """
        Handle a GET request
        """
        if settings.APPRISE_API_ONLY:
            # API Mode only - Nothing further to parse
            return Error421View.as_view()(request)

        # The header key takes precedence over the URL key.
        key = resolve_config_key(request, key)
        if not key:
            if config_key_header_present_but_invalid(request):
                return _invalid_key_response(request)
            return _missing_key_response(request)

        # Protect the editor page even though configuration loads separately.
        if not key_auth_ok(request, key):
            return _key_access_denied_response(request, key)

        auth_state = config_auth_state(key, request)
        auth_username = auth_state.username
        if auth_username is None and request.apprise_auth_permission == AUTH_ROLE_USER:
            # A legacy lock cannot reveal its username, but this request can.
            auth_username = request.apprise_auth_username

        return render(
            request,
            self.template_name,
            {
                "key": key,
                "form_url": AddByUrlForm(),
                "form_cfg": AddByConfigForm(),
                "form_notify": NotifyForm(),
                "auth_mode": auth_state.mode,
                "auth_username": auth_username or "",
            },
        )

    def post(self, request, key):
        """Return stored configuration through the web UI alias."""
        return _get_config_response(request, key)


def _current_browser_config_key(request):
    """Return the configuration remembered by an authenticated browser."""
    has_browser_login = bool(request.COOKIES.get(WEB_AUTH_COOKIE))
    has_open_key = not settings.APPRISE_AUTH_REQUIRED and bool(request.COOKIES.get("key"))
    if not has_browser_login and not has_open_key:
        return None

    # A shared session trusts only the key signed into its login cookie.
    key = (
        getattr(request, "apprise_web_auth_key", None)
        if request.apprise_auth_permission == AUTH_ROLE_USER
        else getattr(request, "default_config_id", None)
    )
    return key if isinstance(key, str) and CONFIG_KEY_PATTERN.match(key) else None


@method_decorator(never_cache, name="dispatch")
class CurrentConfigView(View):
    """Open the remembered configuration without placing its ID in the URL."""

    def get(self, request):
        """Resolve the browser key and show its editor."""
        if not is_html_response(request):
            # API clients retain the explicit URL/header contract.
            return _missing_key_response(request)

        key = _current_browser_config_key(request)
        return ConfigView.as_view()(request, key=key) if key else _missing_key_response(request)

    def post(self, request):
        """Select another Config ID while keeping it out of the address bar."""
        if not is_html_response(request):
            return _missing_key_response(request)

        form = ConfigKeyForm(request.POST)
        if not form.is_valid():
            return _invalid_key_response(request)

        key = form.cleaned_data["key"]
        request.default_config_id = key
        if request.apprise_auth_permission == AUTH_ROLE_USER and request.apprise_web_auth_key != key:
            # A shared login proves access to one key only. Start a clean login
            # for the requested key before any configuration view is reached.
            login_url = "{}?{}".format(
                reverse("login"),
                urlencode({"next": reverse("config_current")}),
            )
            response = redirect(login_url)
            clear_web_auth_cookie(response)
            response["Cache-Control"] = "no-store"
            return response

        return redirect("config_current")


@method_decorator(never_cache, name="dispatch")
class ConfigListView(View):
    """
    A Django view used to list all configuration keys
    """

    template_name = "config_list.html"

    def get(self, request):
        """
        Handle a GET request
        """
        # Detect the format our response should be in. An explicit Accept
        # wins; a missing/wildcard Accept falls back to Content-Type so a
        # JSON API client on a bodyless GET still gets a JSON reply.
        json_response = is_json_response(request)

        if settings.APPRISE_API_ONLY and not json_response:
            # API-only mode hides HTML. JSON still follows the admin check.
            return Error421View.as_view()(request)

        # The complete list is available only in SIMPLE mode. CONFIG_LOCK also
        # requires an authenticated administrator so an open site stays private.
        if not can_list_configurations(request):
            msg = _("The site has been configured to deny this request")
            status = ResponseCode.no_access
            return error_response(request, msg, status)

        stored_keys = ConfigCache.keys()
        entries = []
        for key in stored_keys:
            auth_state = config_auth_state(key, request)

            entries.append(
                {
                    "key": key,
                    "user": auth_state.username,
                    "assigned": auth_state.assigned,
                }
            )
        status = ResponseCode.okay
        return (
            render(
                request,
                self.template_name,
                {
                    "keys": entries,
                },
                status=status,
            )
            if not json_response
            else JsonResponse(
                stored_keys
                if not settings.APPRISE_AUTH_REQUIRED
                else [{"key": entry["key"], "user": entry["user"]} for entry in entries],
                encoder=JSONEncoder,
                safe=False,
                status=status,
            )
        )


@method_decorator(never_cache, name="dispatch")
class AddView(View):
    """
    A Django view used to store Apprise configuration
    """

    def post(self, request, key=None):
        """Store configuration using a URL or header key."""
        if not stateful_store_enabled():
            return _stateful_mode_unavailable_response(request)

        # Detect the format our incoming payload
        json_payload = (
            MIME_IS_JSON.match(
                request.content_type if request.content_type else request.headers.get("content-type", "")
            )
            is not None
        )

        # Detect the format our response should be in
        json_response = is_json_response(request)

        key = resolve_config_key(request, key)
        if not key:
            if config_key_header_present_but_invalid(request):
                return _invalid_key_response(request)
            return _missing_key_response(request)

        if not key_auth_ok(request, key):
            return _key_access_denied_response(request, key)

        if request.apprise_auth_permission == AUTH_ROLE_USER:
            # Only administrators may create or replace configurations. Key
            # users may move their own configuration but cannot overwrite it.
            logger.warning(
                "ADD - %s - Restricted User Denied - KEY: %s",
                request.META["REMOTE_ADDR"],
                key,
            )
            msg = _("Global administrator credentials are required to add or replace a configuration")
            status = ResponseCode.no_access
            return error_response(request, msg, status)

        if settings.APPRISE_CONFIG_LOCK:
            # General Access Control
            logger.warning(
                "ADD - %s - Config Lock Active - Request Denied",
                request.META["REMOTE_ADDR"],
            )
            msg = _("The site has been configured to deny this request")
            status = ResponseCode.no_access
            return error_response(request, msg, status)

        # our content
        content = {}
        if not json_payload:
            content = {}
            form = AddByConfigForm(request.POST)
            if form.is_valid():
                content.update(form.cleaned_data)

            form = AddByUrlForm(request.POST)
            if form.is_valid():
                content.update(form.cleaned_data)

        else:  # JSON Payload
            # Prepare our default response
            try:
                # load our JSON content
                content = json.loads(request.body.decode("utf-8"))

            except RequestDataTooBig:
                # APPRISE_UPLOAD_MAX_MEMORY_SIZE exceeded its value; this is usually
                # the case when there is a very large file attachment that can't be pulled
                # out of the payload without exceeding memory limitations (default is 3MB)
                logger.warning(
                    "ADD - %s - JSON Payload Exceeded %dMB; operation aborted using KEY: %s",
                    request.META["REMOTE_ADDR"],
                    (settings.APPRISE_UPLOAD_MAX_MEMORY_SIZE / 1048576),
                    key,
                )

                status = ResponseCode.fields_too_large
                msg = _("JSON Payload provided is too large")
                return (
                    HttpResponse(msg, status=status, content_type="text/plain")
                    if not json_response
                    else JsonResponse(
                        {
                            "error": msg,
                        },
                        encoder=JSONEncoder,
                        safe=False,
                        status=status,
                    )
                )

            except (AttributeError, ValueError):
                # could not parse JSON response...
                logger.warning(
                    "ADD - %s - Invalid JSON Payload provided using KEY: %s",
                    request.META["REMOTE_ADDR"],
                    key,
                )

                status = ResponseCode.bad_request
                msg = _("Invalid JSON Payload provided")
                return (
                    HttpResponse(msg, status=status, content_type="text/plain")
                    if not json_response
                    else JsonResponse(
                        {
                            "error": msg,
                        },
                        encoder=JSONEncoder,
                        safe=False,
                        status=status,
                    )
                )

        if not content:
            # No information was posted
            logger.warning(
                "ADD - %s - Invalid payload structure provided using KEY: %s",
                request.META["REMOTE_ADDR"],
                key,
            )

            msg = _("Invalid payload structure provided")
            status = ResponseCode.bad_request
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse(
                    {
                        "error": msg,
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )
            )

        # Create ourselves an apprise object to work with
        a_obj = apprise.Apprise()
        if "urls" in content:
            # Load our content
            a_obj.add(content["urls"])
            if not len(a_obj):
                # No URLs were loaded
                logger.warning(
                    "ADD - %s - No valid URLs defined using KEY: %s",
                    request.META["REMOTE_ADDR"],
                    key,
                )

                msg = _("No valid URLs defined")
                status = ResponseCode.bad_request
                return (
                    HttpResponse(msg, status=status, content_type="text/plain")
                    if not json_response
                    else JsonResponse(
                        {
                            "error": msg,
                        },
                        encoder=JSONEncoder,
                        safe=False,
                        status=status,
                    )
                )

            if not ConfigCache.put(
                key,
                "\r\n".join([s.url() for s in a_obj]),
                apprise.ConfigFormat.TEXT.value,
            ):
                logger.warning(
                    "ADD - %s - configuration could not be saved using KEY: %s",
                    request.META["REMOTE_ADDR"],
                    key,
                )
                msg = _("The configuration could not be saved")
                status = ResponseCode.internal_server_error
                return (
                    HttpResponse(msg, status=status, content_type="text/plain")
                    if not json_response
                    else JsonResponse(
                        {
                            "error": msg,
                        },
                        encoder=JSONEncoder,
                        safe=False,
                        status=status,
                    )
                )

        elif "config" in content:
            if len(content["config"]) > settings.APPRISE_CONFIG_MAX_LENGTH:
                # Configuration payload exceeds the maximum allowed length
                logger.warning(
                    "ADD - %s - Config payload exceeds maximum allowed length (%d bytes) using KEY: %s",
                    request.META["REMOTE_ADDR"],
                    settings.APPRISE_CONFIG_MAX_LENGTH,
                    key,
                )
                msg = _("The configuration payload is too large")
                status = ResponseCode.bad_request
                return (
                    HttpResponse(msg, status=status, content_type="text/plain")
                    if not json_response
                    else JsonResponse(
                        {
                            "error": msg,
                        },
                        encoder=JSONEncoder,
                        safe=False,
                        status=status,
                    )
                )

            fmt = content.get("format", "").lower()
            if fmt not in [i[0] for i in CONFIG_FORMATS]:
                # Format must be one supported by apprise
                logger.warning(
                    "ADD - %s - Invalid configuration format specified (%s) using KEY: %s",
                    request.META["REMOTE_ADDR"],
                    fmt,
                    key,
                )
                msg = _("Invalid configuration format specified")
                status = ResponseCode.bad_request
                return (
                    HttpResponse(msg, status=status, content_type="text/plain")
                    if not json_response
                    else JsonResponse(
                        {
                            "error": msg,
                        },
                        encoder=JSONEncoder,
                        safe=False,
                        status=status,
                    )
                )

            # Prepare our apprise config object
            ac_obj = apprise.AppriseConfig(recursion=settings.APPRISE_RECURSION_MAX)

            if fmt == AUTO_DETECT_CONFIG_KEYWORD:
                # By setting format to None, it is automatically detected from
                # within the add_config() call
                fmt = None

            # Load our configuration
            if not ac_obj.add_config(content["config"], format=fmt):
                # The format could not be detected
                logger.warning(
                    "ADD - %s - The configuration format could not be auto-detected using KEY: %s",
                    request.META["REMOTE_ADDR"],
                    key,
                )
                msg = _("The configuration format could not be auto-detected")
                status = ResponseCode.bad_request
                return (
                    HttpResponse(msg, status=status, content_type="text/plain")
                    if not json_response
                    else JsonResponse(
                        {
                            "error": msg,
                        },
                        encoder=JSONEncoder,
                        safe=False,
                        status=status,
                    )
                )

            # Add our configuration
            a_obj.add(ac_obj)

            if not len(a_obj):
                # No specified URL(s) were loaded due to
                # mis-configuration on the caller's part
                logger.warning(
                    "ADD - %s - No valid URL(s) defined using KEY: %s",
                    request.META["REMOTE_ADDR"],
                    key,
                )
                msg = _("No valid URL(s) defined")
                status = ResponseCode.bad_request
                return (
                    HttpResponse(msg, status=status, content_type="text/plain")
                    if not json_response
                    else JsonResponse(
                        {
                            "error": msg,
                        },
                        encoder=JSONEncoder,
                        safe=False,
                        status=status,
                    )
                )

            if not ConfigCache.put(key, content["config"], fmt=ac_obj[0].config_format.value):
                # Something went very wrong; return 500
                logger.error(
                    "ADD - %s - Configuration could not be saved using KEY: %s",
                    request.META["REMOTE_ADDR"],
                    key,
                )
                msg = _("An error occurred saving configuration")
                status = ResponseCode.internal_server_error
                return (
                    HttpResponse(msg, status=status, content_type="text/plain")
                    if not json_response
                    else JsonResponse(
                        {
                            "error": msg,
                        },
                        encoder=JSONEncoder,
                        safe=False,
                        status=status,
                    )
                )

        else:
            # No configuration specified; we're done
            msg = _("No configuration provided")
            logger.warning(
                "ADD - %s - No configuration provided using KEY: %s",
                request.META["REMOTE_ADDR"],
                key,
            )
            status = ResponseCode.bad_request
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse(
                    {
                        "error": msg,
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )
            )

        # If we reach here; we successfully loaded the configuration so we can
        # go ahead and write it to disk and alert our caller of the success.
        logger.info(
            "ADD - %s - Configuration saved using KEY: %s",
            request.META["REMOTE_ADDR"],
            key,
        )

        status = ResponseCode.okay
        msg = _("Successfully saved configuration")
        return (
            HttpResponse(msg, status=status, content_type="text/plain")
            if not json_response
            else JsonResponse(
                {
                    "error": None,
                },
                encoder=JSONEncoder,
                safe=False,
                status=status,
            )
        )


@method_decorator(never_cache, name="dispatch")
class MoveView(View):
    """
    A Django view used to move an Apprise configuration to another location.
    """

    def post(self, request, key=None):
        """Move a configuration from one configuration ID to another."""
        if not stateful_store_enabled():
            return _stateful_mode_unavailable_response(request)

        # Resolve response format and payload format
        json_payload = (
            MIME_IS_JSON.match(
                request.content_type if request.content_type else request.headers.get("content-type", "")
            )
            is not None
        )
        # Detect the format our response should be in
        json_response = is_json_response(request)

        # The header key takes precedence over the URL key.
        key = resolve_config_key(request, key)
        if not key:
            if config_key_header_present_but_invalid(request):
                return _invalid_key_response(request)
            return _missing_key_response(request)

        if not key_auth_ok(request, key):
            return _key_access_denied_response(request, key)

        if not can_move_or_delete_configuration(request):
            # A locked site permits this only for an authenticated administrator.
            logger.warning(
                "MOVE - %s - Config Lock Active - Request Denied",
                request.META["REMOTE_ADDR"],
            )
            msg = _("The site has been configured to deny this request")
            status = ResponseCode.no_access
            return error_response(request, msg, status)

        shared_user = request.apprise_auth_permission == AUTH_ROLE_USER

        if json_payload:
            from_config_id, to_config_id, parse_error = self._parse_json_payload(
                request,
                key,
            )
            if parse_error is not None:
                return parse_error

        else:
            from_config_id, to_config_id, parse_error = self._parse_form_payload(
                request,
                key,
                shared_user,
            )
            if parse_error is not None:
                return parse_error

        # The from_config_id must match the key or the caller must have access to it.
        if from_config_id != key and not key_auth_ok(request, from_config_id):
            return _key_access_denied_response(request, from_config_id)

        if not settings.APPRISE_CONFIG_LOCK:
            # CONFIG_LOCK reports a read-only store, but administrators may
            # still move entries through the guarded operation below.
            health = healthcheck(lazy=True)
            if not health.get("can_write_config", False):
                logger.warning(
                    "MOVE - %s - Configuration store is not writable; move aborted for KEY: %s",
                    request.META["REMOTE_ADDR"],
                    from_config_id,
                )
                status = ResponseCode.failed_dependency
                msg = _("The configuration store is not currently writable")
                return error_response(request, msg, status)

        return self._perform_move(request, from_config_id, to_config_id, json_response)

    def _parse_json_payload(self, request, key):
        """Return the move IDs and any JSON validation error."""
        try:
            content = json.loads(request.body.decode("utf-8"))

        except RequestDataTooBig:
            # Reject JSON larger than the configured request memory limit.
            logger.warning(
                "MOVE - %s - JSON Payload Exceeded %dMB; operation aborted using KEY: %s",
                request.META["REMOTE_ADDR"],
                (settings.APPRISE_UPLOAD_MAX_MEMORY_SIZE / 1048576),
                key,
            )
            status = ResponseCode.fields_too_large
            msg = _("JSON Payload provided is too large")
            return None, None, error_response(request, msg, status)

        except (AttributeError, ValueError):
            logger.warning(
                "MOVE - %s - Invalid JSON Payload provided using KEY: %s",
                request.META["REMOTE_ADDR"],
                key,
            )
            status = ResponseCode.bad_request
            msg = _("Invalid JSON Payload provided")
            return None, None, error_response(request, msg, status)

        if not isinstance(content, dict):
            status = ResponseCode.bad_request
            msg = _("The JSON payload must be an object")
            return None, None, error_response(request, msg, status)

        to_config_id = content.get("to")
        if not isinstance(to_config_id, str) or not CONFIG_KEY_PATTERN.match(to_config_id) or to_config_id == key:
            status = ResponseCode.bad_request
            msg = _("A valid destination (to), different from the source, is required")
            return None, None, error_response(request, msg, status, field="to")

        return key, to_config_id, None

    def _parse_form_payload(self, request, key, shared_user):
        """Return the move IDs and any form validation error."""
        form = MoveConfigForm(request.POST, restricted=shared_user, current_from=key)
        if not form.is_valid():
            status = ResponseCode.bad_request
            field, errors = next(iter(form.errors.items()))
            msg = errors[0]
            return None, None, error_response(request, msg, status, field=field)

        return form.cleaned_data["from"], form.cleaned_data["to"], None

    def _perform_move(self, request, from_config_id, to_config_id, json_response):
        """Move the configuration and return the matching response."""
        result = ConfigCache.move(from_config_id, to_config_id)

        if result == MoveResult.NOT_FOUND:
            logger.warning(
                "MOVE - %s - No configuration to move using KEY: %s",
                request.META["REMOTE_ADDR"],
                from_config_id,
            )
            status = ResponseCode.not_found
            msg = _("There was no configuration to move")
            return error_response(request, msg, status)

        if result == MoveResult.CONFLICT:
            logger.warning(
                "MOVE - %s - Destination KEY %s already in use (moving from %s)",
                request.META["REMOTE_ADDR"],
                to_config_id,
                from_config_id,
            )
            status = ResponseCode.conflict
            msg = _("A configuration already exists at the destination")
            return error_response(request, msg, status, field="to")

        if result == MoveResult.FAILED:
            logger.error(
                "MOVE - %s - Configuration could not be moved from KEY %s to %s",
                request.META["REMOTE_ADDR"],
                from_config_id,
                to_config_id,
            )
            status = ResponseCode.internal_server_error
            msg = _("The configuration could not be moved")
            return error_response(request, msg, status)

        logger.info(
            "MOVE - %s - Moved configuration from KEY %s to %s",
            request.META["REMOTE_ADDR"],
            from_config_id,
            to_config_id,
        )
        status = ResponseCode.okay
        msg = _("Successfully moved configuration")
        response = (
            HttpResponse(msg, status=status, content_type="text/plain")
            if not json_response
            else JsonResponse({"error": None}, encoder=JSONEncoder, safe=False, status=status)
        )
        if (
            request.headers.get(WEB_AUTH_HEADER) == "1"
            and getattr(request, "default_config_id", None) == from_config_id
        ):
            # Keep an administrator's current-key alias aligned after a GUI move.
            request.default_config_id = to_config_id
        if (
            request.apprise_auth_permission == AUTH_ROLE_USER
            and getattr(request, "apprise_web_auth_key", None) == from_config_id
        ):
            # Refresh the browser login after its lock moves to the new ID.
            request.default_config_id = to_config_id
            set_web_auth_cookie(
                response,
                request,
                AUTH_ROLE_USER,
                request.apprise_auth_username,
                to_config_id,
            )
        return response


@method_decorator(never_cache, name="dispatch")
class DelView(View):
    """
    A Django view for removing content associated with a key
    """

    def post(self, request, key=None):
        """Delete configuration using a URL or header key."""
        if not stateful_store_enabled():
            return _stateful_mode_unavailable_response(request)

        # Detect the format our response should be in. An explicit Accept
        # wins; a missing/wildcard Accept falls back to Content-Type so a
        # JSON API client on a bodyless GET still gets a JSON reply.
        json_response = is_json_response(request)

        key = resolve_config_key(request, key)
        if not key:
            if config_key_header_present_but_invalid(request):
                return _invalid_key_response(request)
            return _missing_key_response(request)

        if not key_auth_ok(request, key):
            return _key_access_denied_response(request, key)

        if request.apprise_auth_permission == AUTH_ROLE_USER:
            # Only administrators may delete configurations. Key users may
            # move their own configuration but cannot remove their login.
            logger.warning(
                "DEL - %s - Restricted User Denied - KEY: %s",
                request.META["REMOTE_ADDR"],
                key,
            )
            msg = _("Global administrator credentials are required to delete a configuration")
            status = ResponseCode.no_access
            return error_response(request, msg, status)

        if not can_move_or_delete_configuration(request):
            # A locked site permits this only for an authenticated administrator.
            logger.warning(
                "DEL - %s - Config Lock Active - Request Denied",
                request.META["REMOTE_ADDR"],
            )
            msg = _("The site has been configured to deny this request")
            status = ResponseCode.no_access
            return error_response(request, msg, status)

        # Clear the key
        result = ConfigCache.clear(key)

        if result is False:
            # Keep the auth lock when configuration deletion fails.
            logger.error(
                "DEL - %s - Configuration could not be removed associated using KEY: %s",
                request.META["REMOTE_ADDR"],
                key,
            )

            msg = _("The configuration could not be removed")
            status = ResponseCode.internal_server_error
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse(
                    {
                        "error": msg,
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )
            )

        # Remove the auth lock only after the configuration is gone.
        if ConfigCache.clear_auth(key) is False:
            logger.error(
                "DEL - %s - Configuration removed but its authentication lock could not be, using KEY: %s",
                request.META["REMOTE_ADDR"],
                key,
            )

        if result is None:
            logger.warning(
                "DEL - %s - No configuration associated using KEY: %s",
                request.META["REMOTE_ADDR"],
                key,
            )
            msg = _("There was no configuration to remove")
            status = ResponseCode.no_content
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse(
                    {
                        "error": msg,
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )
            )

        # Removed content
        logger.info(
            "DEL - %s - Removed configuration associated using KEY: %s",
            request.META["REMOTE_ADDR"],
            key,
        )

        status = ResponseCode.okay
        msg = _("Successfully removed configuration")
        return (
            HttpResponse(msg, status=status, content_type="text/plain")
            if not json_response
            else JsonResponse(
                {
                    "error": None,
                },
                encoder=JSONEncoder,
                safe=False,
                status=status,
            )
        )


class AuthView(View):
    """Set, replace, or remove Basic Auth for one configuration key."""

    template_name = "auth.html"

    def get(self, request, key=None):
        """Show the auth editor or return its current state as JSON."""
        json_response = is_json_response(request)
        if settings.APPRISE_API_ONLY and not json_response:
            return Error421View.as_view()(request)

        if not stateful_store_enabled():
            return _stateful_mode_unavailable_response(request)

        if not settings.APPRISE_AUTH_REQUIRED:
            if is_html_response(request):
                return render(request, "auth_disabled.html")
            return _per_key_auth_unavailable_response(request)

        key = resolve_config_key(request, key)
        if not key:
            if config_key_header_present_but_invalid(request):
                return _invalid_key_response(request)
            return _missing_key_response(request)

        if not key_auth_ok(request, key):
            return _key_access_denied_response(request, key)

        auth_state = config_auth_state(key, request)
        # Only a saved per-key username belongs in this editor. The global
        # username remains separate and is never copied into a new lock.
        username = auth_state.username if auth_state.assigned else ""
        if username is None and request.apprise_auth_permission == AUTH_ROLE_USER:
            username = request.apprise_auth_username
        shared_user = request.apprise_auth_permission == AUTH_ROLE_USER
        if json_response:
            return JsonResponse(
                {"mode": auth_state.mode, "username": username},
                encoder=JSONEncoder,
                safe=False,
                status=ResponseCode.okay,
            )

        return render(
            request,
            self.template_name,
            {
                "key": key,
                "auth_mode": auth_state.mode,
                "auth_username": username,
                "shared_user": shared_user,
                "form_auth": AuthForm(
                    initial={"username": username},
                    shared=shared_user,
                    current_username=username,
                    require_current=shared_user,
                ),
                # Do not build a form for an action this request cannot use.
                "form_move": (
                    MoveConfigForm(
                        initial={"from": key},
                        restricted=shared_user,
                        current_from=key,
                    )
                    if can_move_or_delete_configuration(request)
                    else None
                ),
            },
        )

    def post(self, request, key=None):
        """Set credentials from JSON, reading a missing key from the header."""
        json_response = is_json_response(request)

        if not stateful_store_enabled():
            return _stateful_mode_unavailable_response(request)

        # Require JSON so a plain cross-site HTML form cannot alter access.
        # OriginValidationMiddleware provides the broader CSRF check.
        if request.content_type != "application/json":
            status = ResponseCode.bad_request
            msg = _("Content-Type must be application/json")
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse({"error": msg}, encoder=JSONEncoder, safe=False, status=status)
            )

        if not settings.APPRISE_AUTH_REQUIRED:
            return _per_key_auth_unavailable_response(request)

        key = resolve_config_key(request, key)
        if not key:
            if config_key_header_present_but_invalid(request):
                return _invalid_key_response(request)
            return _missing_key_response(request)

        # Only the global administrator may create a key's first lock.
        # Existing locks accept either global or current per-key credentials.
        if not ConfigCache.has_auth(key):
            if not getattr(request, "globally_authenticated", False):
                return _key_access_denied_response(request, key)
        elif not key_auth_ok(request, key):
            return _key_access_denied_response(request, key)

        try:
            content = json.loads(request.body.decode("utf-8"))

        except RequestDataTooBig:
            status = ResponseCode.fields_too_large
            msg = _("JSON Payload provided is too large")
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse({"error": msg}, encoder=JSONEncoder, safe=False, status=status)
            )

        except (AttributeError, ValueError):
            status = ResponseCode.bad_request
            msg = _("Invalid JSON Payload provided")
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse({"error": msg}, encoder=JSONEncoder, safe=False, status=status)
            )

        if not isinstance(content, dict):
            status = ResponseCode.bad_request
            msg = _("The JSON payload must be an object")
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse({"error": msg}, encoder=JSONEncoder, safe=False, status=status)
            )

        shared_user = request.apprise_auth_permission == AUTH_ROLE_USER
        browser_shared_user = shared_user and getattr(request, "apprise_web_auth_key", None) == key
        current_username = ""
        if shared_user:
            current_username = config_auth_state(key, request).username
            if current_username is None:
                # Legacy locks did not record their username. The successful
                # login tells us which value must remain in use.
                current_username = request.apprise_auth_username or ""

        username = content.get("username")
        current_password = content.get("current_password")
        password = content.get("password")
        password_confirm = content.get("password_confirm")
        if (
            not isinstance(password, str)
            or not isinstance(username, str)
            or (browser_shared_user and not isinstance(password_confirm, str))
            or (shared_user and password_confirm is not None and not isinstance(password_confirm, str))
            or (browser_shared_user and current_password is not None and not isinstance(current_password, str))
        ):
            status = ResponseCode.bad_request
            msg = _("Valid username and password fields are required")
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse({"error": msg}, encoder=JSONEncoder, safe=False, status=status)
            )

        if shared_user and username is not None and username != current_username:
            status = ResponseCode.no_access
            msg = _("The username cannot be changed by a configuration user")
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse(
                    {"error": msg, "field": "username"},
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )
            )

        # API users repeat their saved username but do not need the browser's
        # confirmation field.
        form_data = content.copy()
        if shared_user and not browser_shared_user and password_confirm is None:
            form_data["password_confirm"] = password

        form = AuthForm(
            form_data,
            shared=shared_user,
            current_username=current_username,
            require_current=browser_shared_user,
        )
        if not form.is_valid():
            status = ResponseCode.bad_request
            field, errors = next(iter(form.errors.items()))
            msg = errors[0]
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse(
                    {"error": msg, "field": field},
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )
            )

        username = form.cleaned_data["username"]
        password = form.cleaned_data["password"]
        if browser_shared_user and not ConfigCache.verify_auth(
            key,
            username,
            form.cleaned_data["current_password"],
        ):
            status = ResponseCode.bad_request
            msg = _("The current password was not accepted")
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse(
                    {"error": msg, "field": "current_password"},
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )
            )

        if shared_user and ConfigCache.verify_auth(key, username, password):
            status = ResponseCode.bad_request
            msg = _("The new password must differ from the current password")
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse(
                    {"error": msg, "field": "password"},
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )
            )

        if not ConfigCache.set_auth(key, username, password):
            logger.error(
                "AUTH - %s - Could not write authentication associated using KEY: %s",
                request.META["REMOTE_ADDR"],
                key,
            )
            status = ResponseCode.internal_server_error
            msg = _("The authentication could not be saved")
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse({"error": msg}, encoder=JSONEncoder, safe=False, status=status)
            )

        logger.info(
            "AUTH - %s - Authentication set for KEY: %s",
            request.META["REMOTE_ADDR"],
            key,
        )
        status = ResponseCode.okay
        msg = _("Successfully set authentication")
        response = (
            HttpResponse(msg, status=status, content_type="text/plain")
            if not json_response
            else JsonResponse({"error": None}, encoder=JSONEncoder, safe=False, status=status)
        )
        if shared_user and getattr(request, "apprise_web_auth_key", None) == key:
            # Keep the current browser signed in with the newly saved digest.
            set_web_auth_cookie(response, request, AUTH_ROLE_USER, username, key)
        return response

    def delete(self, request, key=None):
        """Remove per-key auth using a URL or header key."""
        json_response = is_json_response(request)

        if not stateful_store_enabled():
            return _stateful_mode_unavailable_response(request)

        if not settings.APPRISE_AUTH_REQUIRED:
            return _per_key_auth_unavailable_response(request)

        key = resolve_config_key(request, key)
        if not key:
            if config_key_header_present_but_invalid(request):
                return _invalid_key_response(request)
            return _missing_key_response(request)

        if not key_auth_ok(request, key):
            return _key_access_denied_response(request, key)

        # Configuration users may rotate their password, but only the global
        # administrator can remove their account.
        if not getattr(request, "globally_authenticated", False):
            status = ResponseCode.no_access
            msg = _("Global administrator credentials are required to remove authentication")
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse({"error": msg}, encoder=JSONEncoder, safe=False, status=status)
            )

        result = ConfigCache.clear_auth(key)
        if result is False:
            logger.error(
                "AUTH - %s - Authentication could not be removed associated using KEY: %s",
                request.META["REMOTE_ADDR"],
                key,
            )
            status = ResponseCode.internal_server_error
            msg = _("The authentication could not be removed")
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse({"error": msg}, encoder=JSONEncoder, safe=False, status=status)
            )

        logger.info(
            "AUTH - %s - Authentication removed for KEY: %s",
            request.META["REMOTE_ADDR"],
            key,
        )
        status = ResponseCode.okay
        msg = _("Successfully removed authentication")
        return (
            HttpResponse(msg, status=status, content_type="text/plain")
            if not json_response
            else JsonResponse({"error": None}, encoder=JSONEncoder, safe=False, status=status)
        )


@method_decorator(never_cache, name="dispatch")
class CurrentAuthView(View):
    """Manage the remembered configuration without exposing its ID in the URL."""

    def _key(self, request):
        """Return the current browser key for every supported method."""
        return _current_browser_config_key(request)

    def get(self, request):
        """Show the current configuration's authentication editor."""
        key = self._key(request)
        return AuthView.as_view()(request, key=key) if key else _missing_key_response(request)

    def post(self, request):
        """Update credentials for the current configuration."""
        key = self._key(request)
        return AuthView.as_view()(request, key=key) if key else _missing_key_response(request)

    def delete(self, request):
        """Remove credentials from the current configuration."""
        key = self._key(request)
        return AuthView.as_view()(request, key=key) if key else _missing_key_response(request)


@method_decorator((gzip_page, never_cache), name="dispatch")
class GetView(View):
    """
    A Django view used to retrieve previously stored Apprise configuration
    """

    def post(self, request, key=None):
        """Return a configuration, using the header key on bare ``/get``."""
        return _get_config_response(request, key)


@method_decorator((gzip_page, never_cache), name="dispatch")
class NotifyView(View):
    """
    A Django view for sending a notification in a stateful manner
    """

    def post(self, request, key):
        """Send through a stored configuration, preferring the header key."""
        if not stateful_store_enabled():
            return _stateful_mode_unavailable_response(request)

        key = resolve_config_key(request, key)
        if not key:
            return _invalid_key_response(request)

        # Detect the format our incoming payload
        json_payload = (
            MIME_IS_JSON.match(
                request.content_type if request.content_type else request.headers.get("content-type", "")
            )
            is not None
        )

        # Detect the format our response should be in
        json_response = is_json_response(request)

        if not key_auth_ok(request, key):
            return _key_access_denied_response(request, key)

        # Event streams use their own format instead of JSON, HTML, or text.
        stream_response = _stream_requested(request)

        # rules
        rules = {k[1:]: v for k, v in request.GET.items() if k[0] == ":"}

        # our content
        content = {}
        if not json_payload:
            if rules:
                # Create a copy
                data = request.POST.copy()
                if not remap_fields(rules, data):
                    status = ResponseCode.bad_request
                    msg = _("Payload field mapping failed using KEY: {}").format(key)
                    return (
                        HttpResponse(msg, status=status, content_type="text/plain")
                        if not json_response
                        else JsonResponse(
                            {
                                "error": msg,
                            },
                            encoder=JSONEncoder,
                            safe=False,
                            status=status,
                        )
                    )

            else:
                # Just create a pointer
                data = request.POST

            form = NotifyForm(data=data, files=request.FILES)
            if form.is_valid():
                content.update(form.cleaned_data)

        else:  # JSON Payload
            # Prepare our default response
            try:
                # load our JSON content
                content = json.loads(request.body.decode("utf-8"))

                # Apply content rules
                if rules and not remap_fields(rules, content):
                    status = ResponseCode.bad_request
                    msg = _("Payload field mapping failed using KEY: {}").format(key)
                    return (
                        HttpResponse(msg, status=status, content_type="text/plain")
                        if not json_response
                        else JsonResponse(
                            {
                                "error": msg,
                            },
                            encoder=JSONEncoder,
                            safe=False,
                            status=status,
                        )
                    )

            except RequestDataTooBig:
                # APPRISE_UPLOAD_MAX_MEMORY_SIZE exceeded its value; this is usually
                # the case when there is a very large file attachment that can't be pulled
                # out of the payload without exceeding memory limitations (default is 3MB)
                logger.warning(
                    "NOTIFY - %s - JSON Payload Exceeded %dMB using KEY: %s",
                    request.META["REMOTE_ADDR"],
                    (settings.APPRISE_UPLOAD_MAX_MEMORY_SIZE / 1048576),
                    key,
                )

                status = ResponseCode.fields_too_large
                msg = _("JSON Payload provided is too large")
                return (
                    HttpResponse(msg, status=status, content_type="text/plain")
                    if not json_response
                    else JsonResponse(
                        {
                            "error": msg,
                        },
                        encoder=JSONEncoder,
                        safe=False,
                        status=status,
                    )
                )

            except (AttributeError, ValueError):
                # could not parse JSON response...
                logger.warning(
                    "NOTIFY - %s - Invalid JSON Payload provided using KEY: %s",
                    request.META["REMOTE_ADDR"],
                    key,
                )

                status = ResponseCode.bad_request
                msg = _("Invalid JSON Payload provided")
                return (
                    HttpResponse(msg, status=status, content_type="text/plain")
                    if not json_response
                    else JsonResponse(
                        {
                            "error": msg,
                        },
                        encoder=JSONEncoder,
                        safe=False,
                        status=status,
                    )
                )

        if not content:
            # We could not handle the Content-Type
            logger.warning(
                "NOTIFY - %s - Invalid FORM Payload provided using KEY: %s",
                request.META["REMOTE_ADDR"],
                key,
            )

            msg = _("Bad FORM Payload provided")
            status = ResponseCode.bad_request
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse(
                    {
                        "error": msg,
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )
            )

        # Handle Attachments
        # Normalize all three accepted attachment aliases — 'attach',
        # 'attachment', and 'attachments' — into the canonical 'attachment'
        # key before calling parse_attachments.  Priority order (highest
        # first): attach > attachment > attachments.  POST form-data keys are
        # resolved before JSON body keys of the same name so that multipart
        # form submissions always take precedence.
        attach = None
        _post_key = next(
            (k for k in ("attach", "attachment", "attachments") if k in request.POST),
            None,
        )
        if _post_key:
            # Collect URL strings from form-data, discarding blank entries.
            content["attachment"] = [a for a in request.POST.getlist(_post_key) if isinstance(a, str) and a.strip()]
        else:
            # Resolve from the JSON body in the same priority order, then
            # rename the alias to the canonical 'attachment' key.
            _json_key = next(
                (k for k in ("attach", "attachment", "attachments") if content.get(k)),
                None,
            )
            if _json_key and _json_key != "attachment":
                content["attachment"] = content.pop(_json_key)

        if "attachment" in content or request.FILES:
            try:
                attach = parse_attachments(content.get("attachment"), request.FILES)

            except (TypeError, ValueError) as e:
                # Invalid entry found in list
                logger.warning(
                    "NOTIFY - %s - Bad attachment using KEY: %s - %s",
                    request.META["REMOTE_ADDR"],
                    key,
                    str(e),
                )

                status = ResponseCode.bad_request
                msg = _("Bad Attachment")
                return (
                    HttpResponse(msg, status=status, content_type="text/plain")
                    if not json_response
                    else JsonResponse(
                        {
                            "error": msg,
                        },
                        encoder=JSONEncoder,
                        safe=False,
                        status=status,
                    )
                )

        #
        # Allow 'tag' value to be specified as part of the URL parameters
        # if not found otherwise defined.
        #
        tag = content.get("tag") if content.get("tag") else content.get("tags")
        if not tag:
            # Allow GET parameter over-rides
            if "tag" in request.GET:
                tag = request.GET["tag"]

            elif "tags" in request.GET:
                tag = request.GET["tags"]

        if settings.APPRISE_CONFIG_LOCK and request.headers.get(WEB_AUTH_HEADER) == "1" and not tag:
            # The locked GUI cannot show destinations for manual selection.
            # Require a tag server-side in case browser validation is bypassed.
            status = ResponseCode.bad_request
            msg = _("At least one tag is required while configuration locking is enabled")
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse({"error": msg}, encoder=JSONEncoder, safe=False, status=status)
            )

        # Validation - Tag Logic:
        # "TagA"                        : TagA
        # "TagA, TagB"                  : TagA OR TagB
        # "TagA TagB"                  : TagA AND TagB
        # "TagA TagC, TagB"             : (TagA AND TagC) OR TagB
        # ['TagA', 'TagB']              : TagA OR TagB
        # [('TagA', 'TagC'), 'TagB']    : (TagA AND TagC) OR TagB
        # [('TagB', 'TagC')]            : TagB AND TagC
        if tag:
            if isinstance(tag, list | set | tuple):
                # Assign our tags as they were provided
                content["tag"] = tag

            elif isinstance(tag, str):
                try:
                    content["tag"] = parse_tag_expression(tag)

                except ValueError:
                    # Invalid entry found in list
                    logger.warning(
                        "NOTIFY - %s - Ignored invalid tag specified (type %s): %s using KEY: %s",
                        request.META["REMOTE_ADDR"],
                        str(type(tag)),
                        str(tag)[:12],
                        key,
                    )

                    msg = _("Unsupported characters found in tag definition")
                    status = ResponseCode.bad_request
                    return (
                        HttpResponse(msg, status=status, content_type="text/plain")
                        if not json_response
                        else JsonResponse(
                            {
                                "error": msg,
                            },
                            encoder=JSONEncoder,
                            safe=False,
                            status=status,
                        )
                    )

            else:  # Could be int, float or some other unsupported type
                logger.warning(
                    "NOTIFY - %s - Ignored invalid tag specified (type %s): %s using KEY: %s",
                    request.META["REMOTE_ADDR"],
                    str(type(tag)),
                    str(tag)[:12],
                    key,
                )

                msg = _("Unsupported characters found in tag definition")
                status = ResponseCode.bad_request
                return (
                    HttpResponse(msg, status=status, content_type="text/plain")
                    if not json_response
                    else JsonResponse(
                        {
                            "error": msg,
                        },
                        encoder=JSONEncoder,
                        safe=False,
                        status=status,
                    )
                )
        #
        # Allow 'format' value to be specified as part of the URL
        # parameters if not found otherwise defined.
        #
        if not content.get("format") and "format" in request.GET:
            content["format"] = request.GET["format"]

        #
        # Allow 'type' value to be specified as part of the URL parameters
        # if not found otherwise defined.
        #
        if not content.get("type") and "type" in request.GET:
            content["type"] = request.GET["type"]

        #
        # Allow 'title' value to be specified as part of the URL parameters
        # if not found otherwise defined.
        #
        if not content.get("title") and "title" in request.GET:
            content["title"] = request.GET["title"]

        # Some basic error checking
        if (not content.get("body") and not attach) or content.get(
            "type", apprise.NotifyType.INFO.value
        ) not in apprise.NOTIFY_TYPES:
            logger.warning(
                "NOTIFY - %s - Payload lacks minimum requirements using KEY: %s",
                request.META["REMOTE_ADDR"],
                key,
            )

            status = ResponseCode.bad_request
            msg = _("Payload lacks minimum requirements")
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse(
                    {
                        "error": msg,
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=ResponseCode.bad_request,
                )
            )

        # Acquire our body format (if identified). Formatting is entirely
        # optional:
        #  - "format" absent from the payload entirely: falls back to the
        #    server-configured APPRISE_DEFAULT_FORMAT (unset by default)
        #    rather than assuming TEXT.
        #  - "format" explicitly blank or null: forces pass-through, even
        #    overriding a configured APPRISE_DEFAULT_FORMAT -- the caller
        #    is explicitly telling us not to apply one.
        #  - "format" set to a value: used as-is (validated below).
        if "format" not in content:
            body_format = settings.APPRISE_DEFAULT_FORMAT
        else:
            body_format = content.get("format")
            if isinstance(body_format, str):
                body_format = body_format.strip()
            body_format = body_format or None
        if body_format and body_format not in apprise.NOTIFY_FORMATS:
            logger.warning(
                "NOTIFY - %s - Format parameter contains an unsupported value (%s) using KEY: %s",
                request.META["REMOTE_ADDR"],
                str(body_format),
                key,
            )

            msg = _("An invalid body input format was specified")
            status = ResponseCode.bad_request
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse(
                    {
                        "error": msg,
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )
            )

        # If we get here, we have enough information to generate a notification
        # with.
        config, format = ConfigCache.get(key)
        if config is None:
            # The returned value of config and format tell a rather cryptic
            # story; this portion could probably be updated in the future.
            # but for now it reads like this:
            #   config == None and format == None: We had an internal error
            #   config == None and format != None: we simply have no data
            #   config != None: we simply have no data
            if format is not None:
                # no content to return
                logger.debug(
                    "NOTIFY - %s - Empty configuration found using KEY: %s",
                    request.META["REMOTE_ADDR"],
                    key,
                )

                msg = _("There was no configuration found")
                status = ResponseCode.no_content
                return (
                    HttpResponse(msg, status=status, content_type="text/plain")
                    if not json_response
                    else JsonResponse(
                        {
                            "error": msg,
                        },
                        encoder=JSONEncoder,
                        safe=False,
                        status=status,
                    )
                )

            logger.error(
                "NOTIFY - %s - I/O error accessing configuration using KEY: %s",
                request.META["REMOTE_ADDR"],
                key,
            )

            # Something went very wrong; return 500
            msg = _("An error occurred accessing configuration")
            status = ResponseCode.internal_server_error
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse(
                    {
                        "error": msg,
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )
            )

        # Prepare our keyword arguments (to be passed into an AppriseAsset object)
        kwargs = {
            # Load our dynamic plugin path
            "plugin_paths": settings.APPRISE_PLUGIN_PATHS,
            # Load our persistent storage path
            "storage_path": settings.APPRISE_STORAGE_DIR,
            # Our storage URL ID Length
            "storage_idlen": settings.APPRISE_STORAGE_UID_LENGTH,
            # Define if we flush to disk as soon as possible or not when required
            "storage_mode": settings.APPRISE_STORAGE_MODE,
            # Emoji configuration (values are None, True, or False)
            "interpret_emojis": settings.APPRISE_INTERPRET_EMOJIS,
            # HTTP redirect behaviour (values are None, True, or False)
            "http_redirects": settings.APPRISE_HTTP_REDIRECTS,
        }

        if body_format:
            # Store our defined body format
            kwargs["body_format"] = body_format

        # Acquire our recursion count (if defined)
        recursion = request.headers.get("X-Apprise-Recursion-Count", 0)
        try:
            recursion = int(recursion)

            if recursion < 0:
                # We do not accept negative numbers
                raise TypeError("Invalid Recursion Value")

            if recursion > settings.APPRISE_RECURSION_MAX:
                logger.warning(
                    "NOTIFY - %s - Recursion limit reached (%d > %d)",
                    request.META["REMOTE_ADDR"],
                    recursion,
                    settings.APPRISE_RECURSION_MAX,
                )

                status = ResponseCode.method_not_accepted
                msg = _("The recursion limit has been reached")
                return (
                    HttpResponse(msg, status=status, content_type="text/plain")
                    if not json_response
                    else JsonResponse(
                        {
                            "error": msg,
                        },
                        encoder=JSONEncoder,
                        safe=False,
                        status=status,
                    )
                )

            # Store our recursion value for our AppriseAsset() initialization
            kwargs["_recursion"] = recursion

        except (TypeError, ValueError):
            logger.warning(
                "NOTIFY - %s - Invalid recursion value (%s) provided",
                request.META["REMOTE_ADDR"],
                str(recursion),
            )

            status = ResponseCode.bad_request
            msg = _("An invalid recursion value was specified")
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse(
                    {
                        "error": msg,
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )
            )

        # Acquire our unique identifier (if defined)
        uid = request.headers.get("X-Apprise-ID", "").strip()
        if uid:
            kwargs["_uid"] = uid

        #
        # Apply Any Global Filters (if identified)
        #
        apply_global_filters()

        # Put result-log storage limits on the asset Apprise already receives.
        kwargs["result_log_memory_size"] = settings.APPRISE_STREAM_MEMORY_SIZE
        kwargs["result_log_disk_size"] = settings.APPRISE_STREAM_DISK_SIZE
        asset = apprise.AppriseAsset(**kwargs)

        # Prepare our apprise object
        a_obj = apprise.Apprise(asset=asset)

        # Create an apprise config object
        ac_obj = apprise.AppriseConfig(asset=asset, recursion=settings.APPRISE_RECURSION_MAX)

        # Load our configuration
        ac_obj.add_config(config, format=format)

        # Add our configuration
        a_obj.add(ac_obj)

        # Our return content type can be controlled by the Accept keyword
        # If it includes /* or /html somewhere then we return html, otherwise
        # we return the logs as they're processed in their text format.
        # The HTML response type has a bit of overhead where as it's not
        # the case with text/plain
        if not json_response:
            content_type = (
                "text/html"
                if re.search(
                    r"text\/(\*|html)",
                    request.headers.get(
                        "Accept",
                        (request.content_type if request.content_type else request.headers.get("Content-Type", "")),
                    ),
                    re.IGNORECASE,
                )
                else "text/plain"
            )

        else:
            content_type = "application/json"

        # Use the request level when valid, then the configured default.
        level = parse_log_level(
            request.headers.get("X-Apprise-Log-Level"),
            settings.APPRISE_LOG_LEVEL,
        )

        if stream_response:
            # Return live progress while notification runs in the background.
            return stream_notify_response(
                a_obj,
                body=content.get("body"),
                title=content.get("title", ""),
                notify_type=content.get("type", apprise.NotifyType.INFO.value),
                tag=(content.get("tag") or None),
                attach=attach,
                log_level=level,
                webhook_source=request.META.get("REMOTE_ADDR", ""),
            )

        # Capture notification logs at the caller's requested level.
        result = a_obj.notify(
            content.get("body"),
            title=content.get("title", ""),
            notify_type=content.get("type", apprise.NotifyType.INFO.value),
            tag=(content.get("tag") or None),
            attach=attach,
            log_level=level,
        )

        # Send the optional webhook from a separate bounded walk of the logs.
        send_notify_webhook(request.META.get("REMOTE_ADDR", ""), result)

        if not result:
            # If at least one notification couldn't be sent; change up
            # the response to a 424 error code
            msg = _("One or more notification could not be sent")
            status = ResponseCode.failed_dependency
            logger.warning(
                "NOTIFY - %s - One or more notifications not sent%s using KEY: %s",
                request.META["REMOTE_ADDR"],
                "" if not tag else f" (Tags: {tag})",
                key,
            )
            return stream_result_response(
                result,
                json_response=json_response,
                content_type=content_type,
                status=status,
                error=msg,
            )

        logger.info(
            "NOTIFY - %s - Delivered Notification(s) - %sKEY: %s",
            request.META["REMOTE_ADDR"],
            "" if not tag else f"Tags: {tag}, ",
            key,
        )

        # Return our success message
        status = ResponseCode.okay
        return stream_result_response(
            result,
            json_response=json_response,
            content_type=content_type,
            status=status,
        )


@method_decorator((gzip_page, never_cache), name="dispatch")
class StatelessNotifyView(View):
    """
    A Django view for sending a stateless notification
    """

    def post(self, request):
        """Send statelessly, or use stored configuration when a key is supplied."""
        raw_config_key = request.headers.get(CONFIG_KEY_HEADER, "").strip()
        if raw_config_key:
            config_key = resolve_config_key(request, "")
            if not config_key:
                return _invalid_key_response(request)
            return NotifyView().post(request, config_key)

        # Detect the format our incoming payload
        json_payload = (
            MIME_IS_JSON.match(
                request.content_type if request.content_type else request.headers.get("content-type", "")
            )
            is not None
        )

        # Detect the format our response should be in
        json_response = is_json_response(request)

        # Event streams use their own format instead of JSON, HTML, or text.
        stream_response = _stream_requested(request)

        if settings.APPRISE_STATELESS_MODE == "disabled":
            # General Access Control
            logger.warning(
                "STATELESS NOTIFY - %s - Stateless Mode Disabled - Request Denied",
                request.META["REMOTE_ADDR"],
            )
            msg = _("The site has been configured to deny this request")
            status = ResponseCode.no_access
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse(
                    {
                        "error": msg,
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )
            )

        # rules
        rules = {k[1:]: v for k, v in request.GET.items() if k[0] == ":"}

        # our content
        content = {}
        if not json_payload:
            if rules:
                # Create a copy
                data = request.POST.copy()
                if not remap_fields(rules, data, form=NotifyByUrlForm()):
                    status = ResponseCode.bad_request
                    msg = _("Payload field mapping failed")
                    return (
                        HttpResponse(msg, status=status, content_type="text/plain")
                        if not json_response
                        else JsonResponse(
                            {
                                "error": msg,
                            },
                            encoder=JSONEncoder,
                            safe=False,
                            status=status,
                        )
                    )

            else:
                # Just create a pointer
                data = request.POST

            form = NotifyByUrlForm(data=data, files=request.FILES)
            if form.is_valid():
                content.update(form.cleaned_data)

        else:  # JSON Payload
            # Prepare our default response
            try:
                # load our JSON content
                content = json.loads(request.body.decode("utf-8"))

                # Apply content rules
                if rules and not remap_fields(rules, content, form=NotifyByUrlForm()):
                    status = ResponseCode.bad_request
                    msg = _("Payload field mapping failed")
                    return (
                        HttpResponse(msg, status=status, content_type="text/plain")
                        if not json_response
                        else JsonResponse(
                            {
                                "error": msg,
                            },
                            encoder=JSONEncoder,
                            safe=False,
                            status=status,
                        )
                    )

            except RequestDataTooBig:
                # APPRISE_UPLOAD_MAX_MEMORY_SIZE exceeded its value; this is usually
                # the case when there is a very large file attachment that can't be pulled
                # out of the payload without exceeding memory limitations (default is 3MB)
                logger.warning(
                    "NOTIFY - %s - JSON Payload Exceeded %dMB; operation aborted",
                    request.META["REMOTE_ADDR"],
                    (settings.APPRISE_UPLOAD_MAX_MEMORY_SIZE / 1048576),
                )

                status = ResponseCode.fields_too_large
                msg = _("JSON Payload provided is too large")
                return (
                    HttpResponse(msg, status=status, content_type="text/plain")
                    if not json_response
                    else JsonResponse(
                        {
                            "error": msg,
                        },
                        encoder=JSONEncoder,
                        safe=False,
                        status=status,
                    )
                )

            except (AttributeError, ValueError):
                # could not parse JSON response...
                logger.warning(
                    "NOTIFY - %s - Invalid JSON Payload provided",
                    request.META["REMOTE_ADDR"],
                )

                status = ResponseCode.bad_request
                msg = _("Invalid JSON Payload provided")
                return (
                    HttpResponse(msg, status=status, content_type="text/plain")
                    if not json_response
                    else JsonResponse(
                        {
                            "error": msg,
                        },
                        encoder=JSONEncoder,
                        safe=False,
                        status=status,
                    )
                )

        if not content:
            # We could not handle the Content-Type
            logger.warning(
                "NOTIFY - %s - Invalid FORM Payload provided",
                request.META["REMOTE_ADDR"],
            )

            status = ResponseCode.bad_request
            msg = _("Bad FORM Payload provided")
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse(
                    {
                        "error": msg,
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )
            )

        if not content.get("urls") and settings.APPRISE_STATELESS_URLS:
            # fallback to settings.APPRISE_STATELESS_URLS if no urls were
            # defined
            content["urls"] = settings.APPRISE_STATELESS_URLS

        #
        # Allow 'tag' value to be specified as part of the URL parameters
        # if not found otherwise defined.
        #
        tag = content.get("tag") if content.get("tag") else content.get("tags")
        if not tag:
            if "tag" in request.GET:
                tag = request.GET["tag"]

            elif "tags" in request.GET:
                tag = request.GET["tags"]

        if tag:
            if isinstance(tag, list | set | tuple):
                content["tag"] = tag

            elif isinstance(tag, str):
                try:
                    content["tag"] = parse_tag_expression(tag)

                except ValueError:
                    logger.warning(
                        "NOTIFY - %s - Ignored invalid tag specified (type %s): %s",
                        request.META["REMOTE_ADDR"],
                        str(type(tag)),
                        str(tag)[:12],
                    )

                    status = ResponseCode.bad_request
                    msg = _("Unsupported characters found in tag definition")
                    return (
                        HttpResponse(msg, status=status, content_type="text/plain")
                        if not json_response
                        else JsonResponse(
                            {
                                "error": msg,
                            },
                            encoder=JSONEncoder,
                            safe=False,
                            status=status,
                        )
                    )

            else:
                logger.warning(
                    "NOTIFY - %s - Ignored invalid tag specified (type %s): %s",
                    request.META["REMOTE_ADDR"],
                    str(type(tag)),
                    str(tag)[:12],
                )

                status = ResponseCode.bad_request
                msg = _("Unsupported characters found in tag definition")
                return (
                    HttpResponse(msg, status=status, content_type="text/plain")
                    if not json_response
                    else JsonResponse(
                        {
                            "error": msg,
                        },
                        encoder=JSONEncoder,
                        safe=False,
                        status=status,
                    )
                )

        #
        # Allow 'format' value to be specified as part of the URL
        # parameters if not found otherwise defined.
        #
        if not content.get("format") and "format" in request.GET:
            content["format"] = request.GET["format"]

        #
        # Allow 'type' value to be specified as part of the URL parameters
        # if not found otherwise defined.
        #
        if not content.get("type") and "type" in request.GET:
            content["type"] = request.GET["type"]

        #
        # Allow 'title' value to be specified as part of the URL parameters
        # if not found otherwise defined.
        #
        if not content.get("title") and "title" in request.GET:
            content["title"] = request.GET["title"]

        # Handle Attachments
        # Normalize all three accepted attachment aliases — 'attach',
        # 'attachment', and 'attachments' — into the canonical 'attachment'
        # key before calling parse_attachments.  Priority order (highest
        # first): attach > attachment > attachments.  POST form-data keys are
        # resolved before JSON body keys of the same name so that multipart
        # form submissions always take precedence.
        attach = None
        _post_key = next(
            (k for k in ("attach", "attachment", "attachments") if k in request.POST),
            None,
        )
        if _post_key:
            # Collect URL strings from form-data, discarding blank entries.
            content["attachment"] = [a for a in request.POST.getlist(_post_key) if isinstance(a, str) and a.strip()]
        else:
            # Resolve from the JSON body in the same priority order, then
            # rename the alias to the canonical 'attachment' key.
            _json_key = next(
                (k for k in ("attach", "attachment", "attachments") if content.get(k)),
                None,
            )
            if _json_key and _json_key != "attachment":
                content["attachment"] = content.pop(_json_key)

        if "attachment" in content or request.FILES:
            try:
                attach = parse_attachments(content.get("attachment"), request.FILES)

            except (TypeError, ValueError) as e:
                # Invalid entry found in list
                logger.warning(
                    "NOTIFY - %s - Bad attachment: %s",
                    request.META["REMOTE_ADDR"],
                    str(e),
                )

                status = ResponseCode.bad_request
                msg = _("Bad Attachment")
                return (
                    HttpResponse(msg, status=status, content_type="text/plain")
                    if not json_response
                    else JsonResponse(
                        {
                            "error": msg,
                        },
                        encoder=JSONEncoder,
                        safe=False,
                        status=status,
                    )
                )

        # Some basic error checking
        # A notification requires at minimum a body or at least one valid
        # attachment; either is sufficient to proceed.
        if (not content.get("body") and not attach) or content.get(
            "type", apprise.NotifyType.INFO.value
        ) not in apprise.NOTIFY_TYPES:
            logger.warning(
                "NOTIFY - %s - Payload lacks minimum requirements",
                request.META["REMOTE_ADDR"],
            )

            status = ResponseCode.bad_request
            msg = _("Payload lacks minimum requirements")
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse(
                    {
                        "error": msg,
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )
            )

        # Acquire our body format (if identified). Formatting is entirely
        # optional:
        #  - "format" absent from the payload entirely: falls back to the
        #    server-configured APPRISE_DEFAULT_FORMAT (unset by default)
        #    rather than assuming TEXT.
        #  - "format" explicitly blank or null: forces pass-through, even
        #    overriding a configured APPRISE_DEFAULT_FORMAT -- the caller
        #    is explicitly telling us not to apply one.
        #  - "format" set to a value: used as-is (validated below).
        if "format" not in content:
            body_format = settings.APPRISE_DEFAULT_FORMAT
        else:
            body_format = content.get("format")
            if isinstance(body_format, str):
                body_format = body_format.strip()
            body_format = body_format or None
        if body_format and body_format not in apprise.NOTIFY_FORMATS:
            logger.warning(
                "NOTIFY - %s - Format parameter contains an unsupported value (%s)",
                request.META["REMOTE_ADDR"],
                str(body_format),
            )

            status = ResponseCode.bad_request
            msg = _("An invalid body input format was specified")
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse(
                    {
                        "error": msg,
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )
            )

        # Prepare our keyword arguments (to be passed into an AppriseAsset object)
        kwargs = {
            # Load our dynamic plugin path
            "plugin_paths": settings.APPRISE_PLUGIN_PATHS,
            # Emoji configuration (values are None, True, or False)
            "interpret_emojis": settings.APPRISE_INTERPRET_EMOJIS,
            # HTTP redirect behaviour (values are None, True, or False)
            "http_redirects": settings.APPRISE_HTTP_REDIRECTS,
        }
        if settings.APPRISE_STATELESS_STORAGE:
            # Persistent Storage is allowed with Stateless queries
            kwargs.update(
                {
                    # Load our persistent storage path
                    "storage_path": settings.APPRISE_STORAGE_DIR,
                    # Our storage URL ID Length
                    "storage_idlen": settings.APPRISE_STORAGE_UID_LENGTH,
                    # Define if we flush to disk as soon as possible or not when required
                    "storage_mode": settings.APPRISE_STORAGE_MODE,
                }
            )

        if body_format:
            # Store our defined body format
            kwargs["body_format"] = body_format

        # Acquire our recursion count (if defined)
        recursion = request.headers.get("X-Apprise-Recursion-Count", 0)
        try:
            recursion = int(recursion)

            if recursion < 0:
                # We do not accept negative numbers
                raise TypeError("Invalid Recursion Value")

            if recursion > settings.APPRISE_RECURSION_MAX:
                logger.warning(
                    "NOTIFY - %s - Recursion limit reached (%d > %d)",
                    request.META["REMOTE_ADDR"],
                    recursion,
                    settings.APPRISE_RECURSION_MAX,
                )

                status = ResponseCode.method_not_accepted
                msg = _("The recursion limit has been reached")
                return (
                    HttpResponse(msg, status=status, content_type="text/plain")
                    if not json_response
                    else JsonResponse(
                        {
                            "error": msg,
                        },
                        encoder=JSONEncoder,
                        safe=False,
                        status=status,
                    )
                )

            # Store our recursion value for our AppriseAsset() initialization
            kwargs["_recursion"] = recursion

        except (TypeError, ValueError):
            logger.warning(
                "NOTIFY - %s - Invalid recursion value (%s) provided",
                request.META["REMOTE_ADDR"],
                str(recursion),
            )

            status = ResponseCode.bad_request
            msg = _("An invalid recursion value was specified")
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse(
                    {
                        "error": msg,
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )
            )

        # Acquire our unique identifier (if defined)
        uid = request.headers.get("X-Apprise-ID", "").strip()
        if uid:
            kwargs["_uid"] = uid

        #
        # Apply Any Global Filters (if identified)
        #
        apply_global_filters()

        # Put result-log storage limits on the asset Apprise already receives.
        kwargs["result_log_memory_size"] = settings.APPRISE_STREAM_MEMORY_SIZE
        kwargs["result_log_disk_size"] = settings.APPRISE_STREAM_DISK_SIZE
        asset = apprise.AppriseAsset(**kwargs)

        # Prepare our apprise object
        a_obj = apprise.Apprise(asset=asset)

        # Add URLs
        a_obj.add(content.get("urls"))
        if not len(a_obj):
            logger.warning(
                "NOTIFY - %s - No valid URLs provided",
                request.META["REMOTE_ADDR"],
            )

            status = ResponseCode.no_content
            msg = _("There was no valid URLs provided to notify")
            return (
                HttpResponse(msg, status=status, content_type="text/plain")
                if not json_response
                else JsonResponse(
                    {
                        "error": msg,
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )
            )

        # Our return content type can be controlled by the Accept keyword
        # If it includes /* or /html somewhere then we return html, otherwise
        # we return the logs as they're processed in their text format.
        # The HTML response type has a bit of overhead where as it's not
        # the case with text/plain
        if not json_response:
            content_type = (
                "text/html"
                if re.search(
                    r"text\/(\*|html)",
                    request.headers.get(
                        "Accept",
                        (request.content_type if request.content_type else request.headers.get("Content-Type", "")),
                    ),
                    re.IGNORECASE,
                )
                else "text/plain"
            )
        else:
            content_type = "application/json"

        # Use the request level when valid, then the configured default.
        level = parse_log_level(
            request.headers.get("X-Apprise-Log-Level"),
            settings.APPRISE_LOG_LEVEL,
        )

        if stream_response:
            # Return live progress while notification runs in the background.
            return stream_notify_response(
                a_obj,
                body=content.get("body"),
                title=content.get("title", ""),
                notify_type=content.get("type", apprise.NotifyType.INFO.value),
                tag=(content.get("tag") or "all"),
                attach=attach,
                log_level=level,
                webhook_source=request.META.get("REMOTE_ADDR", ""),
            )

        # Capture notification logs at the caller's requested level.
        result = a_obj.notify(
            content.get("body"),
            title=content.get("title", ""),
            notify_type=content.get("type", apprise.NotifyType.INFO.value),
            tag=(content.get("tag") or "all"),
            attach=attach,
            log_level=level,
        )

        # Send the optional webhook from a separate bounded walk of the logs.
        send_notify_webhook(request.META.get("REMOTE_ADDR", ""), result)

        if not result:
            # If at least one notification couldn't be sent; change up the
            # response to a 424 error code
            logger.warning(
                "NOTIFY - %s - One or more stateless notification(s) could not be actioned",
                request.META["REMOTE_ADDR"],
            )

            status = ResponseCode.failed_dependency
            msg = _("One or more notifications could not be sent")

            return stream_result_response(
                result,
                json_response=json_response,
                content_type=content_type,
                status=status,
                error=msg,
            )

        logger.info(
            "NOTIFY - %s - Delivered Stateless Notification(s)",
            request.META["REMOTE_ADDR"],
        )

        # Return our success message
        status = ResponseCode.okay
        return stream_result_response(
            result,
            json_response=json_response,
            content_type=content_type,
            status=status,
        )


@method_decorator((gzip_page, never_cache), name="dispatch")
class JsonUrlView(View):
    """
    A Django view that lists all loaded tags and URLs for a given key
    """

    def get(self, request, key=None):
        """List URLs using a URL or header key."""
        if not stateful_store_enabled():
            return _stateful_mode_unavailable_response(request)

        key = resolve_config_key(request, key)
        if not key:
            if config_key_header_present_but_invalid(request):
                return _invalid_key_response(request)
            return _missing_key_response(request)

        if not key_auth_ok(request, key):
            return _key_access_denied_response(request, key)

        # A configuration lock permits sending, but never exposes saved URLs
        # or tags through this discovery endpoint.
        if settings.APPRISE_CONFIG_LOCK:
            logger.warning(
                "JSON URLS - %s - Config Lock Active - Request Denied",
                request.META["REMOTE_ADDR"],
            )
            return error_response(
                request,
                _("The site has been configured to deny this request"),
                ResponseCode.no_access,
            )

        # Now build our tag response that identifies all of the tags
        # and the URL's they're associated with
        #  {
        #    "tags": ["tag1', "tag2", "tag3"],
        #    "urls": [
        #       {
        #          "uid": "efa313ab",
        #          "url": "windows://",
        #          "tags": [],
        #       },
        #       {
        #          "url": "mailto://user:pass@gmail.com"
        #          "tags": ["tag1", "tag2", "tag3"]
        #       }
        #    ]
        #  }
        response = {
            "tags": set(),
            "urls": [],
        }

        # Privacy flag
        # Support 'yes', '1', 'true', 'enable', 'active', and +
        privacy = parse_bool(request.GET.get("privacy"), default=False)

        # Optionally filter on tags. Use comma to identify more then one
        tag = request.GET.get("tag", "all")

        config, format = ConfigCache.get(key)
        if config is None:
            # The returned value of config and format tell a rather cryptic
            # story; this portion could probably be updated in the future.
            # but for now it reads like this:
            #   config == None and format == None: We had an internal error
            #   config == None and format != None: we simply have no data
            #   config != None: we simply have no data
            if format is not None:
                # no content to return
                return JsonResponse(
                    response,
                    encoder=JSONEncoder,
                    safe=False,
                    status=ResponseCode.no_content,
                )

            # Something went very wrong; return 500
            response["error"] = _("There was no configuration found")
            return JsonResponse(
                response,
                encoder=JSONEncoder,
                safe=False,
                status=ResponseCode.internal_server_error,
            )

        # Prepare our apprise object
        a_obj = apprise.Apprise()

        # Create an apprise config object
        ac_obj = apprise.AppriseConfig(recursion=settings.APPRISE_RECURSION_MAX)

        # Load our configuration
        ac_obj.add_config(config, format=format)

        # Add our configuration
        a_obj.add(ac_obj)

        for notification in a_obj.find(tag):
            details = sorted(
                [tag_detail(t) for t in notification.tags],
                key=lambda item: (item["name"], item["priority"]),
            )
            url = notification.url(privacy=privacy)
            retry = service_retry(notification, url)
            optional = service_optional(notification, url)

            # Set Notification
            response["urls"].append(
                {
                    "id": notification.url_id(),
                    "service_name": str(notification.service_name) if notification.service_name else "",
                    "enabled": bool(notification.enabled),
                    "url": url,
                    "retry": retry,
                    "optional": optional,
                    "tags": sorted(tag_names(notification.tags)),
                    "tag_details": details,
                }
            )

            # Store Tags
            response["tags"] |= tag_names(notification.tags)

        # Return our retrieved content
        return JsonResponse(response, encoder=JSONEncoder, safe=False, status=ResponseCode.okay)
