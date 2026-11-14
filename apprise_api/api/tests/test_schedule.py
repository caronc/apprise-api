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
"""When repeating reminders are due around clock changes."""

from datetime import UTC, datetime
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest

from ..schedule import _cron_keeps_clock_time, next_run, upcoming

NEW_YORK = ZoneInfo("America/New_York")


def _local(times, tz=NEW_YORK):
    """Return UNIX timestamps as local times with their UTC offsets, e.g. "11-01 01:30-0400"."""
    return [datetime.fromtimestamp(due, tz).strftime("%m-%d %H:%M%z") for due in times]


@pytest.mark.parametrize(
    "when",
    [
        {"tz": "America/New_York", "at": "2026-10-30T01:30", "every": {"unit": "days"}},
        {"tz": "America/New_York", "cron": "0 30 1 * * *"},
        {"tz": "America/New_York", "cron": "30 1 * * *", "isc": True},
    ],
)
def test_set_times_of_day_send_once_when_the_clocks_go_back(when):
    """A time in the hour the clocks repeat is sent once, the first time round."""
    times = upcoming({"when": when}, datetime(2026, 10, 31, tzinfo=UTC), 3)
    assert _local(times) == ["10-31 01:30-0400", "11-01 01:30-0400", "11-02 01:30-0500"]

    # Asked again right after that send, the next time is the next day
    after = datetime.fromtimestamp(times[1], UTC)
    assert _local([next_run({"when": when}, after)]) == ["11-02 01:30-0500"]


def test_hourly_rules_follow_real_time_through_the_repeated_hour():
    """Rules that run every hour (or more often) send in both passes of the repeated hour."""
    after = datetime(2026, 11, 1, 4, tzinfo=UTC)
    both = ["11-01 00:30-0400", "11-01 01:30-0400", "11-01 01:30-0500", "11-01 02:30-0500"]
    hourly = {"tz": "America/New_York", "at": "2026-10-31T23:30", "every": {"unit": "hours"}}
    assert _local(upcoming({"when": hourly}, after, 4)) == both
    assert _local(upcoming({"when": {"tz": "America/New_York", "cron": "0 30 * * * *"}}, after, 4)) == both

    # Every 20 minutes carries on without a gap
    every_20 = {"tz": "America/New_York", "cron": "0 */20 * * * *"}
    times = upcoming({"when": every_20}, datetime(2026, 11, 1, 5, 30, tzinfo=UTC), 4)
    assert _local(times) == ["11-01 01:40-0400", "11-01 01:00-0500", "11-01 01:20-0500", "11-01 01:40-0500"]


@pytest.mark.parametrize(
    ("when", "after", "expected"),
    [
        # Daily, and the same as a cron
        (
            {"tz": "America/New_York", "at": "2026-03-06T02:30", "every": {"unit": "days"}},
            datetime(2026, 3, 7, tzinfo=UTC),
            ["03-07 02:30-0500", "03-08 03:30-0400", "03-09 02:30-0400"],
        ),
        (
            {"tz": "America/New_York", "cron": "0 30 2 * * *"},
            datetime(2026, 3, 7, tzinfo=UTC),
            ["03-07 02:30-0500", "03-08 03:30-0400", "03-09 02:30-0400"],
        ),
        # Monthly on the day the clocks jump
        (
            {"tz": "America/New_York", "at": "2026-02-08T02:30", "every": {"unit": "months"}},
            datetime(2026, 2, 1, tzinfo=UTC),
            ["02-08 02:30-0500", "03-08 03:30-0400", "04-08 02:30-0400"],
        ),
        # A rule whose own start is a skipped time
        (
            {"tz": "America/New_York", "at": "2026-03-08T02:30", "every": {"unit": "days"}},
            datetime(2026, 3, 7, tzinfo=UTC),
            ["03-08 03:30-0400", "03-09 02:30-0400", "03-10 02:30-0400"],
        ),
    ],
)
def test_skipped_times_are_sent_when_the_clocks_jump_ahead(when, after, expected):
    """A time the clocks skip is sent at the time they jumped to, like a one-off."""
    assert _local(upcoming({"when": when}, after, 3)) == expected


def test_midnight_skipped_by_the_clocks_is_sent_at_one():
    """Where the clocks jump at midnight, a midnight reminder is sent at 01:00 that day."""
    santiago = ZoneInfo("America/Santiago")
    when = {"tz": "America/Santiago", "at": "2026-09-04T00:00", "every": {"unit": "days"}}
    times = upcoming({"when": when}, datetime(2026, 9, 4, 12, tzinfo=UTC), 3)
    assert _local(times, santiago) == ["09-05 00:00-0400", "09-06 01:00-0300", "09-07 00:00-0300"]


