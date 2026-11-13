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
from concurrent.futures import Future
from datetime import UTC, date, datetime, time as clock
import io
import os
import signal
import threading
import time
from unittest.mock import patch
from zoneinfo import ZoneInfo

import apprise
from dateblock import dateblock
from django.core import management
from django.core.management.base import CommandError
from django.test import override_settings
import pytest

from .. import schedule as schedule_module, scheduler as scheduler_module
from ..auth import ConfigAuthRecord
from ..exceptions import AppriseAPIError
from ..schedule import (
    drop_past_skips,
    format_moment,
    holiday_countries,
    holiday_dates,
    holiday_language,
    holiday_start,
    is_one_off,
    next_run,
    parse_moment,
    parse_time_of_day,
    shortest_gap,
    upcoming,
)
from ..scheduler import (
    Scheduler,
    _leave_signals_to_main_thread,
    access_allows,
    deliver,
    prepare_send,
    reminder_attachments,
    wait_until,
)
from ..utils import AppriseConfigCache, AppriseStoreMode
from .helpers import notify_result

TORONTO = ZoneInfo("America/Toronto")


@pytest.fixture
def store(tmpdir):
    """Point the scheduler at an empty configuration directory."""
    cache = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    with patch.object(scheduler_module, "ConfigCache", cache):
        yield cache


@pytest.fixture
def restore_signals():
    """Put back the signal handlers the scheduler replaces."""
    saved = {signum: signal.getsignal(signum) for signum in (signal.SIGUSR1, signal.SIGTERM, signal.SIGINT)}
    yield
    for signum, handler in saved.items():
        signal.signal(signum, handler)


def _save(store, key, events):
    """Store a configuration and its reminders, returning the reminder file."""
    assert store.put(key, "json://localhost", apprise.ConfigFormat.TEXT.value)
    reminder_file = store.reminder_path(key)
    assert store.write_reminders(reminder_file, events)
    return reminder_file


def test_next_run_one_off():
    """A one-off is due at its local time once, then never again."""
    after = datetime(2026, 12, 1, tzinfo=UTC)
    event = {"when": {"tz": "America/Toronto", "at": "2026-12-25T09:00:00"}}
    expected = int(datetime(2026, 12, 25, 9, tzinfo=TORONTO).timestamp())
    assert next_run(event, after) == expected

    # It stays due after its time passes so a late send can be considered
    assert next_run(event, datetime(2027, 1, 1, tzinfo=UTC)) == expected

    # A time with its own offset ignores the event timezone
    event = {"when": {"tz": "America/Toronto", "at": "2026-12-25T09:00:00+00:00"}}
    assert next_run(event, after) == int(datetime(2026, 12, 25, 9, tzinfo=UTC).timestamp())

    event["runs"] = 1
    assert next_run(event, after) is None


def test_next_run_repeating_follows_local_time():
    """A repeating event keeps its local time across a daylight saving change."""
    event = {"when": {"tz": "America/Toronto", "cron": "0 0 9 * * Mon-Fri"}}
    # Friday before the clocks change on Sunday November 1st, 2026
    friday = datetime(2026, 10, 30, 12, tzinfo=TORONTO)
    monday = next_run(event, friday)
    assert datetime.fromtimestamp(monday, TORONTO) == datetime(2026, 11, 2, 9, tzinfo=TORONTO)

    # The 5-field crontab layout gives the same answer
    event = {"when": {"tz": "America/Toronto", "cron": "0 9 * * Mon-Fri", "isc": True}}
    assert next_run(event, friday) == monday


@override_settings(TIME_ZONE="Etc/UTC")
def test_next_run_limits():
    """Run counts, end times and missing times are all respected."""
    after = datetime(2026, 1, 1, tzinfo=UTC)
    daily = {"when": {"cron": "0 0 9"}}
    assert next_run(daily, after) == int(datetime(2026, 1, 1, 9, tzinfo=UTC).timestamp())

    assert next_run({**daily, "count": 3, "runs": 2}, after) is not None
    assert next_run({**daily, "count": 3, "runs": 3}, after) is None

    assert next_run({**daily, "until": "2026-01-01T08:00:00+00:00"}, after) is None

    with pytest.raises(ValueError):
        next_run({"when": {}}, after)

    with pytest.raises(SyntaxError):
        next_run({"when": {"cron": "garbage"}}, after)


def test_upcoming_lists_the_next_runs():
    """Upcoming runs stop at the run count or end time, and a one-off has one."""
    after = datetime(2026, 1, 1, tzinfo=UTC)
    daily = {"when": {"tz": "America/Toronto", "cron": "0 0 9"}}
    assert upcoming(daily, after, 3) == [
        int(datetime(2026, 1, day, 9, tzinfo=TORONTO).timestamp()) for day in (1, 2, 3)
    ]
    assert len(upcoming({**daily, "count": 2}, after, 5)) == 2
    # The end time is read in the reminder's own timezone
    assert len(upcoming({**daily, "until": "2026-01-02T12:00:00"}, after, 5)) == 2

    once = {"when": {"tz": "America/Toronto", "at": "2026-02-01T10:00:00"}}
    assert upcoming(once, after, 5) == [int(datetime(2026, 2, 1, 10, tzinfo=TORONTO).timestamp())]


def _every(at, every, after=None, count=4):
    """Return the next local times an ``every`` rule starting at ``at`` is due."""
    event = {"when": {"tz": "America/Toronto", "at": at, "every": every}}
    after = after or datetime(2026, 11, 8, 12, tzinfo=UTC)
    return [datetime.fromtimestamp(due, TORONTO).strftime("%a %Y-%m-%d %H:%M") for due in upcoming(event, after, count)]


