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

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import logging
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
from .schedule import PAUSED_ACCESS, SCHEDULE_ERRORS, drop_past_skips, next_run
from .utils import Attachment, ConfigCache, apply_global_filters, notify_asset_kwargs, parse_attachments
from .views import parse_tag_expression, send_notify_webhook

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


def reminder_attachments(event: dict) -> list:
    """Build the attachments a reminder sends.

    - A stored file that has gone missing is left out with a warning.
    - Web addresses are checked again, the same way /notify checks them.
    """
    # Each entry is either a stored upload ({"file": ...}) or a web address.
    entries = event.get("attach") or []
    if entries and settings.APPRISE_ATTACH_SIZE <= 0:
        raise AppriseAPIImproperlyConfigured("Attachment support has been disabled")

    attach = []
    for entry in entries:
        if entry.get("file"):
            # A file uploaded with the reminder and kept on disk.
            path = ConfigCache.reminder_file_path(entry["file"])
            if not os.path.isfile(path):
                logger.warning("REMINDER - An attachment of reminder %s is missing", event.get("id"))
                continue

            # delete=False keeps the stored file for the reminder's next send.
            attach.append(Attachment(entry.get("filename") or entry["file"], path=path, delete=False))

        else:
            # A web address, downloaded now so it holds its latest content.
            attach.extend(parse_attachments([entry], None))

    return attach


def prepare_send(reminder_file: str, event: dict) -> tuple[apprise.Apprise, dict] | None:
    """Load what one reminder sends through, without sending it.

    - Returns the Apprise object and the ``notify()`` arguments.
    - Returns ``None`` when the configuration or attachments cannot be used.
    """
    # The configuration is read fresh on every send, so a change to its
    # services or tags since the reminder was saved is picked up. A reminder
    # with its own URLs and no tag sends to those alone, the same as /notify;
    # an empty tag ("") also sends to the configuration's untagged URLs.
    urls = event.get("urls")
    tag = event.get("tag")
    alone = bool(urls) and tag is None
    if urls and settings.APPRISE_REMINDER_URLS_MAX <= 0:
        # The server no longer allows URLs in reminders, so they are left
        # out; a reminder with nothing else fails.
        logger.warning("REMINDER - Left out the URLs of reminder %s; the server no longer allows them", event.get("id"))
        if alone:
            return None

        urls = None

    config, fmt = (None, None) if alone else ConfigCache.reminder_config(reminder_file)
    if config is None and not alone:
        logger.warning("REMINDER - No configuration found for reminder %s", event.get("id"))
        return None

    # Apply the server's allow and deny rules for services, as /notify does.
    apply_global_filters()
    try:
        attach = reminder_attachments(event)

    except AppriseAPIError as e:
        logger.warning("REMINDER - The attachments of reminder %s could not be used", event.get("id"))
        logger.debug("Reminder attachment error: %s", e)
        return None

    # Build the same Apprise object /notify would, from the stored configuration.
    asset = apprise.AppriseAsset(
        language=settings.LANGUAGE_CODE,
        **notify_asset_kwargs(event.get("format") or None),
    )
    a_obj = apprise.Apprise(asset=asset)
    if not alone:
        ac_obj = apprise.AppriseConfig(asset=asset, recursion=settings.APPRISE_RECURSION_MAX)
        ac_obj.add_config(config, format=fmt)
        a_obj.add(ac_obj)

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


def deliver(reminder_file: str, event: dict, source: str = WEBHOOK_SOURCE) -> bool:
    """Send one reminder using the configuration beside it.

    - ``source``: who the completion webhook says sent it.
    """
    prepared = prepare_send(reminder_file, event)
    if prepared is None:
        return False

    a_obj, kwargs = prepared
    try:
        # The body is the first argument; everything else is a keyword.
        result = a_obj.notify(kwargs.pop("body"), **kwargs)

    except apprise.exception.AppriseException as e:
        logger.warning("REMINDER - Reminder %s could not be sent", event.get("id"))
        logger.debug("Reminder error: %s", e)
        return False

    # Tell the completion webhook, when one is set, how the send went.
    send_notify_webhook(source, result)
    if not result:
        logger.warning("REMINDER - One or more notifications not sent for reminder %s", event.get("id"))
        return False

    logger.info("REMINDER - Delivered reminder %s", event.get("id"))
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


def _needs_config(event: dict) -> bool:
    """Return whether an event sends through its configuration, not only its own URLs."""
    return not event.get("urls") or event.get("tag") is not None


