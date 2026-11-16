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
"""Reminder diagnostics, to trace a reminder that was not sent as expected.

- ``reminder_diagnostics()`` gathers what decides when and where one
  reminder is sent: its schedule, tag and URLs (their passwords and tokens
  hidden), how its last send went, the server's clock, settings and
  scheduler, and the versions of everything that works out its times. Its
  title, body and Config ID are left out. Times are written out in UTC as
  ISO 8601 dates and times, so they read at a glance; the server's local
  time is given beside the copy time to compare its clock with.
- Redacted, its name and tag are hidden too, and each URL keeps only its
  scheme.
- ``check_diagnostics()`` reads them back on any machine and explains them,
  working the times out again with the scheduler's own code.
"""

from datetime import UTC, datetime
from functools import cache
import json
import os
import platform
from typing import Any
import zoneinfo

import apprise
import dateblock
from django.conf import settings
import holidays

from .auth import AuthStorageError
from .schedule import (
    DAMAGED_ERRORS,
    SCHEDULE_ERRORS,
    find_zone,
    is_one_off,
    log_time,
    needs_config,
    next_run,
    until_passed,
    upcoming,
)
from .utils import ConfigCache, url_scheme

# Raised to 2 and up if the layout ever changes, so older copies still read.
DIAGNOSTICS_FORMAT = 1

# The reminder fields copied: what decides when and where it is sent, and
# how it went.
DIAGNOSTICS_FIELDS = (
    "id",
    "name",
    "enabled",
    "when",
    "start",
    "until",
    "count",
    "skip",
    "runs",
    "next",
    "last",
    "series",
    "occurrence",
    "owner",
    "created",
    "updated",
    "tag",
    "urls",
    "attach",
)

# Reminder fields saved as a UNIX timestamp, written out as a date and time.
# ``skip`` (a list of them) and ``last.at`` are too.
TIME_FIELDS = ("next", "occurrence", "created", "updated")

# What stands in for a redacted name, tag or URL.
REDACTED = "(redacted)"

# How many upcoming times are listed.
DIAGNOSTICS_UPCOMING = 5

# What each ``last.reason`` means.
REASONS = {
    "late": "it was overdue by more than the grace time, so it was skipped",
    "idle": "its configuration went unused for too long, so its reminders were paused",
    "urls": "it only sends to its own URLs, which the server no longer allows",
    "config": "it sends through a configuration, but none was found",
    "attach": "its attachments could not be used",
    "error": "Apprise could not send it at all",
}

# How a failed send reads without a reason: some services did not take it.
REASON_SERVICES = "one or more services did not take it; the server's log says which"


@cache
def tz_database() -> str | None:
    """Return the version of the timezone database Python reads, e.g. ``2026a``.

    - The system's timezone files are read first, then the ``tzdata``
      package; ``None`` when neither gives a version.
    """
    for root in zoneinfo.TZPATH:
        try:
            with open(os.path.join(root, "tzdata.zi"), encoding="utf-8") as f:
                # It starts with a line like "# version 2026a".
                first = f.readline()

        except OSError:
            continue

        if first.startswith("# version "):
            return first[len("# version ") :].strip()

    # The package is only needed, and often only installed, without them.
    try:
        import tzdata

    except ImportError:
        return None

    return tzdata.IANA_VERSION


def versions() -> dict:
    """Return the versions of everything that works out a reminder's times."""
    return {
        "apprise-api": settings.APP_VERSION,
        "apprise": apprise.__version__,
        "dateblock": dateblock.__version__,
        "holidays": holidays.__version__,
        "python": platform.python_version(),
        "tzdata": tz_database(),
    }


def _written(due: Any) -> Any:
    """Write a UNIX timestamp as an ISO 8601 date and time in UTC.

    - e.g. ``2026-11-17T14:00:00+00:00``.
    - Anything else, such as ``None`` or a damaged value, is left as it is.
    """
    if not isinstance(due, int):
        return due

    try:
        return datetime.fromtimestamp(due, UTC).isoformat(timespec="seconds")

    except (OverflowError, OSError, ValueError):
        return due