def test_every_rule_counts_its_interval_from_the_start():
    """Each unit repeats from the start time, skipping the units between sends."""
    assert _every("2026-11-10T09:00:00", {"unit": "days", "interval": 3}) == [
        "Tue 2026-11-10 09:00",
        "Fri 2026-11-13 09:00",
        "Mon 2026-11-16 09:00",
        "Thu 2026-11-19 09:00",
    ]
    assert _every("2026-11-10T09:00:00", {"unit": "weeks", "interval": 2, "days": ["Mon", "Wed"]}) == [
        "Wed 2026-11-11 09:00",
        "Mon 2026-11-23 09:00",
        "Wed 2026-11-25 09:00",
        "Mon 2026-12-07 09:00",
    ]
    # Five months carries on across the end of the year
    assert _every("2026-11-10T09:00:00", {"unit": "months", "interval": 5}, count=3) == [
        "Tue 2026-11-10 09:00",
        "Sat 2027-04-10 09:00",
        "Fri 2027-09-10 09:00",
    ]
    # A day some months lack, and a day some years lack, are skipped there
    assert _every("2026-01-31T09:00:00", {"unit": "months"}, count=3) == [
        "Thu 2026-12-31 09:00",
        "Sun 2027-01-31 09:00",
        "Wed 2027-03-31 09:00",
    ]
    assert _every("2026-11-10T09:00:00", {"unit": "years", "interval": 3}, count=2) == [
        "Tue 2026-11-10 09:00",
        "Sat 2029-11-10 09:00",
    ]
    assert _every("2024-02-29T09:00:00", {"unit": "years", "interval": 2}, count=2) == [
        "Tue 2028-02-29 09:00",
        "Sun 2032-02-29 09:00",
    ]


def test_every_rule_limits_and_short_units():
    """Minutes and hours repeat through the day, inside the chosen weekdays and months."""
    assert _every("2026-11-08T09:00:00", {"unit": "minutes", "interval": 90, "days": ["Mon"]}, count=2) == [
        "Mon 2026-11-09 00:00",
        "Mon 2026-11-09 01:30",
    ]
    assert _every("2026-11-10T09:00:00", {"unit": "hours", "interval": 7, "months": [12]}, count=3) == [
        "Tue 2026-12-01 02:00",
        "Tue 2026-12-01 09:00",
        "Tue 2026-12-01 16:00",
    ]
    # A start that its own limits leave out is not sent; the next match is
    assert _every("2026-11-14T09:00:00", {"unit": "days", "days": ["Mon"]}, count=1) == ["Mon 2026-11-16 09:00"]
    # Weeks without chosen days keep to the start date's weekday
    assert _every("2026-11-10T09:00:00", {"unit": "weeks"}, count=2) == ["Tue 2026-11-10 09:00", "Tue 2026-11-17 09:00"]

    with patch.object(schedule_module, "EVERY_SEARCH_MAX", 1):
        assert _every("2026-11-10T09:00:00", {"unit": "days", "interval": 4, "days": ["Mon"]}, count=1) == []

    with pytest.raises(ValueError):
        # No year has a 30th of February
        _every("2026-01-30T09:00:00", {"unit": "months", "months": [2]})

    assert is_one_off({"at": "2026-11-10T09:00:00"})
    assert not is_one_off({"at": "2026-11-10T09:00:00", "every": {"unit": "days"}})


def test_every_rule_in_seconds():
    """Seconds repeat from the start, inside the chosen weekdays."""
    after = datetime(2026, 11, 8, tzinfo=UTC)
    start = int(datetime(2026, 11, 10, 9, tzinfo=UTC).timestamp())
    event = {"when": {"tz": "Etc/UTC", "at": "2026-11-10T09:00:00", "every": {"unit": "seconds", "interval": 45}}}
    assert upcoming(event, after, 3) == [start, start + 45, start + 90]

    # The start is a Tuesday; the next allowed time is Wednesday at midnight
    event["when"]["every"]["days"] = ["Wed"]
    assert upcoming(event, after, 2) == [start + 54000, start + 54045]

    # Times on or after the end of a window are left out
    assert upcoming(event, after, 5, before=start + 54045) == [start + 54000]


def test_shortest_gap_between_runs():
    """The gap ignores skipped times, ends and counts, and a one-off has none."""
    after = datetime(2026, 11, 8, tzinfo=UTC)
    start = int(datetime(2026, 11, 10, 9, tzinfo=UTC).timestamp())
    event = {"when": {"tz": "Etc/UTC", "at": "2026-11-10T09:00:00", "every": {"unit": "seconds", "interval": 30}}}
    assert shortest_gap(event, after) == 30
    assert shortest_gap({**event, "skip": [start + 30], "count": 1, "until": "2026-11-10T09:00:00"}, after) == 30

    assert shortest_gap({"when": {"tz": "Etc/UTC", "cron": "0,10 * * * * *"}}, after) == 10
    assert shortest_gap({"when": {"tz": "Etc/UTC", "at": "2026-11-10T09:00:00"}}, after) is None


def test_dates_and_times_can_leave_out_the_time():
    """A date alone is midnight, and the time may stop at the hour or the minute."""
    assert format_moment("2026-12-25") == "2026-12-25T00:00:00"
    assert format_moment("2026-12-25 09") == "2026-12-25T09:00:00"
    assert format_moment("2026-12-25T09:30") == "2026-12-25T09:30:00"
    assert format_moment(" 2026-12-25 09:30:15 ") == "2026-12-25T09:30:15"
    assert format_moment("2026-12-25T09:30:15+02:00") == "2026-12-25T09:30:15+02:00"

    for bad in ("2026-12-25 25", "2026-13-01", "soon", "2026-12-25 9:5"):
        with pytest.raises(ValueError):
            parse_moment(bad)

    with pytest.raises(TypeError):
        parse_moment(20261225)


def test_start_holds_back_a_repeating_reminder():
    """Nothing repeating is sent before its start, and a start already passed changes nothing."""
    after = datetime(2026, 11, 8, tzinfo=UTC)
    daily = {"when": {"tz": "Etc/UTC", "cron": "0 0 9"}}
    assert next_run({**daily, "start": "2026-11-20"}, after) == int(datetime(2026, 11, 20, 9, tzinfo=UTC).timestamp())
    assert next_run({**daily, "start": "2020-01-01"}, after) == int(datetime(2026, 11, 8, 9, tzinfo=UTC).timestamp())


def test_drift_moves_every_time_later():
    """Drift adds the same seconds to each time, whatever the schedule."""
    after = datetime(2026, 11, 8, tzinfo=UTC)
    nine = int(datetime(2026, 11, 8, 9, tzinfo=UTC).timestamp())
    daily = {"when": {"tz": "Etc/UTC", "cron": "0 0 9", "drift": 90}}
    assert upcoming(daily, after, 2) == [nine + 90, nine + 86400 + 90]

    # A time whose drift has not passed yet is still due
    assert next_run(daily, datetime.fromtimestamp(nine + 30, UTC)) == nine + 90

    # Larger than the repeat, it still just moves each time later
    assert next_run({"when": {**daily["when"], "drift": 90000}}, after) == nine - 86400 + 90000

    # Skipped times and the end date are the drifted times
    assert upcoming({**daily, "skip": [nine + 90]}, after, 1) == [nine + 86400 + 90]
    assert upcoming({**daily, "until": "2026-11-08T09:01:00"}, after, 2) == []

    once = {"when": {"tz": "Etc/UTC", "at": "2026-11-08T09:00:00", "drift": 5}}
    assert next_run(once, after) == nine + 5

    every = {"when": {"tz": "Etc/UTC", "at": "2026-11-08T09:00:00", "every": {"unit": "days"}, "drift": 30}}
    assert upcoming(every, after, 2) == [nine + 30, nine + 86400 + 30]

    christmas = {"country": "CA", "name": "Christmas Day"}
    holiday = {"when": {"tz": "Etc/UTC", "holiday": christmas}, "start": "2026-01-01T09:00:00"}
    plain = next_run(holiday, after)
    assert next_run({**holiday, "when": {**holiday["when"], "drift": 60}}, after) == plain + 60