def _pending(event: dict) -> bool:
    """Return whether an event is switched on and still has a time to send."""
    # A missing "enabled" counts as on; "next" is None once nothing is left.
    return event.get("enabled", True) is not False and isinstance(event.get("next"), int)


class Scheduler:
    """Wait for the soonest reminder and send what is due."""

    def __init__(self, workers: int, grace: int) -> None:
        """Prepare the scheduler.

        - ``workers``: the number of threads that send notifications.
        - ``grace``: how many seconds late a notification may still be sent.
        """
        self.grace = grace
        # The threads that do the sending; the main thread only waits and plans.
        self.pool = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="apprise-scheduler",
            initializer=_leave_signals_to_main_thread,
        )
        # Sends handed to the pool and not yet finished, and how many may be.
        self.queued = 0
        self.capacity = workers * (1 + QUEUE_PER_WORKER)
        self.queue_lock = threading.Lock()
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

    def run(self) -> bool:
        """Run until stopped. Return ``False`` if another scheduler is running."""
        # Only one scheduler may send reminders at a time.
        with ConfigCache.scheduler_lock() as held:
            if not held:
                return False

            # SIGUSR1 means reminders changed; SIGTERM and SIGINT mean stop.
            for signum in WAKE_SIGNALS:
                signal.signal(signum, self._on_signal)

            logger.info("REMINDER - Scheduler started")

            # Finish any configuration removal a crash left part way, so the
            # reminders it set aside are sent again or removed.
            ConfigCache.recover_removed_reminders()
            try:
                while not self.stopping:
                    self.dirty = False
                    # Send what is due now, and learn when the next one is.
                    due = self.scan()
                    # With the pool full, wait for a send to finish instead.
                    self.sleep(None if self.backlog and self.room() <= 0 else due)

            finally:
                # Let sends already started finish before exiting.
                self.pool.shutdown(wait=True)
                logger.info("REMINDER - Scheduler stopped")

        return True

    def _on_signal(self, signum: int, _frame: FrameType | None) -> None:
        """Note a reminder change or a stop request, and end any sleep."""
        if signum != signal.SIGUSR1:
            self.stopping = True

        self.dirty = True
        if self.sleeping:
            # Break out of the sleep so the main loop looks again right away.
            raise Reschedule()

    def sleep(self, due: int | None) -> None:
        """Sleep until ``due`` unless a signal has already asked to look again.

        Every ``RECHECK`` seconds it also checks whether reminders changed.
        """
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
                        break

            finally:
                self.sleeping = False

        except Reschedule:
            # A signal that lands anywhere above, even mid-finally, ends here.
            pass

    def scan(self) -> int | None:
        """Hand out every event that is due and return the soonest one left."""
        now = int(time.time())
        # The earliest time still to come; None when nothing is queued.
        soonest = None
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

        for reminder_file in files:
            events = ConfigCache.read_reminders(reminder_file)
            has_config = ConfigCache.reminder_has_config(reminder_file) if events else False
            if events is None or has_config is None:
                unread = True
                continue

            if not has_config:
                # Without a configuration, only reminders sent to their own URLs
                # alone have somewhere to go; the rest wait for one to be saved.
                events = [event for event in events if not _needs_config(event)]

            if not events:
                continue

            # Only take the file's lock when something in it is due.
            if any(_pending(event) and event["next"] <= now for event in events):
                claimed = self.claim(reminder_file, now, configured=has_config)
                if claimed is None:
                    # Try the file again soon rather than straight away.
                    soonest = now + RECHECK if soonest is None else min(soonest, now + RECHECK)
                    continue

                events = claimed

            # Keep track of the soonest time among what is left.
            for event in events:
                if not has_config and _needs_config(event):
                    continue

                if _pending(event) and (soonest is None or event["next"] < soonest):
                    soonest = event["next"]

        if unread:
            soonest = now + RECHECK if soonest is None else min(soonest, now + RECHECK)

        return soonest

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
            logger.warning("REMINDER - A scheduled send failed unexpectedly")
            logger.debug("Scheduled send error: %s", future.exception())

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
        - Events on a disabled configuration are paused instead.
        - An event with no room in the pool stays due for a later scan.
        - Returns ``None``, sending nothing, when the file cannot be saved or
          its configuration's access cannot be read; it is tried again later.
        """
        # The events to send once the file is saved.
        jobs = []
        # How many sends the pool can take right now.
        room = self.room()
        with ConfigCache.reminder_guard():
            # Read again under the lock; the file may have changed since.
            events = ConfigCache.read_reminders(reminder_file)
            if not events:
                return []

            # Check the configuration's access and use once for the whole file.
            allowed = access_allows(reminder_file)
            if allowed is None:
                # Neither sent nor paused, so they go out once it reads again.
                logger.warning("REMINDER - Held the reminders in %s; its access could not be read", reminder_file)
                return None

            idle = self.idle(reminder_file, now)
            if idle and room <= 0:
                # The notice needs room in the pool like any send, so pause
                # them on a later scan instead.
                self.backlog = True
                return events

            if idle:
                logger.warning("REMINDER - Paused the reminders in %s; nobody has used it lately", reminder_file)
                for event in events:
                    if event.get("enabled", True) is not False:
                        event["enabled"] = False
                        event["last"] = {"at": now, "ok": False, "reason": "idle"}

            after = datetime.fromtimestamp(now, UTC)
            for event in events:
                # Leave alone anything paused, finished, not due yet, or
                # waiting for a configuration.
                if not _pending(event) or event["next"] > now or (not configured and _needs_config(event)):
                    continue

                if not allowed:
                    logger.warning("REMINDER - Paused reminder %s; its configuration is disabled", event.get("id"))
                    # It stays paused until someone turns it back on.
                    event["enabled"] = False
                    event["last"] = {"at": now, "ok": False, "reason": "access"}
                    continue

                # How many seconds past its time this event is.
                late = now - event["next"]
                if late <= self.grace and len(jobs) >= room:
                    # No room in the pool; leave it due for the next scan.
                    self.backlog = True
                    continue

                # Count this run and move the event on to its next time.
                try:
                    event["runs"] = (event.get("runs") or 0) + 1
                    drop_past_skips(event, now)
                    event["next"] = next_run(event, after)

                # A reminder damaged on disk (such as one edited by hand) is
                # stopped rather than stopping the scheduler.
                except (*SCHEDULE_ERRORS, AttributeError, KeyError) as e:
                    logger.warning("REMINDER - Stopped reminder %s; its time is invalid", event.get("id"))
                    logger.debug("Schedule time error: %s", e)
                    event["next"] = None

                if late > self.grace:
                    logger.warning(
                        "REMINDER - Skipped reminder %s; it was %d seconds overdue",
                        event.get("id"),
                        late,
                    )
                    event["last"] = {"at": now, "ok": False, "reason": "late"}
                    continue

                # A copy, so later changes to the file cannot alter what is sent.
                jobs.append(dict(event))

            if not ConfigCache.write_reminders(reminder_file, events):
                # Sending now could send it again once the file is readable.
                logger.warning("REMINDER - Could not save %s; its due reminders were not sent", reminder_file)
                return None

        # The file is saved, so it is now safe to send.
        for event in jobs:
            self.submit(self.send, reminder_file, event)

        if idle:
            self.submit(self.send_idle_notice, reminder_file)

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
    def send_idle_notice(reminder_file: str) -> None:
        """Tell a configuration's own services that its reminders were paused."""
        # Written in the server's language, since there is no browser to ask.
        with translation.override(settings.LANGUAGE_CODE):
            body = _("Reminders for this configuration were paused because it was not used for {} days").format(
                settings.APPRISE_REMINDER_IDLE_DAYS
            )

        deliver(reminder_file, {"id": "idle", "body": body, "type": apprise.NotifyType.WARNING.value})

    def send(self, reminder_file: str, event: dict) -> None:
        """Send one event and record how it went."""
        try:
            ok = deliver(reminder_file, event)

        except Exception as e:
            # Recorded as a failed send, rather than lost with the worker.
            logger.warning("REMINDER - Reminder %s failed unexpectedly", event.get("id"))
            logger.debug("Reminder error: %s", e)
            ok = False

        # Save the result on the reminder, read fresh in case it was changed.
        with ConfigCache.reminder_guard():
            events = ConfigCache.read_reminders(reminder_file)
            for stored in events or []:
                if stored.get("id") == event.get("id"):
                    stored["last"] = {"at": int(time.time()), "ok": ok}
                    if not ConfigCache.write_reminders(reminder_file, events):
                        logger.warning("REMINDER - Could not record how reminder %s went", event.get("id"))
                    break
