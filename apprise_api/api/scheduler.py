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
"""Send reminders when they are due.

One process runs this. Its main thread works out the next due time and
sleeps until then with dateblock. A small thread pool does the sending.

- Saving or removing a reminder sends this process ``SIGUSR1``. The signal
  interrupts the sleep, and every reminder file is read again so the sleep
  always targets the soonest event.
- A change that could not signal it, such as one saved on another host
  sharing the directory, is found within ``RECHECK`` seconds.
- Only a few sends wait for a free worker at a time. Other due reminders
  stay in their files until there is room.
- A due reminder is only sent once its file has been saved with its next
  time, so a failed save never sends it twice.
- ``SIGTERM`` or ``SIGINT`` stops it after in-flight sends finish.
- Reminders on a disabled configuration are paused when due.
- With ``APPRISE_REMINDER_IDLE_DAYS`` set, a configuration nobody has used
  for that long has every reminder paused, and is sent one notice saying so.
"""

from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import logging
from operator import itemgetter
import os
import signal
import threading
import time
from types import FrameType
from typing import Any

import apprise
from dateblock import dateblock
from django.conf import settings
from django.utils import translation
from django.utils.translation import gettext as _

from .auth import AuthStorageError
from .exceptions import AppriseAPIError, AppriseAPIImproperlyConfigured
from .schedule import DAMAGED_ERRORS, PAUSED_ACCESS, log_time, needs_config, next_run
from .utils import (
    Attachment,
    ConfigCache,
    apply_global_filters,
    config_apprise,
    notify_asset_kwargs,
    parse_attachments,
)
from .views import find_reminder, parse_tag_expression, send_notify_webhook

# Get an instance of a logger
logger = logging.getLogger("django")

# The source named in the completion webhook for reminders.
WEBHOOK_SOURCE = "reminder"

# How often, in seconds, a sleeping scheduler looks for reminder changes it
# was not signalled about, such as ones saved on another host. A reminder
# file that could not be saved is also tried again after this long.
RECHECK = 60

# How many sends may wait for a free worker, for each worker. The rest stay
# due in their files until there is room.
QUEUE_PER_WORKER = 2

# The signals the main thread acts on:
# - SIGUSR1: reminders changed, so look again.
# - SIGTERM and SIGINT: stop.
WAKE_SIGNALS = (signal.SIGUSR1, signal.SIGTERM, signal.SIGINT)

# Send threads are named this, then a number, e.g. sched-runner-001.
RUNNER_PREFIX = "sched-runner"

# How the main thread names itself in log lines.
MAIN_ACTOR = "scheduler"


def _actor() -> str:
    """Name the scheduler thread writing a log line.

    - A send thread gives its own name, e.g. ``sched-runner-001``.
    - Anything else is the main thread, which plans and claims.
    """
    name = threading.current_thread().name
    return name if name.startswith(RUNNER_PREFIX) else MAIN_ACTOR


def _leave_signals_to_main_thread() -> None:
    """Block the wake and stop signals in a send thread.

    A signal sent to the process can land on any thread that does not block
    it. Only one that lands on the main thread ends its sleep right away, so
    every other thread blocks them. Threads started from here block them too.
    """
    signal.pthread_sigmask(signal.SIG_BLOCK, WAKE_SIGNALS)


class Reschedule(Exception):
    """Raised from a signal handler to end the scheduler's sleep early."""


def wait_until(due: int) -> None:
    """Sleep until a UNIX timestamp."""
    moment = datetime.fromtimestamp(due, UTC)
    # Starting one second early makes dateblock match exactly this second,
    # and return at once if that second has already passed.
    dateblock(
        "{} {} {} {} {}".format(moment.second, moment.minute, moment.hour, moment.day, moment.month),
        ref=moment - timedelta(seconds=1),
    )