def test_negative_drift_sends_early():
    """A negative drift sends each time early, like a notice a week before rent is due."""
    week = 7 * 86400
    after = datetime(2026, 11, 10, tzinfo=UTC)
    first = int(datetime(2026, 12, 1, 9, tzinfo=UTC).timestamp())
    second = int(datetime(2027, 1, 1, 9, tzinfo=UTC).timestamp())
    rent = {"when": {"tz": "Etc/UTC", "at": "2026-12-01T09:00:00", "every": {"unit": "months"}, "drift": -week}}

    # The first notice comes a week before the start date
    assert upcoming(rent, after, 2) == [first - week, second - week]

    # Once a notice has passed, the next one is due, though rent itself is not yet
    assert next_run(rent, datetime(2026, 11, 25, tzinfo=UTC)) == second - week

    # The end date and skipped times are the notice times
    assert upcoming({**rent, "until": "2026-11-24T09:00:00"}, after, 2) == [first - week]
    assert upcoming({**rent, "skip": [first - week]}, after, 1) == [second - week]

    once = {"when": {"tz": "Etc/UTC", "at": "2026-12-01T09:00:00", "drift": -week}}
    assert next_run(once, after) == first - week

    christmas = {"country": "CA", "name": "Christmas Day"}
    holiday = {"when": {"tz": "Etc/UTC", "holiday": christmas}, "start": "2026-01-01T09:00:00"}
    assert next_run({**holiday, "when": {**holiday["when"], "drift": -week}}, after) == next_run(holiday, after) - week

    # The start holds back the holiday itself, not its drifted time: a holiday
    # on its start date with a drift of -9 days is sent 9 days before it
    on_the_day = {**holiday, "start": "2026-12-25T09:00:00", "when": {**holiday["when"], "drift": -9 * 86400}}
    christmas = int(datetime(2026, 12, 25, 9, tzinfo=UTC).timestamp())
    assert next_run(on_the_day, after) == christmas - 9 * 86400


@pytest.mark.parametrize(
    ("tz", "cron", "drift", "after"),
    (
        ("Etc/UTC", "0 0 9 * * *", -3600, datetime(2026, 11, 8, 7, 59, tzinfo=UTC)),
        ("Etc/UTC", "0 0 9 * * *", -3600, datetime(2026, 11, 8, 8, 1, tzinfo=UTC)),
        ("Etc/UTC", "0 0 9 * * *", -90000, datetime(2026, 11, 8, tzinfo=UTC)),
        ("Etc/UTC", "0 0 9 1 * *", -604800, datetime(2026, 11, 20, tzinfo=UTC)),
        ("America/Toronto", "0 0 9 11 11 *", -604800, datetime(2027, 1, 1, tzinfo=UTC)),
        ("America/Toronto", "0 0 9 * * *", 90, datetime(2026, 11, 8, 13, tzinfo=UTC)),
    ),
)
def test_drift_matches_dateblock(tz, cron, drift, after):
    """A reminder's drift, early or late, lands where dateblock's own drift does."""
    expected = dateblock(f"{cron} {drift}", block=False, ref=after.astimezone(ZoneInfo(tz)))
    assert next_run({"when": {"tz": tz, "cron": cron, "drift": drift}}, after) == int(expected.timestamp())


def test_start_on_a_second_keeps_that_second():
    """A start with seconds sends on that exact second, every time."""
    after = datetime(2026, 11, 8, tzinfo=UTC)
    start = int(datetime(2026, 11, 8, 9, 0, 30, tzinfo=UTC).timestamp())
    once = {"when": {"tz": "Etc/UTC", "at": "2026-11-08T09:00:30"}}
    assert next_run(once, after) == start

    # Each repeat lands on the same second as the start
    for unit, step in (("minutes", 60), ("hours", 3600), ("days", 86400)):
        every = {"when": {"tz": "Etc/UTC", "at": "2026-11-08T09:00:30", "every": {"unit": unit}}}
        assert upcoming(every, after, 3) == [start, start + step, start + 2 * step], unit

    monthly = {"when": {"tz": "Etc/UTC", "at": "2026-11-08T09:00:30", "every": {"unit": "months"}}}
    assert upcoming(monthly, after, 2) == [start, int(datetime(2026, 12, 8, 9, 0, 30, tzinfo=UTC).timestamp())]

    # A holiday's time, taken from its start, can carry seconds too
    christmas = {"country": "CA", "name": "Christmas Day"}
    holiday = {"when": {"tz": "Etc/UTC", "holiday": christmas}, "start": "2026-11-08T09:00:30"}
    assert next_run(holiday, after) == int(datetime(2026, 12, 25, 9, 0, 30, tzinfo=UTC).timestamp())


def test_skipped_times_are_passed_over():
    """Skipped times are not due and do not count toward the run limit."""
    after = datetime(2026, 1, 1, tzinfo=UTC)
    daily = {"when": {"tz": "Etc/UTC", "cron": "0 0 9"}}
    first, second, third = upcoming(daily, after, 3)
    assert upcoming({**daily, "skip": [first, second]}, after, 2) == [third, third + 86400]
    assert upcoming({**daily, "skip": [second], "count": 2}, after, 5) == [first, third]

    # A skipped one-off is still due; only repeating reminders skip times
    once = {"when": {"tz": "Etc/UTC", "at": "2026-01-02T09:00:00"}}
    assert next_run({**once, "skip": [second]}, after) == second

    event = {"skip": [first, third]}
    drop_past_skips(event, second)
    assert event == {"skip": [third]}


