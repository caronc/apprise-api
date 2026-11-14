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
"""Work out when a reminder is due.

An event's ``when`` holds a timezone and one of:

- ``at``: a single local date and time, e.g. ``2026-12-25T09:00:00``. Add
  ``every`` to repeat from that time, e.g.
  ``{"interval": 2, "unit": "weeks", "days": ["Mon", "Wed"]}``:

  - ``unit``: one of ``EVERY_UNITS``; ``interval`` (default 1) counts them.
  - ``months`` (1 to 12) limits it to those months, for every unit but years.
  - ``days`` (``Sun`` to ``Sat``) limits it to those weekdays, for seconds,
    minutes, hours, days and weeks. Weeks without days use the start date's weekday.
- ``cron``: a dateblock cron, e.g. ``0 0 9 * * Mon-Fri`` (add ``isc`` to
  use the 5-field crontab layout instead).
- ``holiday``: a public holiday every year it falls, e.g.
  ``{"country": "CA", "region": "ON", "name": "Christmas Day"}``.
  ``region`` (a province, state or other part of the country) is optional.
  Only the year and time of day of ``start`` are used: it is sent at that
  time (midnight without a start), from that year on. The holiday sets the
  month and day.

``drift`` (optional, in seconds, from dateblock's ``DRIFT_MIN`` to ``DRIFT_MAX``)
is added to every time the ``when`` works out to, e.g. 90 sends a 09:00 reminder
at 09:01:30, and -604800 sends it a week early.

An optional ``start`` (a local date and time like ``at``) holds back a
repeating reminder's own times, before ``drift`` is added, so a holiday on its
start date with a negative drift is still sent early. ``until`` stops it after
that time, ``count`` ends it after that many times, and ``skip`` lists times
(UNIX timestamps) it is not sent at.

Dates and times are read as ``YYYY-MM-DD`` followed by an optional ``hh``,
``hh:mm`` or ``hh:mm:ss``, joined by a space or ``T``. A missing time is
midnight, and an ISO offset such as ``+00:00`` may follow.
"""

from datetime import UTC, date, datetime, time, timedelta
from functools import cache, lru_cache
from itertools import pairwise
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateblock import dateblock
from django.conf import settings
import holidays

from .auth import Authentication

# Reminders are kept but never sent on a configuration with one of these
# access settings.
PAUSED_ACCESS = frozenset({Authentication.ACCESS_DISABLED})

# Errors raised by a reminder whose time cannot be worked out.
# NotImplementedError comes from an unknown holiday country or region.
SCHEDULE_ERRORS = (SyntaxError, ValueError, TypeError, OverflowError, ZoneInfoNotFoundError, NotImplementedError)

# How many years ahead to look for a holiday's next date.
HOLIDAY_YEARS = 5

# The units an ``every`` rule can repeat in.
EVERY_UNITS = ("seconds", "minutes", "hours", "days", "weeks", "months", "years")

# Units of a fixed length, counted in seconds.
FIXED_UNITS = {"seconds": 1, "minutes": 60, "hours": 3600}

# How many candidate times to try before deciding an ``every`` rule never matches.
EVERY_SEARCH_MAX = 500

# How many upcoming times are compared to find how often a reminder repeats.
GAP_RUNS = 10


# A date with an optional hour, minute and second, e.g. "2026-12-25 09:30".
LOCAL_TIME_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})(?:[ T](?P<hour>\d{1,2})(?::(?P<minute>\d{2})(?::(?P<second>\d{2}))?)?)?$"
)


# A time of day on its own, e.g. "09:30" or "09:30:15".
TIME_OF_DAY_RE = re.compile(r"^(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?$")


def parse_time_of_day(text: Any) -> time | None:
    """Read a time of day given on its own, as ``hh:mm`` or ``hh:mm:ss``.

    - Returns ``None`` for anything else, such as a full date and time.
    - Raises ``ValueError`` for a time that does not exist, e.g. ``25:00``.
    """
    match = TIME_OF_DAY_RE.match(text.strip()) if isinstance(text, str) else None
    if match is None:
        return None

    return time(int(match["hour"]), int(match["minute"]), int(match["second"] or 0))


