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
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
import queue
import re
import struct
import tempfile
import threading
import time
from typing import Any
from urllib.parse import parse_qs, parse_qsl, quote, urlencode, urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import apprise
from apprise.config.base import ConfigBase
from apprise.utils.parse import parse_urls
from core.utils import parse_bool, parse_log_level
from dateblock import DRIFT_MAX, DRIFT_MIN
from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.core.serializers.json import DjangoJSONEncoder
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    JsonResponse,
    StreamingHttpResponse,
    UnreadablePostError,
)
from django.shortcuts import redirect, render
from django.urls import Resolver404, resolve, reverse
from django.utils.datastructures import MultiValueDict
from django.utils.decorators import method_decorator
from django.utils.html import escape
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import get_language, gettext_lazy as _
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.gzip import gzip_page
from error.views import Error421View
import yaml

from .auth import Authentication
from .exceptions import (
    AppriseAPIError,
    AppriseAPIImproperlyConfigured,
    AppriseAPIStorageError,
)
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
from .responses import error_response, parse_json_object_body
from .schedule import (
    EVERY_UNITS,
    HOLIDAY_YEARS,
    SCHEDULE_ERRORS,
    format_moment,
    holiday_countries,
    holiday_dates,
    holiday_language,
    holiday_start,
    is_one_off,
    next_run,
    parse_time_of_day,
    shortest_gap,
    until_passed,
    upcoming,
)
from .stream_manager import StreamManager
from .utils import (
    CONFIG_KEY_HEADER,
    CONFIG_KEY_PATTERN,
    MIME_IS_JSON,
    AttachmentPayload,
    ConfigCache,
    MoveResult,
    TemplateValueError,
    apply_global_filters,
    check_attachment_url,
    config_key_header_present_but_invalid,
    healthcheck,
    is_html_response,
    is_json_response,
    normalize_template_values,
    notify_asset_kwargs,
    parse_attachments,
    reminders_enabled,
    resolve_config_key,
    send_webhook,
    stateful_store_enabled,
)

# Get an instance of a logger
logger = logging.getLogger("django")


def _request_language(request):
    """Return the normalized language selected by locale middleware."""
    # Middleware sets this for web requests; direct callers fall back to English.
    return getattr(request, "LANGUAGE_CODE", settings.LANGUAGE_CODE)


def _apprise_asset(request: HttpRequest, **kwargs: Any) -> apprise.AppriseAsset:
    """Create an asset carrying the request language for core localization."""
    # Load custom plugins so every view can read URLs that use them, not
    # just the notify views.
    kwargs.setdefault("plugin_paths", settings.APPRISE_PLUGIN_PATHS)

    # The asset validates the code as it is created and stores it in Apprise's
    # own spelling, so a de-DE request becomes de_DE here. An asset language
    # cannot be changed afterwards, which is why it is passed in here.
    return apprise.AppriseAsset(language=_request_language(request), **kwargs)


def _apprise_object(request, asset=None):
    """Create an Apprise object prepared for the request language."""
    # Reuse one asset so every Apprise component sees the same settings.
    asset = asset or _apprise_asset(request)

    # Apprise resolves the asset language on its own, preferring a regional
    # catalog, then its plain language, and finally English.
    return apprise.Apprise(asset=asset)


# Used by the Review tab to narrow a quick test to the selected card.
NOTIFY_ENTRY_INDEX_HEADER = "X-Apprise-Notification-Index"

# With these content types, the whole request body is the message and its
# media type sets the input format.
NOTIFY_RAW_BODY_FORMATS = {
    "text/plain": apprise.NotifyFormat.TEXT.value,
    "text/html": apprise.NotifyFormat.HTML.value,
    "text/markdown": apprise.NotifyFormat.MARKDOWN.value,
}

# Notification fields that may also be sent as request headers.
NOTIFY_HEADER_FIELDS = (
    ("title", "X-Apprise-Title"),
    ("type", "X-Apprise-Type"),
    ("tag", "X-Apprise-Tag"),
)

# Template values sent as URL-encoded pairs, e.g. token=abc&room=ops
NOTIFY_TEMPLATE_HEADER = "X-Apprise-Template"
NOTIFY_TEMPLATE_MAX_FIELDS = 256
NOTIFY_INVALID_PERCENT_ESCAPE_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")

# Each disk entry starts with its byte length.
_EVENT_SIZE = struct.Struct("!Q")


def _is_string_or_string_list(value):
    """Return whether a JSON field is one string or a list of strings."""
    return isinstance(value, str) or (isinstance(value, list) and all(isinstance(item, str) for item in value))


# Report the first event moved to disk.
_STREAM_PUT_SPOOLED = "spooled"

# Report an event that could not be retained.
_STREAM_PUT_FAILED = "failed"

# Bound active and queued notification streams, including delivery that
# continues after a streaming client disconnects.
_STREAM_MANAGER = StreamManager(
    capacity=settings.APPRISE_STREAM_WORKER_COUNT,
    queue_size=settings.APPRISE_STREAM_QUEUE_SIZE,
)

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
                        raise AppriseAPIStorageError("Temporary storage accepted an incomplete event.")
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
                        raise AppriseAPIStorageError("Incomplete event size in temporary storage.")

                    size = _EVENT_SIZE.unpack(size_data)[0]

                    # Read exactly the event size declared above.
                    payload = self._spool.read(size)

                    if len(payload) != size:
                        raise AppriseAPIStorageError("Incomplete event in temporary storage.")

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

# Split OR groups only on commas or pipes. Spaces around them are stripped
# from each group afterwards; matching them here is very slow on long runs
# of spaces.
TAG_OR_DELIM_RE = re.compile(r"[|,]")

# Split each AND group into individual tags.
TAG_AND_DELIM_RE = re.compile(r"[\s&+]+")

# A single Apprise tag token. Supports [priority:]name[:retry].
TAG_TOKEN_RE = re.compile(
    r"^(?:[0-9]{1,9}:)?[a-z0-9][a-z0-9_-]*(?::[0-9]{1,9})?$",
    re.IGNORECASE,
)

# A notification never needs an unbounded number of routing tags.
TAG_SELECTION_MAX_ITEMS = 256


def parse_tag_expression(tag):
    """Convert a tag expression into Apprise's OR/AND structure.

    Commas and pipes mean OR. Spaces, ampersands, and plus signs mean AND.
    Tokens may use ``[priority:]tag[:retry]``.
    """
    if not isinstance(tag, str) or not TAG_VALIDATION_RE.match(tag):
        raise AppriseAPIImproperlyConfigured("Unsupported characters found in tag definition")

    tags = []
    for group in TAG_OR_DELIM_RE.split(tag):
        group = group.strip()
        if not group:
            continue

        tokens = [token for token in TAG_AND_DELIM_RE.split(group) if token]
        if not tokens or any(not TAG_TOKEN_RE.match(token) for token in tokens):
            raise AppriseAPIImproperlyConfigured("Unsupported characters found in tag definition")

        tags.append(tuple(tokens) if len(tokens) > 1 else tokens[0])

    return tags


def _tag_expression_items(tag):
    """Flatten a bounded tag selection without recursive processing."""
    pending = [tag]
    seen = set()
    items = []
    while pending:
        item = pending.pop()
        if isinstance(item, list | set | tuple):
            identity = id(item)
            if identity in seen:
                return None
            seen.add(identity)
            pending.extend(item)
        elif isinstance(item, str):
            items.append(item)
        else:
            return None

        if len(pending) + len(items) > TAG_SELECTION_MAX_ITEMS:
            return None
    return items


def tag_expression_uses_all(tag):
    """Return whether a parsed tag selection contains the special ``all`` tag."""
    items = _tag_expression_items(tag)
    return bool(items and any(tag_detail(item)["name"].casefold() == "all" for item in items))


def tag_expression_is_specific(tag):
    """Return whether every supplied item is a valid tag other than ``all``."""
    items = _tag_expression_items(tag)
    return bool(items and all(TAG_TOKEN_RE.match(item) for item in items) and not tag_expression_uses_all(items))


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


class _ClosingStreamIterator:
    """Run stream cleanup once when iteration ends or the response closes."""

    def __init__(self, iterator, close_callback):
        self._iterator = iterator
        self._close_callback = close_callback
        self._close_lock = threading.Lock()
        self._closed = False

    def __iter__(self):
        """Yield every response chunk and always finish stream cleanup."""
        try:
            yield from self._iterator
        finally:
            self.close()

    def close(self):
        """Close the iterator and release its resources once."""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True

        try:
            self._iterator.close()
        finally:
            self._close_callback()


def stream_notify_response(
    a_obj,
    *,
    body,
    title,
    notify_type,
    tag,
    attach,
    log_level,
    template=None,
    webhook_source=None,
    match_always=True,
    stream_manager=None,
):
    """
    Stream notification progress while ``notify()`` runs in the background.

    Send logs as they occur, followed by a ``result`` or ``error`` event.
    Release the active slot when work stops and admission when the stream closes.
    """
    events = _SpooledEventQueue()
    finished = threading.Event()
    stream_closed = threading.Event()
    admission_lock = threading.Lock()
    admission_released = [False]
    final_event = [_sse_event("error", {"message": "Notification processing failed."})]
    # The caller reserves this slot before building the streaming response.
    # Heartbeats prevent proxies from closing a healthy but quiet stream.
    heartbeat_seconds = settings.APPRISE_STREAM_HEARTBEAT_SECONDS

    def release_admission_if_done():
        """Release admission after both notification work and streaming end."""
        if stream_manager is None or not finished.is_set() or not stream_closed.is_set():
            return

        with admission_lock:
            if admission_released[0]:  # pragma: no cover - concurrent duplicate guard
                return
            admission_released[0] = True

        stream_manager.release_admission()

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
        # Admitted streams wait here when every active slot is busy.
        if stream_manager is not None:
            stream_manager.acquire_worker()

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
                template=template,
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
                # Events are already queued, so release result storage after
                # the optional webhook. Cleanup must not strand the stream.
                try:
                    result.close()
                except Exception:
                    _safe_stream_log(
                        logging.ERROR,
                        "Notification result storage failed to close.",
                        exc_info=True,
                    )

            # Wake the response loop even when notification work failed.
            finished.set()
            if stream_manager is not None:
                # Notification work no longer needs an active slot.
                stream_manager.release_worker()
            release_admission_if_done()

    try:
        threading.Thread(target=run, daemon=True).start()
    except RuntimeError:
        _safe_stream_log(
            logging.ERROR,
            "Notification streaming could not start.",
            exc_info=True,
        )
        finished.set()
        release_admission_if_done()

    def close_stream():
        """Close stream storage and release admission after the response ends."""
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
        # Disconnected work remains admitted until its background task ends.
        stream_closed.set()
        release_admission_if_done()

    def generator():
        storage_warning_sent = False
        # Send an ignored SSE comment so headers arrive immediately.
        yield ": connected\n\n"
        # Record activity so heartbeats are sent only while idle.
        last_sent = time.monotonic()
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
                # The warning counts as activity for the idle timer.
                last_sent = time.monotonic()
                continue

            if events.qsize():
                try:
                    yield events.get(timeout=0.25)
                except queue.Empty:
                    continue
                # Delay the next heartbeat after sending a queued event.
                last_sent = time.monotonic()
                continue

            if finished.is_set():
                # All queued logs were sent, so the final event can follow.
                break

            try:
                yield events.get(timeout=0.25)
            except queue.Empty:
                # Heartbeats keep an idle connection open without timing
                # out queued or slow notification work.
                if time.monotonic() - last_sent >= heartbeat_seconds:
                    yield ": heartbeat\n\n"
                    last_sent = time.monotonic()
                continue
            # An event that arrived while waiting also resets the timer.
            last_sent = time.monotonic()

        yield final_event[0]

    content = _ClosingStreamIterator(generator(), close_stream)
    response = StreamingHttpResponse(content, content_type="text/event-stream")
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
    payload_too_large = 413
    expectation_failed = 417
    misdirected_request = 421
    failed_dependency = 424
    fields_too_large = 431
    too_many_requests = 429
    internal_server_error = 500
    # A new live stream cannot start until server capacity becomes available.
    service_unavailable = 503


def _key_access_denied_response(request, key):
    """Return the standard Basic Auth challenge for a protected key."""
    if getattr(request, "apprise_disabled_config_key", None) == key:
        return error_response(
            request,
            _("This configuration has been disabled by an administrator"),
            ResponseCode.no_access,
        )

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


def _build_apprise_mobile_url(request, key):
    """Build a password-safe Apprise Mobile URL for this Config ID.

    A configuration login takes priority over the administrator login. Saved
    passwords are never included; ``:username@`` tells the app which username
    to prefill while still requiring its password.
    """
    host = request.get_host()
    secure = request.is_secure() or f"https://{host}".lower() in settings.APPRISE_TRUSTED_ORIGINS
    scheme = "apprises" if secure else "apprise"
    base = settings.BASE_URL or ""

    username, password_required, _ = _apprise_mobile_credentials(request, key)

    if username and password_required:
        userinfo = ":{}@".format(quote(username, safe=""))
    else:
        userinfo = "@" if password_required else ""

    return "{}://{}{}{}/{}".format(scheme, userinfo, host, base, key)


def _apprise_mobile_credentials(request, key):
    """Choose the Config ID login, falling back to admin when unassigned."""
    if not settings.APPRISE_AUTH_REQUIRED:
        return "", False, False

    state = Authentication.config_state(key, request)
    if state.assigned:
        return state.username or "", True, False

    uses_admin_credentials = getattr(request, "globally_authenticated", False)
    username = (settings.APPRISE_USER or "") if uses_admin_credentials else ""
    password_required = bool(settings.APPRISE_PASSWORD) if uses_admin_credentials else False
    return username, password_required, uses_admin_credentials


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

    if not Authentication.key_ok(request, key):
        return _key_access_denied_response(request, key)

    if not Authentication.config_lock_allows(request, key):
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
        # A missing format means the storage read failed; an empty
        # format means this key has no saved configuration.
        if format is not None:
            # no content to return
            logger.warning(
                "VIEW - %s - No configuration associated using KEY: %s",
                request.META["REMOTE_ADDR"],
                key,
            )

            # A 204 has no body and tells clients that the key is empty.
            return HttpResponse(status=ResponseCode.no_content)

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
    # reference to it through the CLI's -c option.
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

        if request.apprise_auth_permission == Authentication.ROLE_USER:
            # A shared browser login cannot inspect another key through examples.
            key = request.apprise_web_auth_key

        # Examples use the key's username when one is saved. The password is
        # always shown as a placeholder and is never read from storage.
        example_username = settings.APPRISE_USER
        if settings.APPRISE_AUTH_REQUIRED and CONFIG_KEY_PATTERN.match(key):
            example_username = Authentication.config_state(key, request).username or example_username

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