def test_claim_forgets_skipped_times_that_passed(store):
    """Moving a reminder on also drops the skipped times behind it."""
    now = int(time.time())
    event = {"id": "a", "when": {"cron": "0 0 9"}, "next": now, "skip": [now - 60, now + 86400 * 400]}
    reminder_file = _save(store, "key", [event])

    scheduler = Scheduler(workers=1, grace=60)
    with patch.object(scheduler.pool, "submit"):
        scheduler.claim(reminder_file, now)

    assert store.read_reminders(reminder_file)[0]["skip"] == [now + 86400 * 400]


def test_next_run_holiday():
    """A holiday reminder is due on the holiday each year, at its start's local time of day."""
    rule = {"country": "CA", "region": "ON", "name": "Christmas Day"}
    event = {"when": {"tz": "America/Toronto", "holiday": rule}, "start": "2026-12-25T09:00:00"}
    christmas = datetime(2026, 12, 25, 9, tzinfo=TORONTO)
    assert next_run(event, datetime(2026, 12, 1, tzinfo=UTC)) == int(christmas.timestamp())

    # Once this year's has passed, next year's is due
    assert next_run(event, christmas) == int(datetime(2027, 12, 25, 9, tzinfo=TORONTO).timestamp())

    # A start in a later year holds it back until then
    later = {**event, "start": "2028-12-01T17:30:00"}
    assert next_run(later, christmas) == int(datetime(2028, 12, 25, 17, 30, tzinfo=TORONTO).timestamp())

    # Without a start, it is sent at midnight
    midnight = {"when": event["when"]}
    midnight_due = int(datetime(2026, 12, 25, tzinfo=TORONTO).timestamp())
    assert next_run(midnight, datetime(2026, 12, 1, tzinfo=UTC)) == midnight_due

    # Each year's date is worked out for that year, so holidays that move are
    # sent on the right day: Thanksgiving is the second Monday of October, and
    # Good Friday follows Easter
    for name, first, second in (
        ("Thanksgiving Day", (2026, 10, 12), (2027, 10, 11)),
        ("Good Friday", (2026, 4, 3), (2027, 3, 26)),
    ):
        moving = {**event, "when": {**event["when"], "holiday": {**rule, "name": name}}, "start": "2026-01-01T09:00:00"}
        assert upcoming(moving, datetime(2026, 1, 1, tzinfo=UTC), 2) == [
            int(datetime(*first, 9, tzinfo=TORONTO).timestamp()),
            int(datetime(*second, 9, tzinfo=TORONTO).timestamp()),
        ], name

        # Sent only once, it has nothing more after the first
        once = {**moving, "count": 1}
        assert upcoming(once, datetime(2026, 1, 1, tzinfo=UTC), 2) == [
            int(datetime(*first, 9, tzinfo=TORONTO).timestamp())
        ]

    # A name the country does not use is never due
    event = {"when": {"tz": "America/Toronto", "holiday": {**rule, "name": "Not A Holiday"}}}
    assert next_run(event, christmas) is None

    with pytest.raises(NotImplementedError):
        next_run({"when": {"holiday": {**rule, "country": "ZZ"}}}, christmas)


def test_holiday_start_uses_only_its_year_and_time():
    """A holiday's start sets the year it begins in and the time of day, never the date."""
    rule = {"country": "CA", "name": "Christmas Day"}
    after = datetime(2026, 12, 1, tzinfo=UTC)
    event = {"when": {"tz": "Etc/UTC", "holiday": rule}, "start": "2026-12-26T09:30:15"}
    assert next_run(event, after) == int(datetime(2026, 12, 25, 9, 30, 15, tzinfo=UTC).timestamp())

    later = {**event, "start": "2027-01-01T09:00:00"}
    assert next_run(later, after) == int(datetime(2027, 12, 25, 9, tzinfo=UTC).timestamp())


def test_holiday_start_from_a_time_of_day():
    """A time of day alone is placed on the holiday's next date to send."""
    assert parse_time_of_day("09:30") == clock(9, 30)
    assert parse_time_of_day("9:30:15") == clock(9, 30, 15)
    assert parse_time_of_day("2026-12-25 09:30") is None
    assert parse_time_of_day(None) is None
    with pytest.raises(ValueError):
        parse_time_of_day("25:00")

    rule = {"country": "CA", "name": "Christmas Day"}
    utc = ZoneInfo("Etc/UTC")
    start = holiday_start(rule, utc, clock(9, 30))
    assert start.endswith("-12-25T09:30:00")
    due = next_run({"when": {"tz": "Etc/UTC", "holiday": rule}, "start": start}, datetime.now(UTC))
    assert datetime.fromtimestamp(due, UTC).replace(tzinfo=None).isoformat() == start

    # A holiday not held in the years looked at uses today's date
    before = datetime.now(utc).date()
    start = holiday_start({**rule, "name": "Not A Holiday"}, utc, clock(9), drift=60)
    assert start in {f"{day}T09:00:00" for day in (before, datetime.now(utc).date())}


def test_holiday_names_keep_the_country_language():
    """Saved names stay in the country's language whatever the server locale is."""
    holiday_dates.cache_clear()
    with patch.dict(os.environ, {"LANGUAGE": "en_US"}):
        german = dict(holiday_dates("DE", None, 2027))

    assert german[date(2027, 12, 25)] == ("Erster Weihnachtstag",)
    assert dict(holiday_dates("DE", None, 2027, "en_US"))[date(2027, 12, 25)] == ("Christmas Day",)

    # A country without translations keeps the names it has
    assert dict(holiday_dates("AG", None, 2027))[date(2027, 12, 25)] == ("Christmas Day",)


def test_holiday_countries_and_languages():
    """Countries list their regions, and a page language finds its closest match."""
    canada = next(country for country in holiday_countries() if country["code"] == "CA")
    assert {"code": "ON", "name": "Ontario"} in canada["regions"]

    assert holiday_language("CA", "fr") == "fr"
    assert holiday_language("DE", "en") == "en_US"
    assert holiday_language("CN", "zh-hans") == "zh_CN"
    assert holiday_language("CA", "pl") is None


def test_wait_until_targets_the_exact_second():
    """The sleep ends at the requested second, or at once if it has passed."""
    started = time.monotonic()
    wait_until(int(time.time()) - 30)
    assert time.monotonic() - started < 1

    due = int(datetime(2026, 2, 28, 23, 59, 58, tzinfo=UTC).timestamp())
    with patch.object(scheduler_module, "dateblock") as block:
        wait_until(due)

    assert block.call_args.args[0] == "58 59 23 28 2"
    assert block.call_args.kwargs["ref"] == datetime(2026, 2, 28, 23, 59, 57, tzinfo=UTC)