def _read_time(value: Any) -> int | None:
    """Read a time written by ``_written()`` back as a UNIX timestamp.

    - Returns ``None`` for anything else, including a time without an offset.
    """
    if not isinstance(value, str):
        return None

    try:
        moment = datetime.fromisoformat(value)

    except ValueError:
        return None

    return int(moment.timestamp()) if moment.tzinfo else None


def _scheme(url: Any) -> str:
    """Return just the scheme of an address, e.g. ``json://``, or REDACTED when it has none."""
    # Pasted or damaged reminders may hold anything here, not only text.
    return (isinstance(url, str) and url_scheme(url)) or REDACTED


def _redact(reminder: dict) -> None:
    """Hide what could name a person or service, but not where it sends.

    - A name or tag is replaced; an empty or missing tag is kept, as it
      picks the configuration's untagged services.
    - Each URL keeps only its scheme, and each attachment only its size or
      its address's scheme, so how many there are, and of what, still shows.
    """
    for name in ("name", "tag"):
        if reminder.get(name):
            reminder[name] = REDACTED

    if isinstance(reminder.get("urls"), list):
        reminder["urls"] = [_scheme(url) for url in reminder["urls"]]

    if isinstance(reminder.get("attach"), list):
        reminder["attach"] = [
            {"url": _scheme(entry["url"])} if entry.get("url") else {"size": entry.get("size")}
            for entry in reminder["attach"]
            if isinstance(entry, dict)
        ]


def reminder_diagnostics(reminder_file: str, event: dict, now: int, privacy: bool = False) -> dict:
    """Gather one reminder's diagnostics as of ``now``, a UNIX timestamp.

    - ``event``: the reminder with its URLs' passwords and tokens already
      hidden.
    - ``privacy``: hide its name and tag too, and all but each URL's scheme.
    - Every time is in UTC; ``server.time`` gives the copy time on the
      server's own clock.
    """
    reminder = {name: event[name] for name in DIAGNOSTICS_FIELDS if name in event}
    for name in TIME_FIELDS:
        if name in reminder:
            reminder[name] = _written(reminder[name])

    if isinstance(reminder.get("skip"), list):
        reminder["skip"] = [_written(due) for due in reminder["skip"]]

    if isinstance(reminder.get("last"), dict) and "at" in reminder["last"]:
        reminder["last"] = {**reminder["last"], "at": _written(reminder["last"]["at"])}

    if privacy:
        _redact(reminder)

    try:
        coming = [_written(due) for due in upcoming(event, datetime.fromtimestamp(now, UTC), DIAGNOSTICS_UPCOMING)]

    except DAMAGED_ERRORS:
        # A reminder damaged on disk; its fields above still show why.
        coming = None

    try:
        # None means there is no access record, so the administrator manages it.
        access = ConfigCache.reminder_access(reminder_file)

    except AuthStorageError:
        access = "unreadable"

    seen = ConfigCache.reminder_seen(reminder_file)
    return {
        "format": DIAGNOSTICS_FORMAT,
        "copied": _written(now),
        "server": {
            # The copy time on the server's own clock, the one its log lines use.
            "time": datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
            "tz": settings.TIME_ZONE,
            "scheduler_running": ConfigCache.scheduler_running(),
            "grace": settings.APPRISE_REMINDER_GRACE,
            "idle_days": settings.APPRISE_REMINDER_IDLE_DAYS,
            "workers": settings.APPRISE_REMINDER_WORKERS,
            "urls_allowed": settings.APPRISE_REMINDER_URLS_MAX > 0,
            "attach_allowed": settings.APPRISE_ATTACH_SIZE > 0,
            "auth_required": settings.APPRISE_AUTH_REQUIRED,
        },
        "config": {
            "saved": ConfigCache.reminder_has_config(reminder_file),
            "access": access,
            "last_used": None if seen is None else _written(int(seen)),
        },
        "versions": versions(),
        "reminder": reminder,
        "upcoming": coming,
    }