def parse_moment(text: str) -> datetime:
    """Read a date and time; a missing time is midnight.

    - Takes ``YYYY-MM-DD`` with an optional ``hh``, ``hh:mm`` or ``hh:mm:ss``,
      or any ISO date and time, such as one with an offset.
    - Raises ``TypeError`` or ``ValueError`` when it cannot be read.
    """
    if not isinstance(text, str):
        raise TypeError("A date and time must be text")

    match = LOCAL_TIME_RE.match(text.strip())
    if match is None:
        # Not our short form, so let Python read it as a full ISO time.
        return datetime.fromisoformat(text.strip())

    # Any part of the time left out counts as zero.
    return datetime.combine(
        date.fromisoformat(match["date"]),
        time(int(match["hour"] or 0), int(match["minute"] or 0), int(match["second"] or 0)),
    )


def format_moment(text: str) -> str:
    """Return a date and time in the one form reminders store, to the second."""
    return parse_moment(text).isoformat(timespec="seconds")


def _moment(text: str, tz: ZoneInfo) -> datetime:
    """Read a date and time, placing it in ``tz`` when it has no zone."""
    moment = parse_moment(text)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=tz)


@lru_cache(maxsize=128)
def holiday_dates(
    country: str, region: str | None, year: int, language: str | None = None
) -> tuple[tuple[date, tuple[str, ...]], ...]:
    """Return a year's holidays as ``(date, names)`` pairs in date order.

    - Names are in ``language``, or else the country's own language. Fixing
      the language keeps a saved name matching whatever locale the server
      runs in.
    - Raises ``NotImplementedError`` for an unknown country or region.
    """
    table = holidays.country_holidays(country, subdiv=region, years=year)
    language = language or table.default_language
    if language:
        # Load the table again with its names in the chosen language.
        table = holidays.country_holidays(country, subdiv=region, years=year, language=language)

    # A day can hold more than one holiday, so each date keeps a list of names.
    return tuple((day, tuple(table.get_list(day))) for day in sorted(table))


@cache
def holiday_countries() -> list[dict]:
    """Return every country with holidays, and its regions.

    Each region has a ``code`` and, when one is known, a ``name``.
    """
    countries = []
    # Aliases are left out so each country is listed once.
    for code, regions in sorted(holidays.list_supported_countries(include_aliases=False).items()):
        names = {}
        for alias, region in holidays.country_holidays(code).subdivisions_aliases.items():
            # Several aliases can share a region; keep the first.
            names.setdefault(region, alias)

        # e.g. {"code": "CA", "regions": [{"code": "ON", "name": "Ontario"}, ...]}
        countries.append({"code": code, "regions": [{"code": region, "name": names.get(region)} for region in regions]})

    return countries


def holiday_language(country: str, language: str) -> str | None:
    """Return the country's holiday language closest to ``language``, if any."""
    # Browsers write "en-US"; the holidays package writes "en_US".
    wanted = language.replace("-", "_").lower()
    supported = holidays.country_holidays(country).supported_languages
    # The same language and country first, e.g. fr_CA for fr-CA.
    exact = [code for code in supported if code.lower() == wanted]
    # Otherwise any variant of the same language, e.g. en_US for en.
    close = [code for code in supported if code.split("_")[0].lower() == wanted.split("_")[0]]
    # The best match, or None when the country has nothing close.
    return (exact + close + [None])[0]


def _holiday_due(rule: dict, tz: ZoneInfo, after: datetime, at: time) -> int | None:
    """Return the first time after ``after`` that a holiday rule is due.

    - ``at``: the local time of day it is sent.
    """
    # Start with the year ``after`` falls in, in the reminder's own timezone.
    first = after.astimezone(tz).year
    # Look a few years ahead, since some holidays are not held every year.
    for year in range(first, first + HOLIDAY_YEARS):
        for day, names in holiday_dates(rule["country"], rule.get("region"), year):
            due = datetime.combine(day, at, tz)
            if rule["name"] in names and due > after:
                return int(due.timestamp())

    # The holiday is not held in the years looked at.
    return None


def holiday_start(rule: dict, tz: ZoneInfo, at: time, drift: int = 0) -> str:
    """Return the start to store for a holiday given only a time of day.

    - It is the holiday's next date to send from now, at ``at``.
    - Today's date is used when the holiday is not held in the years looked at.
    """
    now = datetime.now(tz)
    # The drift moves the send, so look for the holiday that far the other way.
    due = _holiday_due(rule, tz, now - timedelta(seconds=drift), at)
    day = datetime.fromtimestamp(due, tz).date() if due is not None else now.date()
    return datetime.combine(day, at).isoformat(timespec="seconds")


