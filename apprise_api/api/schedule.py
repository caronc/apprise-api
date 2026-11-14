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

from collections.abc import Callable
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

# How many years of holidays the calendar and holiday lists offer.
HOLIDAY_YEARS = 5

# How many years ahead to look for a holiday's next date. Some are only held
# when a date falls on a weekend, which can skip years; the calendar repeats
# its weekdays every 28 years.
HOLIDAY_SEARCH_YEARS = 28

# The units an ``every`` rule can repeat in.
EVERY_UNITS = ("seconds", "minutes", "hours", "days", "weeks", "months", "years")

# Units of a fixed length, counted in seconds.
FIXED_UNITS = {"seconds": 1, "minutes": 60, "hours": 3600}

# How many candidate times to try before deciding an ``every`` rule never matches.
EVERY_SEARCH_MAX = 500

# How many upcoming times are compared to find how often a reminder repeats.
GAP_RUNS = 10

# Cron shorthands that send at a set time of day (unlike @hourly).
CRON_CLOCK_SHORTHANDS = frozenset({"@yearly", "@annually", "@monthly", "@weekly", "@daily", "@midnight"})

# The longest timezone name looked up. Real names are far shorter, and a very
# long one can overflow Python's own lookup.
ZONE_NAME_MAX = 64


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


def find_zone(name: Any) -> ZoneInfo:
    """Return the timezone called ``name``, or the server's own when it is empty.

    - Raises ``ValueError`` for a name too long to be a real timezone.
    """
    name = name or settings.TIME_ZONE
    if isinstance(name, str) and len(name) > ZONE_NAME_MAX:
        raise ValueError("Timezone name is too long")

    return ZoneInfo(name)


def local_moment(text: str, tz: ZoneInfo) -> datetime:
    """Read a date and time, placing it in ``tz`` when it has no zone.

    - A time the clocks skip is read with the offset from before the jump
      (02:30 becomes 03:30), and a time that happens twice is the first one.
      This is the rule calendars follow:
      https://www.rfc-editor.org/rfc/rfc5545#section-3.3.5
    """
    moment = parse_moment(text)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=tz)


@cache
def _known_countries() -> frozenset[str]:
    """Return every country code the holidays package knows, aliases included."""
    return frozenset(holidays.list_supported_countries())


def holiday_country_known(country: str) -> bool:
    """Return whether the holidays package knows a country code."""
    return country in _known_countries()


def _country_holidays(country: str, **kwargs: Any) -> holidays.HolidayBase:
    """Load a country's holidays table.

    - Raises ``NotImplementedError`` for a code the holidays package does not
      know. It looks codes up by name inside its own module, so a word such
      as ``PUBLIC`` would otherwise find something that is not a country.
    """
    if not holiday_country_known(country):
        raise NotImplementedError(f"Unknown holiday country: {country}")

    return holidays.country_holidays(country, **kwargs)


# Room for many reminders' holidays over the years searched, so the
# calendar and scheduler do not keep loading the same tables again.
@lru_cache(maxsize=1024)
def holiday_dates(
    country: str, region: str | None, year: int, language: str | None = None
) -> tuple[tuple[date, tuple[str, ...]], ...]:
    """Return a year's holidays as ``(date, names)`` pairs in date order.

    - Names are in ``language``, or else the country's own language. Fixing
      the language keeps a saved name matching whatever locale the server
      runs in.
    - Raises ``NotImplementedError`` for an unknown country or region.
    """
    table = _country_holidays(country, subdiv=region, years=year)
    language = language or table.default_language
    if language:
        # Load the table again with its names in the chosen language.
        table = _country_holidays(country, subdiv=region, years=year, language=language)

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
        for alias, region in _country_holidays(code).subdivisions_aliases.items():
            # Several aliases can share a region; keep the first.
            names.setdefault(region, alias)

        # e.g. {"code": "CA", "regions": [{"code": "ON", "name": "Ontario"}, ...]}
        countries.append({"code": code, "regions": [{"code": region, "name": names.get(region)} for region in regions]})

    return countries