def test_deliver_sends_with_stored_configuration(store):
    """A scheduled send uses the configuration beside its reminders."""
    reminder_file = _save(store, "key", [{"id": "a"}])
    event = {"id": "a", "title": "Hi", "body": "There", "type": "warning", "tag": "ops dev, qa", "format": "markdown"}

    with (
        patch.object(apprise.Apprise, "notify", return_value=notify_result()) as notify,
        patch.object(scheduler_module, "send_notify_webhook") as webhook,
    ):
        assert deliver(reminder_file, event) is True

    assert notify.call_args.args == ("There",)
    assert notify.call_args.kwargs["title"] == "Hi"
    assert notify.call_args.kwargs["notify_type"] == "warning"
    # A space means AND and a comma means OR, the same as a direct notify
    assert notify.call_args.kwargs["tag"] == [("ops", "dev"), "qa"]
    assert webhook.call_args.args[0] == "reminder"

    with (
        patch.object(apprise.Apprise, "notify", return_value=notify_result(success=False)),
        patch.object(scheduler_module, "send_notify_webhook"),
    ):
        assert deliver(reminder_file, event) is False

    with patch.object(apprise.Apprise, "notify", side_effect=apprise.exception.AppriseException("boom")):
        assert deliver(reminder_file, event) is False

    # The configuration was deleted after the event was queued
    store.clear("key")
    with patch.object(apprise.Apprise, "notify") as notify:
        assert deliver(reminder_file, event) is False

    notify.assert_not_called()


def test_send_uses_the_reminders_own_urls(store):
    """A reminder's own URLs are sent to alone, or with the configuration's its tag picks."""
    reminder_file = _save(store, "key", [{"id": "a"}])
    assert store.put("key", "json://plain.example\nops=json://ops.example", apprise.ConfigFormat.TEXT.value)

    def hosts(event):
        a_obj, kwargs = prepare_send(reminder_file, {"id": "a", "body": "Hi", **event})
        return sorted(server.host for server in a_obj.find(kwargs["tag"]))

    urls = ["json://one.example", "json://two.example"]
    assert hosts({"urls": urls}) == ["one.example", "two.example"]
    # An empty tag adds the configuration's untagged URLs, and a tag its matching ones
    assert hosts({"urls": urls, "tag": ""}) == ["one.example", "plain.example", "two.example"]
    assert hosts({"urls": urls, "tag": "ops"}) == ["one.example", "ops.example", "two.example"]
    # Without them, no tag picks the configuration's untagged URLs, as /notify/{KEY} does
    assert hosts({}) == ["plain.example"]
    assert hosts({"tag": "ops"}) == ["ops.example"]

    # The configuration is not needed to send to them
    store.clear("key")
    assert hosts({"urls": ["json://one.example"]}) == ["one.example"]


def test_send_leaves_out_urls_once_turned_off(store):
    """A reminder's own URLs are not sent to once the server stops allowing them."""
    reminder_file = _save(store, "key", [{"id": "a"}])
    assert store.put("key", "json://plain.example", apprise.ConfigFormat.TEXT.value)
    urls = ["json://one.example"]
    with override_settings(APPRISE_REMINDER_URLS_MAX=0):
        # With nothing else to send to, it is not sent
        assert prepare_send(reminder_file, {"id": "a", "body": "Hi", "urls": urls}) is None
        # Otherwise only the configuration's services are
        a_obj, kwargs = prepare_send(reminder_file, {"id": "a", "body": "Hi", "urls": urls, "tag": ""})
        assert [server.host for server in a_obj.find(kwargs["tag"])] == ["plain.example"]


def test_send_uses_the_configuration_as_it_is_now(store):
    """Each send reads the configuration again, so later tag changes apply."""
    reminder_file = _save(store, "key", [{"id": "a"}])
    event = {"id": "a", "body": "Hi", "tag": "friends"}

    def hosts():
        a_obj, kwargs = prepare_send(reminder_file, event)
        return sorted(server.host for server in a_obj.find(kwargs["tag"]))

    assert store.put("key", "friends=json://one.example", apprise.ConfigFormat.TEXT.value)
    assert hosts() == ["one.example"]

    # The tag moved to another service and gained one after the reminder was saved
    config = "family=json://one.example\nfriends=json://two.example\nfriends=json://three.example"
    assert store.put("key", config, apprise.ConfigFormat.TEXT.value)
    assert hosts() == ["three.example", "two.example"]


def test_scan_hands_out_due_events(store):
    """Due events are claimed and sent, and the soonest remaining time is returned."""
    now = int(time.time())
    soon = now + 600
    reminder_file = _save(
        store,
        "key",
        [
            {"id": "once", "when": {"at": "2000-01-01T00:00:00"}, "next": now - 5},
            {"id": "later", "when": {"at": "2999-01-01T00:00:00"}, "next": soon},
            {"id": "off", "enabled": False, "next": now - 5},
            {"id": "done", "next": None},
        ],
    )
    scheduler = Scheduler(workers=1, grace=60)
    with patch.object(scheduler_module, "deliver", return_value=True) as send:
        assert scheduler.scan() == soon
        scheduler.pool.shutdown(wait=True)

    assert send.call_args.args[1]["id"] == "once"
    events = {event["id"]: event for event in store.read_reminders(reminder_file)}
    assert events["once"]["next"] is None
    assert events["once"]["runs"] == 1
    assert events["once"]["last"]["ok"] is True
    assert "runs" not in events["off"]


def test_scan_looks_again_soon_when_something_cannot_be_read(store):
    """A file, folder or configuration that cannot be read is looked at again soon."""
    now = int(time.time())
    later = now + 3 * scheduler_module.RECHECK
    _save(store, "key", [{"id": "later", "when": {"at": "2999-01-01T00:00:00"}, "next": later}])
    scheduler = Scheduler(workers=1, grace=60)
    try:
        assert scheduler.scan() == later

        # A damaged reminder file
        other = store.reminder_path("damaged")
        os.makedirs(os.path.dirname(other), exist_ok=True)
        with open(other, "w") as f:
            f.write("not json")

        assert scheduler.scan() < later
        os.unlink(other)
        assert scheduler.scan() == later

        # A configuration whose file cannot be checked
        with patch.object(store, "reminder_has_config", return_value=None):
            assert scheduler.scan() < later

        # A folder that cannot be listed; the others are still gone through
        real = store.reminder_files
        with patch.object(store, "reminder_files", side_effect=lambda complete=False: None if complete else real()):
            assert scheduler.scan() < later

    finally:
        scheduler.pool.shutdown(wait=True)