_LOGOUT_NOTICE_COOKIE = "apprise_logout_notice"


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

        logged_out = request.COOKIES.get(_LOGOUT_NOTICE_COOKIE) == "1"
        response = render(
            request,
            self.template_name,
            {
                "AUTH_ENABLED": False,
                "logged_out": logged_out,
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
        Authentication.clear_web_cookie(response)
        if logged_out:
            response.delete_cookie(
                _LOGOUT_NOTICE_COOKIE,
                path=settings.BASE_URL or "/",
                samesite="Lax",
            )
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
            # Administrator credentials always win, even when a configuration
            # happens to use the same username and password.
            if Authentication.global_credentials_ok(username, password):
                mode = Authentication.ROLE_ADMIN
                key = None
            elif CONFIG_KEY_PATTERN.match(key) and Authentication.key_credentials_ok(request, key, username, password):
                if not Authentication.config_state(key, request).disabled:
                    mode = Authentication.ROLE_USER
                else:
                    # Credentials are intentionally retained while an account is
                    # frozen, but they cannot start a browser session.
                    request.apprise_auth_permission = Authentication.ROLE_DISABLED
                    request.apprise_auth_username = None

            if mode:
                next_url = form.cleaned_data["next"] or (
                    reverse("config", kwargs={"key": key}) if mode == Authentication.ROLE_USER else reverse("welcome")
                )
                if not url_has_allowed_host_and_scheme(
                    next_url,
                    allowed_hosts={request.get_host()},
                    require_https=request.is_secure(),
                ):
                    next_url = reverse("config", kwargs={"key": key}) if key else reverse("welcome")

                if mode == Authentication.ROLE_USER:
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
                Authentication.set_web_cookie(response, request, mode, username, key)
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
        Authentication.clear_web_cookie(response)
        return response


class LogoutView(View):
    """End the signed browser login."""

    def get(self, request):
        """Redirect safe navigations without changing browser state."""
        if settings.APPRISE_API_ONLY:
            return Error421View.as_view()(request)
        return redirect("welcome")

    def post(self, request):
        """Delete the browser login after origin validation."""
        if settings.APPRISE_API_ONLY:
            return Error421View.as_view()(request)
        if not settings.APPRISE_AUTH_REQUIRED:
            return redirect("welcome")

        # DetectConfigMiddleware sees this marker while the response unwinds.
        request.clear_config_cookie = True
        response = redirect("login")
        Authentication.clear_web_cookie(response)
        secure = request.is_secure() or f"https://{request.get_host()}".lower() in settings.APPRISE_TRUSTED_ORIGINS
        response.set_cookie(
            _LOGOUT_NOTICE_COOKIE,
            "1",
            max_age=60,
            httponly=True,
            secure=secure,
            samesite="Lax",
            path=settings.BASE_URL or "/",
        )
        response["Cache-Control"] = "no-store"
        return response


# Describe this caller's access: either one key ("user") or unrestricted
# access ("admin"). Open deployments are also unrestricted.
_PRIVILEGE_LABELS = {
    Authentication.ROLE_ADMIN: "admin",
    Authentication.ROLE_USER: "user",
    Authentication.ROLE_DISABLED: "admin",
}


def _health_check_response(request, key=None):
    """Return status for keyed and keyless requests.

    ``config_lock`` says whether this caller may read configuration
    content; an administrator is never reported as locked out.
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
                "config_lock": not Authentication.config_lock_allows(request, key),
                "attach_lock": settings.APPRISE_ATTACH_SIZE <= 0,
                "stateful_enabled": stateful_enabled,
                "stateless_enabled": stateless_enabled,
                "degraded": not stateful_enabled and not stateless_enabled,
                "max_attachments": settings.APPRISE_MAX_ATTACHMENTS,
                "attach_size": settings.APPRISE_ATTACH_SIZE,
                "status": response,
                "privilege": _PRIVILEGE_LABELS[request.apprise_auth_permission],
                # Left out when reminders are off, so the server looks like
                # one from before reminders existed. Otherwise it says whether
                # this caller may add reminders to the Config ID asked about.
                **(
                    {
                        "reminders_enabled": reminders_allowed(request, key),
                        "scheduler_running": ConfigCache.scheduler_running(),
                        # The most Apprise URLs a reminder may hold; 0 when it may hold none.
                        "reminder_urls_max": settings.APPRISE_REMINDER_URLS_MAX,
                    }
                    if reminders_enabled()
                    else {}
                ),
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
            if not Authentication.key_ok(request, config_key):
                return _key_access_denied_response(request, config_key)

        return _health_check_response(request, config_key if raw_config_key else None)


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

        if not Authentication.key_ok(request, key):
            return _key_access_denied_response(request, key)

        return _health_check_response(request, key)


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
            if not Authentication.key_ok(request, config_key):
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
        a_obj = _apprise_object(request)

        # Load our details; the asset language decides what they read as
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
        if not Authentication.key_ok(request, key):
            return _key_access_denied_response(request, key)

        auth_state = Authentication.config_state(key, request)
        auth_username = auth_state.username
        config_created, config_mtime = ConfigCache.get_file_times(key)

        return render(
            request,
            self.template_name,
            {
                "key": key,
                "form_url": AddByUrlForm(),
                "form_cfg": AddByConfigForm(),
                "form_notify": NotifyForm(),
                "auth_mode": auth_state.mode,
                "auth_access": auth_state.access,
                "auth_indicator": auth_state.indicator,
                "auth_username": auth_username or "",
                "config_created": config_created,
                "config_mtime": config_mtime,
                "config_timezone": settings.TIME_ZONE,
                # Reminders show only when the API would let this caller use them.
                "reminders_allowed": reminders_allowed(request, key),
                # The Apprise URLs rows show for callers who may send to URLs of their
                # own, or keep them in reminders.
                "stateless_allowed": stateless_allowed(request, key),
                "reminder_urls_allowed": reminders_allowed(request, key) and settings.APPRISE_REMINDER_URLS_MAX > 0,
                # Only the signed-in administrator is free of the reminder limits.
                "reminders_admin": request.apprise_auth_permission == Authentication.ROLE_ADMIN,
                # Seconds are offered only when repeating more often than every minute is allowed.
                "reminder_seconds": reminder_gap_limit(request) < 60,
                # The drift range dateblock accepts.
                "reminder_drift_min": DRIFT_MIN,
                "reminder_drift_max": DRIFT_MAX,
                # The last year a holiday can be looked up, like the holidays endpoint.
                "reminder_holiday_last": datetime.now(ZoneInfo(settings.TIME_ZONE)).year + HOLIDAY_YEARS - 1,
                # A notice shows when nothing is running to send reminders.
                "reminder_scheduler_running": ConfigCache.scheduler_running(),
            },
        )

    def post(self, request, key):
        """Return stored configuration through the web UI alias."""
        return _get_config_response(request, key)


def _current_browser_config_key(request):
    """Return the configuration remembered by an authenticated browser."""
    has_browser_login = bool(request.COOKIES.get(Authentication.WEB_COOKIE))
    has_open_key = not settings.APPRISE_AUTH_REQUIRED and bool(request.COOKIES.get("key"))
    if not has_browser_login and not has_open_key:
        return None

    # A shared session trusts only the key signed into its login cookie.
    key = (
        getattr(request, "apprise_web_auth_key", None)
        if request.apprise_auth_permission == Authentication.ROLE_USER
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
        # The selector is shared by the configuration and authentication pages.
        next_name = request.POST.get("next")
        if next_name not in {"auth_current", "config_current"}:
            next_name = "config_current"
        request.default_config_id = key
        if request.apprise_auth_permission == Authentication.ROLE_USER and request.apprise_web_auth_key != key:
            # A shared login proves access to one key only. Start a clean login
            # for the requested key before any configuration view is reached.
            login_url = "{}?{}".format(
                reverse("login"),
                urlencode({"next": reverse(next_name)}),
            )
            response = redirect(login_url)
            Authentication.clear_web_cookie(response)
            response["Cache-Control"] = "no-store"
            return response

        return redirect(next_name)


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
        if not Authentication.can_list_configurations(request):
            msg = _("The site has been configured to deny this request")
            status = ResponseCode.no_access
            return error_response(request, msg, status)

        stored_keys = ConfigCache.keys()
        entries = []
        for key in stored_keys:
            auth_state = Authentication.config_state(key, request)

            entries.append(
                {
                    "key": key,
                    "user": auth_state.username,
                    "assigned": auth_state.assigned,
                    "configured": auth_state.configured,
                    "access": auth_state.access,
                    "auth_indicator": auth_state.indicator,
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
                else [
                    {
                        "key": entry["key"],
                        "user": entry["user"],
                        "access": entry["access"],
                    }
                    for entry in entries
                ],
                encoder=JSONEncoder,
                safe=False,
                status=status,
            )
        )


def _yaml_refused(content, asset):
    """Whether Apprise would refuse this YAML configuration as a whole.

    Reject syntax, structure, and section errors that discard the entire file.
    Empty files and unusable individual URLs remain valid.
    """
    try:
        document = yaml.safe_load(content)
    except (RecursionError, yaml.YAMLError):
        # PyYAML runs out of stack on very deeply nested lists or maps
        return True
    if document is None:
        return False
    if not isinstance(document, dict):
        return True
    # Only the file's own sections; its URLs are judged one by one
    try:
        sections = yaml.safe_dump({**document, "urls": []})
    except RecursionError:
        # Writing YAML back out gives up at a shallower depth than reading
        return True
    with apprise.LogCapture(level=logging.ERROR) as logs:
        ConfigBase.config_parse_yaml(sections, asset=asset)
    return bool(logs.getvalue())


@method_decorator(never_cache, name="dispatch")
class AddView(View):
    """
    A Django view used to store Apprise configuration
    """

    def post(self, request, key=None):
        """Store configuration using a URL or header key."""
        if not stateful_store_enabled():
            return _stateful_mode_unavailable_response(request)

        # Detect the format of the incoming payload.
        json_payload = MIME_IS_JSON.match(request.content_type or request.headers.get("content-type", "")) is not None

        # Detect the format our response should be in
        json_response = is_json_response(request)

        key = resolve_config_key(request, key)
        if not key:
            if config_key_header_present_but_invalid(request):
                return _invalid_key_response(request)
            return _missing_key_response(request)

        if not Authentication.key_ok(request, key):
            return _key_access_denied_response(request, key)

        if not Authentication.config_lock_allows(request, key):
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
                # An empty configuration is saved only when one was sent;
                # the form reports a missing field as empty too.
                if "config" not in request.POST:
                    content.pop("config", None)

            form = AddByUrlForm(request.POST)
            if form.is_valid():
                content.update(form.cleaned_data)

        else:  # JSON Payload
            content, parse_error = parse_json_object_body(request, "ADD", key)
            if parse_error is not None:
                return parse_error

            urls = content.get("urls")
            if "urls" in content and not _is_string_or_string_list(urls):
                return error_response(
                    request,
                    _("The urls field must be a string or list of strings"),
                    ResponseCode.bad_request,
                    field="urls",
                )

            if "config" in content and not isinstance(content["config"], str):
                return error_response(
                    request,
                    _("The config field must be a string"),
                    ResponseCode.bad_request,
                    field="config",
                )

            if "format" in content and not isinstance(content["format"], str):
                return error_response(
                    request,
                    _("The format field must be a string"),
                    ResponseCode.bad_request,
                    field="format",
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
        a_obj = _apprise_object(request)
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
            asset = _apprise_asset(request, allow_templates=settings.APPRISE_ALLOW_TEMPLATES)
            ac_obj = apprise.AppriseConfig(asset=asset, recursion=settings.APPRISE_RECURSION_MAX)

            if fmt == AUTO_DETECT_CONFIG_KEYWORD:
                # By setting format to None, it is automatically detected from
                # within the add_config() call
                fmt = None

            # Load our configuration; empty content has nothing to read
            if content["config"].strip() and not ac_obj.add_config(content["config"], format=fmt):
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

            # Add our configuration. One that loads no URLs (everything
            # commented out, or nothing there yet) is still the writer's to
            # keep, as is an empty one; only unreadable content is refused.
            a_obj.add(ac_obj)
            if not len(a_obj):
                if (
                    len(ac_obj)
                    and ac_obj[0].config_format == apprise.ConfigFormat.YAML
                    and _yaml_refused(content["config"], asset)
                ):
                    # A typo must never replace a working configuration
                    logger.warning(
                        "ADD - %s - The YAML configuration could not be parsed using KEY: %s",
                        request.META["REMOTE_ADDR"],
                        key,
                    )
                    return error_response(
                        request,
                        _("The configuration could not be parsed"),
                        ResponseCode.bad_request,
                    )
                logger.info(
                    "ADD - %s - Configuration saved with no URL(s) defined using KEY: %s",
                    request.META["REMOTE_ADDR"],
                    key,
                )

            # Empty content keeps the format asked for (plain text when
            # auto-detecting); anything else, the format it was read as.
            stored_format = ac_obj[0].config_format.value if len(ac_obj) else (fmt or apprise.ConfigFormat.TEXT.value)
            if not ConfigCache.put(key, content["config"], fmt=stored_format):
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
        response = (
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
        config_created, config_mtime = ConfigCache.get_file_times(key)
        if config_created is not None:
            response["X-Apprise-Config-Created"] = config_created.isoformat(timespec="seconds")
        if config_mtime is not None:
            response["X-Apprise-Config-MTime"] = config_mtime.isoformat(timespec="seconds")
        response["X-Apprise-Timezone"] = settings.TIME_ZONE
        return response


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

        if not Authentication.key_ok(request, key):
            return _key_access_denied_response(request, key)

        if not Authentication.can_move_or_delete(request):
            # A locked site permits this only for an authenticated administrator.
            logger.warning(
                "MOVE - %s - Config Lock Active - Request Denied",
                request.META["REMOTE_ADDR"],
            )
            msg = _("The site has been configured to deny this request")
            status = ResponseCode.no_access
            return error_response(request, msg, status)

        shared_user = request.apprise_auth_permission == Authentication.ROLE_USER

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
        if from_config_id != key and not Authentication.key_ok(request, from_config_id):
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
        content, parse_error = parse_json_object_body(request, "MOVE", key)
        if parse_error is not None:
            return None, None, parse_error

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
            request.headers.get(Authentication.WEB_HEADER) == "1"
            and getattr(request, "default_config_id", None) == from_config_id
        ):
            # Keep an administrator's current-key alias aligned after a GUI move.
            request.default_config_id = to_config_id
        if (
            request.apprise_auth_permission == Authentication.ROLE_USER
            and getattr(request, "apprise_web_auth_key", None) == from_config_id
        ):
            # Refresh the browser login after its lock moves to the new ID.
            request.default_config_id = to_config_id
            Authentication.set_web_cookie(
                response,
                request,
                Authentication.ROLE_USER,
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

        if not Authentication.key_ok(request, key):
            return _key_access_denied_response(request, key)

        if not Authentication.config_lock_allows(request, key):
            # Only normal user access or an administrator may remove content.
            logger.warning(
                "DEL - %s - Config Lock Active - Request Denied",
                request.META["REMOTE_ADDR"],
            )
            msg = _("The site has been configured to deny this request")
            status = ResponseCode.no_access
            return error_response(request, msg, status)

        shared_user = request.apprise_auth_permission == Authentication.ROLE_USER

        # A configuration user keeps their login and receives a fresh prune
        # grace period so they can return and save replacement content.
        result = ConfigCache.clear_preserving_auth(key) if shared_user else ConfigCache.clear(key)

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

        # Administrator deletion removes the whole account. A configuration
        # user's credential file is intentionally retained above.
        if not shared_user and ConfigCache.clear_auth(key) is False:
            logger.error(
                "DEL - %s - Configuration removed but its authentication lock could not be, using KEY: %s",
                request.META["REMOTE_ADDR"],
                key,
            )
            msg = _("The configuration was removed, but its authentication could not be removed")
            return error_response(request, msg, ResponseCode.internal_server_error)

        if not shared_user and not ConfigCache.set_limits(key, {}):
            # Nothing is left for the limits to apply to, so this only leaves a small file.
            logger.warning("DEL - %s - Limits could not be removed using KEY: %s", request.META["REMOTE_ADDR"], key)

        if result is None:
            logger.warning(
                "DEL - %s - No configuration associated using KEY: %s",
                request.META["REMOTE_ADDR"],
                key,
            )
            # A 204 has no body and tells clients there is nothing to remove.
            return HttpResponse(status=ResponseCode.no_content)

        # Removed content
        logger.info(
            "DEL - %s - Removed configuration associated using KEY: %s",
            request.META["REMOTE_ADDR"],
            key,
        )

        # Like any successful delete, the answer is a 204 with no body.
        return HttpResponse(status=ResponseCode.no_content)


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

        if not Authentication.key_ok(request, key):
            return _key_access_denied_response(request, key)

        auth_state = Authentication.config_state(key, request)
        # Only a saved per-key username belongs in this editor. The global
        # username remains separate and is never copied into a new lock.
        username = auth_state.username if auth_state.assigned else ""
        shared_user = request.apprise_auth_permission == Authentication.ROLE_USER
        selected_access = auth_state.saved_access or auth_state.access
        access_downgraded = selected_access != auth_state.access
        if json_response:
            return JsonResponse(
                {
                    "mode": auth_state.mode,
                    "access": selected_access,
                    "username": username,
                },
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
                "auth_access": auth_state.access,
                "auth_indicator": auth_state.indicator,
                "auth_selected_access": selected_access,
                "auth_access_downgraded": access_downgraded,
                "auth_has_credentials": auth_state.assigned,
                "auth_username": username,
                "shared_user": shared_user,
                # The reminder limits show only where reminders can be used.
                "reminders_allowed": reminders_allowed(request, key),
                # The Apprise URLs rows show for callers who may send to URLs of their
                # own, or keep them in reminders.
                "stateless_allowed": stateless_allowed(request, key),
                "reminder_urls_allowed": reminders_allowed(request, key) and settings.APPRISE_REMINDER_URLS_MAX > 0,
                "form_auth": AuthForm(
                    initial={"access": selected_access, "username": username},
                    shared=shared_user,
                    current_username=username,
                    current_access=selected_access,
                    has_credentials=auth_state.assigned,
                    require_current=shared_user,
                ),
                # Do not build a form for an action this request cannot use.
                "form_move": (
                    MoveConfigForm(
                        initial={"from": key},
                        restricted=shared_user,
                        current_from=key,
                    )
                    if Authentication.can_move_or_delete(request)
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
            return error_response(
                request,
                _("Content-Type must be application/json"),
                ResponseCode.bad_request,
            )

        if not settings.APPRISE_AUTH_REQUIRED:
            return _per_key_auth_unavailable_response(request)

        key = resolve_config_key(request, key)
        if not key:
            if config_key_header_present_but_invalid(request):
                return _invalid_key_response(request)
            return _missing_key_response(request)

        auth_state = Authentication.config_state(key, request)
        # Only the global administrator may create a key's first access record.
        # Existing credentials accept either the admin or their current user.
        if not auth_state.configured:
            if not getattr(request, "globally_authenticated", False):
                return _key_access_denied_response(request, key)
        elif not Authentication.key_ok(request, key):
            return _key_access_denied_response(request, key)

        content, parse_error = parse_json_object_body(request, "AUTH", key)
        if parse_error is not None:
            return parse_error

        shared_user = request.apprise_auth_permission == Authentication.ROLE_USER
        browser_shared_user = shared_user and getattr(request, "apprise_web_auth_key", None) == key
        current_username = auth_state.username or ""
        # Password-only updates preserve the administrator's saved selection,
        # even when a global lock currently enforces a stricter effective mode.
        current_access = auth_state.saved_access or auth_state.access

        if shared_user and "access" in content:
            # A configuration user may rotate credentials, but access is an
            # administrator-owned policy even when the submitted value matches.
            return error_response(
                request,
                _("Only an administrator may set configuration access"),
                ResponseCode.no_access,
                field="access",
            )

        username = content.get("username", "" if shared_user else current_username)
        access = content.get("access", current_access)
        current_password = content.get("current_password")
        password = content.get("password", "" if not shared_user else None)
        password_confirm = content.get("password_confirm")
        if username is None:
            username = ""
        if (
            not isinstance(password, str)
            or not isinstance(username, str)
            or not isinstance(access, str)
            or (browser_shared_user and not isinstance(password_confirm, str))
            or (shared_user and password_confirm is not None and not isinstance(password_confirm, str))
            or (browser_shared_user and current_password is not None and not isinstance(current_password, str))
        ):
            return error_response(
                request,
                _("Valid username and password fields are required"),
                ResponseCode.bad_request,
            )

        # Forms and Basic Auth both ignore surrounding username whitespace.
        username = username.strip()
        if shared_user and username != current_username:
            return error_response(
                request,
                _("The username cannot be changed by a configuration user"),
                ResponseCode.no_access,
                field="username",
            )
        # API users repeat their saved username but do not need the browser's
        # confirmation field.
        form_data = content.copy()
        form_data.update({"access": access, "username": username, "password": password})
        if shared_user and not browser_shared_user and password_confirm is None:
            form_data["password_confirm"] = password

        form = AuthForm(
            form_data,
            shared=shared_user,
            current_username=current_username,
            current_access=current_access,
            has_credentials=auth_state.assigned,
            require_current=browser_shared_user,
        )
        if not form.is_valid():
            field, errors = next(iter(form.errors.items()))
            return error_response(
                request,
                errors[0],
                ResponseCode.bad_request,
                field=field,
            )

        username = form.cleaned_data["username"]
        password = form.cleaned_data["password"]
        access = form.cleaned_data["access"]
        if browser_shared_user and not ConfigCache.verify_auth(
            key,
            username,
            form.cleaned_data["current_password"],
        ):
            return error_response(
                request,
                _("The current password was not accepted"),
                ResponseCode.bad_request,
                field="current_password",
            )

        if shared_user and ConfigCache.verify_auth(key, username, password):
            return error_response(
                request,
                _("The new password must differ from the current password"),
                ResponseCode.bad_request,
                field="password",
            )

        saved = (
            ConfigCache.set_auth(key, username, password, access=access)
            if password
            else ConfigCache.set_access(key, access)
        )
        if not saved:
            logger.error(
                "AUTH - %s - Could not write authentication associated using KEY: %s",
                request.META["REMOTE_ADDR"],
                key,
            )
            return error_response(
                request,
                _("The authentication could not be saved"),
                ResponseCode.internal_server_error,
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
            else JsonResponse(
                {
                    "error": None,
                    "auth_state": Authentication.effective_config_access(access),
                },
                encoder=JSONEncoder,
                safe=False,
                status=status,
            )
        )
        if shared_user and getattr(request, "apprise_web_auth_key", None) == key:
            # Keep the current browser signed in with the newly saved digest.
            Authentication.set_web_cookie(response, request, Authentication.ROLE_USER, username, key)
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

        if not Authentication.key_ok(request, key):
            return _key_access_denied_response(request, key)

        # Configuration users may rotate their password, but only the global
        # administrator can remove their account.
        if not getattr(request, "globally_authenticated", False):
            return error_response(
                request,
                _("Global administrator credentials are required to remove authentication"),
                ResponseCode.no_access,
            )

        # The user's reminders go with their login, unless ?keep_reminders=yes
        # hands them to the administrator. How they were is kept, so a failure
        # either way leaves the login and its reminders as they were.
        reminder_file = ConfigCache.reminder_path(key)
        released = ConfigCache.release_user_reminders(key, parse_bool(request.GET.get("keep_reminders", False)))
        if released is None:
            logger.error(
                "AUTH - %s - Reminders of the user could not be updated using KEY: %s",
                request.META["REMOTE_ADDR"],
                key,
            )
            return error_response(
                request,
                _("The authentication could not be removed"),
                ResponseCode.internal_server_error,
            )

        before, after = released
        result = ConfigCache.clear_auth(key)
        with ConfigCache.reminder_guard():
            if result is False:
                # Put the user's reminders back beside the login that is still
                # there, keeping any change someone made meanwhile.
                current = ConfigCache.read_reminders(reminder_file)
                if current is not None:
                    ConfigCache.write_reminders(reminder_file, _undo_release(before, after, current))

            else:
                # Files only the removed reminders used can go now.
                ConfigCache.drop_unused_reminder_files(
                    entry["file"] for event in before for entry in event.get("attach") or [] if "file" in entry
                )

        if result is False:
            logger.error(
                "AUTH - %s - Authentication could not be removed associated using KEY: %s",
                request.META["REMOTE_ADDR"],
                key,
            )
            return error_response(
                request,
                _("The authentication could not be removed"),
                ResponseCode.internal_server_error,
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
            else JsonResponse(
                {"error": None, "auth_state": "admin"},
                encoder=JSONEncoder,
                safe=False,
                status=status,
            )
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


@method_decorator(never_cache, name="dispatch")
class MobileQrView(View):
    """Return the apprise[s]:// URL used to prefill the Apprise Mobile app."""

    def get(self, request, key=None):
        """Return the URL as JSON for the page's QR code icon to render."""
        if not stateful_store_enabled():
            return _stateful_mode_unavailable_response(request)

        key = resolve_config_key(request, key)
        if not key:
            if config_key_header_present_but_invalid(request):
                return _invalid_key_response(request)
            return _missing_key_response(request)

        if not Authentication.key_ok(request, key):
            return _key_access_denied_response(request, key)

        url = _build_apprise_mobile_url(request, key)
        _, password_required, uses_admin_credentials = _apprise_mobile_credentials(request, key)
        return JsonResponse(
            {
                "url": url,
                "uses_admin_credentials": uses_admin_credentials,
                "password_required": password_required,
            },
            encoder=JSONEncoder,
            safe=False,
            status=ResponseCode.okay,
        )


@method_decorator(never_cache, name="dispatch")
class CurrentMobileQrView(View):
    """Return the current browser configuration's Apprise Mobile URL."""

    def get(self, request):
        """Resolve the remembered Config ID, then reuse MobileQrView."""
        key = _current_browser_config_key(request)
        return MobileQrView.as_view()(request, key=key) if key else _missing_key_response(request)


@method_decorator((gzip_page, never_cache), name="dispatch")
class GetView(View):
    """
    A Django view used to retrieve previously stored Apprise configuration
    """

    def post(self, request, key=None):
        """Return a configuration, using the header key on bare ``/get``."""
        return _get_config_response(request, key)


def _notify_log_level(request, key=None):
    """Allow detailed stored-config logs only for admins or ``user`` access."""
    level = parse_log_level(
        request.headers.get("X-Apprise-Log-Level"),
        settings.APPRISE_LOG_LEVEL,
    )

    if key is None or level >= logging.INFO:
        # Nothing stored is at stake, or nothing detailed was asked for
        return level

    if not settings.APPRISE_AUTH_REQUIRED:
        # No access control is set up on this server at all
        return level

    role = getattr(request, "apprise_auth_permission", None)
    if role == Authentication.ROLE_ADMIN:
        return level

    if (
        role == Authentication.ROLE_USER
        and Authentication.config_state(key, request).access == Authentication.ACCESS_USER
    ):
        return level

    return logging.INFO


def _deliver_notification(request, a_obj, content, attach, json_response, stream_response, tag, key=None):
    """Run and render the common stateful/stateless notification path."""
    if json_response:
        content_type = "application/json"
    else:
        accept = request.headers.get(
            "Accept",
            request.content_type or request.headers.get("Content-Type", ""),
        )
        content_type = "text/html" if re.search(r"text\/(\*|html)", accept, re.IGNORECASE) else "text/plain"

    level = _notify_log_level(request, key)
    selected_tag = content.get("tag") or ("all" if key is None else None)
    notify_kwargs = {
        "title": content.get("title", ""),
        "notify_type": content.get("type") or apprise.NotifyType.INFO.value,
        "tag": selected_tag,
        "attach": attach,
        "template": content.get("template") or None,
        "log_level": level,
    }

    if stream_response:
        # Reserve capacity before creating another background stream thread.
        if not _STREAM_MANAGER.try_admit():
            # Record the rejection without including notification content.
            logger.warning(
                "NOTIFY - %s - Live notification stream rejected; capacity reached",
                request.META["REMOTE_ADDR"],
            )
            # Tell the caller when it is reasonable to try again.
            return error_response(
                request,
                _("The server is temporarily unable to start a new live notification stream"),
                ResponseCode.service_unavailable,
                headers={"Retry-After": str(settings.APPRISE_STREAM_RETRY_AFTER_SECONDS)},
            )

        return stream_notify_response(
            a_obj,
            body=content.get("body"),
            webhook_source=request.META.get("REMOTE_ADDR", ""),
            stream_manager=_STREAM_MANAGER,
            **notify_kwargs,
        )

    try:
        result = a_obj.notify(content.get("body"), **notify_kwargs)

    except apprise.exception.AppriseException as e:
        logger.error(
            "NOTIFY - %s - Notification delivery failed%s: %s",
            request.META["REMOTE_ADDR"],
            "" if key is None else f" using KEY: {key}",
            e,
        )
        return error_response(
            request,
            _("An error occurred delivering the notification"),
            ResponseCode.bad_request,
        )

    send_notify_webhook(request.META.get("REMOTE_ADDR", ""), result)

    if not result:
        status = ResponseCode.failed_dependency
        msg = _(
            "One or more notification could not be sent"
            if key is not None
            else "One or more notifications could not be sent"
        )
        if key is None:
            logger.warning(
                "NOTIFY - %s - One or more stateless notification(s) could not be actioned",
                request.META["REMOTE_ADDR"],
            )
        else:
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

    if key is None:
        logger.info("NOTIFY - %s - Delivered Stateless Notification(s)", request.META["REMOTE_ADDR"])
    else:
        logger.info(
            "NOTIFY - %s - Delivered Notification(s) - %sKEY: %s",
            request.META["REMOTE_ADDR"],
            "" if not tag else f"Tags: {tag}, ",
            key,
        )
    return stream_result_response(
        result,
        json_response=json_response,
        content_type=content_type,
        status=ResponseCode.okay,
    )


def _select_notify_entry(request, a_obj):
    """Select one loaded entry for a Review-tab quick test.

    The request header carries the card's index. Return ``(a_obj, None)``
    on success or ``(None, error_response)`` if it is invalid or stale.
    """
    # Without this header, the normal request sends to every matching entry.
    raw_index = request.headers.get(NOTIFY_ENTRY_INDEX_HEADER)
    if raw_index is None:
        return a_obj, None

    # Accept only short, ordinary decimal indexes before converting to int.
    if not re.fullmatch(r"0|[1-9][0-9]{0,9}", raw_index):
        return None, error_response(
            request,
            _("The selected notification entry is invalid"),
            ResponseCode.bad_request,
        )

    wanted = int(raw_index)
    # Read loaded entries without resolving a pending template first.
    selected = next(
        (service for index, service in enumerate(a_obj.find(resolve=False)) if index == wanted),
        None,
    )
    if selected is None:
        # A card may have been removed since the Review tab was opened.
        return None, error_response(
            request,
            _("The selected notification entry is no longer available"),
            ResponseCode.bad_request,
        )

    # Keep pending templates unresolved and exclude sibling entries.
    a_obj.services[:] = [selected]
    return a_obj, None


def _load_notify_content(request, form_class, key=None):
    """Load and remap one notification payload for either notify endpoint."""
    json_response = is_json_response(request)
    json_payload = MIME_IS_JSON.match(request.content_type or request.headers.get("content-type", "")) is not None
    rules = {name[1:]: value for name, value in request.GET.items() if name.startswith(":")}

    raw_format = NOTIFY_RAW_BODY_FORMATS.get(request.content_type)
    if raw_format is not None:
        try:
            body = request.body.decode(request.encoding or settings.DEFAULT_CHARSET)

        except RequestDataTooBig:
            logger.warning(
                "NOTIFY - %s - Message body exceeded %dMB%s",
                request.META["REMOTE_ADDR"],
                settings.APPRISE_UPLOAD_MAX_MEMORY_SIZE / 1048576,
                " using KEY: {}".format(key) if key is not None else "",
            )
            # Match the status a JSON payload of the same size receives.
            return (
                None,
                json_response,
                error_response(
                    request,
                    _("The message body provided is too large"),
                    ResponseCode.fields_too_large,
                ),
            )

        except UnreadablePostError:
            # The client went away before the whole body arrived.
            logger.warning(
                "NOTIFY - %s - Message body could not be read%s",
                request.META["REMOTE_ADDR"],
                " using KEY: {}".format(key) if key is not None else "",
            )
            return (
                None,
                json_response,
                error_response(
                    request,
                    _("The message body could not be read"),
                    ResponseCode.bad_request,
                ),
            )

        except (UnicodeError, LookupError):
            # Bytes that do not fit the charset, or a charset such as rot13
            # that names a codec but not a text encoding.
            logger.warning(
                "NOTIFY - %s - Message body could not be decoded%s",
                request.META["REMOTE_ADDR"],
                " using KEY: {}".format(key) if key is not None else "",
            )
            return (
                None,
                json_response,
                error_response(
                    request,
                    _("The message body could not be decoded"),
                    ResponseCode.bad_request,
                ),
            )

        # A ?format= value still takes priority over the media type.
        content = {"body": body, "format": request.GET.get("format") or raw_format}
        invalid = _apply_notify_headers(request, content)
        return (None, json_response, invalid) if invalid is not None else (content, json_response, None)

    if json_payload:
        content, parse_error = parse_json_object_body(request, "NOTIFY", key)
        if parse_error is not None:
            return None, json_response, parse_error
        data = content
    else:
        data = request.POST.copy() if rules else request.POST

    if rules and not remap_fields(rules, data, form=form_class()):
        message = (
            _("Payload field mapping failed using KEY: {}").format(key)
            if key is not None
            else _("Payload field mapping failed")
        )
        return None, json_response, error_response(request, message, ResponseCode.bad_request)

    if json_payload:
        content = data
        for field in ("body", "title", "type"):
            if field in content and not isinstance(content[field], str):
                return (
                    None,
                    json_response,
                    error_response(
                        request,
                        _("The {} field must be a string").format(field),
                        ResponseCode.bad_request,
                        field=field,
                    ),
                )

        if "format" in content and content["format"] is not None and not isinstance(content["format"], str):
            return (
                None,
                json_response,
                error_response(
                    request,
                    _("The format field must be a string or null"),
                    ResponseCode.bad_request,
                    field="format",
                ),
            )

        # urls is parsed for stateless notifications only
        urls = content.get("urls")
        if "urls" in form_class.base_fields and "urls" in content and not _is_string_or_string_list(urls):
            return (
                None,
                json_response,
                error_response(
                    request,
                    _("The urls field must be a string or list of strings"),
                    ResponseCode.bad_request,
                    field="urls",
                ),
            )

        if "template" in content:
            template, error = _notify_template(request, content["template"])
            if error is not None:
                return None, json_response, error

            content["template"] = template
    else:
        form = form_class(data=data, files=request.FILES)
        content = dict(form.cleaned_data) if form.is_valid() else {}
        if content and not data.get("type"):
            # The form fills in "info" on its own; drop it so a header or
            # query string type can still apply.
            content.pop("type", None)

    if content:
        invalid = _apply_notify_headers(request, content)
        if invalid is not None:
            return None, json_response, invalid

        return content, json_response, None

    logger.warning(
        "NOTIFY - %s - Invalid FORM Payload provided%s",
        request.META["REMOTE_ADDR"],
        " using KEY: {}".format(key) if key is not None else "",
    )
    return (
        None,
        json_response,
        error_response(
            request,
            _("Bad FORM Payload provided"),
            ResponseCode.bad_request,
        ),
    )


def _notify_template(request, template):
    """Validate JSON template values without revealing required names.

    Return ``(values, None)`` on success or ``(None, response)`` on error.
    """

    try:
        # One shared reader keeps this in step with the form fields.
        # A missing field simply reads as nothing supplied.
        values = normalize_template_values(template)

    except TemplateValueError:
        return None, error_response(
            request,
            _("The template field must be an object of name/value pairs"),
            ResponseCode.bad_request,
            field="template",
        )

    return (values or None), None


def _notify_header(request, name: str) -> str:
    """Return a header value, reading UTF-8 text that arrived as Latin-1."""
    value = request.headers.get(name, "").strip()
    try:
        # Servers hand header bytes over as Latin-1, so UTF-8 text such as
        # an accented title shows up garbled until it is decoded again.
        return value.encode("latin-1").decode("utf-8")

    except UnicodeError:
        return value


def _apply_notify_headers(request, content: dict) -> HttpResponse | None:
    """Fill in fields the payload left out from ``X-Apprise-*`` headers.

    - A payload field always wins over its header.
    - A header wins over the matching query string value.

    Return ``None`` on success or an error response.
    """
    for name, header in NOTIFY_HEADER_FIELDS:
        value = _notify_header(request, header)
        if value and not content.get(name) and not (name == "tag" and content.get("tags")):
            content[name] = value

    raw_template = _notify_header(request, NOTIFY_TEMPLATE_HEADER)
    if not raw_template or content.get("template"):
        return None

    try:
        if NOTIFY_INVALID_PERCENT_ESCAPE_RE.search(raw_template):
            raise ValueError("invalid percent escape")

        pairs = parse_qsl(
            raw_template,
            keep_blank_values=True,
            strict_parsing=True,
            encoding="utf-8",
            errors="strict",
            max_num_fields=NOTIFY_TEMPLATE_MAX_FIELDS,
        )
        if len({name for name, _value in pairs}) != len(pairs):
            raise TemplateValueError("duplicate template variable name")

        content["template"] = normalize_template_values(dict(pairs)) or None

    except (UnicodeError, ValueError):
        # Keep the error independent of saved template names and values.
        return error_response(
            request,
            _("The X-Apprise-Template header is invalid"),
            ResponseCode.bad_request,
            field="template",
        )

    return None


def _notify_tag(request, content, key=None, require_specific=False):
    """Normalize a notification tag and optionally require a non-``all`` tag."""
    tag = content.get("tag") or content.get("tags")
    if not tag:
        tag = request.GET.get("tag") or request.GET.get("tags")

    try:
        if isinstance(tag, str):
            normalized = parse_tag_expression(tag)
        elif isinstance(tag, list | set | tuple):
            items = _tag_expression_items(tag)
            if not items or any(not TAG_TOKEN_RE.match(item) for item in items):
                raise AppriseAPIImproperlyConfigured("Invalid tag token")
            normalized = tag
        elif tag:
            raise AppriseAPIImproperlyConfigured("Invalid tag value")
        else:
            normalized = None
    except ValueError:
        if require_specific:
            return tag, error_response(
                request,
                _("A specific tag other than 'all' is required for this configuration"),
                ResponseCode.bad_request,
            )
        logger.warning(
            "NOTIFY - %s - Ignored invalid tag specified (type %s): %s%s",
            request.META["REMOTE_ADDR"],
            str(type(tag)),
            str(tag)[:12],
            " using KEY: {}".format(key) if key is not None else "",
        )
        return tag, error_response(
            request,
            _("Unsupported characters found in tag definition"),
            ResponseCode.bad_request,
        )

    if require_specific and not tag_expression_is_specific(normalized):
        return tag, error_response(
            request,
            _("A specific tag other than 'all' is required for this configuration"),
            ResponseCode.bad_request,
        )
    if normalized is not None:
        content["tag"] = normalized
    return tag, None


def _notify_attachments(request, content, key=None):
    """Normalize attachment aliases and build the common attachment objects."""
    post_key = next(
        (name for name in ("attach", "attachment", "attachments") if name in request.POST),
        None,
    )
    if post_key:
        content["attachment"] = [
            value for value in request.POST.getlist(post_key) if isinstance(value, str) and value.strip()
        ]
    else:
        json_key = next(
            (name for name in ("attach", "attachment", "attachments") if content.get(name)),
            None,
        )
        if json_key and json_key != "attachment":
            content["attachment"] = content.pop(json_key)

    if "attachment" not in content and not request.FILES:
        return None, None
    try:
        return parse_attachments(content.get("attachment"), request.FILES), None
    except AppriseAPIError as error:
        logger.warning(
            "NOTIFY - %s - Bad attachment%s: %s",
            request.META["REMOTE_ADDR"],
            " using KEY: {}".format(key) if key is not None else "",
            str(error),
        )
        return None, error_response(
            request,
            _("Bad Attachment"),
            ResponseCode.bad_request,
        )


def _apply_notify_query_overrides(request, content):
    """Apply the three notification fields supported through the query string."""
    for name in ("format", "type", "title"):
        if not content.get(name) and name in request.GET:
            content[name] = request.GET[name]


def _notify_body_format(request, content, key=None):
    """Normalize and validate the requested message format."""
    if "format" not in content:
        body_format = settings.APPRISE_DEFAULT_FORMAT
    else:
        body_format = content.get("format")
        if isinstance(body_format, str):
            body_format = body_format.strip()
        body_format = body_format or None

    if not body_format or body_format in apprise.NOTIFY_FORMATS:
        return body_format, None
    logger.warning(
        "NOTIFY - %s - Format parameter contains an unsupported value (%s)%s",
        request.META["REMOTE_ADDR"],
        str(body_format),
        " using KEY: {}".format(key) if key is not None else "",
    )
    return None, error_response(
        request,
        _("An invalid body input format was specified"),
        ResponseCode.bad_request,
    )


def _notify_asset(request, body_format, persistent):
    """Build the common Apprise asset and validate recursion once."""
    kwargs = notify_asset_kwargs(body_format, persistent)

    recursion = request.headers.get("X-Apprise-Recursion-Count", 0)
    try:
        recursion = int(recursion)
        if recursion < 0:
            raise AppriseAPIImproperlyConfigured("Invalid Recursion Value")
        if recursion > settings.APPRISE_RECURSION_MAX:
            logger.warning(
                "NOTIFY - %s - Recursion limit reached (%d > %d)",
                request.META["REMOTE_ADDR"],
                recursion,
                settings.APPRISE_RECURSION_MAX,
            )
            return None, error_response(
                request,
                _("The recursion limit has been reached"),
                ResponseCode.method_not_accepted,
            )
        kwargs["_recursion"] = recursion
    except (TypeError, ValueError):
        logger.warning(
            "NOTIFY - %s - Invalid recursion value (%s) provided",
            request.META["REMOTE_ADDR"],
            str(recursion),
        )
        return None, error_response(
            request,
            _("An invalid recursion value was specified"),
            ResponseCode.bad_request,
        )

    uid = request.headers.get("X-Apprise-ID", "").strip()
    if uid:
        kwargs["_uid"] = uid
    apply_global_filters()
    try:
        return _apprise_asset(request, **kwargs), None

    except apprise.exception.AppriseException as e:
        logger.error("NOTIFY - %s - Could not prepare Apprise asset: %s", request.META["REMOTE_ADDR"], e)
        return None, error_response(
            request,
            _("An error occurred preparing the notification"),
            ResponseCode.internal_server_error,
        )


@method_decorator((gzip_page, never_cache), name="dispatch")
class StatefulNotifyView(View):
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

        if not Authentication.key_ok(request, key, allow_public=True):
            return _key_access_denied_response(request, key)

        # Public and locked callers may send only to a tag they already know.
        # Administrators keep unrestricted access to every destination.
        tag_only_access = (
            request.apprise_auth_permission != Authentication.ROLE_ADMIN
            and Authentication.configuration_is_locked(key, request)
        )

        # Event streams use their own format instead of JSON, HTML, or text.
        stream_response = _stream_requested(request)
        content, json_response, invalid = _load_notify_content(request, NotifyForm, key)
        if invalid is not None:
            return invalid

        # Check restricted routing before attachments can trigger file or
        # network IO. Unrestricted callers follow the same normalization once.
        tag, invalid = _notify_tag(request, content, key, require_specific=tag_only_access)
        if invalid is not None:
            return invalid

        attach, invalid = _notify_attachments(request, content, key)
        if invalid is not None:
            return invalid

        # Validation - Tag Logic:
        # "TagA"                        : TagA
        # "TagA, TagB"                  : TagA OR TagB
        # "TagA TagB"                  : TagA AND TagB
        # "TagA TagC, TagB"             : (TagA AND TagC) OR TagB
        # ['TagA', 'TagB']              : TagA OR TagB
        # [('TagA', 'TagC'), 'TagB']    : (TagA AND TagC) OR TagB
        # [('TagB', 'TagC')]            : TagB AND TagC
        #
        # Allow 'format' value to be specified as part of the URL
        # parameters if not found otherwise defined.
        #
        _apply_notify_query_overrides(request, content)

        # Some basic error checking
        # An empty type counts as not sent, so it falls back to "info"
        if (not content.get("body") and not attach) or (
            content.get("type") or apprise.NotifyType.INFO.value
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

        body_format, invalid = _notify_body_format(request, content, key)
        if invalid is not None:
            return invalid

        # If we get here, we have enough information to generate a notification
        # with.
        config, format = ConfigCache.get(key)
        if config is None:
            # A missing format means the storage read failed; an empty
            # format means this key has no saved configuration.
            if format is not None:
                # no content to return
                logger.debug(
                    "NOTIFY - %s - Empty configuration found using KEY: %s",
                    request.META["REMOTE_ADDR"],
                    key,
                )

                # Report a failure because no notification can be sent.
                return error_response(
                    request,
                    _("There was no configuration found"),
                    ResponseCode.not_found,
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

        asset, invalid = _notify_asset(request, body_format, persistent=True)
        if invalid is not None:
            return invalid

        # Prepare our apprise object
        a_obj = _apprise_object(request, asset=asset)

        # Create an apprise config object
        ac_obj = apprise.AppriseConfig(asset=asset, recursion=settings.APPRISE_RECURSION_MAX)

        # Load our configuration
        ac_obj.add_config(config, format=format)

        # Add our configuration
        a_obj.add(ac_obj)

        # A card-level quick test must not include sibling entries that happen
        # to share its tag. The tag is retained as an additional restriction.
        a_obj, invalid = _select_notify_entry(request, a_obj)
        if invalid is not None:
            return invalid

        return _deliver_notification(
            request,
            a_obj,
            content,
            attach,
            json_response,
            stream_response,
            tag,
            key=key,
        )


@method_decorator((gzip_page, never_cache), name="dispatch")
class StatelessNotifyView(View):
    """
    A Django view for sending a stateless notification
    """

    def post(self, request):
        """Send statelessly, or preserve a header-scoped stateful v2 call."""
        raw_config_key = request.headers.get(CONFIG_KEY_HEADER, "").strip()
        config_key = None
        if raw_config_key:
            config_key = resolve_config_key(request, "")
            if not config_key:
                return _invalid_key_response(request)

        content, json_response, invalid = _load_notify_content(request, NotifyByUrlForm)
        if invalid is not None:
            return invalid

        if config_key is not None and not content.get("urls"):
            # Apprise API v2 keeps the saved Config ID out of the URL. Preserve
            # that established stateful contract when no arbitrary destination
            # URLs were supplied. Explicit URLs identify a stateless request
            # and use the key only as its authentication scope.
            return StatefulNotifyView.as_view()(request, key=config_key)

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

        refused = _stateless_refusal(request, config_key)
        if refused is not None:
            return refused

        if not content.get("urls") and settings.APPRISE_STATELESS_URLS:
            # fallback to settings.APPRISE_STATELESS_URLS if no urls were
            # defined
            content["urls"] = settings.APPRISE_STATELESS_URLS

        #
        # Allow 'tag' value to be specified as part of the URL parameters
        # if not found otherwise defined.
        #
        tag, invalid = _notify_tag(request, content)
        if invalid is not None:
            return invalid

        #
        # Allow 'format' value to be specified as part of the URL
        # parameters if not found otherwise defined.
        #
        _apply_notify_query_overrides(request, content)

        attach, invalid = _notify_attachments(request, content)
        if invalid is not None:
            return invalid

        # Some basic error checking
        # A notification requires at minimum a body or at least one valid
        # attachment; either is sufficient to proceed.
        # An empty type counts as not sent, so it falls back to "info"
        if (not content.get("body") and not attach) or (
            content.get("type") or apprise.NotifyType.INFO.value
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

        body_format, invalid = _notify_body_format(request, content)
        if invalid is not None:
            return invalid

        asset, invalid = _notify_asset(
            request,
            body_format,
            persistent=settings.APPRISE_STATELESS_STORAGE,
        )
        if invalid is not None:
            return invalid

        # Prepare our apprise object
        a_obj = _apprise_object(request, asset=asset)

        # Add URLs
        a_obj.add(content.get("urls"))
        if not len(a_obj):
            logger.warning(
                "NOTIFY - %s - No valid URLs provided",
                request.META["REMOTE_ADDR"],
            )

            # Report bad input because no notification can be sent.
            return error_response(
                request,
                _("There was no valid URLs provided to notify"),
                ResponseCode.bad_request,
            )

        return _deliver_notification(
            request,
            a_obj,
            content,
            attach,
            json_response,
            stream_response,
            tag,
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

        if not Authentication.key_ok(request, key):
            return _key_access_denied_response(request, key)

        # Only a verified global administrator may inspect a locked entry.
        if not Authentication.config_lock_allows(request, key):
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

        # Template names are always listed. Their configuration defaults are
        # only shown with privacy=0 and never while CONFIG_LOCK is set. Server
        # environment values are never returned or hinted at.
        expose_template_names = settings.APPRISE_ALLOW_TEMPLATES
        expose_template_defaults = expose_template_names and not privacy and not settings.APPRISE_CONFIG_LOCK

        # Optionally filter on tags. Use comma to identify more then one
        tag = request.GET.get("tag", "all")

        config, format = ConfigCache.get(key)
        if config is None:
            # A missing format means the storage read failed; an empty
            # format means this key has no saved configuration.
            if format is not None:
                # no content to return
                # A 204 has no body and tells clients that the key is empty.
                return HttpResponse(status=ResponseCode.no_content)

            # Something went very wrong; return 500
            response["error"] = _("There was no configuration found")
            return JsonResponse(
                response,
                encoder=JSONEncoder,
                safe=False,
                status=ResponseCode.internal_server_error,
            )

        # Use one asset so parsing and reporting share the server toggle.
        asset = _apprise_asset(request, allow_templates=settings.APPRISE_ALLOW_TEMPLATES)
        a_obj = _apprise_object(request, asset=asset)

        # Create an apprise config object
        ac_obj = apprise.AppriseConfig(asset=asset, recursion=settings.APPRISE_RECURSION_MAX)

        # Load our configuration
        ac_obj.add_config(config, format=format)

        # Add our configuration
        a_obj.add(ac_obj)

        # List pending entries as written so their markers remain visible.
        for notification in a_obj.find(tag, resolve=False):
            details = sorted(
                [tag_detail(t) for t in notification.tags],
                key=lambda item: (item["name"], item["priority"]),
            )
            url = notification.url(privacy=privacy)
            retry = service_retry(notification, url)
            optional = service_optional(notification, url)

            # Each name maps to its YAML default when allowed, otherwise None.
            template = {}
            if expose_template_names:
                for name in sorted(getattr(notification, "template_names", ())):
                    default = notification.template_schema.variables[name].default
                    template[name] = default if expose_template_defaults else None
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
                    "template": template,
                }
            )

            # Store Tags
            response["tags"] |= tag_names(notification.tags)

        # Return our retrieved content
        return JsonResponse(response, encoder=JSONEncoder, safe=False, status=ResponseCode.okay)


def _undo_release(before: list[dict], after: list[dict], current: list[dict]) -> list[dict]:
    """Return the reminders as they were before a login's reminders were released.

    - ``before`` / ``after``: the reminders just before and after the release.
    - ``current``: the reminders now. Ones changed or removed since the
      release keep that change; the rest go back to how they were, and the
      ones the release removed come back.
    """
    was = {event.get("id"): event for event in before}
    released = {event.get("id"): event for event in after}
    # Untouched since the release: back to how it was. Changed since: kept.
    restored = [
        was.get(event.get("id"), event) if released.get(event.get("id")) == event else event for event in current
    ]
    # Removed by the release and not added again since: brought back.
    present = {event.get("id") for event in current}
    return restored + [event for event in before if event.get("id") not in present and event.get("id") not in released]


def _saved_config_missing(request, key: str) -> HttpResponse | None:
    """Return the error to send when no configuration is saved under ``key``.

    - 404 when nothing is saved, 500 when it could not be read.
    - Returns ``None`` when a configuration is saved.
    """
    config, config_format = ConfigCache.get(key)
    if config is not None:
        return None

    # An empty format means nothing is saved; None means a read error.
    if config_format is not None:
        return error_response(request, _("There was no configuration found"), ResponseCode.not_found)

    return error_response(request, _("An error occurred accessing configuration"), ResponseCode.internal_server_error)


def _reminder_targets_missing(request, key: str, event: dict) -> HttpResponse | None:
    """Return the error to send when a reminder would reach no service.

    - It reaches its own ``urls``, plus the configuration's URLs its tag
      picks (the untagged ones when it has no tag), the same as when sent.
    - Returns ``None`` when it reaches at least one.
    """
    if event.get("urls"):
        # Its own URLs are always sent to.
        return None

    config, config_format = ConfigCache.get(key)
    if config is None:
        # Reported by _saved_config_missing().
        return None

    # Loaded the way the scheduler loads it, with the same allowed services.
    apply_global_filters()
    asset = apprise.AppriseAsset(plugin_paths=settings.APPRISE_PLUGIN_PATHS)
    a_obj = apprise.Apprise(asset=asset)
    ac_obj = apprise.AppriseConfig(asset=asset, recursion=settings.APPRISE_RECURSION_MAX)
    ac_obj.add_config(config, format=config_format)
    a_obj.add(ac_obj)
    tag = parse_tag_expression(event["tag"]) if event.get("tag") else None
    if next(iter(a_obj.find(tag)), None) is not None:
        return None

    return error_response(
        request,
        _(
            "No notification targets are selected. Add configuration endpoints or choose one or more matching tags "
            "before sending."
        ),
        ResponseCode.bad_request,
        field="tag" if event.get("tag") else "urls",
    )


def _stateless_refusal(request, key: str | None) -> HttpResponse | None:
    """Return why this caller may not send to Apprise URLs of their own, or ``None``.

    - Stateless mode itself is checked by the caller.
    - With logins required, only the administrator, or the user of a Config
      ID whose access is ``user``, may.
    """
    if not settings.APPRISE_AUTH_REQUIRED or getattr(request, "globally_authenticated", False):
        return None

    if key is None or not Authentication.key_ok(request, key):
        return _key_access_denied_response(request, key or "stateless")

    # A successful non-administrator key check identifies the caller as
    # that configuration's user. Only normal ``user`` access grants the
    # additional privilege of sending arbitrary stateless URLs.
    if Authentication.config_state(key, request).access != Authentication.ACCESS_USER:
        return error_response(
            request,
            _("This configuration does not permit stateless notifications"),
            ResponseCode.no_access,
        )

    return None


def stateless_allowed(request, key: str | None) -> bool:
    """Return whether this caller may send to Apprise URLs of their own."""
    return settings.APPRISE_STATELESS_MODE != "disabled" and _stateless_refusal(request, key) is None


# The most Apprise URLs one /resolve request may check.
RESOLVE_URLS_MAX = 100


@method_decorator(never_cache, name="dispatch")
class ResolveView(View):
    """Check Apprise URLs and return them in full and with their secrets hidden."""

    def post(self, request, key=None):
        """Return each URL's service, whether it loads, and the URL as Apprise reads it.

        - The JSON body holds ``urls``: one string (split on spaces and
          commas) or a list, where each entry is one URL.
        - The answer lists them in the same order. ``url`` is the full URL
          with every option written out, and ``privacy_url`` is the same URL
          with its secrets hidden, safe to show to anyone looking.
        - Needs a Config ID (in the URL or X-Apprise-Config-ID) the caller
          may use. On a ``public`` or ``disabled`` configuration only the
          administrator may, so it never answers anyone who has not signed in.
        - It works whatever the stateful and stateless modes are. Nothing is
          contacted; the URLs are only read.
        """
        # A malformed X-Apprise-Config-ID header is refused outright; the
        # header wins over the key in the URL.
        if config_key_header_present_but_invalid(request):
            return _invalid_key_response(request)

        key = resolve_config_key(request, key or "")
        if not key:
            return _missing_key_response(request)

        if not Authentication.key_ok(request, key):
            return _key_access_denied_response(request, key)

        state = Authentication.config_state(key, request)
        admin = getattr(request, "apprise_auth_permission", None) == Authentication.ROLE_ADMIN
        if not admin and (state.public or state.disabled):
            return error_response(
                request, _("The site has been configured to deny this request"), ResponseCode.no_access
            )

        # Only a JSON body is read.
        if request.content_type != "application/json":
            return error_response(request, _("Content-Type must be application/json"), ResponseCode.bad_request)

        content, invalid = parse_json_object_body(request, "RESOLVE", key)
        if invalid is not None:
            return invalid

        urls = content.get("urls")
        if not _is_string_or_string_list(urls):
            return error_response(
                request, _("The urls field must be a string or list of strings"), ResponseCode.bad_request, field="urls"
            )

        # A list keeps one entry per URL, so each answer lines up with what was sent.
        entries = parse_urls(urls) if isinstance(urls, str) else [url.strip() for url in urls]
        if len(entries) > RESOLVE_URLS_MAX:
            return error_response(
                request, _("The {} field is too long").format("urls"), ResponseCode.bad_request, field="urls"
            )

        # Read the way a send reads them, with the same allowed services.
        apply_global_filters()
        asset = apprise.AppriseAsset(plugin_paths=settings.APPRISE_PLUGIN_PATHS)
        resolved = []
        for url in entries:
            plugin = apprise.Apprise.instantiate(url, asset=asset) if url else None
            if plugin is None:
                # The URL comes back as it was sent; its hidden form is only
                # the schema, as the rest may hold a secret.
                resolved.append(
                    {
                        "url": url,
                        "privacy_url": url.split("://", 1)[0] + "://" if "://" in url else "",
                        "service": None,
                        "valid": False,
                        "error": str(_("This URL could not be loaded")),
                    }
                )
                continue

            resolved.append(
                {
                    "url": plugin.url(),
                    "privacy_url": plugin.url(privacy=True),
                    "service": str(plugin.service_name),
                    # The ID /json/urls gives the same URL in a configuration.
                    "id": plugin.url_id(),
                    "valid": True,
                }
            )

        return JsonResponse({"urls": resolved}, encoder=JSONEncoder, safe=False, status=ResponseCode.okay)


@method_decorator(never_cache, name="dispatch")
class AttachPreviewView(View):
    """Download an attachment web address the way a send would, so it can be checked."""

    def post(self, request, key):
        """Return the downloaded file, or why it could not be downloaded.

        - The JSON body holds ``url``; the same rules as /notify apply.
        - The file comes back as ``application/octet-stream``, so a browser never
          runs it. Its own type is in ``X-Apprise-Content-Type`` and its name in
          ``X-Apprise-Filename``.
        - A failed download returns 424 with ``logs``: what the download
          reported, such as a 404 or an address the rules refuse.
        """
        # Previews belong to a saved configuration, so they need stateful storage.
        if not stateful_store_enabled():
            return _stateful_mode_unavailable_response(request)

        # The caller must be allowed to use this Config ID.
        if not Authentication.key_ok(request, key):
            return _key_access_denied_response(request, key)

        # Only a saved configuration can preview attachments, so a made-up
        # key cannot be used to download anything.
        missing = _saved_config_missing(request, key)
        if missing is not None:
            return missing

        # Only a JSON body is read.
        if request.content_type != "application/json":
            return error_response(request, _("Content-Type must be application/json"), ResponseCode.bad_request)

        content, invalid = parse_json_object_body(request, "ATTACH", key)
        if invalid is not None:
            return invalid

        # Download it the way /notify would, keeping everything Apprise logs on
        # the way so a failure can explain itself.
        url = content.get("url")
        with apprise.LogCapture(level=logging.DEBUG, fmt="%(levelname)s - %(message)s") as logs:
            try:
                # A size limit of 0 means attachments are turned off.
                if settings.APPRISE_ATTACH_SIZE <= 0:
                    raise AppriseAPIImproperlyConfigured("Attachment support has been disabled")

                # Refuse addresses the attachment rules block, then download it.
                check_attachment_url(url)
                (attachment,) = parse_attachments([url], None)

            except AppriseAPIError as e:
                logger.warning("ATTACH - %s - Preview failed using KEY: %s", request.META["REMOTE_ADDR"], key)
                logger.debug("Attachment preview error: %s", e)
                # 424: the download failed; the logs say why (blank lines dropped).
                return JsonResponse(
                    {
                        "error": str(e),
                        "logs": [line for line in logs.getvalue().splitlines() if line.strip()],
                    },
                    encoder=JSONEncoder,
                    status=ResponseCode.failed_dependency,
                )

        # FileResponse closes the file once it has been sent.
        response = FileResponse(open(attachment.path, "rb"), content_type="application/octet-stream")  # noqa: SIM115
        # The real type and name travel in headers, since the body is always
        # sent as plain bytes.
        response["X-Apprise-Content-Type"] = attachment.mimetype or "application/octet-stream"
        response["X-Apprise-Filename"] = quote(attachment.name or "attachment")
        # Never show or sniff it as a page from this site.
        response["Content-Disposition"] = "attachment"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "sandbox"
        return response


# Fields a caller may set on a reminder. The server keeps the rest.
REMINDER_FIELDS = (
    "name",
    "enabled",
    "title",
    "body",
    "type",
    "format",
    "tag",
    "template",
    "when",
    "start",
    "until",
    "count",
    "skip",
    "attach",
    "urls",
)

# The longest reminder name.
REMINDER_NAME_MAX = 100

# The longest text each reminder field may hold. Reminders are saved and read
# again on every check, so they are kept to a sensible size:
# - title: the same as the form's Title field.
# - body: the most most services accept in one message.
# - tag and cron: far more than any real entry needs.
REMINDER_TEXT_MAX = {
    "title": apprise.NotifyBase.title_maxlen,
    "body": apprise.NotifyBase.body_maxlen,
    "tag": 1000,
    "cron": 255,
}

# The most calendar occurrences listed for one reminder.
REMINDER_OCCURRENCES_MAX = 200

# The most skipped times one repeating reminder holds.
REMINDER_SKIP_MAX = 200

# The highest send count a reminder may stop after.
REMINDER_COUNT_MAX = 1000000

# The latest time a reminder can hold: the end of the year 9999 (UTC).
REMINDER_TIME_MAX = 253402300799

# The most space one saved reminder may take, in bytes of JSON. Every field
# has its own limit too; this also covers template values and the rest.
REMINDER_SIZE_MAX = 262144

# How many upcoming times a preview lists by default, and at most.
REMINDER_PREVIEW_DEFAULT = 5
REMINDER_PREVIEW_MAX = 25


class ReminderFieldError(ValueError):
    """A reminder field that cannot be saved."""

    def __init__(self, message: str, field: str) -> None:
        """Keep the message and the name of the field it is about."""
        super().__init__(message)
        self.message = message
        self.field = field


def _invalid_reminder_field(field: str) -> ReminderFieldError:
    """Return the error for a reminder field holding an unusable value."""
    return ReminderFieldError(_("The {} field is invalid").format(field), field)


def _check_reminder_length(field: str, text: Any, kind: str | None = None) -> None:
    """Refuse text longer than ``REMINDER_TEXT_MAX`` allows.

    - ``field``: the field named in the error.
    - ``kind``: the limit to use, when it is not the field's own.
    """
    if isinstance(text, str) and len(text) > REMINDER_TEXT_MAX[kind or field]:
        raise ReminderFieldError(_("The {} field is too long").format(field), field)


def _clean_reminder_holiday(rule: Any) -> dict:
    """Check a holiday rule and return it ready to store.

    - ``country`` and ``name`` are required; ``region`` is optional.
    - It is sent at the time of day of the reminder's ``start``, or at
      midnight without one. The start's month and day are not used.
    """
    if not isinstance(rule, dict):
        raise _invalid_reminder_field("when")

    # Every field must be non-empty text; only the region may be left out.
    cleaned = {}
    for field in ("country", "region", "name"):
        value = rule.get(field)
        if value is None and field == "region":
            continue

        if not isinstance(value, str) or not value.strip():
            raise _invalid_reminder_field("when")

        cleaned[field] = value.strip()

    # Country codes are stored in capitals, e.g. "CA".
    cleaned["country"] = cleaned["country"].upper()
    try:
        # Fails when the country or region is unknown.
        holiday_dates(cleaned["country"], cleaned.get("region"), datetime.now(UTC).year)

    except SCHEDULE_ERRORS:
        raise _invalid_reminder_field("when") from None

    return cleaned


# The most units an ``every`` rule may wait between sends.
REMINDER_INTERVAL_MAX = 999

# Weekday names an ``every`` rule takes, which dateblock reads in its cron.
REMINDER_DAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")


def _clean_reminder_every(rule: Any) -> dict:
    """Check an ``every`` rule and return it ready to store.

    - ``unit`` is required; ``interval`` defaults to 1.
    - ``months`` and ``days`` are dropped when they name every month or day,
      except days for weeks, where no days means the start date's weekday.
    - Months suit every unit but years; days suit seconds to weeks.
    """
    if not isinstance(rule, dict) or rule.get("unit") not in EVERY_UNITS:
        raise _invalid_reminder_field("when")

    # How many units to wait between sends; True/False are not numbers here.
    interval = 1 if rule.get("interval") is None else rule["interval"]
    if isinstance(interval, bool) or not isinstance(interval, int) or not 1 <= interval <= REMINDER_INTERVAL_MAX:
        raise _invalid_reminder_field("when")

    cleaned = {"interval": interval, "unit": rule["unit"]}

    # Months are numbered 1 (January) to 12 (December).
    months = rule.get("months") or []
    if (
        not isinstance(months, list)
        or not all(isinstance(month, int) and not isinstance(month, bool) and 1 <= month <= 12 for month in months)
        or (months and rule["unit"] == "years")
    ):
        raise _invalid_reminder_field("when")

    # Days are named like REMINDER_DAYS, e.g. "Mon".
    days = rule.get("days") or []
    if (
        not isinstance(days, list)
        or not all(day in REMINDER_DAYS for day in days)
        or (days and rule["unit"] in ("months", "years"))
    ):
        raise _invalid_reminder_field("when")

    # Store them without duplicates, in calendar order.
    if 0 < len(set(months)) < 12:
        cleaned["months"] = sorted(set(months))

    if 0 < len(set(days)) < 7 or (days and rule["unit"] == "weeks"):
        cleaned["days"] = [day for day in REMINDER_DAYS if day in days]

    return cleaned


def _clean_reminder_moment(content: dict, field: str) -> str | None:
    """Return an optional date and time field in its stored form."""
    # Left out: there is no such time.
    value = content.get(field)
    if value is None:
        return None

    try:
        # Stored as YYYY-MM-DDThh:mm:ss.
        return format_moment(value)

    except (TypeError, ValueError):
        raise _invalid_reminder_field(field) from None


def _clean_reminder_time(content: dict) -> dict:
    """Check a reminder's ``when``, ``start``, ``until`` and ``count`` fields.

    - An ``every`` rule without ``at`` counts from ``start``, or else from now.
    - A holiday's ``start`` may be just a time of day (``hh:mm`` or
      ``hh:mm:ss``); it is stored on the holiday's next date.
    """
    when = content.get("when")
    if isinstance(when, dict):
        # A part set to null is the same as one left out.
        when = {part: value for part, value in when.items() if value is not None}

    clock = None
    if isinstance(when, dict) and "holiday" in when:
        try:
            # A holiday may start from a time of day alone.
            clock = parse_time_of_day(content.get("start"))

        except ValueError:
            raise _invalid_reminder_field("start") from None

    # A time of day alone is given a date once the holiday is checked.
    start = None if clock is not None else _clean_reminder_moment(content, "start")

    # Times are read in the reminder's timezone, or the server's when none is set.
    tz = (when.get("tz") if isinstance(when, dict) else None) or settings.TIME_ZONE
    try:
        zone = ZoneInfo(tz)

    except SCHEDULE_ERRORS:
        raise _invalid_reminder_field("when") from None

    # Whether the caller gave a time to start from, rather than "now".
    given = bool(start) or (isinstance(when, dict) and "at" in when)

    # An "every" rule on its own starts at ``start``, or at the start of this minute.
    if isinstance(when, dict) and "every" in when and not any(kind in when for kind in ("at", "cron", "holiday")):
        when = {**when, "at": start or datetime.now(zone).replace(second=0, microsecond=0, tzinfo=None).isoformat()}

    kinds = [kind for kind in ("at", "cron", "holiday") if isinstance(when, dict) and kind in when]
    if len(kinds) != 1:
        # Exactly one of a single time, a repeating cron or a holiday is needed.
        raise _invalid_reminder_field("when")

    # Check the one kind given:
    # - "holiday": a holiday rule.
    # - "at": a date and time, stored in one form.
    # - "cron": a cron entry, kept as written; ``isc`` marks the 5-field form.
    kind = kinds[0]
    isc = when.get("isc", False)
    if kind == "holiday":
        rule = _clean_reminder_holiday(when["holiday"])

    elif not isinstance(when[kind], str) or not when[kind].strip() or not isinstance(isc, bool):
        raise _invalid_reminder_field("when")

    elif kind == "at":
        try:
            rule = format_moment(when[kind])

        except ValueError:
            raise _invalid_reminder_field("when") from None

    else:
        rule = when[kind].strip()
        _check_reminder_length("when", rule, "cron")

    cleaned = {"tz": tz, kind: rule}
    if kind == "cron" and isc:
        cleaned["isc"] = True

    # Seconds added to every time; only kept when there are some.
    drift = when.get("drift") or 0
    if isinstance(drift, bool) or not isinstance(drift, int) or not DRIFT_MIN <= drift <= DRIFT_MAX:
        raise _invalid_reminder_field("drift")

    if drift:
        cleaned["drift"] = drift

    if clock is not None:
        # Stored on the holiday's next date, as the web form does.
        start = holiday_start(rule, zone, clock, drift)

    if "every" in when:
        if kind != "at":
            # A repeating rule counts from a start time.
            raise _invalid_reminder_field("when")

        cleaned["every"] = _clean_reminder_every(when["every"])

    # When to stop: an end date, a number of sends, or neither.
    until = _clean_reminder_moment(content, "until")

    # The end cannot come before a start that was given; an "every" rule
    # starts at its own time, and a holiday at the start of its start's
    # year. Starting now with an end already passed is allowed: it is saved
    # but not sent. Both are stored as local "YYYY-MM-DDThh:mm:ss", so they
    # compare as text.
    begins = start or (cleaned["at"] if "every" in cleaned else None)
    if begins is not None and kind == "holiday":
        begins = begins[:4] + "-01-01T00:00:00"

    if until is not None and given and begins is not None and until < begins:
        raise ReminderFieldError(_("The end date is before the start"), "until")

    count = content.get("count")
    if count is not None and (
        isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= REMINDER_COUNT_MAX
    ):
        raise _invalid_reminder_field("count")

    # Times (UNIX timestamps) a repeating reminder passes over.
    skip = content.get("skip") or []
    if (
        not isinstance(skip, list)
        or len(skip) > REMINDER_SKIP_MAX
        or not all(isinstance(due, int) and not isinstance(due, bool) and due <= REMINDER_TIME_MAX for due in skip)
    ):
        raise _invalid_reminder_field("skip")

    # Times that have already passed are not kept.
    now = time.time()
    return {
        "when": cleaned,
        "start": start,
        "until": until,
        "count": count,
        "skip": sorted({due for due in skip if due > now}),
    }


def _clean_reminder_urls(urls: Any, kept: list[str] | None = None, known: list[str] | None = None) -> list[str] | None:
    """Check the Apprise URLs a reminder sends to instead of its configuration's.

    - Read the same way as /notify: one string of URLs (split on spaces and
      commas) or a list of them.
    - Each must load in Apprise, and there may be at most
      APPRISE_REMINDER_URLS_MAX of them (0 turns them off).
    - ``kept``: the URLs the reminder holds now. Saved again unchanged, they
      are not checked, so a reminder can still be changed or paused after
      the server stops allowing them.
    - ``known``: URLs that ``@<id>`` may name (see ``url_ids`` in a
      reminder read), so a caller that only sees hidden URLs can keep some
      and drop or add others. Defaults to ``kept``. URLs named that way, or
      already ``kept``, are not checked again; any URL written out is.
    """
    if not _is_string_or_string_list(urls):
        raise ReminderFieldError(_("The urls field must be a string or list of strings"), "urls")

    known = known if known is not None else (kept or [])
    entries, named = _expand_kept_urls(parse_urls(urls), known)
    if not entries:
        return None

    if entries == kept:
        return entries

    limit = settings.APPRISE_REMINDER_URLS_MAX
    if limit <= 0:
        raise ReminderFieldError(_("This server does not save URLs with reminders"), "urls")

    if len(entries) > limit:
        raise ReminderFieldError(_("A reminder can hold at most {} URLs").format(limit), "urls")

    # Checked against the server's allowed and denied services, as when sent.
    # URLs it already held are not checked again, only new ones.
    apply_global_filters()
    a_obj = apprise.Apprise(asset=apprise.AppriseAsset(plugin_paths=settings.APPRISE_PLUGIN_PATHS))
    unchecked = set(named) | set(kept or [])
    if not all(a_obj.add(url) for url in entries if url not in unchecked):
        raise _invalid_reminder_field("urls")

    return entries


def _reminder_url_id(url: str) -> str:
    """Return the ID of one of a reminder's own Apprise URLs.

    It is the ID /json/urls gives the same URL in a configuration, so a
    caller can tell which of its configuration's URLs a reminder holds. A
    URL Apprise can't identify (or no longer loads) gets one made from the
    URL itself, so every URL can still be kept by ID.
    """
    plugin = apprise.Apprise.instantiate(url)
    url_id = plugin.url_id() if plugin is not None else None
    return url_id or hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]


def _expand_kept_urls(entries: list[str], known: list[str]) -> tuple[list[str], list[str]]:
    """Replace each ``@<id>`` in ``entries`` with the known URL(s) of that ID.

    - Returns the URLs to save, and which of them were named by ID.
    - An ID none of ``known`` has is refused as an invalid ``urls`` field.
    """
    if not any(entry.startswith("@") for entry in entries):
        return entries, []

    by_id: dict[str, list[str]] = {}
    for url in known:
        by_id.setdefault(_reminder_url_id(url), []).append(url)

    expanded: list[str] = []
    named: list[str] = []
    for entry in entries:
        found = by_id.get(entry[1:]) if entry.startswith("@") else [entry]
        if not found:
            raise _invalid_reminder_field("urls")

        if entry.startswith("@"):
            named.extend(found)
        expanded.extend(url for url in found if url not in expanded)

    return expanded, named


def _clean_reminder(
    content: dict,
    attached: bool = False,
    kept_urls: list[str] | None = None,
    known_urls: list[str] | None = None,
) -> dict:
    """Check the reminder fields a caller sent and return them ready to store.

    - ``attached``: attachments were sent, so the body may be empty.
    - ``kept_urls``: the Apprise URLs the reminder holds now, if any.
    - ``known_urls``: URLs ``urls`` may name by ``@<id>``; see ``_clean_reminder_urls()``.
    - ``attach`` is checked separately by ``_reminder_attachments()``.
    - A field set to null is the same as one left out, so it takes its default.
    """
    # With its own URLs, a tag given as null or empty also sends to the
    # configuration's untagged URLs, the way /notify/{KEY} does without a tag.
    untagged = "tag" in content
    content = {field: value for field, value in content.items() if value is not None}
    # The title and body are text, and a body is needed unless files are attached.
    for field in ("title", "body"):
        if not isinstance(content.get(field, ""), str):
            raise ReminderFieldError(_("The {} field must be a string").format(field), field)

        _check_reminder_length(field, content.get(field, ""))

    if not content.get("body", "").strip() and not attached:
        raise ReminderFieldError(_("Payload lacks minimum requirements"), "body")

    name = content.get("name")
    if name is None or (isinstance(name, str) and not name.strip()):
        # Like the form, a reminder without a name is named after its title or
        # the first line of its message.
        text = content.get("title", "").strip() or content.get("body", "").strip()
        name = text.splitlines()[0][:REMINDER_NAME_MAX] if text else str(_("Reminder"))

    if not isinstance(name, str) or len(name.strip()) > REMINDER_NAME_MAX:
        raise _invalid_reminder_field("name")

    # The message type (info, success, warning or failure), info by default.
    notify_type = content.get("type") or apprise.NotifyType.INFO.value
    if not isinstance(notify_type, str) or notify_type not in apprise.NOTIFY_TYPES:
        raise _invalid_reminder_field("type")

    # The body's input format (text, markdown or html); blank means none.
    body_format = content.get("format", settings.APPRISE_DEFAULT_FORMAT)
    if isinstance(body_format, str):
        body_format = body_format.strip() or None

    if body_format is not None and (not isinstance(body_format, str) or body_format not in apprise.NOTIFY_FORMATS):
        raise ReminderFieldError(_("An invalid body input format was specified"), "format")

    tag = content.get("tag") or None
    if isinstance(tag, list):
        # A list means any of its entries, the same as commas. An entry that is
        # itself a list needs all of its tags, the same as /notify.
        # Empty entries are left out, so [] and [[]] are the same as no tag.
        groups = [item if isinstance(item, list) else [item] for item in tag]
        groups = [group for group in groups if group]
        if not all(isinstance(word, str) for group in groups for word in group):
            raise ReminderFieldError(_("Unsupported characters found in tag definition"), "tag")

        tag = ", ".join(" ".join(group) for group in groups) or None

    if tag is not None:
        _check_reminder_length("tag", tag)
        try:
            # The scheduler turns the saved text into Apprise's tag form.
            tag = tag.strip() if parse_tag_expression(tag) else None

        except ValueError:
            raise ReminderFieldError(_("Unsupported characters found in tag definition"), "tag") from None

    # No more template values than /notify reads from a form.
    entries = content.get("template")
    if isinstance(entries, dict) and len(entries) > NOTIFY_TEMPLATE_MAX_FIELDS:
        raise ReminderFieldError(_("The {} field is too long").format("template"), "template")

    try:
        # Checked the same way as /notify; blank values are left out so the
        # configuration's fallback values apply.
        template = normalize_template_values(entries) or None

    except TemplateValueError:
        raise ReminderFieldError(_("The template field must be an object of name/value pairs"), "template") from None

    # A paused reminder is kept but not sent.
    enabled = content.get("enabled", True)
    if not isinstance(enabled, bool):
        raise _invalid_reminder_field("enabled")

    cleaned = {
        "name": name.strip(),
        "enabled": enabled,
        "title": content.get("title", ""),
        "body": content.get("body", ""),
        "type": notify_type,
        "format": body_format,
        "tag": tag,
        "template": template,
        "urls": _clean_reminder_urls(content.get("urls", []), kept_urls, known_urls),
        **_clean_reminder_time(content),
    }

    if cleaned["urls"] and untagged and not cleaned["tag"]:
        # Saved as an empty tag, unlike a reminder sending to its URLs alone.
        cleaned["tag"] = ""

    return cleaned


def _stored_fields(stored: dict) -> dict:
    """Return the fields a caller may set from a saved reminder, for a PATCH to change.

    A field saved as null was never set, so it is left out rather than read
    as one sent as null.
    """
    return {field: stored[field] for field in REMINDER_FIELDS if stored.get(field) is not None}


def _reminder_occurrences(event: dict, start: int, end: int, paused: bool = False) -> list[int]:
    """Return when a reminder is due between ``start`` and ``end``, from now on.

    - ``paused``: also list the times a paused reminder would be due.
    """
    # Nothing to list for a paused reminder (unless asked) or one that is finished.
    if (event.get("enabled") is False and not paused) or event.get("next") is None:
        return []

    # A one-off already sent, or a time already passed, has nothing to show.
    after = datetime.fromtimestamp(max(start, int(time.time())) - 1, UTC)
    try:
        times = upcoming(event, after, REMINDER_OCCURRENCES_MAX, before=end)

    except SCHEDULE_ERRORS:
        # A saved time that can no longer be read shows nothing.
        return []

    return [due for due in times if start <= due < end]


def _never_sent(event: dict, after: datetime) -> ReminderFieldError:
    """Return why a reminder has no time to send, naming the end date when it is the cause."""
    # Would it be sent without the end date? Then the end date is the problem.
    if event.get("until") is not None:
        try:
            if next_run({**event, "until": None}, after) is not None:
                return ReminderFieldError(_("Nothing would be sent before the end date"), "until")

        except SCHEDULE_ERRORS:
            pass

    return ReminderFieldError(_("This reminder would never be sent"), "when")


def _plan_reminder(event: dict, now: int) -> int | None:
    """Return when a reminder is next due, refusing one that can never be sent.

    - Returns ``None`` for one whose time has already passed: a one-off set in
      the past, or a repeating one past its end date. It is saved but never
      sent, and the form warns about it.
    """
    try:
        due = next_run(event, datetime.fromtimestamp(now, UTC))

    except SCHEDULE_ERRORS:
        raise _invalid_reminder_field("when") from None

    # No time left at all; fine only when its end date has gone by.
    if due is None:
        if until_passed(event, datetime.fromtimestamp(now, UTC)):
            return None

        raise _never_sent(event, datetime.fromtimestamp(now, UTC))

    # A one-off set in the past.
    if due < now:
        return None

    return due


def reminder_gap_limit(request) -> int:
    """Return the fewest seconds apart the caller may set a reminder to repeat.

    Everyone but the administrator is limited. One second is the finest a
    reminder can repeat, so 0 or 1 is no limit either.
    """
    # The administrator has no limit.
    if request.apprise_auth_permission == Authentication.ROLE_ADMIN:
        return 0

    return settings.APPRISE_REMINDER_USER_MIN_INTERVAL


def _check_reminder_gap(request, event: dict, now: int, stored: dict | None = None) -> None:
    """Refuse a reminder that repeats more often than the caller may set.

    - ``stored``: the reminder being changed. Its own times may repeat more
      often, since the administrator may have set it up, so keeping them, or
      changing them to repeat no more often, is still allowed.
    - Raises ``ReminderFieldError`` for the ``when`` field.
    """
    # Nothing to check without a limit, or when the times were not changed.
    limit = reminder_gap_limit(request)
    if limit <= 1 or (stored is not None and event["when"] == stored.get("when")):
        return

    # The shortest time between two sends; None for a one-off.
    after = datetime.fromtimestamp(now, UTC)
    try:
        gap = shortest_gap(event, after)

    except SCHEDULE_ERRORS:
        raise _invalid_reminder_field("when") from None

    if gap is None or gap >= limit:
        return

    # Too short, unless the saved reminder already repeated this often or faster.
    try:
        allowed = shortest_gap(stored, after) if stored is not None else None

    except SCHEDULE_ERRORS:
        allowed = None

    if allowed is None or gap < allowed:
        raise ReminderFieldError(_("A reminder cannot repeat more often than every {} seconds").format(limit), "when")


def _reminder_gate(request, key):
    """Find the Config ID a reminder request is for and check its access.

    - ``key``: from the URL; ``@`` or ``None`` reads it from the
      X-Apprise-Config-ID header, and ``@`` falls back to the browser login.
    - Every reminder request needs what /status reports as
      ``reminders_enabled``; see ``_reminder_refusal()``.
    - Returns ``(key, None)``, or ``(None, response)`` to send back instead.
    """
    if not reminders_enabled():
        # Without reminders the API looks the same as before they existed.
        raise Http404()

    # A malformed X-Apprise-Config-ID header is refused outright.
    if config_key_header_present_but_invalid(request):
        return None, _invalid_key_response(request)

    # Work out which Config ID is meant: URL, then header, then browser login.
    resolved = resolve_config_key(request, "" if key in {None, "@"} else key)
    if not resolved and key == "@":
        resolved = _current_browser_config_key(request)

    if not resolved:
        return None, _missing_key_response(request)

    # The caller must be allowed to use this Config ID, the same as /notify.
    if not Authentication.key_ok(request, resolved):
        return None, _key_access_denied_response(request, resolved)

    # Then they must also be allowed to use its reminders.
    refusal = _reminder_refusal(request, resolved)
    if refusal is not None:
        return None, error_response(request, refusal, ResponseCode.no_access)

    return resolved, None


def _reminder_refusal(request, key: str) -> str | None:
    """Return why a caller already let in to a Config ID cannot use its reminders.

    - The administrator always may, whatever the configuration's access.
    - The configuration's own user may only while its access is ``user``.
    - Returns ``None`` when they may.
    """
    # A configuration user is refused once their access is locked, public or disabled.
    if (
        request.apprise_auth_permission == Authentication.ROLE_USER
        and Authentication.config_state(key, request).access != Authentication.ACCESS_USER
    ):
        return _("Only an administrator may manage reminders for this configuration")

    return None


def reminders_allowed(request, key: str | None) -> bool:
    """Return whether this caller may use reminders, as /status reports it.

    - Reminders must be turned on for the server.
    - With a Config ID the caller was let in to, the same rules apply as for
      every reminder route; see ``_reminder_refusal()``.
    - Without one, only the administrator may (or anyone, without logins).
    """
    if not reminders_enabled():
        return False

    # No Config ID: only the administrator, or anyone when logins are off.
    if key is None:
        return not settings.APPRISE_AUTH_REQUIRED or getattr(request, "globally_authenticated", False)

    return _reminder_refusal(request, key) is None


def _reminder_notify_fields(request, content: dict) -> HttpResponse | None:
    """Read a reminder's message fields the same way /notify reads them.

    - ``tags`` is the same as ``tag``, and ``attachment`` or ``attachments``
      the same as ``attach``.
    - The ``X-Apprise-Title``, ``-Type``, ``-Tag`` and ``-Template`` headers,
      then ``?title=``, ``?type=``, ``?format=`` and ``?tag=``, fill in fields
      the payload left out.
    - Returns ``None``, or the response to send back for a bad header.
    """
    # Fold the other names into the one stored; the main name wins when both
    # are sent. An empty ``tags`` still counts as a tag given.
    if "tags" in content:
        tags = content.pop("tags")
        if tags or "tag" not in content:
            content["tag"] = content.get("tag") or tags

    for alias in ("attachment", "attachments"):
        attachments = content.pop(alias, None)
        if not content.get("attach") and attachments:
            content["attach"] = attachments

    # Headers fill in what the payload left out, then the URL query does.
    invalid = _apply_notify_headers(request, content)
    if invalid is not None:
        return invalid

    _apply_notify_query_overrides(request, content)
    if not content.get("tag"):
        tag = request.GET.get("tag") or request.GET.get("tags")
        if tag:
            content["tag"] = tag

    return None


def _reminder_payload(request, key):
    """Read a reminder, or return the response explaining why it cannot be read.

    - JSON bodies hold the reminder itself.
    - ``multipart/form-data`` holds it as JSON in a ``reminder`` field, beside
      the uploaded files. The origin checks stop cross-site forms here.
    - Message fields are then read the same way as /notify; see
      ``_reminder_notify_fields()``.
    - Returns ``(content, files, None)``, or ``(None, None, response)``.
    """
    if request.content_type == "multipart/form-data":
        try:
            # Django reads multipart bodies only for POST, so PUT and PATCH
            # are read here the same way.
            form, files = (
                (request.POST, request.FILES)
                if request.method == "POST"
                else request.parse_file_upload(request.META, request)
            )
            # The reminder itself is JSON text in the "reminder" field.
            content = json.loads(form.get("reminder", ""))

        except RequestDataTooBig:
            return (
                None,
                None,
                error_response(request, _("JSON Payload provided is too large"), ResponseCode.fields_too_large),
            )

        except ValueError:
            return None, None, error_response(request, _("Invalid JSON Payload provided"), ResponseCode.bad_request)

        if not isinstance(content, dict):
            return (
                None,
                None,
                error_response(request, _("The JSON payload must be an object"), ResponseCode.bad_request),
            )

        invalid = _reminder_notify_fields(request, content)
        return (None, None, invalid) if invalid is not None else (content, files, None)

    # Anything else must be JSON, so a plain cross-site HTML form cannot
    # change reminders.
    if request.content_type != "application/json":
        return (
            None,
            None,
            error_response(request, _("Content-Type must be application/json"), ResponseCode.bad_request),
        )

    # A JSON body never has uploaded files, so an empty set is returned.
    content, invalid = parse_json_object_body(request, "REMINDER", key)
    if invalid is None:
        invalid = _reminder_notify_fields(request, content)

    return (None, None, invalid) if invalid is not None else (content, MultiValueDict(), None)


def _reminder_attachments(request, content, kept, files):
    """Prepare the attachments a reminder will save.

    - ``{"file": ...}`` entries keep a file from ``kept``, the attachments the
      reminder already has.
    - ``files``: the files uploaded with the request.
    - Other entries and uploaded files are checked the same way as /notify.
      New files are only hashed here; ``_store_reminder_uploads()`` copies
      them into the reminder file store once the save is allowed.
    - Returns ``(attachments, uploads, None)``, or ``(None, None, response)``
      to send back. ``uploads`` holds the new files still to be stored, each
      as ``{"file", "size", "item"}``: its stored name, its size and the
      uploaded file itself.
    """
    # A single entry is treated as a list of one.
    entries = content.get("attach")
    if entries is None:
        entries = []
    elif not isinstance(entries, list):
        entries = [entries]

    # - ``have``: stored files this configuration may keep, by name.
    # - ``uploads``: how many files came with the request.
    have = {entry["file"]: entry for entry in kept if isinstance(entry.get("file"), str)}
    uploads = sum(len(items) for _, items in files.lists())

    # Too many attachments in total (0 means no limit).
    if 0 < settings.APPRISE_MAX_ATTACHMENTS < len(entries) + uploads:
        return None, None, _bad_attachment(request)

    prepared = []
    try:
        # A size limit of 0 means attachments are turned off.
        if (entries or uploads) and settings.APPRISE_ATTACH_SIZE <= 0:
            raise AppriseAPIImproperlyConfigured("Attachment support has been disabled")

        for entry in entries:
            # {"file": ...} keeps a stored file; only this configuration's own count.
            if isinstance(entry, dict) and "file" in entry:
                if not isinstance(entry["file"], str) or entry["file"] not in have:
                    raise AppriseAPIImproperlyConfigured("Unknown stored attachment")

                prepared.append(have[entry["file"]])
                continue

            if isinstance(entry, str) and not entry.strip():
                # Empty form entries are skipped, the same as /notify.
                continue

            if isinstance(entry, str) or (isinstance(entry, dict) and AttachmentPayload.BASE64 not in entry):
                # A web address is checked now but only downloaded when sent,
                # so it can point at something that changes, like a snapshot.
                url = entry if isinstance(entry, str) else entry.get("url")
                name = entry.get("filename") if isinstance(entry, dict) else None
                if name is not None and (not isinstance(name, str) or len(name) > 250):
                    raise AppriseAPIImproperlyConfigured("Invalid attachment filename")

                check_attachment_url(url)
                prepared.append({"url": url.strip(), **({"filename": name.strip()} if name and name.strip() else {})})
                continue

            # Anything else (such as base64 content) is read like /notify reads it.
            prepared.extend(parse_attachments([entry], None))

        # Then the uploaded files.
        prepared.extend(parse_attachments(None, files))

    except AppriseAPIError as e:
        logger.warning("REMINDER - %s - Bad attachment: %s", request.META["REMOTE_ADDR"], e)
        return None, None, _bad_attachment(request)

    # Web addresses and kept files are saved as they are. New files are only
    # hashed for now, which gives the name and size they will be stored under.
    saved = []
    uploads = []
    for item in prepared:
        if isinstance(item, dict):
            saved.append(item)
            continue

        digest = ConfigCache.reminder_file_digest(item.path)
        if digest is None:
            return None, None, _reminders_unsaved(request)

        saved.append({"file": digest[0], "filename": item.filename, "size": digest[1]})
        uploads.append({"file": digest[0], "size": digest[1], "item": item})

    return saved, uploads, None


def _store_reminder_uploads(request, uploads: list, missing_only: bool = False) -> HttpResponse | None:
    """Copy new attachment files into the reminder file store.

    - Call it without the reminder lock, since large files take a while.
    - ``missing_only``: store only files no longer in the store. Under the
      lock, this puts back one that another save stopped using and removed
      after this request stored it.
    - Returns ``None``, or the response to send back when one could not be stored.
    """
    for upload in uploads:
        if missing_only and ConfigCache.reminder_file_stored(upload["file"]):
            continue

        if ConfigCache.store_reminder_file(upload["item"].path) is None:
            if not missing_only:
                # Files already stored for this request are of no use now.
                # Under the lock, the caller does this itself.
                with ConfigCache.reminder_guard():
                    _drop_refused_uploads(uploads)

            return _reminders_unsaved(request)

    return None


def _server_room(request, limits: dict, uploads: list, stored: bool = False) -> HttpResponse | None:
    """Return why new files do not fit APPRISE_REMINDER_STORE_MAX, or ``None``.

    - Only callers held to limits are checked, and only for files not stored yet.
    - 0 means the server has no such limit.
    - ``stored``: the uploads are in the store now. Checking again under the
      reminder lock catches several saves that each fit alone but not together.
    - A store whose size cannot be read has no room.
    """
    most = settings.APPRISE_REMINDER_STORE_MAX
    if not limits or most <= 0:
        return None

    if not stored:
        # Note which files are new; one already stored takes no more room.
        for upload in uploads:
            upload["new"] = not ConfigCache.reminder_file_stored(upload["file"])

    new = sum(upload["size"] for upload in uploads if upload.get("new"))
    if not new:
        return None

    # Once stored, the new files are already part of the size.
    size = ConfigCache.reminder_store_size()
    if size is not None and (size if stored else size + new) <= most:
        return None

    return error_response(
        request,
        _("There is not enough attachment space left on this server"),
        ResponseCode.payload_too_large,
        field="attach",
    )


def _drop_refused_uploads(uploads: list) -> None:
    """Remove files this request stored once its save is refused, unless something uses them.

    Hold the reminder lock while calling.
    """
    ConfigCache.drop_unused_reminder_files(upload["file"] for upload in uploads)


def _attachments_stored(attach: list) -> bool:
    """Return whether every stored file a reminder uses is still in the store.

    Prune removes unused files while holding the reminder lock, so check this
    while holding it too.
    """
    return all(ConfigCache.reminder_file_stored(entry["file"]) for entry in attach if "file" in entry)


def _bad_attachment(request) -> HttpResponse:
    """Return the error for an attachment that cannot be used."""
    return error_response(request, _("Bad Attachment"), ResponseCode.bad_request, field="attach")


def _stand_in_refusal(request, events: list, event: dict, content: dict, now: int) -> HttpResponse | None:
    """Mark the time a one-off stands in for as skipped on its repeating reminder.

    - Does nothing for a reminder that is not a stand-in.
    - Changes ``events`` and ``event``; see ``_replace_occurrence()``.
    - Returns ``None``, or the response to send back when it cannot stand in.
    """
    if "series" not in event:
        return None

    try:
        _replace_occurrence(events, event, content.get("occurrence"), now)

    except ReminderFieldError as e:
        return error_response(request, e.message, ResponseCode.bad_request, field=e.field)

    return None


def _reminder_too_large(request, event: dict) -> HttpResponse | None:
    """Return the error for a reminder too large to save, or ``None``."""
    if len(json.dumps(event, ensure_ascii=False).encode()) <= REMINDER_SIZE_MAX:
        return None

    return error_response(request, _("The reminder is too large to save"), ResponseCode.bad_request)


def _reminder_room(request, limits: dict, events: list, event: dict, previous: list | None) -> HttpResponse | None:
    """Return why a reminder does not fit the caller's limits, or ``None``.

    - ``previous``: the attachments the reminder had before this change, or
      ``None`` for a new reminder, which must also fit the reminder count.
    """
    # Already at the most reminders allowed.
    if previous is None and "reminders" in limits and len(events) >= limits["reminders"]:
        return error_response(
            request,
            _("This configuration already has the most reminders allowed"),
            ResponseCode.conflict,
        )

    # A quota of 0 turns attachments off, web addresses included. Ones the
    # reminder already has may stay.
    had = {entry.get("file") or entry.get("url") for entry in previous or []}
    added = [entry for entry in event.get("attach") or [] if (entry.get("file") or entry.get("url")) not in had]
    if limits.get("attach_size") == 0 and added:
        return error_response(
            request,
            _("Attachments are turned off for this configuration's reminders"),
            ResponseCode.payload_too_large,
            field="attach",
        )

    if not _attachments_fit(limits, events, event, previous or []):
        return _no_attachment_room(request)

    return None


def _attachment_sizes(events, skip=None):
    """Return the size of each stored file the reminders use, by name."""
    return {
        entry["file"]: entry.get("size") or 0
        for event in events
        if event.get("id") != skip
        for entry in event.get("attach") or []
        if isinstance(entry.get("file"), str)
    }


def _attachments_fit(limits, events, event, previous):
    """Return whether a reminder's stored files fit the configuration's quota.

    - Only new files are checked, so a lowered quota never blocks other edits.
    - Files used by several reminders count once.
    """
    # - ``quota``: bytes allowed, or None for no limit.
    # - ``used``: files the other reminders already store.
    # - ``known``: those plus the files this reminder had before the change.
    # - ``wanted``: the files this reminder will store.
    quota = limits.get("attach_size")
    used = _attachment_sizes(events, skip=event["id"])
    known = {**used, **{entry["file"]: entry.get("size") or 0 for entry in previous if entry.get("file")}}
    wanted = {entry["file"]: entry["size"] for entry in event.get("attach") or [] if entry.get("file")}

    # No limit, or no new files: nothing to check.
    if quota is None or all(name in known for name in wanted):
        return True

    return sum({**used, **wanted}.values()) <= quota


def _reminders_unreadable(request):
    """Return the error for a reminder file that could not be read."""
    return error_response(request, _("The reminders could not be read"), ResponseCode.internal_server_error)


def _reminders_unsaved(request):
    """Return the error for a reminder file that could not be written."""
    return error_response(request, _("The reminders could not be saved"), ResponseCode.internal_server_error)


def _reminder_missing(request):
    """Return the error for a reminder ID that does not exist."""
    return error_response(request, _("The reminder was not found"), ResponseCode.not_found)


def _find_reminder(events, rid):
    """Return the stored reminder with this ID, or ``None``."""
    return next((event for event in events if event.get("id") == rid), None)


def _replace_occurrence(events: list, event: dict, occurrence: Any, now: int) -> None:
    """Skip one time of a repeating reminder, which ``event`` is sent in place of.

    - ``event["series"]``: the ID of the repeating reminder.
    - ``occurrence``: the UNIX timestamp of the time being replaced.
    - Raises ``ReminderFieldError`` when that reminder is not due then.
    """
    # The series must be a repeating reminder of this same configuration.
    series = _find_reminder(events, event["series"])
    if series is None or is_one_off(series["when"]):
        raise _invalid_reminder_field("series")

    # The time replaced must be a whole number still in the future.
    if isinstance(occurrence, bool) or not isinstance(occurrence, int) or occurrence <= now:
        raise _invalid_reminder_field("occurrence")

    try:
        # The time must be one the reminder is due at, skipped or not.
        due = next_run({**series, "skip": []}, datetime.fromtimestamp(occurrence - 1, UTC))
        if due != occurrence:
            raise _invalid_reminder_field("occurrence")

        # The series passes over that time from now on.
        series["skip"] = sorted({*(series.get("skip") or ()), occurrence})
        series["next"] = next_run(series, datetime.fromtimestamp(now, UTC))

    except SCHEDULE_ERRORS:
        raise _invalid_reminder_field("occurrence") from None

    series["updated"] = now
    event["occurrence"] = occurrence


def _reminder_limits(request, key: str) -> dict | None:
    """Return the limits that hold back the caller, or ``None`` when they cannot be read.

    Everyone but the administrator is limited: the configuration's own user,
    or anyone when logins are off. The administrator's limits for the Config
    ID apply, or else the defaults.
    """
    # The administrator has no limits.
    if request.apprise_auth_permission == Authentication.ROLE_ADMIN:
        return {}

    limits = ConfigCache.get_limits(key)
    return None if limits is None else {**_default_limits(), **limits}


def _default_limits() -> dict:
    """Return the limits a Config ID has until the administrator sets its own.

    - ``reminders``: the most reminders it can have.
    - ``attach_size``: the most bytes its stored attachments may take.
    """
    return {"reminders": settings.APPRISE_REMINDER_MAX, "attach_size": settings.APPRISE_REMINDER_ATTACH_QUOTA}


def _limits_unreadable(request):
    """Return the error for a limits file that could not be read."""
    return error_response(request, _("An error occurred accessing configuration"), ResponseCode.internal_server_error)


def _no_attachment_room(request):
    """Return the error for attachments that do not fit the configuration's quota."""
    return error_response(
        request,
        _("There is not enough attachment space left for this configuration"),
        ResponseCode.payload_too_large,
        field="attach",
    )


def _reminder_response(name, value):
    """Return one reminder value as JSON."""
    return JsonResponse({name: value}, encoder=JSONEncoder, safe=False, status=ResponseCode.okay)


def _private_url(url: str) -> str:
    """Return an Apprise URL with its secrets hidden, the way /json/urls shows it.

    A URL that no longer loads (for example its service was turned off since)
    shows only its schema.
    """
    plugin = apprise.Apprise.instantiate(url)
    return url.split("://", 1)[0] + "://" if plugin is None else plugin.url(privacy=True)


def _reminder_privacy(request, events: list[dict]) -> list[dict]:
    """Add each reminder's URL IDs, and hide its URLs' secrets when ``?privacy=`` asks for it.

    - ``url_ids`` lists one ID per URL, in the same order (see
      ``_reminder_url_id()``), so a caller can match them with its
      configuration's URLs and keep them on an edit as ``@<id>``.
    - Each reminder is copied first, so a stored one is never changed.
    """
    private = parse_bool(request.GET.get("privacy"), default=False)
    shown = []
    for event in events:
        urls = event.get("urls")
        if not urls:
            shown.append(event)
            continue

        ids = {"url_ids": [_reminder_url_id(url) for url in urls]}
        shown.append({**event, **ids, **({"urls": [_private_url(url) for url in urls]} if private else {})})

    return shown


def _reminder_saved(request, event: dict) -> HttpResponse:
    """Answer a saved reminder the way /add answers a saved configuration.

    - JSON callers get the reminder; others get a short message.
    - The ``X-Apprise-Reminder-ID`` header names the reminder either way.
    """
    response = (
        _reminder_response("reminder", _reminder_privacy(request, [event])[0])
        if is_json_response(request)
        else HttpResponse(_("Successfully saved reminder"), status=ResponseCode.okay, content_type="text/plain")
    )
    response["X-Apprise-Reminder-ID"] = event["id"]
    return response


@method_decorator((gzip_page, never_cache), name="dispatch")
class ReminderListView(View):
    """List a Config ID's reminders, or add one."""

    def get(self, request, key=None):
        """Return every reminder saved for the configuration.

        With ``?start=`` and ``end=`` (UNIX timestamps), each reminder also
        lists the ``occurrences`` it is due between them, for a calendar.
        A paused reminder lists none, unless ``?disabled=show`` asks for the
        times it would be due.
        """
        key, invalid = _reminder_gate(request, key)
        if invalid is not None:
            return invalid

        # The calendar window, when one was asked for.
        window = None
        if "start" in request.GET or "end" in request.GET:
            try:
                window = int(request.GET.get("start", "")), int(request.GET.get("end", ""))
                # A start too far off to be a date is refused here.
                datetime.fromtimestamp(window[0], UTC)

            except (ValueError, OverflowError, OSError):
                return error_response(
                    request, _("The {} field is invalid").format("start"), ResponseCode.bad_request, field="start"
                )

        events = ConfigCache.read_reminders(ConfigCache.reminder_path(key))
        if events is None:
            return _reminders_unreadable(request)

        # Add each reminder's due times inside the window.
        if window is not None:
            paused = request.GET.get("disabled", "").strip().lower() == "show"
            for event in events:
                event["occurrences"] = _reminder_occurrences(event, *window, paused=paused)

        return _reminder_response("reminders", _reminder_privacy(request, events))

    def post(self, request, key=None):
        """Add a reminder and return it with its new ID and next due time."""
        key, invalid = _reminder_gate(request, key)
        if invalid is not None:
            return invalid

        content, files, invalid = _reminder_payload(request, key)
        if invalid is not None:
            return invalid

        # Build the new reminder:
        # - ``id``: a random ID that never changes.
        # - ``owner``: who added it, "user" or "admin".
        # - ``runs``/``last``: how often it has been sent, and the last result.
        # - ``next``: when it is next due (a UNIX timestamp).
        now = int(time.time())
        # Like its files, a new reminder may keep by ID URLs this
        # configuration's reminders already hold: a copy, or one standing in
        # for a time of a series.
        held = ConfigCache.read_reminders(ConfigCache.reminder_path(key)) or []
        known = [url for stored in held for url in stored.get("urls") or []]
        try:
            event = {
                "id": uuid4().hex,
                # Removing the configuration's user also removes what they added.
                "owner": "user" if request.apprise_auth_permission == Authentication.ROLE_USER else "admin",
                **_clean_reminder(content, attached=bool(content.get("attach") or files), known_urls=known),
                "runs": 0,
                "last": None,
                "created": now,
                "updated": now,
            }
            event["next"] = _plan_reminder(event, now)
            _check_reminder_gap(request, event, now)

            # A one-off may replace one time of a repeating reminder.
            if content.get("series") is not None or content.get("occurrence") is not None:
                # Only a one-off can stand in for one time of a repeating reminder.
                if not isinstance(content.get("series"), str) or not is_one_off(event["when"]):
                    raise _invalid_reminder_field("series")

                event["series"] = content["series"]

        except ReminderFieldError as e:
            return error_response(request, e.message, ResponseCode.bad_request, field=e.field)

        # A reminder needs at least one service to send to, and a saved
        # configuration unless it is sent to its own URLs alone.
        alone = bool(event["urls"]) and event.get("tag") is None
        missing = (None if alone else _saved_config_missing(request, key)) or _reminder_targets_missing(
            request, key, event
        )
        if missing is not None:
            return missing

        reminder_file = ConfigCache.reminder_path(key)
        limits = _reminder_limits(request, key)
        if limits is None:
            return _limits_unreadable(request)

        # Check everything once without the lock first, so a large upload never
        # holds up other saves or the scheduler, and a refused request stores
        # nothing. The file is replaced in one step, so this read is safe.
        events = ConfigCache.read_reminders(reminder_file)
        if events is None:
            return _reminders_unreadable(request)

        # A new reminder may keep files this configuration's reminders already
        # store, such as one standing in for a time of a series, or a copy.
        kept = [
            entry for stored in events for entry in stored.get("attach") or [] if isinstance(entry.get("file"), str)
        ]
        event["attach"], uploads, invalid = _reminder_attachments(request, content, kept, files)
        if invalid is not None:
            return invalid

        # The stand-in check changes what it is given, so it gets copies here.
        refused = (
            _reminder_too_large(request, event)
            or _reminder_room(request, limits, events, event, None)
            or _server_room(request, limits, uploads)
            or _stand_in_refusal(request, [dict(stored) for stored in events], dict(event), content, now)
        )
        if refused is not None:
            return refused

        invalid = _store_reminder_uploads(request, uploads)
        if invalid is not None:
            return invalid

        # Then hold the lock while reading, checking and saving again, so two
        # saves at once cannot undo each other.
        with ConfigCache.reminder_guard():
            events = ConfigCache.read_reminders(reminder_file)
            refused = (
                _reminders_unreadable(request)
                if events is None
                else _reminder_room(request, limits, events, event, None)
                or _stand_in_refusal(request, events, event, content, now)
                # Put back any file uploaded here that another save removed meanwhile.
                or _store_reminder_uploads(request, uploads, missing_only=True)
                or _server_room(request, limits, uploads, stored=True)
            )
            if refused is None and not _attachments_stored(event["attach"]):
                # A kept file may have been pruned since it was checked.
                refused = _bad_attachment(request)

            # Save the list with the new reminder added at the end.
            if refused is None and not ConfigCache.write_reminders(reminder_file, [*events, event]):
                refused = _reminders_unsaved(request)

            if refused is not None:
                # Nothing was saved, so files only this request stored can go.
                _drop_refused_uploads(uploads)
                return refused

        logger.info("REMINDER - %s - Added reminder %s using KEY: %s", request.META["REMOTE_ADDR"], event["id"], key)
        return _reminder_saved(request, event)


@method_decorator((gzip_page, never_cache), name="dispatch")
class ReminderOverviewView(View):
    """List the reminders of every Config ID for the administrator."""

    template_name = "reminder_list.html"

    def get(self, request):
        """Return each reminder with the ``key`` of the Config ID it belongs to.

        The same rules as the configuration list apply: it needs simple
        storage mode, APPRISE_ADMIN, and the administrator's login when
        logins are required.
        """
        if not reminders_enabled():
            raise Http404()

        # Without the web pages, only JSON is answered.
        json_response = is_json_response(request)
        if settings.APPRISE_API_ONLY and not json_response:
            return Error421View.as_view()(request)

        # Only whoever may list every configuration may list every reminder.
        if not Authentication.can_list_configurations(request):
            return error_response(
                request, _("The site has been configured to deny this request"), ResponseCode.no_access
            )

        found = []
        stored_keys = ConfigCache.keys()
        for key in stored_keys:
            # A file that cannot be read is left out rather than failing the list.
            events = ConfigCache.read_reminders(ConfigCache.reminder_path(key)) or []
            found.extend({"key": key, **event} for event in events)

        # Soonest first; finished reminders (no next time) go last.
        found.sort(key=lambda event: (event.get("next") is None, event.get("next") or 0, event["key"]))
        if json_response:
            return _reminder_response("reminders", found)

        return render(
            request,
            self.template_name,
            {"reminders": found, "config_timezone": settings.TIME_ZONE},
            status=ResponseCode.okay,
        )


@method_decorator((gzip_page, never_cache), name="dispatch")
class ReminderView(View):
    """Read, change or remove one reminder."""

    def get(self, request, key, rid):
        """Return one reminder."""
        key, invalid = _reminder_gate(request, key)
        if invalid is not None:
            return invalid

        # Only this configuration's own reminders are searched.
        events = ConfigCache.read_reminders(ConfigCache.reminder_path(key))
        if events is None:
            return _reminders_unreadable(request)

        event = _find_reminder(events, rid)
        return (
            _reminder_missing(request)
            if event is None
            else _reminder_response("reminder", _reminder_privacy(request, [event])[0])
        )

    def put(self, request, key, rid):
        """Replace every field a caller may set."""
        return self._save(request, key, rid, merge=False)

    def patch(self, request, key, rid):
        """Change only the fields sent, keeping the rest."""
        return self._save(request, key, rid, merge=True)

    def _save(self, request, key, rid, merge):
        """Save new values for a reminder and work out its next due time."""
        key, invalid = _reminder_gate(request, key)
        if invalid is not None:
            return invalid

        content, files, invalid = _reminder_payload(request, key)
        if invalid is not None:
            return invalid

        now = int(time.time())
        reminder_file = ConfigCache.reminder_path(key)
        limits = _reminder_limits(request, key)
        if limits is None:
            return _limits_unreadable(request)

        # Check the change once without the lock first, so a large upload never
        # holds up other saves or the scheduler, and a refused change stores
        # nothing. The file is replaced in one step, so this read is safe.
        events = ConfigCache.read_reminders(reminder_file)
        if events is None:
            return _reminders_unreadable(request)

        stored = _find_reminder(events, rid)
        if stored is None:
            return _reminder_missing(request)

        # PATCH: start from the saved values, then lay the new ones on top.
        # The fields sent are kept apart, to lay them over the reminder again
        # once it is read under the lock.
        changes = content
        if merge:
            content = {**_stored_fields(stored), **changes}

        # The files it had may be kept.
        attach, uploads, invalid = _reminder_attachments(request, content, stored.get("attach") or [], files)
        if invalid is not None:
            return invalid

        changed, refused = self._changed(request, events, stored, content, attach, limits, now)
        if refused is None:
            refused = (
                _reminder_targets_missing(request, key, changed)
                or _server_room(request, limits, uploads)
                or _store_reminder_uploads(request, uploads)
            )

        if refused is not None:
            return refused

        # Then hold the lock while reading, checking and saving again.
        with ConfigCache.reminder_guard():
            event, refused = self._commit(
                request,
                key,
                reminder_file,
                rid,
                changes if merge else content,
                (attach, uploads),
                limits,
                now,
                merge=merge,
            )
            if refused is not None:
                # Nothing was saved, so files only this request stored can go.
                _drop_refused_uploads(uploads)
                return refused

        logger.info("REMINDER - %s - Updated reminder %s using KEY: %s", request.META["REMOTE_ADDR"], rid, key)
        return _reminder_saved(request, event)

    @classmethod
    def _commit(cls, request, key, reminder_file, rid, content, attachments, limits, now, merge=False):
        """Check a change again and save it. Hold the reminder lock while calling.

        - ``content``: every field for PUT, or only the fields sent for PATCH.
        - ``attachments``: the attachments and new uploads from ``_reminder_attachments()``.
        - ``merge``: PATCH, so ``content`` is laid over the reminder as it is now.
        - Returns ``(event, None)`` once saved, or ``(None, response)`` to send back.
        """
        attach, uploads = attachments
        events = ConfigCache.read_reminders(reminder_file)
        if events is None:
            return None, _reminders_unreadable(request)

        stored = _find_reminder(events, rid)
        if stored is None:
            return None, _reminder_missing(request)

        if merge:
            # PATCH: a change saved meanwhile to a field not sent here is kept.
            if "attach" not in content:
                # Its attachment list was not replaced, so it keeps the ones it
                # has now, plus any uploaded here (always the last ones prepared).
                attach = (stored.get("attach") or []) + attach[len(attach) - len(uploads) :]

            content = {**_stored_fields(stored), **content}

        event, refused = cls._changed(request, events, stored, content, attach, limits, now)
        # A change saved meanwhile may have left it nowhere to send. Put back
        # any file uploaded here that another save removed meanwhile.
        refused = (
            refused
            or _reminder_targets_missing(request, key, event)
            or _store_reminder_uploads(request, uploads, missing_only=True)
            or _server_room(request, limits, uploads, stored=True)
        )
        if refused is None and (not _attachments_stored(attach) or 0 < settings.APPRISE_MAX_ATTACHMENTS < len(attach)):
            # A kept file may have been pruned since it was checked, or one
            # added meanwhile may leave too many.
            refused = _bad_attachment(request)

        if refused is not None:
            return None, refused

        # Put the changed reminder back in its place and save.
        events[events.index(stored)] = event
        if not ConfigCache.write_reminders(reminder_file, events):
            return None, _reminders_unsaved(request)

        # Files it no longer uses can go now, unless another reminder uses them.
        ConfigCache.drop_unused_reminder_files(entry["file"] for entry in stored.get("attach") or [] if "file" in entry)
        return event, None

    @staticmethod
    def _changed(request, events, stored, content, attach, limits, now):
        """Return the reminder as it will be saved, without changing anything given.

        - ``stored``: the reminder as it is saved now.
        - ``content``: the fields to save, already laid over ``stored`` for PATCH.
        - ``attach``: its attachments, from ``_reminder_attachments()``.
        - Returns ``(event, None)``, or ``(None, response)`` to send back.
        """
        try:
            cleaned = _clean_reminder(content, attached=bool(attach), kept_urls=stored.get("urls"))
            # Times that one-offs stand in for always stay skipped.
            replaced = (e["occurrence"] for e in events if e.get("series") == stored["id"] and e["occurrence"] > now)
            cleaned["skip"] = sorted({*cleaned["skip"], *replaced})
            event = {**stored, **cleaned, "attach": attach, "updated": now}
            if "series" in event and not is_one_off(cleaned["when"]):
                # Made to repeat, it is no longer one time of its series and
                # stands on its own. The series still skips the time it replaced.
                event.pop("series")
                event.pop("occurrence", None)

            if cleaned["when"] != stored.get("when") or cleaned["start"] != stored.get("start"):
                # A new time or start starts the run count again, so a holiday
                # sent once can be moved to another year. How the last send
                # went belongs to the old time, so it is forgotten too.
                event["runs"] = 0
                event["last"] = None

            if "last" in content:
                # Only null is taken: it forgets how the last send went.
                if content["last"] is not None:
                    raise _invalid_reminder_field("last")

                event["last"] = None

            # Work out the next time again when the timing changed or it was
            # just resumed; otherwise keep the one already planned.
            retimed = any(cleaned[field] != stored.get(field) for field in ("when", "start", "until", "count", "skip"))
            if retimed or (cleaned["enabled"] and stored.get("enabled") is False):
                event["next"] = _plan_reminder(event, now)

            _check_reminder_gap(request, event, now, stored)

        except ReminderFieldError as e:
            return None, error_response(request, e.message, ResponseCode.bad_request, field=e.field)

        # It must still fit the caller's limits; the files it had are free.
        return event, _reminder_too_large(request, event) or _reminder_room(
            request, limits, events, event, stored.get("attach") or []
        )

    def delete(self, request, key, rid):
        """Remove a reminder.

        Removing a repeating reminder also removes the one-offs that replaced
        some of its times.
        """
        key, invalid = _reminder_gate(request, key)
        if invalid is not None:
            return invalid

        reminder_file = ConfigCache.reminder_path(key)
        with ConfigCache.reminder_guard():
            events = ConfigCache.read_reminders(reminder_file)
            if events is None:
                return _reminders_unreadable(request)

            stored = _find_reminder(events, rid)
            if stored is None:
                return _reminder_missing(request)

            # Drop it, and any one-offs standing in for its times.
            removed = [event for event in events if event is stored or event.get("series") == rid]
            events = [event for event in events if event is not stored and event.get("series") != rid]
            if not ConfigCache.write_reminders(reminder_file, events):
                return _reminders_unsaved(request)

            # Their files can go now, unless another reminder uses them.
            ConfigCache.drop_unused_reminder_files(
                entry["file"] for event in removed for entry in event.get("attach") or [] if "file" in entry
            )

        logger.info("REMINDER - %s - Removed reminder %s using KEY: %s", request.META["REMOTE_ADDR"], rid, key)
        # The same answer /del gives: a 204 with no body.
        return HttpResponse(status=ResponseCode.no_content)


@method_decorator(never_cache, name="dispatch")
class ReminderTestView(View):
    """Send a reminder now without changing when it is next due."""

    def post(self, request, key, rid):
        """Send the reminder once and report whether it went out."""
        key, invalid = _reminder_gate(request, key)
        if invalid is not None:
            return invalid

        # Only reading, so no lock is needed; nothing about the reminder changes.
        reminder_file = ConfigCache.reminder_path(key)
        events = ConfigCache.read_reminders(reminder_file)
        if events is None:
            return _reminders_unreadable(request)

        event = _find_reminder(events, rid)
        if event is None:
            return _reminder_missing(request)

        # Imported here because the scheduler module imports this one.
        from .scheduler import prepare_send

        # Load the configuration and attachments exactly as the scheduler would.
        prepared = prepare_send(reminder_file, event)
        if prepared is None:
            return error_response(
                request, _("One or more notification could not be sent"), ResponseCode.failed_dependency
            )

        # Sent and answered exactly as /notify would: its log as text, HTML or
        # JSON, or streamed live when asked for.
        a_obj, kwargs = prepared
        content = {
            "body": kwargs["body"],
            "title": kwargs["title"],
            "type": kwargs["notify_type"],
            "tag": kwargs["tag"],
            "template": kwargs["template"],
        }
        return _deliver_notification(
            request,
            a_obj,
            content,
            kwargs["attach"],
            is_json_response(request),
            _stream_requested(request),
            event.get("tag"),
            key=key,
        )


@method_decorator((gzip_page, never_cache), name="dispatch")
class ReminderPreviewView(View):
    """List the next times a reminder would be due, without saving anything."""

    def get(self, request, key=None):
        """Work out upcoming times from ``at``, ``cron`` or ``holiday`` in the query string.

        - A holiday also reads ``country`` and ``region``; ``start`` sets its time of day.
        - ``every`` (a unit) repeats from ``at``, with ``interval``, and
          ``months`` and ``days`` as comma-separated lists.
        - A rule that repeats more often than the caller may set is refused,
          exactly as saving it would be. ``id`` names the reminder being
          changed, which may keep times that repeat more often.
        """
        key, invalid = _reminder_gate(request, key)
        if invalid is not None:
            return invalid

        # Rebuild a ``when`` object from the query string, as a saved reminder holds it.
        query = request.GET
        when = {name: query[name] for name in ("tz", "at", "cron") if name in query}
        if parse_bool(query.get("isc", False)):
            when["isc"] = True

        if "every" in query:
            try:
                when["every"] = {
                    "unit": query["every"],
                    "interval": int(query.get("interval") or 1),
                    "months": [int(month) for month in query.get("months", "").split(",") if month],
                    "days": [day for day in query.get("days", "").split(",") if day],
                }

            except ValueError:
                return error_response(
                    request, _("The {} field is invalid").format("when"), ResponseCode.bad_request, field="when"
                )

        if "drift" in query:
            try:
                when["drift"] = int(query["drift"])

            except ValueError:
                return error_response(
                    request, _("The {} field is invalid").format("drift"), ResponseCode.bad_request, field="drift"
                )

        if "holiday" in query:
            when["holiday"] = {
                "name": query["holiday"],
                **{name: query[name] for name in ("country", "region") if query.get(name)},
            }

        try:
            # - ``count``: stop after this many sends.
            # - ``limit``: how many upcoming times to list.
            content = {"when": when, "start": query.get("start"), "until": query.get("until")}
            for name, default in (("count", None), ("limit", REMINDER_PREVIEW_DEFAULT)):
                try:
                    content[name] = int(query[name]) if name in query else default

                except ValueError:
                    raise _invalid_reminder_field(name) from None

            limit = content.pop("limit")
            if not 1 <= limit <= REMINDER_PREVIEW_MAX:
                raise _invalid_reminder_field("limit")

            # Checked the same way as a save, then the next times are listed.
            now = datetime.now(UTC)
            cleaned = _clean_reminder_time(content)
            times = upcoming(cleaned, now, limit)
            # Too short a gap between times is refused the same way a save refuses it.
            stored = None
            if query.get("id"):
                stored = _find_reminder(ConfigCache.read_reminders(ConfigCache.reminder_path(key)) or [], query["id"])
            _check_reminder_gap(request, cleaned, int(now.timestamp()), stored)
            # Nothing ahead is an error, unless its time or end date has passed.
            if not times and not is_one_off(cleaned["when"]) and not until_passed(cleaned, now):
                raise _never_sent(cleaned, now)

        except ReminderFieldError as e:
            return error_response(request, e.message, ResponseCode.bad_request, field=e.field)

        except SCHEDULE_ERRORS:
            return error_response(
                request, _("The {} field is invalid").format("when"), ResponseCode.bad_request, field="when"
            )

        # A one-off whose time has passed has nothing left to show.
        return _reminder_response("next", [due for due in times if due >= now.timestamp()])


@method_decorator((gzip_page, never_cache), name="dispatch")
class ReminderHolidaysView(View):
    """List the public holidays a reminder can be sent on."""

    def get(self, request, key=None):
        """Return the countries with holidays, or one country's holidays.

        - No ``country``: every country code with its regions.
        - ``country`` (and optional ``region``): its holidays over the next
          12 months, or over ``year`` when one is given.
        - Each holiday's ``name`` is what a holiday reminder saves, and
          ``label`` is the same holiday in the page language when known.
        """
        key, invalid = _reminder_gate(request, key)
        if invalid is not None:
            return invalid

        # No country: list the countries to choose from.
        country = request.GET.get("country", "").strip().upper()
        if not country:
            return _reminder_response("countries", holiday_countries())

        # By default, the next 12 months, which can span two calendar years.
        region = request.GET.get("region", "").strip() or None
        today = datetime.now(ZoneInfo(settings.TIME_ZONE)).date()
        last = today + timedelta(days=365)
        years = (today.year, today.year + 1)
        if "year" in request.GET:
            # Only the years a holiday reminder looks ahead to are offered.
            allowed = {str(each): each for each in range(today.year, today.year + HOLIDAY_YEARS)}
            if request.GET["year"] not in allowed:
                return error_response(request, _("The {} field is invalid").format("year"), ResponseCode.bad_request)

            years = (allowed[request.GET["year"]],)

        found = []
        try:
            # The page language's names, when the country has them, used as labels.
            language = holiday_language(country, get_language() or settings.LANGUAGE_CODE)
            for year in years:
                labels = dict(holiday_dates(country, region, year, language)) if language else {}
                for day, names in holiday_dates(country, region, year):
                    # Without a year, keep only the next 12 months.
                    if "year" not in request.GET and not today <= day < last:
                        continue

                    # Use the saved names when the page language lists the day differently.
                    shown = labels.get(day, ())
                    shown = shown if len(shown) == len(names) else names
                    found.extend(
                        {"date": day.isoformat(), "name": name, "label": label}
                        for name, label in zip(names, shown, strict=True)
                    )

        except NotImplementedError:
            # The holidays library does not know this country or region.
            field = "region" if region else "country"
            return error_response(request, _("The {} field is invalid").format(field), ResponseCode.bad_request)

        return _reminder_response("holidays", found)


# The limits an administrator may set on a Config ID, and the lowest value each takes.
LIMIT_FIELDS = {"reminders": 1, "attach_size": 0}


def _limits_summary(limits, events):
    """Return a Config ID's limits beside how much of each it uses.

    - ``reminders`` / ``reminders_used``: the most reminders, and how many there are.
    - ``attach_size`` / ``attach_used``: the attachment quota in bytes, and how
      much the stored files take, including any the administrator added.
    - Limits the administrator did not set show their defaults.
    """
    limits = {**_default_limits(), **limits}
    return {
        "reminders": limits["reminders"],
        "reminders_used": len(events),
        "attach_size": limits["attach_size"],
        "attach_used": sum(_attachment_sizes(events).values()),
    }


@method_decorator((gzip_page, never_cache), name="dispatch")
class ReminderLimitsView(View):
    """Show or change the limits on a Config ID's reminders."""

    def get(self, request, key):
        """Return the limits and how much of each is used."""
        key, invalid = _reminder_gate(request, key)
        if invalid is not None:
            return invalid

        # Both files are needed: the limits, and the reminders that use them.
        limits = ConfigCache.get_limits(key)
        events = ConfigCache.read_reminders(ConfigCache.reminder_path(key))
        if limits is None:
            return _limits_unreadable(request)

        if events is None:
            return _reminders_unreadable(request)

        # The rules a reminder must follow ride along, so an app can check a
        # reminder before saving it:
        # - ``min_interval``: the fewest seconds apart it may repeat (0 is no limit).
        # - ``drift_min`` / ``drift_max``: the drift range, in seconds.
        # - ``holiday_last_year``: the last year a holiday can be looked up.
        # - ``timezone``: the zone used when a reminder names none.
        # - ``preview_max``: the most times one preview can list.
        # - ``urls_max``: the most ``urls`` a reminder can hold (0 is none).
        rules = {
            "min_interval": reminder_gap_limit(request),
            "drift_min": DRIFT_MIN,
            "drift_max": DRIFT_MAX,
            "holiday_last_year": datetime.now(ZoneInfo(settings.TIME_ZONE)).year + HOLIDAY_YEARS - 1,
            "timezone": settings.TIME_ZONE,
            "preview_max": REMINDER_PREVIEW_MAX,
            "urls_max": settings.APPRISE_REMINDER_URLS_MAX,
        }
        return JsonResponse(
            {"limits": _limits_summary(limits, events), "rules": rules},
            encoder=JSONEncoder,
            safe=False,
            status=ResponseCode.okay,
        )

    def post(self, request, key):
        """Save new limits; only the administrator may change them.

        ``null`` puts a limit back to its default: APPRISE_REMINDER_MAX
        reminders, and APPRISE_REMINDER_ATTACH_QUOTA for attachments. An
        ``attach_size`` of 0 turns uploads off.
        """
        key, invalid = _reminder_gate(request, key)
        if invalid is not None:
            return invalid

        # Others may read the limits but not change them.
        if request.apprise_auth_permission != Authentication.ROLE_ADMIN:
            return error_response(
                request,
                _("Only an administrator may change the limits of this configuration"),
                ResponseCode.no_access,
            )

        content, _files, invalid = _reminder_payload(request, key)
        if invalid is not None:
            return invalid

        # Each limit is a whole number no lower than its minimum, or null.
        for name, lowest in LIMIT_FIELDS.items():
            value = content.get(name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < lowest):
                return error_response(
                    request, _("The {} field is invalid").format(name), ResponseCode.bad_request, field=name
                )

        with ConfigCache.reminder_guard():
            limits = ConfigCache.get_limits(key)
            if limits is None:
                return _limits_unreadable(request)

            # Change only the limits sent; null removes one so its default applies.
            for name in LIMIT_FIELDS:
                if name in content:
                    limits[name] = content[name]

            limits = {name: value for name, value in limits.items() if value is not None}
            if not ConfigCache.set_limits(key, limits):
                return error_response(request, _("The limits could not be saved"), ResponseCode.internal_server_error)

            events = ConfigCache.read_reminders(ConfigCache.reminder_path(key))
            if events is None:
                return _reminders_unreadable(request)

        logger.info("REMINDER - %s - Updated limits using KEY: %s", request.META["REMOTE_ADDR"], key)
        return _reminder_response("limits", _limits_summary(limits, events))