def holiday_language(country: str, language: str) -> str | None:
    """Return the country's holiday language closest to ``language``, if any."""
    # Browsers write "en-US"; the holidays package writes "en_US".
    wanted = language.replace("-", "_").lower()
    supported = _country_holidays(country).supported_languages
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
    # Look years ahead, since some holidays are not held every year.
    for year in range(first, first + HOLIDAY_SEARCH_YEARS):
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
    due = _holiday_due(rule, tz, datetime.fromtimestamp(now.timestamp() - drift, tz), at)
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
    # Seconds, minutes and hours follow real time, so any second the limits
    # allow is a candidate; whether it is a whole number of intervals after
    # the start is checked separately.
    if rule["unit"] in FIXED_UNITS:
        # dateblock reads a bare "*" for seconds as second 0, so every second is "*/1".
        return f"*/1 * * * {months} {days}"

    fields = {
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
    """Return how many whole calendar units ``moment`` is after ``start``."""
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
    """Return when the ``index``-th calendar unit after ``start`` begins."""
    # Each unit begins at midnight on its first day.
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


def _second_before(moment: datetime) -> datetime:
    """Return the moment one real second earlier, in the same timezone.

    - ``moment - timedelta(seconds=1)`` counts on the wall clock, so in the
      hour the clocks go back it can land an hour too early.
    """
    return datetime.fromtimestamp(moment.timestamp() - 1, moment.tzinfo)


def _every_due(start: datetime, rule: dict, tz: ZoneInfo, after: datetime) -> int | None:
    """Return the first time after ``after`` that an ``every`` rule is due.

    - ``start``: when the rule starts counting, in ``tz``.
    - dateblock finds the next time the rule's cron matches. When that time
      is between intervals, the search jumps to the next interval and looks
      again.
    """
    # e.g. 2 with "weeks" means every second week.
    interval = rule.get("interval") or 1
    cron = _every_cron(start, rule)
    # The start itself is the first time, when its limits allow it.
    moment = max(after, _second_before(start))
    for _ in range(EVERY_SEARCH_MAX):
        # The next time the cron matches, worked out without waiting for it.
        moment = dateblock(cron, block=False, ref=moment.astimezone(tz))
        if rule["unit"] in FIXED_UNITS:
            # Real time: due only a whole number of intervals after the start,
            # whatever the clocks did in between.
            step = FIXED_UNITS[rule["unit"]] * interval
            behind = (int(moment.timestamp()) - int(start.timestamp())) % step
            if not behind:
                return int(moment.timestamp())

            # Jump to just before the next interval and look again.
            moment = datetime.fromtimestamp(moment.timestamp() + step - behind - 1, tz)
            continue

        index = _every_index(start, moment, rule["unit"])
        if index % interval == 0:
            # It falls in a unit the interval sends on, e.g. week 0, 2, 4...
            return int(moment.timestamp())

        # Jump to just before the next unit the interval sends on and look again.
        moment = _second_before(_every_block(start, index - index % interval + interval, rule["unit"], tz))

    # Its limits never line up with its interval, e.g. only in months it skips.
    return None


def _cron_due(when: dict, after: datetime) -> int:
    """Return the first time after ``after`` that a ``cron`` rule matches.

    - dateblock reads the cron in the timezone of the time it is given.
    """
    return int(dateblock(when["cron"], block=False, ref=after, isc=bool(when.get("isc"))).timestamp())


def _cron_keeps_clock_time(when: dict) -> bool:
    """Return whether a ``cron`` rule sends at set times of day.

    - A cron whose hour field starts with ``*`` (or ``?``) runs through every
      hour, so it follows real time when the clocks change, the same as a
      classic cron; any other cron sends at set times of day.
    """
    fields = when["cron"].split()
    if fields and fields[0].startswith("@"):
        # Shorthands such as @daily name a time of day; @hourly does not.
        return fields[0].lower() in CRON_CLOCK_SHORTHANDS

    # The hour is the third field (the second in the 5-field form); one
    # left out is "*".
    index = 1 if when.get("isc") else 2
    hour = fields[index] if len(fields) > index else "*"
    return hour[:1] not in {"*", "?"}


def _repeated_hour_end(local: datetime) -> datetime:
    """Return the first wall-clock time after the repeated hour ``local`` is in.

    - ``local``: a time in the second pass of the hour the clocks went back.
    - The answer is written in UTC, the way ``_wall_clock_due()`` reads times.
    """
    tz = local.tzinfo
    wall = local.replace(tzinfo=None)
    # The clocks went back by this many seconds, so the repeat ends within it.
    low, high = 0, int((local.replace(fold=0).utcoffset() - local.utcoffset()).total_seconds())
    # The first second ahead that happens only once; a repeated one has two offsets.
    while low < high:
        middle = (low + high) // 2
        moment = wall + timedelta(seconds=middle)
        if moment.replace(tzinfo=tz, fold=0).utcoffset() != moment.replace(tzinfo=tz, fold=1).utcoffset():
            low = middle + 1

        else:
            high = middle

    return (wall + timedelta(seconds=low)).replace(tzinfo=UTC)


def _wall_clock_due(due: Callable[[datetime], int | None], tz: ZoneInfo, after: datetime) -> int | None:
    """Return the first time after ``after`` that a rule read on the wall clock is due.

    - ``due``: finds the rule's next time after a moment, with both written
      as UTC so that every local time exists exactly once.
    - Each local time found is then placed in ``tz`` the way a one-off is: a
      time the clocks skip is sent at the time they jumped to (02:30 becomes
      03:30), and a time that happens twice is sent once, the first time.
    """
    local = after.astimezone(tz)
    # The wall clock, written as UTC.
    wall = local.replace(tzinfo=UTC)
    if local.fold:
        # In the second pass of a repeated hour, every time in it was already
        # due the first time round, so look from where it ends.
        wall = _repeated_hour_end(local) - timedelta(seconds=1)

    else:
        # Just after the clocks jump ahead, a skipped time is due at the time
        # they jumped to, so look back by the jump to find it.
        jump = local.utcoffset() - (after - timedelta(hours=3)).astimezone(tz).utcoffset()
        wall -= max(jump, timedelta(0))

    found = due(wall)
    if found is None:
        return None

    moment = datetime.fromtimestamp(found, UTC)
    if moment.replace(tzinfo=tz).timestamp() > after.timestamp():
        return int(moment.replace(tzinfo=tz).timestamp())

    # Found before ``after`` (a time just before the jump that was not
    # skipped), so look on from ``after`` itself; anything later is after it.
    found = due(max(moment, local.replace(tzinfo=UTC)))
    return None if found is None else int(datetime.fromtimestamp(found, UTC).replace(tzinfo=tz).timestamp())


def _repeat_due(when: dict, tz: ZoneInfo, after: datetime, at: time) -> int | None:
    """Return the first time after ``after`` that a repeating ``when`` matches.

    - ``at``: the local time of day a holiday is sent; other rules ignore it.
    - Rules that repeat by seconds, minutes or hours, and crons that run
      every hour, follow real time. Every other rule is due once per local
      time; see ``_wall_clock_due()``.
    """
    if "holiday" in when:
        # A holiday is placed on the local clock already.
        return _holiday_due(when["holiday"], tz, after, at)

    if "every" in when:
        rule = when["every"]
        start = local_moment(when["at"], tz).astimezone(tz)
        if rule["unit"] in FIXED_UNITS:
            # Counted in real seconds from the start.
            return _every_due(start, rule, tz, after)

        # Days and longer: the same time of day, read on the wall clock.
        start = start.replace(tzinfo=UTC)
        return _wall_clock_due(lambda wall: _every_due(start, rule, UTC, wall), tz, after)

    if not _cron_keeps_clock_time(when):
        # Runs through every hour, so it follows real time.
        return _cron_due(when, after.astimezone(tz))

    # Set times of day, read on the wall clock.
    return _wall_clock_due(lambda wall: _cron_due(when, wall), tz, after)


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

    tz = find_zone(when.get("tz"))
    # Drift moves every time later (or earlier when negative), so times are
    # looked for that much earlier (or later).
    drift = when.get("drift") or 0
    if is_one_off(when):
        if runs:
            # A one-off that was already sent has nothing left.
            return None

        # A one-off stays due even once its time has passed, so the
        # scheduler can decide whether it is still worth sending late.
        due = int(local_moment(when["at"], tz).timestamp()) + drift

    elif any(kind in when for kind in ("every", "cron", "holiday")):
        # A repeating reminder.
        start = event.get("start")
        # A holiday is sent at the start's time of day, or at midnight without one.
        at = local_moment(start, tz).astimezone(tz).time() if start is not None else time()
        # Look for the schedule's own times (before drift) that send after
        # ``after`` once the drift is added.
        # Counted in real seconds, so a clock change cannot shift it.
        search = datetime.fromtimestamp(after.timestamp() - drift, UTC)
        if start is not None:
            # The start holds back the schedule's own times, not the drifted
            # ones, so a holiday on its start date with a drift of -9 days is
            # sent 9 days before it. A start already passed changes nothing.
            begins = local_moment(start, tz)
            if "holiday" in when:
                # A holiday only uses the start's year (in its own timezone);
                # the holiday sets the day.
                begins = begins.astimezone(tz).replace(month=1, day=1, hour=0, minute=0, second=0)

            search = max(search, _second_before(begins))

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
    if until is not None and due > local_moment(until, tz).timestamp():
        return None

    return due


def is_one_off(when: dict) -> bool:
    """Return whether a ``when`` is sent only once."""
    return "at" in when and "every" not in when


def needs_config(event: dict) -> bool:
    """Return whether a reminder sends through its configuration.

    - One with its own URLs and no tag sends to those alone, the same as
      /notify; an empty tag ("") also sends to the configuration's
      untagged URLs.
    """
    return not event.get("urls") or event.get("tag") is not None


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

    tz = find_zone(event["when"].get("tz"))
    return local_moment(until, tz) <= after


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