def reminder_attachments(event: dict, who: str | None = None) -> list:
    """Build the attachments a reminder sends.

    - ``who``: who is sending, for the log; the scheduler thread otherwise.
    - A stored file that has gone missing is left out with a warning.
    - Web addresses are checked again, the same way /notify checks them.
    """
    # Each entry is either a stored upload ({"file": ...}) or a web address.
    entries = event.get("attach") or []
    # Attachments turned off for the whole server stop the send.
    if entries and settings.APPRISE_ATTACH_SIZE <= 0:
        raise AppriseAPIImproperlyConfigured("Attachment support has been disabled")

    # The attachments to send, in the order saved.
    attach = []
    for entry in entries:
        if entry.get("file"):
            # A file uploaded with the reminder and kept on disk.
            path = ConfigCache.reminder_file_path(entry["file"])
            if not os.path.isfile(path):
                logger.warning(
                    "REMINDER - %s - An attachment of reminder %s is missing", who or _actor(), event.get("id")
                )
                continue

            # delete=False keeps the stored file for the reminder's next send.
            attach.append(Attachment(entry.get("filename") or entry["file"], path=path, delete=False))

        else:
            # A web address, downloaded now so it holds its latest content.
            attach.extend(parse_attachments([entry], None))

    return attach


def _failed(outcome: dict | None, reason: str) -> None:
    """Note why a send failed in ``outcome``, when the caller asked for it."""
    if outcome is not None:
        outcome["reason"] = reason


def prepare_send(
    reminder_file: str,
    event: dict,
    stored: tuple[str | None, str | None] | None = None,
    who: str | None = None,
    outcome: dict | None = None,
) -> tuple[apprise.Apprise, dict] | None:
    """Load what one reminder sends through, without sending it.

    - ``stored``: the configuration and its format, already read; it is read
      from beside the reminder file otherwise.
    - ``who``: who is sending, for the log; the scheduler thread otherwise.
    - ``outcome``: given a dict, a failure sets its ``reason`` (see ``deliver()``).
    - Returns the Apprise object and the ``notify()`` arguments.
    - Returns ``None`` when the configuration or attachments cannot be used.
    """
    # The configuration is read fresh on every send, so a change to its
    # services or tags since the reminder was saved is picked up.
    urls = event.get("urls")
    tag = event.get("tag")
    alone = not needs_config(event)
    who = who or _actor()
    if urls and settings.APPRISE_REMINDER_URLS_MAX <= 0:
        # The server no longer allows URLs in reminders, so they are left
        # out; a reminder with nothing else fails.
        logger.warning(
            "REMINDER - %s - Left out the URLs of reminder %s; the server no longer allows them", who, event.get("id")
        )
        if alone:
            return _failed(outcome, "urls")

        urls = None

    # Read now unless the claim already read it under the lock.
    config, fmt = (None, None) if alone else (stored or ConfigCache.reminder_config(reminder_file))
    if config is None and not alone:
        logger.warning("REMINDER - %s - No configuration found for reminder %s", who, event.get("id"))
        return _failed(outcome, "config")

    # Apply the server's allow and deny rules for services, as /notify does.
    apply_global_filters()
    try:
        attach = reminder_attachments(event, who)

    except AppriseAPIError as e:
        logger.warning("REMINDER - %s - The attachments of reminder %s could not be used", who, event.get("id"))
        logger.debug("Reminder attachment error: %s", e)
        return _failed(outcome, "attach")

    # Build the same Apprise object /notify would, from the stored configuration.
    asset = apprise.AppriseAsset(
        language=settings.LANGUAGE_CODE,
        **notify_asset_kwargs(event.get("format") or None),
    )
    # Only its own URLs, or the configuration's services too.
    a_obj = apprise.Apprise(asset=asset) if alone else config_apprise(config, fmt, asset)

    if urls:
        # A tag only picks services that carry it, so beside one the
        # reminder's own URLs are marked "always" to be sent to as well.
        a_obj.add(urls, tag=apprise.common.MATCH_ALWAYS_TAG if tag else None)

    # These match what /notify passes to notify().
    return a_obj, {
        "body": event.get("body"),
        "title": event.get("title") or "",
        "notify_type": event.get("type") or apprise.NotifyType.INFO.value,
        # The tag was checked when saved; this turns it into Apprise's AND/OR
        # form. Without one, only untagged services are sent to, which
        # includes the reminder's own URLs.
        "tag": parse_tag_expression(tag) if tag else None,
        "template": event.get("template") or None,
        "attach": attach or None,
    }