def test_claim_stops_a_damaged_reminder(store):
    """A reminder damaged on disk is stopped instead of stopping the scheduler."""
    now = int(time.time())
    reminder_file = _save(
        store,
        "key",
        [
            {"id": "when", "when": "garbage", "next": now - 5},
            {"id": "skip", "when": {"cron": "0 0 9"}, "skip": ["x"], "next": now - 5},
            {"id": "runs", "when": {"cron": "0 0 9"}, "runs": "x", "next": now - 5},
        ],
    )
    scheduler = Scheduler(workers=1, grace=60)
    with patch.object(scheduler.pool, "submit"):
        events = scheduler.claim(reminder_file, now)

    assert [event["next"] for event in events] == [None, None, None]


def test_reminders_sent_to_their_own_urls_alone_need_no_configuration(store):
    """Without a configuration, a reminder with only its own URLs is still sent; others wait."""
    now = int(time.time())
    urls = ["json://one.example"]
    events = [
        {"id": "own", "urls": urls, "when": {"at": "2000-01-01T00:00:00"}, "next": now - 5},
        {"id": "own-later", "urls": urls, "when": {"at": "2999-01-01T00:00:00"}, "next": now + 600},
        {"id": "untagged", "urls": urls, "tag": "", "when": {"at": "2000-01-01T00:00:00"}, "next": now - 5},
        {"id": "tagged", "tag": "ops", "when": {"at": "2999-01-01T00:00:00"}, "next": now + 60},
    ]
    reminder_file = store.reminder_path("key")
    assert store.write_reminders(reminder_file, events)

    scheduler = Scheduler(workers=1, grace=60)
    with patch.object(scheduler_module, "deliver", return_value=True) as send:
        assert scheduler.scan() == now + 600
        scheduler.pool.shutdown(wait=True)

    assert [call.args[1]["id"] for call in send.call_args_list] == ["own"]
    saved = {event["id"]: event for event in store.read_reminders(reminder_file)}
    assert saved["own"]["next"] is None
    assert saved["untagged"]["next"] == now - 5


def test_scan_skips_reminders_without_a_configuration(store):
    """Reminders without a configuration wait, unchanged, until one is saved."""
    now = int(time.time())
    events = [
        {"id": "due", "when": {"at": "2000-01-01T00:00:00"}, "next": now - 5},
        {"id": "later", "when": {"at": "2999-01-01T00:00:00"}, "next": now + 600},
        {"id": "off", "enabled": False, "when": {"at": "2999-01-01T00:00:00"}, "next": now + 60},
    ]
    reminder_file = store.reminder_path("key")
    assert store.write_reminders(reminder_file, events)

    scheduler = Scheduler(workers=1, grace=60)
    with patch.object(scheduler_module, "deliver", return_value=True) as send:
        # Nothing is sent and nothing is left to wait for
        assert scheduler.scan() is None
        send.assert_not_called()
        assert store.read_reminders(reminder_file) == events

        # Saving a configuration again lets the due reminder go out
        assert store.put("key", "json://localhost", apprise.ConfigFormat.TEXT.value)
        assert scheduler.scan() == now + 600
        scheduler.pool.shutdown(wait=True)

    assert send.call_args.args[1]["id"] == "due"
    saved = {event["id"]: event for event in store.read_reminders(reminder_file)}
    assert saved["off"]["enabled"] is False


def test_claim_skips_events_past_the_grace_time(store):
    """An overdue one-off is skipped and a repeating event moves on without a backlog."""
    now = int(time.time())
    reminder_file = _save(
        store,
        "key",
        [
            {"id": "once", "when": {"at": "2000-01-01T00:00:00"}, "next": now - 7200},
            {"id": "daily", "when": {"cron": "0 0 9"}, "next": now - 7200},
            {"id": "broken", "when": {"cron": "garbage"}, "next": now - 5},
        ],
    )

    scheduler = Scheduler(workers=1, grace=3600)
    with patch.object(scheduler.pool, "submit") as submit:
        events = {event["id"]: event for event in scheduler.claim(reminder_file, now)}

    assert events["once"]["last"]["reason"] == "late"
    assert events["once"]["next"] is None
    assert events["daily"]["last"]["reason"] == "late"
    assert events["daily"]["next"] > now
    # An event whose time can no longer be worked out sends once, then stops
    assert events["broken"]["next"] is None
    assert [call.args[2]["id"] for call in submit.call_args_list] == ["broken"]


@override_settings(APPRISE_AUTH_REQUIRED=True)
@pytest.mark.parametrize(
    "record, runs",
    [
        (None, True),
        (ConfigAuthRecord(access="user", username="u", digest="d"), True),
        (ConfigAuthRecord(access="locked", username="u", digest="d"), True),
        (ConfigAuthRecord(access="public"), True),
        (ConfigAuthRecord(access="disabled"), False),
    ],
)
def test_claim_pauses_reminders_the_access_setting_forbids(store, record, runs):
    """A disabled configuration pauses its due reminders instead of sending them."""
    now = int(time.time())
    reminder_file = _save(store, "key", [{"id": "a", "when": {"at": "2000-01-01T00:00:00"}, "next": now}])
    if record is not None:
        assert store._write_auth_record("key", record)

    scheduler = Scheduler(workers=1, grace=60)
    with patch.object(scheduler.pool, "submit") as submit:
        (event,) = scheduler.claim(reminder_file, now)

    assert submit.called is runs
    if runs:
        assert event["runs"] == 1
    else:
        assert event["enabled"] is False
        assert event["last"]["reason"] == "access"
        # A paused reminder keeps its time so it can be turned back on
        assert event["next"] == now
        assert "runs" not in event


def test_access_allows_without_logins_but_not_with_a_damaged_record(store):
    """Reminders run when logins are off; a damaged access record is unknown."""
    reminder_file = _save(store, "key", [{"id": "a"}])
    assert store._write_auth_record("key", ConfigAuthRecord(access="disabled"))

    with override_settings(APPRISE_AUTH_REQUIRED=False):
        assert access_allows(reminder_file) is True

    with override_settings(APPRISE_AUTH_REQUIRED=True):
        assert access_allows(reminder_file) is False
        path, filename = store.auth_path("key")
        with open(os.path.join(path, filename), "w") as f:
            f.write("not json")

        # A damaged record might say "disabled"
        assert access_allows(reminder_file) is None