def test_next_time_from_inside_the_changed_hours():
    """Asked during the second pass of the repeated hour, or just after the jump."""
    second_pass = datetime(2026, 11, 1, 6, 10, tzinfo=UTC)  # 01:10 EST, the second time round
    daily = {"tz": "America/New_York", "every": {"unit": "days"}}
    # 01:50 was already due the first time round; 02:05 is still ahead
    assert _local([next_run({"when": {**daily, "at": "2026-10-30T01:50"}}, second_pass)]) == ["11-02 01:50-0500"]
    assert _local([next_run({"when": {**daily, "at": "2026-10-30T02:05"}}, second_pass)]) == ["11-01 02:05-0500"]

    # Asked at 03:40, just after the jump, a 03:20 reminder that day has passed
    after_jump = datetime(2026, 3, 8, 7, 40, tzinfo=UTC)
    assert _local([next_run({"when": {**daily, "at": "2026-03-01T03:20"}}, after_jump)]) == ["03-09 03:20-0400"]

    # Saved at 03:10, just after the jump, a 02:30 reminder is still due at 03:30 that day
    after_jump = datetime(2026, 3, 8, 7, 10, tzinfo=UTC)
    assert _local([next_run({"when": {**daily, "at": "2026-03-01T02:30"}}, after_jump)]) == ["03-08 03:30-0400"]


@pytest.mark.parametrize(
    ("when", "keeps"),
    [
        ({"cron": "0 0 9"}, True),
        ({"cron": "0 30 1,13 * * *"}, True),
        ({"cron": "30 1 * * *", "isc": True}, True),
        ({"cron": "@daily"}, True),
        ({"cron": "@WEEKLY"}, True),
        ({"cron": "0 0 * * * *"}, False),
        ({"cron": "0 0 */2 * * *"}, False),
        ({"cron": "0 0 ? * * *"}, False),
        ({"cron": "*/5 * * * *", "isc": True}, False),
        ({"cron": "@hourly"}, False),
        # A cron that leaves the hour out runs every hour
        ({"cron": "0 30"}, False),
    ],
)
def test_a_crons_hour_decides_clock_or_real_time(when, keeps):
    """A cron with set hours keeps clock time; one that runs through every hour follows real time."""
    assert _cron_keeps_clock_time(when) is keeps


def test_holiday_start_with_an_offset_is_read_in_the_reminders_timezone():
    """The time of day of a holiday's start is converted to the reminder's timezone first."""
    event = {
        "when": {"tz": "America/New_York", "holiday": {"country": "US", "name": "Christmas Day"}},
        # 14:00 UTC is 09:00 in New York in winter
        "start": "2026-01-01T14:00:00+00:00",
    }
    assert _local([next_run(event, datetime(2026, 6, 1, tzinfo=UTC))]) == ["12-25 09:00-0500"]


def test_fixed_units_stay_a_whole_interval_apart_through_half_hour_changes():
    """Hours and minutes count real time, even where the clocks move by half an hour."""
    # Lord Howe Island moves its clocks back 30 minutes on 2026-04-05
    lord_howe = ZoneInfo("Australia/Lord_Howe")
    hourly = {"when": {"tz": "Australia/Lord_Howe", "at": "2026-04-04T23:30:00", "every": {"unit": "hours"}}}
    times = upcoming(hourly, datetime(2026, 4, 4, 12, tzinfo=UTC), 6)
    assert {later - earlier for earlier, later in pairwise(times)} == {3600}
    assert _local(times[2:4], lord_howe) == ["04-05 01:30+1100", "04-05 02:00+1030"]

    # Every 90 minutes through New York's repeated hour
    every = {
        "when": {"tz": "America/New_York", "at": "2026-11-01T00:10:00", "every": {"unit": "minutes", "interval": 90}}
    }
    times = upcoming(every, datetime(2026, 11, 1, 3, tzinfo=UTC), 4)
    assert {later - earlier for earlier, later in pairwise(times)} == {5400}


def test_fixed_units_keep_their_month_and_day_limits():
    """An hourly rule limited to some days only sends on those days, on the hour it counts from."""
    when = {"tz": "Etc/UTC", "at": "2026-01-05T09:30:00", "every": {"unit": "hours", "interval": 5, "days": ["Mon"]}}
    times = upcoming({"when": when}, datetime(2026, 1, 5, 20, tzinfo=UTC), 3)
    # Nothing is left on the first Monday after 20:00; the next Monday's times
    # are a whole number of 5 hours (160, 165 and 170) after the start
    assert _local(times, ZoneInfo("Etc/UTC")) == ["01-12 01:30+0000", "01-12 06:30+0000", "01-12 11:30+0000"]


def test_holidays_held_years_apart_are_still_found():
    """A holiday that skips several years (an observed day) is found, not treated as finished."""
    # Christmas Day (observed) in the US: 2027-12-24, then not again until 2032
    event = {
        "when": {"tz": "America/New_York", "holiday": {"country": "US", "name": "Christmas Day (observed)"}},
        "start": "2021-12-24T09:00:00",
    }
    assert _local([next_run(event, datetime(2027, 12, 25, tzinfo=UTC))]) == ["12-24 09:00-0500"]
    assert datetime.fromtimestamp(next_run(event, datetime(2027, 12, 25, tzinfo=UTC)), UTC).year == 2032