def deliver(
    reminder_file: str,
    event: dict,
    source: str = WEBHOOK_SOURCE,
    stored: tuple[str | None, str | None] | None = None,
    outcome: dict | None = None,
) -> bool:
    """Send one reminder using the configuration beside it.

    - ``source``: who the completion webhook says sent it.
    - ``stored``: the configuration and its format, already read.
    - ``outcome``: given a dict, a send that could not be made sets its
      ``reason``, kept as ``last.reason``:

      - ``urls``: it only sends to its own URLs, which the server no longer allows.
      - ``config``: it sends through a configuration that is not saved.
      - ``attach``: its attachments could not be used.
      - ``error``: Apprise could not send it at all.

      A send some services refused leaves it unset.
    """
    prepared = prepare_send(reminder_file, event, stored, outcome=outcome)
    if prepared is None:
        return False

    a_obj, kwargs = prepared
    try:
        # The body is the first argument; everything else is a keyword.
        result = a_obj.notify(kwargs.pop("body"), **kwargs)

    except apprise.exception.AppriseException as e:
        logger.warning("REMINDER - %s - Reminder %s could not be sent", _actor(), event.get("id"))
        logger.debug("Reminder error: %s", e)
        _failed(outcome, "error")
        return False

    # Tell the completion webhook, when one is set, how the send went.
    send_notify_webhook(source, result)
    # Anything not sent counts as a failed send.
    if not result:
        logger.warning("REMINDER - %s - One or more notifications not sent for reminder %s", _actor(), event.get("id"))
        return False

    logger.info("REMINDER - %s - Delivered reminder %s", _actor(), event.get("id"))
    return True


def access_allows(reminder_file: str) -> bool | None:
    """Return whether the configuration's access setting lets its reminders run.

    - Returns ``None`` when the access record cannot be read.
    """
    if not settings.APPRISE_AUTH_REQUIRED:
        # Without logins, every configuration is managed by the administrator.
        return True

    try:
        # No saved access record (None) means the administrator manages it.
        return ConfigCache.reminder_access(reminder_file) not in PAUSED_ACCESS

    except AuthStorageError:
        # It may say "disabled", so nothing is sent until it can be read.
        return None


def _pending(event: dict) -> bool:
    """Return whether an event is switched on and still has a time to send."""
    # A missing "enabled" counts as on; "next" is None once nothing is left.
    return event.get("enabled", True) is not False and isinstance(event.get("next"), int)


def _sendable(events: list[dict], configured: bool) -> Iterator[dict]:
    """Yield the events that are waiting to be sent.

    - Without a configuration (``configured``), only reminders sent to their
      own URLs alone have somewhere to go; the rest wait for one to be saved.
    """
    return (event for event in events if _pending(event) and (configured or not needs_config(event)))


def _zone(event: dict) -> Any:
    """Return the timezone a reminder was saved with, or ``None``."""
    when = event.get("when")
    return when.get("tz") if isinstance(when, dict) else None


def _entries(events: Iterator[dict]) -> Iterator[tuple]:
    """Yield ``(next time, ID, timezone)`` for each event."""
    return ((event["next"], event.get("id"), _zone(event)) for event in events)


def _earliest(*entries: tuple | None) -> tuple | None:
    """Return the entry with the earliest time, or ``None`` when none is set.

    - Each entry starts with its time; the rest only describes it.
    """
    return min((entry for entry in entries if entry is not None), key=itemgetter(0), default=None)