@override_settings(APPRISE_AUTH_REQUIRED=True)
def test_claim_holds_reminders_while_access_cannot_be_read(store):
    """Due reminders are neither sent nor paused until the access record reads again."""
    now = int(time.time())
    events = [{"id": "a", "when": {"at": "2000-01-01T00:00:00"}, "next": now}]
    reminder_file = _save(store, "key", events)
    path, filename = store.auth_path("key")
    with open(os.path.join(path, filename), "w") as f:
        f.write("not json")

    scheduler = Scheduler(workers=1, grace=60)
    with patch.object(scheduler.pool, "submit") as submit:
        assert scheduler.claim(reminder_file, now) is None

    submit.assert_not_called()
    assert store.read_reminders(reminder_file) == events


def test_claim_rereads_the_file(store):
    """An event removed before the claim is not sent."""
    now = int(time.time())
    reminder_file = _save(store, "key", [{"id": "a", "when": {"at": "2000-01-01T00:00:00"}, "next": now}])
    store.write_reminders(reminder_file, [])

    scheduler = Scheduler(workers=1, grace=60)
    with patch.object(scheduler.pool, "submit") as submit:
        assert scheduler.claim(reminder_file, now) == []

    submit.assert_not_called()


def test_send_records_the_result(store):
    """The result is saved on the event, unless the event was removed meanwhile."""
    reminder_file = _save(store, "key", [{"id": "a"}, {"id": "b"}])
    scheduler = Scheduler(workers=1, grace=60)

    with patch.object(scheduler_module, "deliver", return_value=False):
        scheduler.send(reminder_file, {"id": "b"})
        scheduler.send(reminder_file, {"id": "gone"})

    events = store.read_reminders(reminder_file)
    assert "last" not in events[0]
    assert events[1]["last"]["ok"] is False


def test_sleep_is_skipped_after_a_change(store):
    """A change noted during a scan is acted on without sleeping first."""
    scheduler = Scheduler(workers=1, grace=60)
    scheduler._on_signal(signal.SIGUSR1, None)
    assert scheduler.dirty is True
    assert scheduler.stopping is False

    with patch.object(scheduler_module, "wait_until") as wait:
        scheduler.sleep(None)

    wait.assert_not_called()


def test_run_refuses_a_second_scheduler(store):
    """Only one scheduler runs against a configuration directory."""
    with store.scheduler_lock():
        assert Scheduler(workers=1, grace=60).run() is False


def test_run_wakes_for_a_sooner_event(store, restore_signals):
    """A wake signal ends the sleep so a newly added sooner event is sent."""
    # Only something a year away is queued, so the scheduler sleeps on it
    far = int(time.time()) + 365 * 86400
    reminder_file = _save(store, "key", [{"id": "far", "when": {"at": "2999-01-01T00:00:00"}, "next": far}])
    sent = threading.Event()

    def add_sooner():
        # Like a web worker in another process, this thread never takes the signal itself
        _leave_signals_to_main_thread()
        time.sleep(0.5)
        with store.reminder_guard():
            events = store.read_reminders(reminder_file)
            events.append({"id": "soon", "when": {"at": "2000-01-01T00:00:00"}, "next": int(time.time()) + 1})
            store.write_reminders(reminder_file, events)

        # A web request in another process sends this after saving
        os.kill(os.getpid(), signal.SIGUSR1)
        sent.wait(10)
        os.kill(os.getpid(), signal.SIGTERM)

    def record(_schedule_file, event):
        sent.set()
        return True

    worker = threading.Thread(target=add_sooner)
    started = time.monotonic()
    with patch.object(scheduler_module, "deliver", side_effect=record) as send:
        worker.start()
        assert Scheduler(workers=1, grace=60).run() is True

    worker.join()
    assert time.monotonic() - started < 10
    assert [call.args[1]["id"] for call in send.call_args_list] == ["soon"]


def test_run_waits_quietly_when_nothing_is_queued(store, restore_signals):
    """With nothing queued the scheduler waits for a signal, then stops cleanly."""

    def stop():
        _leave_signals_to_main_thread()
        os.kill(os.getpid(), signal.SIGINT)

    threading.Timer(0.5, stop).start()
    assert Scheduler(workers=1, grace=60).run() is True


@override_settings(APPRISE_REMINDER_ENABLED=False)
def test_command_exits_when_disabled():
    """A disabled scheduler exits cleanly without starting."""
    out = io.StringIO()
    with patch("api.management.commands.scheduler.Scheduler") as scheduler:
        management.call_command("scheduler", stdout=out)

    scheduler.assert_not_called()
    assert "disabled" in out.getvalue()


@override_settings(APPRISE_STATEFUL_MODE="disabled")
def test_command_exits_without_stateful_storage():
    """Without stored configurations there is nothing to schedule."""
    with patch("api.management.commands.scheduler.Scheduler") as scheduler:
        management.call_command("scheduler", stdout=io.StringIO())

    scheduler.assert_not_called()


@override_settings(APPRISE_REMINDER_WORKERS=3, APPRISE_REMINDER_GRACE=90)
def test_command_runs_the_scheduler():
    """The command starts the scheduler with its settings."""
    with patch("api.management.commands.scheduler.Scheduler") as scheduler:
        scheduler.return_value.run.return_value = True
        management.call_command("scheduler", stdout=io.StringIO())

    scheduler.assert_called_once_with(3, 90)

    with patch("api.management.commands.scheduler.Scheduler") as scheduler:
        scheduler.return_value.run.return_value = False
        with pytest.raises(CommandError):
            management.call_command("scheduler", stdout=io.StringIO())


def test_reminder_attachments(store, tmpdir):
    """Stored files and web addresses are attached; a missing file is left out."""
    source = tmpdir.join("photo.jpg")
    source.write_binary(b"picture")
    digest, _size = store.store_reminder_file(str(source))
    event = {
        "id": "a",
        "attach": [
            {"file": digest, "filename": "photo.jpg"},
            {"file": "0" * 64, "filename": "gone.jpg"},
            {"url": "https://example.com/a.png"},
        ],
    }

    with patch.object(scheduler_module, "parse_attachments", return_value=["web"]) as fetch:
        attach = reminder_attachments(event)

    # Web addresses are downloaded only now, when the reminder is sent
    fetch.assert_called_once_with([{"url": "https://example.com/a.png"}], None)
    assert attach[0].name == "photo.jpg"
    assert attach[1:] == ["web"]
    # Stored files are never removed when an attachment is cleaned up
    assert attach[0].delete is False

    with override_settings(APPRISE_ATTACH_SIZE=0):
        with pytest.raises(AppriseAPIError):
            reminder_attachments(event)

        # Nothing to attach is fine either way
        assert reminder_attachments({"id": "b"}) == []