def _every_cron(start: datetime, rule: dict) -> str:
    """Build the dateblock cron for every time an ``every`` rule could fall on.

    The cron keeps the parts of ``start`` finer than the unit and applies the
    month and weekday limits; the interval is counted separately.
    """
    # No months or days chosen means any month or day ("*").
    months = ",".join(str(month) for month in rule.get("months") or ()) or "*"
    days = ",".join(rule.get("days") or ()) or "*"
    if rule["unit"] == "weeks" and days == "*":
        # Without chosen days, a weekly reminder repeats on the start date's weekday.
        days = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[start.weekday()]

    # The 6 fields are: second, minute, hour, day of the month, month, weekday.
    # e.g. a daily reminder that starts at 09:30:00 becomes "0 30 9 * * *".
    fields = {
        # dateblock reads a bare "*" for seconds as second 0, so every second is "*/1".
        "seconds": f"*/1 * * * {months} {days}",
        "minutes": f"{start.second} * * * {months} {days}",
        "hours": f"{start.second} {start.minute} * * {months} {days}",
        "days": f"{start.second} {start.minute} {start.hour} * {months} {days}",
        "weeks": f"{start.second} {start.minute} {start.hour} * {months} {days}",
        "months": f"{start.second} {start.minute} {start.hour} {start.day} {months} *",
        "years": f"{start.second} {start.minute} {start.hour} {start.day} {start.month} *",
    }
    return fields[rule["unit"]]


def _week_start(day: date) -> date:
    """Return the Sunday that starts the week ``day`` is in."""
    return day - timedelta(days=(day.weekday() + 1) % 7)


def _every_index(start: datetime, moment: datetime, unit: str) -> int:
    """Return how many whole units ``moment`` is after ``start``."""
    # Seconds, minutes and hours: plain division of the seconds between them.
    if unit in FIXED_UNITS:
        return int(moment.timestamp() - start.timestamp()) // FIXED_UNITS[unit]

    if unit == "days":
        return (moment.date() - start.date()).days

    if unit == "weeks":
        # Weeks are counted Sunday to Saturday, not seven days from the start.
        return (_week_start(moment.date()) - _week_start(start.date())).days // 7

    if unit == "months":
        return (moment.year - start.year) * 12 + moment.month - start.month

    # Years.
    return moment.year - start.year


def _every_block(start: datetime, index: int, unit: str, tz: ZoneInfo) -> datetime:
    """Return when the ``index``-th unit after ``start`` begins."""
    # Seconds, minutes and hours are counted from the start itself.
    if unit in FIXED_UNITS:
        return datetime.fromtimestamp(start.timestamp() + index * FIXED_UNITS[unit], tz)

    # Longer units begin at midnight on their first day.
    if unit == "days":
        return datetime.combine(start.date() + timedelta(days=index), time(), tz)

    if unit == "weeks":
        return datetime.combine(_week_start(start.date()) + timedelta(weeks=index), time(), tz)

    if unit == "months":
        # Carry whole years over, e.g. 14 months after November is January.
        years, month = divmod(start.month - 1 + index, 12)
        return datetime(start.year + years, month + 1, 1, tzinfo=tz)

    # Years.
    return datetime(start.year + index, 1, 1, tzinfo=tz)


def _every_due(when: dict, tz: ZoneInfo, after: datetime) -> int | None:
    """Return the first time after ``after`` that an ``every`` rule is due.

    dateblock finds the next time the rule's cron matches. When that time is
    between intervals, the search jumps to the next interval and looks again.
    """
    start = _moment(when["at"], tz).astimezone(tz)
    rule = when["every"]
    # e.g. 2 with "weeks" means every second week.
    interval = rule.get("interval") or 1
    cron = _every_cron(start, rule)
    # The start itself is the first time, when its limits allow it.
    moment = max(after, start - timedelta(seconds=1))
    for _ in range(EVERY_SEARCH_MAX):
        # The next time the cron matches, worked out without waiting for it.
        moment = dateblock(cron, block=False, ref=moment.astimezone(tz))
        index = _every_index(start, moment, rule["unit"])
        if index % interval == 0:
            # It falls in a unit the interval sends on, e.g. week 0, 2, 4...
            return int(moment.timestamp())

        # Jump to just before the next unit the interval sends on and look again.
        moment = _every_block(start, index - index % interval + interval, rule["unit"], tz) - timedelta(seconds=1)

    # Its limits never line up with its interval, e.g. only in months it skips.
    return None


def _repeat_due(when: dict, tz: ZoneInfo, after: datetime, at: time) -> int | None:
    """Return the first time after ``after`` that a repeating ``when`` matches.

    - ``at``: the local time of day a holiday is sent; other rules ignore it.
    """
    if "holiday" in when:
        return _holiday_due(when["holiday"], tz, after, at)

    if "every" in when:
        return _every_due(when, tz, after)

    # dateblock reads the cron in the timezone of the time it is given.
    return int(dateblock(when["cron"], block=False, ref=after.astimezone(tz), isc=bool(when.get("isc"))).timestamp())