def _read(text: str) -> dict:
    """Read copied diagnostics, whole as the API answers them or just their inside.

    - The ```json fence they are copied in, for a ticket, may be left on.
    - Their times are read back as UNIX timestamps; one that cannot be read
      becomes ``None``.
    - Raises ``ValueError`` when ``text`` is not reminder diagnostics.
    """
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]

    try:
        bundle = json.loads("\n".join(lines))

    # Very deeply nested JSON runs out of stack.
    except (ValueError, RecursionError):
        raise ValueError("The diagnostics are not valid JSON") from None

    if isinstance(bundle, dict) and isinstance(bundle.get("diagnostics"), dict):
        bundle = bundle["diagnostics"]

    if not (
        isinstance(bundle, dict)
        and _read_time(bundle.get("copied")) is not None
        and isinstance(bundle.get("reminder"), dict)
        and isinstance(bundle["reminder"].get("when"), dict)
    ):
        raise ValueError("These are not reminder diagnostics")

    bundle["copied"] = _read_time(bundle["copied"])
    # A part left out (or damaged) reads as empty.
    for part in ("server", "config", "versions"):
        if not isinstance(bundle.get(part), dict):
            bundle[part] = {}

    event = bundle["reminder"]
    for name in TIME_FIELDS:
        if name in event:
            event[name] = _read_time(event[name])

    skips = event.get("skip") if isinstance(event.get("skip"), list) else []
    event["skip"] = [due for due in map(_read_time, skips) if due is not None]
    if isinstance(event.get("last"), dict):
        event["last"]["at"] = _read_time(event["last"].get("at"))

    if isinstance(bundle.get("upcoming"), list):
        bundle["upcoming"] = [_read_time(due) for due in bundle["upcoming"]]

    bundle["config"]["last_used"] = _read_time(bundle["config"].get("last_used"))
    return bundle


def _printable(text: str) -> str:
    """Return ``text`` with each control character written as an escape, e.g. ``\\x1b``."""
    # A character's repr() is its escape, between the quotes.
    return "".join(char if char.isprintable() else repr(char)[1:-1] for char in text)


def _nothing_left(event: dict, ended: bool) -> str:
    """Say why a reminder has no time left to send."""
    count, runs = event.get("count"), event.get("runs") or 0
    if isinstance(count, int) and isinstance(runs, int) and count and runs >= count:
        return f"it was due the {count} times it was meant to be"

    if ended:
        return "its end date has passed"

    if is_one_off(event["when"]) and runs:
        return "it was sent once, and only once"

    return "its schedule has no times left"


def _sends_to(event: dict) -> str:
    """Say where a reminder sends: its configuration, its own URLs, or both."""
    urls = event.get("urls") if isinstance(event.get("urls"), list) else []
    places = []
    if needs_config(event):
        tag = event.get("tag")
        places.append(f"the configuration's services tagged {tag}" if tag else "the configuration's untagged services")

    if urls:
        places.append(f"{len(urls)} URLs of its own ({', '.join(map(str, urls))})")

    return " and ".join(places)