class Scheduler:
    """Wait for the soonest reminder and send what is due."""

    def __init__(self, workers: int, grace: int) -> None:
        """Prepare the scheduler.

        - ``workers``: the number of threads that send notifications.
        - ``grace``: how many seconds late a notification may still be sent.
        """
        self.grace = grace
        self.workers = workers
        # Sends handed to the pool and not yet finished, and how many may be.
        self.queued = 0
        self.capacity = workers * (1 + QUEUE_PER_WORKER)
        self.queue_lock = threading.Lock()
        # How many send threads have started, to number them.
        self.runners = 0
        # The threads that do the sending; the main thread only waits and plans.
        self.pool = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix=RUNNER_PREFIX,
            initializer=self._start_runner,
        )
        # The last wake-up planned, as (time, reminder), so only a change is logged.
        self.planned = None
        # The soonest reminder found by the latest scan, as (time, ID, timezone).
        self.soonest = None
        # Reminder files the latest scan could not send from, with why.
        self.held = {}
        # Set when a due reminder had to wait for room in the pool.
        self.backlog = False
        # When reminders last changed, as seen by the latest scan.
        self.seen = 0
        # The signal handler only interrupts while the main thread sleeps.
        self.sleeping = False
        # Set by a signal so a change that arrives mid-scan is not missed.
        self.dirty = False
        # Set by SIGTERM or SIGINT; the loop ends after its current pass.
        self.stopping = False
        # Set when the last sleep ended on a change found without a signal.
        self.changed = False

    def _start_runner(self) -> None:
        """Prepare a new send thread and name it, e.g. ``sched-runner-001``."""
        _leave_signals_to_main_thread()
        with self.queue_lock:
            self.runners += 1
            threading.current_thread().name = f"{RUNNER_PREFIX}-{self.runners:03d}"

    def run(self, wait: bool = False) -> bool:
        """Run until stopped.

        - ``wait``: when another scheduler is running, wait to take over
          once it stops, instead of giving up.
        - Returns ``False`` when it could not become the running scheduler.
        """
        # A web save may send SIGUSR1 as soon as the lock below records this
        # process, and by default that signal ends a process; until the lock
        # is held it is only noted. SIGTERM and SIGINT still end the process
        # while it waits for the lock.
        signal.signal(signal.SIGUSR1, self._on_signal)

        # Only one scheduler may send reminders at a time.
        with ConfigCache.scheduler_lock(wait=wait) as held:
            if not held:
                return False

            # SIGUSR1 means reminders changed; SIGTERM and SIGINT mean stop.
            for signum in WAKE_SIGNALS:
                signal.signal(signum, self._on_signal)

            logger.info(
                "REMINDER - %s - Scheduler started with %d send threads; reminders up to %d seconds late are sent",
                MAIN_ACTOR,
                self.workers,
                self.grace,
            )

            # Finish any configuration removal a crash left part way, so the
            # reminders it set aside are sent again or removed.
            recovered = ConfigCache.recover_removed_reminders()
            if recovered:
                logger.info(
                    "REMINDER - %s - Finished %d configuration removals that stopped part way", MAIN_ACTOR, recovered
                )

            try:
                while not self.stopping:
                    self.dirty = False
                    # Send what is due now, and learn when the next one is.
                    due = self.scan()
                    # With the pool full, wait for a send to finish instead.
                    due = None if self.backlog and self.room() <= 0 else due
                    self.plan(due)
                    self.sleep(due)
                    # Logged once awake; a signal during logging would be lost in it.
                    if (self.dirty or self.changed) and not self.stopping:
                        logger.debug("REMINDER - %s - Woken by a reminder change", MAIN_ACTOR)

            finally:
                # Let sends already started finish before exiting.
                self.pool.shutdown(wait=True)
                logger.info("REMINDER - %s - Scheduler stopped", MAIN_ACTOR)

        return True

    def _on_signal(self, signum: int, _frame: FrameType | None) -> None:
        """Note a reminder change or a stop request, and end any sleep."""
        if signum != signal.SIGUSR1:
            self.stopping = True

        self.dirty = True
        if self.sleeping:
            # Only the first signal ends a sleep. A second one that lands
            # while the first is still being handled is only noted, so it
            # cannot escape sleep() and stop the scheduler.
            self.sleeping = False
            # Break out of the sleep so the main loop looks again right away.
            raise Reschedule()

    def plan(self, due: int | None) -> None:
        """Log the next wake-up, but only when it differs from the last one.

        - A reminder saved for a new time shows here as a new wake-up, which
          replaces the old one; nothing is sent at the old time.
        """
        # What the latest scan found soonest, when that is what it waits for.
        due, rid, zone = self.soonest if self.soonest is not None and self.soonest[0] == due else (due, None, None)
        if (due, rid) == self.planned:
            return

        self.planned = (due, rid)
        if due is None:
            # Nothing is queued, or the pool is full and a send must finish first.
            logger.debug(
                "REMINDER - %s - Waiting for %s",
                MAIN_ACTOR,
                "a send to finish" if self.backlog else "a reminder to be saved",
            )

        elif rid is None:
            # A file that could not be read or saved is tried again.
            logger.debug("REMINDER - %s - Next wake-up %s to look again", MAIN_ACTOR, log_time(due))

        else:
            logger.debug("REMINDER - %s - Next wake-up %s for reminder %s", MAIN_ACTOR, log_time(due, zone), rid)

    def sleep(self, due: int | None) -> None:
        """Sleep until ``due`` unless a signal has already asked to look again.

        Every ``RECHECK`` seconds it also checks whether reminders changed.
        """
        self.changed = False
        try:
            try:
                self.sleeping = True
                while not self.dirty:
                    # Never sleep longer than RECHECK seconds at a time.
                    wake = int(time.time()) + RECHECK
                    if due is not None and due <= wake:
                        # The next reminder is due before the next check.
                        wait_until(due)
                        break

                    wait_until(wake)
                    # Something changed without a signal, so scan again.
                    if ConfigCache.reminders_changed() != self.seen:
                        self.changed = True
                        break

            finally:
                self.sleeping = False

        except Reschedule:
            # A signal that lands anywhere above, even mid-finally, ends here.
            pass

    def scan(self) -> int | None:
        """Hand out every event that is due and return the soonest one left."""
        now = int(time.time())
        # The earliest time still to come, as (time, reminder ID, timezone);
        # the ID is None for a file to look at again, and the whole is None
        # when nothing is queued.
        soonest = None
        # A file to look at again soon, as an entry like the above.
        retry = (now + RECHECK, None, None)
        # Reminder files nothing can be sent from yet, with why.
        held = {}
        # Read before the files, so a change made during the scan is noticed.
        self.seen = ConfigCache.reminders_changed()
        self.backlog = False
        # Whether something could not be read; if so, look again soon, as a
        # fix to the disk does not mark the reminders as changed.
        unread = False
        files = ConfigCache.reminder_files(complete=True)
        if files is None:
            # A directory could not be listed; go through the ones that can.
            unread = True
            files = ConfigCache.reminder_files()

        # Files with something due, as (how long overdue, file, has a configuration).
        due = []
        for reminder_file in files:
            # Read without the lock; each file is replaced in one step.
            events = ConfigCache.read_reminders(reminder_file)
            # Whether a configuration is saved beside it (None when that cannot be told).
            has_config = ConfigCache.reminder_has_config(reminder_file) if events else False
            if events is None or has_config is None:
                unread = True
                continue

            # A disabled configuration's reminders are neither sent nor waited
            # on; changing its access wakes the scheduler to look again.
            allowed = access_allows(reminder_file) if events else True
            if allowed is None:
                unread = True
                continue

            if not allowed:
                if any(_pending(event) for event in events):
                    held[reminder_file] = "its configuration is disabled"

                continue

            if not has_config and any(_pending(event) and needs_config(event) for event in events):
                # These send through a configuration that is not saved.
                held[reminder_file] = "they wait for a configuration to be saved"

            # The longest overdue event, if any is due now.
            oldest = min(
                (event["next"] for event in _sendable(events, has_config) if event["next"] <= now), default=None
            )
            if oldest is not None:
                # Its lock is taken once every file has been looked at.
                due.append((oldest, reminder_file, has_config))
                continue

            # Keep track of the soonest time among what is left.
            soonest = _earliest(soonest, *_entries(_sendable(events, has_config)))

        # The file overdue the longest is claimed first, so a busy Config ID
        # can never keep the others waiting for room in the pool.
        for _oldest, reminder_file, has_config in sorted(due):
            try:
                claimed = self.claim(reminder_file, now, configured=has_config)

            except OSError as e:
                # The reminder lock could not be opened, such as on a full or
                # read-only disk; nothing was changed, so try again later.
                logger.warning("REMINDER - %s - Could not lock the reminders to send %s", MAIN_ACTOR, reminder_file)
                logger.debug("Reminder lock error: %s", e)
                claimed = None

            if claimed is None:
                # Try the file again soon rather than straight away.
                soonest = _earliest(soonest, retry)
                continue

            soonest = _earliest(soonest, *_entries(_sendable(claimed, has_config)))

        # Something could not be read; look again soon.
        if unread:
            soonest = _earliest(soonest, retry)

        self.hold(held)
        self.soonest = soonest
        return None if soonest is None else soonest[0]

    def hold(self, held: dict[str, str]) -> None:
        """Log each reminder file newly held back from sending, with why.

        - ``held``: the files the latest scan could not send from.
        """
        for reminder_file, why in held.items():
            if self.held.get(reminder_file) != why:
                logger.debug("REMINDER - %s - Holding the reminders in %s; %s", MAIN_ACTOR, reminder_file, why)

        self.held = held

    def room(self) -> int:
        """Return how many more sends the pool can take."""
        with self.queue_lock:
            return self.capacity - self.queued

    def submit(self, task: Callable, *args: Any) -> None:
        """Hand a send to the pool."""
        with self.queue_lock:
            self.queued += 1

        # finished() runs when the send is done, whether it worked or not.
        self.pool.submit(task, *args).add_done_callback(self.finished)

    def finished(self, future: Future) -> None:
        """Count a finished send, and wake the scheduler if it waits for room."""
        if future.exception() is not None:
            logger.warning("REMINDER - %s - A scheduled send failed unexpectedly", _actor())
            logger.debug("Scheduled send error: %s", future.exception())

        # One fewer send in the pool; note if it was full before.
        with self.queue_lock:
            full = self.queued >= self.capacity
            self.queued -= 1

        if full:
            # The pool was full, so the main thread may be waiting for room.
            os.kill(os.getpid(), signal.SIGUSR1)

    def claim(self, reminder_file: str, now: int, configured: bool = True) -> list[dict] | None:
        """Move each due event in a file on to its next time, then send it.

        - ``configured``: a configuration is saved beside the file. Without
          one, only events sent to their own URLs alone are handled.

        - Moving on before sending means a crash mid-send skips that send
          instead of repeating it.
        - An event overdue by more than the grace time is skipped, and a
          repeating one never sends a backlog.
        - Nothing is sent or changed on a disabled configuration; its events
          stay as they are until its access changes.
        - An event with no room in the pool stays due for a later scan.
        - Returns ``None``, sending nothing, when the file cannot be read or
          saved, or its configuration's access cannot be read; it is tried
          again later.
        """
        # The events to send once the file is saved.
        jobs = []
        # How many sends the pool can take right now.
        room = self.room()
        with ConfigCache.reminder_guard():
            # Read again under the lock; the file may have changed since.
            events = ConfigCache.read_reminders(reminder_file)
            if events is None:
                # Unreadable for now; tried again later rather than forgotten.
                return None

            if not events:
                return []

            # Check the configuration's access and use once for the whole file.
            allowed = access_allows(reminder_file)
            if allowed is None:
                # Neither sent nor paused, so they go out once it reads again.
                logger.warning(
                    "REMINDER - %s - Held the reminders in %s; its access could not be read", MAIN_ACTOR, reminder_file
                )
                return None

            if not allowed:
                # Disabled since the scan; leave every event as it is.
                return []

            # A Config ID nobody has used for a while has its reminders paused.
            idle = self.idle(reminder_file, now)
            if idle and room <= 0:
                # The notice needs room in the pool like any send, so pause
                # them on a later scan instead.
                self.backlog = True
                return events

            if idle:
                logger.warning(
                    "REMINDER - %s - Paused the reminders in %s; nobody has used it lately", MAIN_ACTOR, reminder_file
                )
                for event in events:
                    if event.get("enabled", True) is not False:
                        event["enabled"] = False
                        event["last"] = {"at": now, "ok": False, "reason": "idle"}

            # Each due event's next time is looked for after this moment.
            after = datetime.fromtimestamp(now, UTC)
            # Due events left for a later scan, as the pool has no room.
            waiting = 0
            for event in events:
                # Leave alone anything paused, finished, not due yet, or
                # waiting for a configuration.
                if not _pending(event) or event["next"] > now or (not configured and needs_config(event)):
                    continue

                # How many seconds past its time this event is.
                late = now - event["next"]
                if late <= self.grace and len(jobs) >= room:
                    # No room in the pool; leave it due for the next scan.
                    self.backlog = True
                    waiting += 1
                    continue

                # Count this run and move the event on to its next time.
                try:
                    event["runs"] = (event.get("runs") or 0) + 1
                    # Skipped times that have already passed are forgotten.
                    if event.get("skip"):
                        event["skip"] = [due for due in event["skip"] if due > now]

                    event["next"] = next_run(event, after)

                # A reminder damaged on disk (such as one edited by hand) is
                # stopped rather than stopping the scheduler.
                except DAMAGED_ERRORS as e:
                    logger.warning(
                        "REMINDER - %s - Stopped reminder %s; its time is invalid", MAIN_ACTOR, event.get("id")
                    )
                    logger.debug("Schedule time error: %s", e)
                    event["next"] = None

                else:
                    if event["next"] is None:
                        # Its count or end date was reached, or a one-off was due.
                        logger.info(
                            "REMINDER - %s - Reminder %s has no more times to send", MAIN_ACTOR, event.get("id")
                        )

                if late > self.grace:
                    logger.warning(
                        "REMINDER - %s - Skipped reminder %s; it was %d seconds overdue (grace is %d), next %s",
                        MAIN_ACTOR,
                        event.get("id"),
                        late,
                        self.grace,
                        log_time(event["next"], _zone(event)),
                    )
                    event["last"] = {"at": now, "ok": False, "reason": "late"}
                    continue

                logger.debug(
                    "REMINDER - %s - Sending reminder %s, %d seconds after its time; next %s",
                    MAIN_ACTOR,
                    event.get("id"),
                    late,
                    log_time(event["next"], _zone(event)),
                )
                # A copy, so later changes to the file cannot alter what is sent.
                jobs.append(dict(event))

            if waiting:
                logger.debug(
                    "REMINDER - %s - %d due reminders in %s wait for a free send thread",
                    MAIN_ACTOR,
                    waiting,
                    reminder_file,
                )

            if not ConfigCache.write_reminders(reminder_file, events):
                # Sending now could send it again once the file is readable.
                logger.warning(
                    "REMINDER - %s - Could not save %s; its due reminders were not sent", MAIN_ACTOR, reminder_file
                )
                return None

            # Read the configuration under the same lock a Config ID move
            # takes, so a move before the send starts cannot lose it.
            stored = (
                ConfigCache.reminder_config(reminder_file)
                if idle or any(needs_config(event) for event in jobs)
                else None
            )

        # The file is saved, so it is now safe to send.
        for event in jobs:
            self.submit(self.send, reminder_file, event, stored)

        if idle:
            self.submit(self.send_idle_notice, reminder_file, stored)

        return events

    @staticmethod
    def idle(reminder_file: str, now: int) -> bool:
        """Return whether a reminder file's configuration has gone unused too long."""
        days = settings.APPRISE_REMINDER_IDLE_DAYS
        if days <= 0:
            # Turned off.
            return False

        # When the Config ID was last used; None if that could not be read.
        seen = ConfigCache.reminder_seen(reminder_file)
        return seen is not None and now - seen > days * 86400

    @staticmethod
    def send_idle_notice(reminder_file: str, stored: tuple[str | None, str | None] | None = None) -> None:
        """Tell a configuration's own services that its reminders were paused.

        - ``stored``: the configuration and its format, already read.
        """
        # Written in the server's language, since there is no browser to ask.
        with translation.override(settings.LANGUAGE_CODE):
            body = _("Reminders for this configuration were paused because it was not used for {} days").format(
                settings.APPRISE_REMINDER_IDLE_DAYS
            )

        # Sent to the configuration's own services, like a reminder.
        deliver(
            reminder_file,
            {"id": "idle", "body": body, "type": apprise.NotifyType.WARNING.value},
            stored=stored,
        )

    def send(self, reminder_file: str, event: dict, stored: tuple[str | None, str | None] | None = None) -> None:
        """Send one event and record how it went.

        - ``stored``: the configuration and its format, read when it was claimed.
        - A configuration disabled while the send waited for a worker sends
          nothing, and the reminder is left as it is.
        """
        if access_allows(reminder_file) is False:
            logger.info(
                "REMINDER - %s - Dropped reminder %s; its configuration was disabled", _actor(), event.get("id")
            )
            return

        # Why it could not be sent, when that is known.
        outcome = {}
        try:
            ok = deliver(reminder_file, event, stored=stored, outcome=outcome)

        except Exception as e:
            # Recorded as a failed send, rather than lost with the worker.
            logger.warning("REMINDER - %s - Reminder %s failed unexpectedly", _actor(), event.get("id"))
            logger.debug("Reminder error: %s", e)
            ok = False
            outcome["reason"] = "error"

        # Save the result on the reminder, read fresh in case it was changed.
        reason = outcome.get("reason")
        with ConfigCache.reminder_guard():
            if self.record(reminder_file, event, ok, reason=reason):
                return

        # Its Config ID may have moved while it was sending, or it was removed.
        # - The other files are searched without the lock, so saves and the
        #   scheduler are not held up; each file is replaced in one step, so
        #   reading it is safe.
        # - Only the file that holds it is locked and updated.
        for other in ConfigCache.reminder_files():
            if (
                other == reminder_file
                or find_reminder(ConfigCache.read_reminders(other) or [], event.get("id")) is None
            ):
                continue

            with ConfigCache.reminder_guard():
                if self.record(other, event, ok, reason=reason):
                    return

    @staticmethod
    def record(reminder_file: str, event: dict, ok: bool, reason: str | None = None) -> bool:
        """Save how a send went on its reminder; the caller holds the lock.

        - ``reason``: why it could not be sent, when that is known.
        - Returns whether the file holds the reminder.
        """
        # Read fresh under the lock; the file may have changed while sending.
        events = ConfigCache.read_reminders(reminder_file) or []
        saved = find_reminder(events, event.get("id"))
        if saved is None:
            return False

        # When it went out, whether every service took it, and why not when known.
        saved["last"] = {"at": int(time.time()), "ok": ok, **({"reason": reason} if reason else {})}
        if not ConfigCache.write_reminders(reminder_file, events):
            logger.warning("REMINDER - %s - Could not record how reminder %s went", _actor(), event.get("id"))

        return True