def next_run(event: dict, after: datetime) -> int | None:
    """Return the next time an event is due, as a UNIX timestamp.

    - ``after``: an aware time; a repeating event is due only after it.
    - Returns ``None`` when the event has nothing left to send.
    - Raises ``SyntaxError`` or ``ValueError`` for an unusable ``when``.
    """
    when = event.get("when") or {}
    runs = event.get("runs") or 0
    count = event.get("count")
    if count and runs >= count:
        # It has already been sent as many times as it was meant to be.
        return None

    tz = ZoneInfo(when.get("tz") or settings.TIME_ZONE)
    # Drift moves every time later (or earlier when negative), so times are
    # looked for that much earlier (or later).
    drift = when.get("drift") or 0
    if "at" in when and "every" not in when:
        if runs:
            # A one-off that was already sent has nothing left.
            return None

        # A one-off stays due even once its time has passed, so the
        # scheduler can decide whether it is still worth sending late.
        due = int(_moment(when["at"], tz).timestamp()) + drift

    elif any(kind in when for kind in ("every", "cron", "holiday")):
        # A repeating reminder.
        start = event.get("start")
        # A holiday is sent at the start's time of day, or at midnight without one.
        at = _moment(start, tz).time() if start is not None else time()
        # Look for the schedule's own times (before drift) that send after
        # ``after`` once the drift is added.
        search = after - timedelta(seconds=drift)
        if start is not None:
            # The start holds back the schedule's own times, not the drifted
            # ones, so a holiday on its start date with a drift of -9 days is
            # sent 9 days before it. A start already passed changes nothing.
            begins = _moment(start, tz)
            if "holiday" in when:
                # A holiday only uses the start's year; the holiday sets the day.
                begins = begins.replace(month=1, day=1, hour=0, minute=0, second=0)

            search = max(search, begins - timedelta(seconds=1))

        due = _repeat_due(when, tz, search, at)
        # Skipped times are passed over without counting as a run.
        skip = set(event.get("skip") or ())
        while due is not None and due + drift in skip:
            due = _repeat_due(when, tz, datetime.fromtimestamp(due, UTC), at)

        if due is None:
            # Its schedule has no times left.
            return None

        due += drift

    else:
        raise ValueError("No schedule time was given")

    # Nothing is sent after the end date.
    until = event.get("until")
    if until is not None and due > _moment(until, tz).timestamp():
        return None

    return due


def is_one_off(when: dict) -> bool:
    """Return whether a ``when`` is sent only once."""
    return "at" in when and "every" not in when


def drop_past_skips(event: dict, now: float) -> None:
    """Forget skipped times that have already passed."""
    if event.get("skip"):
        event["skip"] = [due for due in event["skip"] if due > now]


def until_passed(event: dict, after: datetime) -> bool:
    """Return whether a reminder's end date had gone by at ``after``."""
    until = event.get("until")
    if until is None:
        # No end date, so it never passes.
        return False

    tz = ZoneInfo(event["when"].get("tz") or settings.TIME_ZONE)
    return _moment(until, tz) <= after


def upcoming(event: dict, after: datetime, limit: int, before: int | None = None) -> list[int]:
    """Return up to ``limit`` of the next times a reminder is due.

    - ``before``: stop at the first time on or after this UNIX timestamp.
    """
    runs = event.get("runs") or 0
    times = []
    while len(times) < limit:
        # Pretend each time found was sent, so ``count`` is honoured.
        due = next_run({**event, "runs": runs}, after)
        if due is None or (before is not None and due >= before):
            break

        times.append(due)
        runs += 1
        # Look for the next one after this time.
        after = datetime.fromtimestamp(due, UTC)

    return times


def shortest_gap(event: dict, after: datetime) -> int | None:
    """Return the fewest seconds between a reminder's next few times.

    - Skipped times, ``until`` and ``count`` are left out, so they cannot
      hide how often the reminder repeats.
    - Returns ``None`` for a reminder sent only once.
    """
    # A copy of the reminder without its start, end, count or skipped times.
    times = upcoming({**event, "skip": [], "start": None, "until": None, "count": None, "runs": 0}, after, GAP_RUNS)
    # Compare each time with the one after it and keep the smallest gap.
    return min((later - earlier for earlier, later in pairwise(times)), default=None)
