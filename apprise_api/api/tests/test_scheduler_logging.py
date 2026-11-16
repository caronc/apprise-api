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
"""What the scheduler logs, so a reminder that did not go out can be traced."""

from datetime import datetime
import logging
import os
import signal
import threading
import time
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.conf import settings
from django.test import override_settings
import pytest

from .. import scheduler as scheduler_module
from ..schedule import LOG_TIME_FORMAT, log_time
from ..scheduler import MAIN_ACTOR, Scheduler, _actor, _leave_signals_to_main_thread, prepare_send
from ..utils import AppriseConfigCache, AppriseStoreMode

# A repeating reminder and a one-off, both long past their start.
_DAILY = {"tz": "Etc/UTC", "cron": "0 0 9"}
_ONCE = {"at": "2000-01-01T00:00:00"}


class _Lines(logging.Handler):
    """Keep each log line as (level, message)."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append((record.levelname, record.getMessage()))


@pytest.fixture
def logs():
    """Collect everything the django logger writes, at every level."""
    logger = logging.getLogger("django")
    handler = _Lines()
    level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield handler.lines
    logger.removeHandler(handler)
    logger.setLevel(level)


@pytest.fixture
def store(tmpdir):
    """Point the scheduler at an empty configuration directory."""
    cache = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    with patch.object(scheduler_module, "ConfigCache", cache):
        yield cache


@pytest.fixture(autouse=True)
def ignore_wake_signal():
    """Keep a send that finishes on a full pool from ending the test run."""
    saved = signal.signal(signal.SIGUSR1, signal.SIG_IGN)
    yield
    signal.signal(signal.SIGUSR1, saved)


@pytest.fixture
def restore_signals():
    """Put back the signal handlers the scheduler replaces."""
    saved = {signum: signal.getsignal(signum) for signum in (signal.SIGUSR1, signal.SIGTERM, signal.SIGINT)}
    yield
    for signum, handler in saved.items():
        signal.signal(signum, handler)


def _save(store, key, events, config=True):
    """Store reminders, with a configuration beside them unless ``config`` is off."""
    if config:
        assert store.put(key, "json://localhost", "text")

    reminder_file = store.reminder_path(key)
    assert store.write_reminders(reminder_file, events)
    return reminder_file


def test_times_are_logged_on_the_server_clock():
    """A logged time reads on the log line's own clock, with the reminder's local time when it differs."""
    due = 1795000000
    server = datetime.fromtimestamp(due).strftime(LOG_TIME_FORMAT)
    assert log_time(due) == server
    assert log_time(due, settings.TIME_ZONE) == server

    # 14 hours ahead of UTC, so it reads differently from the server
    local = datetime.fromtimestamp(due, ZoneInfo("Pacific/Kiritimati")).strftime(LOG_TIME_FORMAT)
    assert local != server
    assert log_time(due, "Pacific/Kiritimati") == f"{server} ({local} Pacific/Kiritimati)"

    # Nothing left to send, a damaged timezone, and a time out of range
    assert log_time(None) == "none"
    assert log_time(due, "Not/AZone") == server
    assert log_time(10**20) == str(10**20)


def test_send_threads_are_numbered():
    """Send threads are named sched-runner-001 and up; the main thread is the scheduler."""
    scheduler = Scheduler(workers=2, grace=60)
    # Both sends wait for each other, so each gets its own thread
    together = threading.Barrier(2)

    def name():
        together.wait(5)
        return _actor()

    try:
        futures = [scheduler.pool.submit(name) for _ in range(2)]
        names = sorted(future.result(5) for future in futures)

    finally:
        scheduler.pool.shutdown(wait=True)

    assert names == ["sched-runner-001", "sched-runner-002"]
    assert _actor() == MAIN_ACTOR


def test_wake_up_is_logged_only_when_it_changes(logs):
    """Each new wake-up is logged once, so a reminder moved to a new time shows its old wake-up replaced."""
    due = 1795000000
    scheduler = Scheduler(workers=1, grace=60)
    scheduler.soonest = (due, "abc", "Pacific/Kiritimati")
    scheduler.plan(due)
    scheduler.plan(due)

    # Saved for a minute later
    scheduler.soonest = (due + 60, "abc", None)
    scheduler.plan(due + 60)

    # A file looked at again, then nothing left
    scheduler.soonest = (due + 120, None, None)
    scheduler.plan(due + 120)
    scheduler.soonest = None
    scheduler.plan(None)

    # A full pool waits for a send instead
    full = Scheduler(workers=1, grace=60)
    full.backlog = True
    full.plan(None)

    assert [line for _level, line in logs] == [
        f"REMINDER - scheduler - Next wake-up {log_time(due, 'Pacific/Kiritimati')} for reminder abc",
        f"REMINDER - scheduler - Next wake-up {log_time(due + 60)} for reminder abc",
        f"REMINDER - scheduler - Next wake-up {log_time(due + 120)} to look again",
        "REMINDER - scheduler - Waiting for a reminder to be saved",
        "REMINDER - scheduler - Waiting for a send to finish",
    ]


@override_settings(APPRISE_AUTH_REQUIRED=True)
def test_held_reminders_say_why_once(store, logs):
    """A file nothing can be sent from is logged with why, once until that changes."""
    later = int(time.time()) + 600
    disabled = _save(store, "off", [{"id": "a", "when": _ONCE, "next": later}])
    assert store.set_access("off", "disabled")
    # Nothing waiting on this disabled one, so it is not mentioned
    _save(store, "paused", [{"id": "b", "when": _ONCE, "next": later, "enabled": False}])
    assert store.set_access("paused", "disabled")
    # Sends through a configuration that was never saved
    waiting = _save(store, "none", [{"id": "c", "when": _ONCE, "next": later}], config=False)

    scheduler = Scheduler(workers=1, grace=60)
    try:
        assert scheduler.scan() is None
        assert scheduler.scan() is None

    finally:
        scheduler.pool.shutdown(wait=True)

    assert sorted(line for _level, line in logs if "Holding" in line) == sorted(
        [
            f"REMINDER - scheduler - Holding the reminders in {disabled}; its configuration is disabled",
            f"REMINDER - scheduler - Holding the reminders in {waiting}; they wait for a configuration to be saved",
        ]
    )


def test_claimed_reminders_log_their_next_time(store, logs):
    """Each claimed reminder logs how late it is and its next time, or why it was not sent."""
    now = int(time.time())
    reminder_file = _save(
        store,
        "key",
        [
            {"id": "once", "when": _ONCE, "next": now - 1},
            {"id": "daily", "when": _DAILY, "next": now - 1},
            {"id": "late", "when": _ONCE, "next": now - 3600},
            {"id": "third", "when": _ONCE, "next": now - 1},
        ],
    )
    scheduler = Scheduler(workers=1, grace=60)
    try:
        with (
            patch.object(scheduler_module, "deliver", return_value=True),
            patch.object(scheduler, "room", return_value=2),
        ):
            scheduler.claim(reminder_file, now)
            scheduler.pool.shutdown(wait=True)

    finally:
        scheduler.pool.shutdown(wait=True)

    tomorrow = next(event["next"] for event in store.read_reminders(reminder_file) if event["id"] == "daily")
    assert logs == [
        ("INFO", "REMINDER - scheduler - Reminder once has no more times to send"),
        ("DEBUG", "REMINDER - scheduler - Sending reminder once, 1 seconds after its time; next none"),
        (
            "DEBUG",
            "REMINDER - scheduler - Sending reminder daily, 1 seconds after its time; "
            f"next {log_time(tomorrow, 'Etc/UTC')}",
        ),
        ("INFO", "REMINDER - scheduler - Reminder late has no more times to send"),
        (
            "WARNING",
            "REMINDER - scheduler - Skipped reminder late; it was 3600 seconds overdue (grace is 60), next none",
        ),
        ("DEBUG", f"REMINDER - scheduler - 1 due reminders in {reminder_file} wait for a free send thread"),
    ]


def test_run_logs_its_start_wake_ups_and_stop(store, logs, restore_signals):
    """Start, settings, finished removals, a wake-up from a change, and the stop are each logged."""

    def change_then_stop():
        # Like a web worker in another process, this thread never takes the signal itself
        _leave_signals_to_main_thread()
        time.sleep(0.3)
        os.kill(os.getpid(), signal.SIGUSR1)
        time.sleep(0.3)
        os.kill(os.getpid(), signal.SIGTERM)

    worker = threading.Thread(target=change_then_stop)
    with patch.object(store, "recover_removed_reminders", return_value=2):
        worker.start()
        assert Scheduler(workers=2, grace=90).run() is True

    worker.join()
    lines = [line for _level, line in logs]
    assert lines[:3] == [
        "REMINDER - scheduler - Scheduler started with 2 send threads; reminders up to 90 seconds late are sent",
        "REMINDER - scheduler - Finished 2 configuration removals that stopped part way",
        "REMINDER - scheduler - Waiting for a reminder to be saved",
    ]
    assert "REMINDER - scheduler - Woken by a reminder change" in lines
    assert lines[-1] == "REMINDER - scheduler - Scheduler stopped"


def test_a_change_found_without_a_signal_is_logged(store, logs, restore_signals):
    """A change seen on the next check, with no signal, is logged like a signalled one."""
    scheduler = Scheduler(workers=1, grace=60)

    def sleep(_due):
        # The first sleep ends on a change saved elsewhere; the second stops
        scheduler.stopping = scheduler.changed
        scheduler.changed = True

    with patch.object(scheduler, "sleep", side_effect=sleep):
        assert scheduler.run() is True

    assert [line for _level, line in logs].count("REMINDER - scheduler - Woken by a reminder change") == 1

    # The real sleep notes such a change
    scheduler.seen = 0
    with patch.object(scheduler_module, "wait_until"), patch.object(store, "reminders_changed", return_value=1):
        Scheduler.sleep(scheduler, None)

    assert scheduler.changed is True


def test_a_send_names_who_asked_for_it(store, logs):
    """A send from the web names the client; one from the scheduler names its thread."""
    reminder_file = store.reminder_path("missing")
    assert prepare_send(reminder_file, {"id": "a", "body": "Hi"}, who="10.0.0.5") is None
    assert prepare_send(reminder_file, {"id": "b", "body": "Hi"}) is None
    assert logs == [
        ("WARNING", "REMINDER - 10.0.0.5 - No configuration found for reminder a"),
        ("WARNING", "REMINDER - scheduler - No configuration found for reminder b"),
    ]