def check_diagnostics(text: str) -> list[str]:
    """Explain copied reminder diagnostics, returning the report's lines.

    - Times read on the copying server's clock, like its logs, with the
      reminder's own local time beside them when that differs.
    - Control characters from the pasted text are shown as escapes.
    - Raises ``ValueError`` when ``text`` is not reminder diagnostics.
    """
    bundle = _read(text)
    copied = bundle["copied"]
    event = bundle["reminder"]
    server = bundle["server"]
    config = bundle["config"]
    zone = event["when"].get("tz")
    clock = server.get("tz")

    def show(due: object) -> str:
        """Show a time on the copying server's clock."""
        return log_time(due, zone, clock)

    # What may stop it being sent, or explain how it was.
    findings = []
    skips = event.get("skip") if isinstance(event.get("skip"), list) else []
    limits = ", ".join(
        f"{name} {event[name]}" for name in ("start", "until", "count", "runs") if event.get(name) is not None
    )
    name = f" ({event['name']})" if event.get("name") else ""
    report = [
        f"Reminder   {event.get('id')}{name}",
        f"Copied     {show(copied)} (server timezone {clock}, its clock read {server.get('time')})",
        f"Schedule   {json.dumps(event['when'], sort_keys=True)}",
        f"Sends to   {_sends_to(event)}",
        f"Limits     {limits or 'none'}; {len(skips)} skipped times",
        f"Next       {show(event.get('next'))} (as saved)",
    ]

    # Worked out again here, from the moment it was copied.
    after = datetime.fromtimestamp(copied, UTC)
    expected = here = None
    ended = worked = False
    try:
        expected = next_run(event, after)
        here = upcoming(event, after, DIAGNOSTICS_UPCOMING)
        ended = until_passed(event, after)
        worked = True

    except DAMAGED_ERRORS as e:
        findings.append(f"Its time cannot be worked out: {e!r}")

    if here is not None:
        report.append(f"Upcoming   {'; '.join(show(due) for due in here) or 'none'} (worked out here)")
        theirs = bundle.get("upcoming")
        if isinstance(theirs, list) and theirs != here:
            report.append(f"           {'; '.join(show(due) for due in theirs) or 'none'} (on the server)")
            findings.append("The upcoming times worked out here differ from the server's; compare the versions.")

    last = event.get("last")
    if isinstance(last, dict):
        # A reason that is not one of ours, even one that is not text, reads
        # as services that did not take it.
        reason = last.get("reason")
        why = REASONS.get(reason, REASON_SERVICES) if isinstance(reason, str) else REASON_SERVICES
        report.append(f"Last       {show(last.get('at'))} {'sent' if last.get('ok') else 'not sent: ' + why}")
        if not last.get("ok"):
            findings.append(f"Its last send was not made: {why}.")

    # Whether the server would send it at all.
    if not server.get("scheduler_running"):
        findings.append("No scheduler was running, so no reminder was being sent.")

    # Its own clock and its TZ setting disagreeing points at a timezone set up wrong.
    local = server.get("time")
    if isinstance(local, str) and isinstance(clock, str):
        try:
            offsets = (
                datetime.fromisoformat(local).utcoffset(),
                datetime.fromtimestamp(copied, find_zone(clock)).utcoffset(),
            )

        except SCHEDULE_ERRORS:
            offsets = None

        if offsets and offsets[0] != offsets[1]:
            findings.append(
                f"The server's clock read {local}, which does not match its timezone {clock}; check how TZ is set."
            )

    if event.get("enabled", True) is False:
        findings.append("It is paused.")

    due = event.get("next")
    grace = server.get("grace")
    if not isinstance(due, int):
        findings.append(f"It has nothing left to send: {_nothing_left(event, ended)}.")

    elif due <= copied:
        late = copied - due
        skipped = isinstance(grace, int) and late > grace
        findings.append(
            f"It was due {late} seconds before this was copied and was not sent yet"
            + ("; being past the grace time, it is skipped as late." if skipped else ".")
        )

    elif worked and expected != due:
        findings.append(f"Its saved next time differs from the {show(expected)} worked out here; compare the versions.")

    if server.get("auth_required") and config.get("access") == "disabled":
        findings.append("Its configuration is disabled, so its reminders are not sent.")

    if config.get("access") == "unreadable":
        findings.append("Its configuration's access could not be read, so its reminders were held.")

    if needs_config(event) and config.get("saved") is False:
        findings.append("It sends through a configuration, but none is saved.")

    if event.get("urls") and server.get("urls_allowed") is False:
        findings.append("The server no longer allows URLs in reminders, so its own URLs are left out.")

    if event.get("attach") and server.get("attach_allowed") is False:
        findings.append("Attachments are turned off on the server, so its sends fail.")

    # A configuration unused this long has its reminders paused when next due.
    idle_days, used = server.get("idle_days"), config.get("last_used")
    if isinstance(idle_days, int) and idle_days > 0 and isinstance(used, int) and copied - used > idle_days * 86400:
        findings.append(f"Its configuration went unused for over {idle_days} days, so its reminders are paused.")

    # Times can differ between versions, such as a new timezone rule or holiday.
    theirs = bundle["versions"]
    report.append("Versions   " + ", ".join(f"{name} {version}" for name, version in theirs.items()))
    changed = [
        f"{name} {version} here, {theirs.get(name)} on the server"
        for name, version in versions().items()
        if theirs.get(name) != version
    ]
    if changed:
        findings.append("Versions differ, so times worked out here may too: " + "; ".join(changed) + ".")

    report.append("Findings")
    report.extend(f"  - {finding}" for finding in findings or ["Nothing found that would stop it being sent."])
    # Pasted text is shown in a terminal, so control characters (such as
    # ones that change its colours or title) are written out as escapes.
    return [_printable(line) for line in report]