def test_deliver_refuses_unusable_attachments(store):
    """A reminder whose attachments cannot be used is not sent."""
    reminder_file = _save(store, "key", [{"id": "a"}])
    event = {"id": "a", "body": "There", "attach": [{"url": "http://localhost/x.png"}]}
    with patch.object(apprise.Apprise, "notify") as notify:
        assert deliver(reminder_file, event) is False

    notify.assert_not_called()


@override_settings(APPRISE_REMINDER_IDLE_DAYS=10)
def test_claim_pauses_idle_configurations(store):
    """An unused configuration has every reminder paused and is told once."""
    now = int(time.time())
    reminder_file = _save(
        store,
        "key",
        [
            {"id": "due", "when": {"at": "2000-01-01T00:00:00"}, "next": now},
            {"id": "later", "when": {"cron": "0 0 9"}, "next": now + 3600},
            {"id": "off", "enabled": False, "when": {"cron": "0 0 9"}, "next": now},
        ],
    )
    seen = store.seen_path("key")
    store.reminder_seen(reminder_file)
    os.utime(seen, (now - 11 * 86400, now - 11 * 86400))

    scheduler = Scheduler(workers=1, grace=60)

    # With the pool full, nothing is paused until the notice can be sent too
    with patch.object(scheduler, "room", return_value=0), patch.object(scheduler.pool, "submit") as submit:
        events = scheduler.claim(reminder_file, now)

    submit.assert_not_called()
    assert scheduler.backlog is True
    assert [event.get("enabled", True) for event in store.read_reminders(reminder_file)] == [True, True, False]

    with patch.object(scheduler.pool, "submit") as submit:
        events = scheduler.claim(reminder_file, now)

    assert [event["enabled"] for event in events] == [False, False, False]
    assert {event["last"]["reason"] for event in events[:2]} == {"idle"}
    # A reminder that was already off keeps its last result
    assert "last" not in events[2]
    assert submit.call_args_list[-1].args == (scheduler.send_idle_notice, reminder_file)
    assert len(submit.call_args_list) == 1

    # A recently used configuration keeps sending
    os.utime(seen, (now, now))
    assert Scheduler.idle(reminder_file, now) is False
    with override_settings(APPRISE_REMINDER_IDLE_DAYS=0):
        assert Scheduler.idle(reminder_file, now + 99 * 86400) is False

    with patch.object(store, "reminder_seen", return_value=None):
        assert Scheduler.idle(reminder_file, now + 99 * 86400) is False


@override_settings(APPRISE_REMINDER_IDLE_DAYS=10)
def test_idle_notice_names_the_days(store):
    """The idle notice says how long the configuration went unused."""
    with patch.object(scheduler_module, "deliver") as send:
        Scheduler.send_idle_notice("reminder-file")

    event = send.call_args.args[1]
    assert "10" in event["body"]
    assert event["type"] == "warning"


def test_claim_sends_nothing_when_the_file_cannot_be_saved(store):
    """A due reminder whose next time cannot be saved is not sent, and is tried again later."""
    now = int(time.time())
    reminder_file = _save(store, "key", [{"id": "a", "when": {"at": "2000-01-01T00:00:00"}, "next": now}])
    scheduler = Scheduler(workers=1, grace=60)
    with patch.object(store, "write_reminders", return_value=False), patch.object(scheduler.pool, "submit") as submit:
        assert scheduler.claim(reminder_file, now) is None
        assert scheduler.scan() >= now + scheduler_module.RECHECK

    submit.assert_not_called()
    assert store.read_reminders(reminder_file)[0]["next"] == now


def test_claim_leaves_reminders_due_while_the_pool_is_full(store):
    """Only as many reminders as the pool has room for are handed out."""
    now = int(time.time())
    reminder_file = _save(
        store, "key", [{"id": rid, "when": {"at": "2000-01-01T00:00:00"}, "next": now} for rid in "abc"]
    )
    scheduler = Scheduler(workers=1, grace=60)
    scheduler.queued = scheduler.capacity - 2
    with patch.object(scheduler.pool, "submit") as submit:
        events = {event["id"]: event for event in scheduler.claim(reminder_file, now)}

    assert [call.args[2]["id"] for call in submit.call_args_list] == ["a", "b"]
    assert scheduler.backlog is True
    assert scheduler.room() == 0
    # The one left over keeps its time for a later scan
    assert events["c"]["next"] == now
    assert "runs" not in events["c"]


def test_finished_send_frees_room(store):
    """A finished send makes room, and wakes the scheduler if the pool was full."""
    scheduler = Scheduler(workers=1, grace=60)
    scheduler.queued = scheduler.capacity
    done = Future()
    done.set_result(None)
    failed = Future()
    failed.set_exception(RuntimeError("boom"))
    with patch.object(scheduler_module.os, "kill") as kill:
        scheduler.finished(done)
        scheduler.finished(failed)

    kill.assert_called_once_with(os.getpid(), signal.SIGUSR1)
    assert scheduler.room() == 2


def test_send_records_an_unexpected_failure(store):
    """An unexpected error while sending is saved as a failed send."""
    reminder_file = _save(store, "key", [{"id": "a"}])
    scheduler = Scheduler(workers=1, grace=60)
    with patch.object(scheduler_module, "deliver", side_effect=RuntimeError("boom")):
        scheduler.send(reminder_file, {"id": "a"})

    assert store.read_reminders(reminder_file)[0]["last"]["ok"] is False

    # A result that cannot be saved is only logged
    with (
        patch.object(scheduler_module, "deliver", return_value=True),
        patch.object(store, "write_reminders", return_value=False),
    ):
        scheduler.send(reminder_file, {"id": "a"})

    assert store.read_reminders(reminder_file)[0]["last"]["ok"] is False


def test_sleep_checks_for_changes_now_and_then(store):
    """A long sleep wakes now and then, and stops once reminders have changed."""
    scheduler = Scheduler(workers=1, grace=60)
    with (
        patch.object(scheduler_module, "wait_until") as wait,
        patch.object(store, "reminders_changed", side_effect=[0, 1]),
    ):
        scheduler.sleep(None)

    assert wait.call_count == 2

    # A time due before the next check is slept on directly
    due = int(time.time()) + 5
    with patch.object(scheduler_module, "wait_until") as wait:
        scheduler.sleep(due)

    wait.assert_called_once_with(due)
