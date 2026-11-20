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
"""Reminder diagnostics: what they hold, how they are explained, and why sends fail."""

from datetime import UTC, datetime
import io
import json
import sys
import types
from unittest.mock import patch
import zoneinfo

import apprise
from django.core import management
from django.core.management.base import CommandError
from django.test import override_settings
import pytest

from .. import diagnostics as diagnostics_module, scheduler as scheduler_module
from ..auth import AuthStorageError
from ..diagnostics import REASON_SERVICES, check_diagnostics, reminder_diagnostics, tz_database, versions
from ..schedule import log_time, next_run, upcoming
from ..scheduler import Scheduler
from ..utils import AppriseConfigCache, AppriseStoreMode

# When the diagnostics below were copied.
COPIED = 1795000000

# A reminder sent every day at 09:00 UTC.
DAILY = {"tz": "Etc/UTC", "cron": "0 0 9"}


@pytest.fixture
def store(tmpdir):
    """Point the scheduler and the diagnostics at an empty configuration directory."""
    cache = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    with patch.object(scheduler_module, "ConfigCache", cache), patch.object(diagnostics_module, "ConfigCache", cache):
        yield cache


def _utc(due):
    """Write a UNIX timestamp the way diagnostics do."""
    return datetime.fromtimestamp(due, UTC).isoformat(timespec="seconds")


def _bundle(**reminder):
    """Build the diagnostics of a healthy daily reminder, changed by ``reminder``.

    - Times are given as UNIX timestamps and written out the way diagnostics write them.
    """
    after = datetime.fromtimestamp(COPIED, UTC)
    event = {"id": "abc", "enabled": True, "when": DAILY, "start": None, "runs": 0, "tag": None, **reminder}
    if "next" not in reminder:
        event["next"] = next_run(event, after)

    coming = upcoming(event, after, 5) if "when" not in reminder else None
    shown = {**event, "next": _utc(event["next"]) if event["next"] is not None else None}
    if "last" in event:
        shown["last"] = {**event["last"], "at": _utc(event["last"]["at"])}

    return {
        "format": 1,
        "copied": _utc(COPIED),
        "server": {
            "time": _utc(COPIED),
            "tz": "Etc/UTC",
            "scheduler_running": True,
            "grace": 3600,
            "idle_days": 0,
            "urls_allowed": True,
            "attach_allowed": True,
            "auth_required": True,
        },
        "config": {"saved": True, "access": None, "last_used": _utc(COPIED)},
        "versions": versions(),
        "reminder": shown,
        "upcoming": None if coming is None else [_utc(due) for due in coming],
    }


def _findings(bundle):
    """Return the findings listed for some diagnostics."""
    report = check_diagnostics(json.dumps(bundle))
    return [line.removeprefix("  - ") for line in report[report.index("Findings") + 1 :]]


def test_tz_database_version_comes_from_the_system_or_the_package(tmp_path):
    """The timezone database version is read from the system files, else the tzdata package."""
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "tzdata.zi").write_text("# not a version line\n")
    (tmp_path / "system").mkdir()
    (tmp_path / "system" / "tzdata.zi").write_text("# version 2099z\n")
    roots = (str(tmp_path / "missing"), str(tmp_path / "other"), str(tmp_path / "system"))
    try:
        tz_database.cache_clear()
        with patch.object(zoneinfo, "TZPATH", roots):
            assert tz_database() == "2099z"

        tz_database.cache_clear()
        with (
            patch.object(zoneinfo, "TZPATH", ()),
            patch.dict(sys.modules, {"tzdata": types.SimpleNamespace(IANA_VERSION="2098y")}),
        ):
            assert tz_database() == "2098y"

        tz_database.cache_clear()
        with patch.object(zoneinfo, "TZPATH", ()), patch.dict(sys.modules, {"tzdata": None}):
            assert tz_database() is None

    finally:
        tz_database.cache_clear()


def test_times_are_written_in_utc(store):
    """Every time reads as a UTC date and time; the server's own clock is given once, beside it."""
    event = {
        "id": "a",
        "when": DAILY,
        "next": COPIED + 3600,
        "skip": [COPIED + 86400],
        "last": {"at": COPIED - 10, "ok": True},
        "created": COPIED - 60,
        # A time damaged on disk is left as it is, and so is one not set
        "updated": 10**20,
        "occurrence": None,
    }
    with patch.object(store, "reminder_seen", return_value=COPIED - 5.0):
        found = reminder_diagnostics(store.reminder_path("key"), event, COPIED)

    assert found["copied"] == _utc(COPIED) == "2026-11-18T11:06:40+00:00"
    assert found["server"]["time"] == datetime.fromtimestamp(COPIED).astimezone().isoformat(timespec="seconds")
    reminder = found["reminder"]
    assert (reminder["next"], reminder["skip"], reminder["last"]) == (
        _utc(COPIED + 3600),
        [_utc(COPIED + 86400)],
        {"at": _utc(COPIED - 10), "ok": True},
    )
    assert (reminder["created"], reminder["updated"], reminder["occurrence"]) == (_utc(COPIED - 60), 10**20, None)
    assert found["upcoming"][0] == "2026-11-19T09:00:00+00:00"
    assert found["config"]["last_used"] == _utc(COPIED - 5)


def test_a_damaged_reminder_still_gives_diagnostics(store):
    """A reminder whose time cannot be worked out, or whose access cannot be read, still gives diagnostics."""
    reminder_file = store.reminder_path("key")
    with (
        patch.object(store, "reminder_access", side_effect=AuthStorageError("damaged")),
        patch.object(store, "reminder_seen", return_value=None),
    ):
        found = reminder_diagnostics(reminder_file, {"id": "a", "when": {"tz": "Not/AZone", "cron": "0 0 9"}}, COPIED)

    assert found["upcoming"] is None
    assert found["config"] == {"saved": False, "access": "unreadable", "last_used": None}


def test_redacted_diagnostics_still_show_where_it_sends(store):
    """Redacted, the name, tag and addresses are hidden, but how many there are, and of what, still shows."""
    event = {
        "id": "a",
        "name": "Standup",
        "when": DAILY,
        "tag": "family",
        "urls": ["json://user:****@private.example", "not a url"],
        "attach": [{"url": "https://private.example/a.png"}, {"file": "abc", "filename": "a.png", "size": 5}, "x"],
    }
    found = reminder_diagnostics(store.reminder_path("key"), event, COPIED, privacy=True)
    reminder = found["reminder"]
    assert (reminder["name"], reminder["tag"]) == ("(redacted)", "(redacted)")
    assert reminder["urls"] == ["json://", "(redacted)"]
    assert reminder["attach"] == [{"url": "https://"}, {"size": 5}]

    # An empty tag picks untagged services, so it is kept
    found = reminder_diagnostics(store.reminder_path("key"), {**event, "tag": ""}, COPIED, privacy=True)
    assert found["reminder"]["tag"] == ""

    # Without URLs or attachments there is nothing more to hide
    found = reminder_diagnostics(store.reminder_path("key"), {"id": "a", "when": DAILY}, COPIED, privacy=True)
    assert "urls" not in found["reminder"] and "attach" not in found["reminder"]

    # Not redacted, everything given is kept
    assert reminder_diagnostics(store.reminder_path("key"), event, COPIED)["reminder"]["tag"] == "family"


def test_healthy_diagnostics_find_nothing_wrong():
    """A reminder the server would send lists its times and finds nothing to fix."""
    bundle = _bundle(name="Standup")
    report = check_diagnostics(json.dumps(bundle))
    nxt = next_run(bundle["reminder"] | {"next": None}, datetime.fromtimestamp(COPIED, UTC))
    assert report[0] == "Reminder   abc (Standup)"
    assert report[1] == (
        f"Copied     {log_time(COPIED, 'Etc/UTC', 'Etc/UTC')} (server timezone Etc/UTC, its clock read {_utc(COPIED)})"
    )
    assert report[3] == "Sends to   the configuration's untagged services"
    assert report[4] == "Limits     runs 0; 0 skipped times"
    assert report[5] == f"Next       {log_time(nxt, 'Etc/UTC', 'Etc/UTC')} (as saved)"
    assert report[6].startswith("Upcoming   ") and report[6].endswith("(worked out here)")
    assert _findings(bundle) == ["Nothing found that would stop it being sent."]

    # Pasted as the API answers, or as copied into a ticket
    fenced = "```json\n" + json.dumps({"diagnostics": bundle}, indent=2) + "\n```\n"
    assert check_diagnostics(fenced) == report
    assert check_diagnostics("```json\n" + json.dumps(bundle)) == report


def test_where_it_sends_is_said():
    """The report says whether it sends through the configuration, its own URLs, or both."""
    for reminder, where in (
        ({"tag": "family"}, "the configuration's services tagged family"),
        (
            {"tag": "family", "urls": ["json://"]},
            "the configuration's services tagged family and 1 URLs of its own (json://)",
        ),
        ({"urls": ["json://", "mailto://"]}, "2 URLs of its own (json://, mailto://)"),
    ):
        assert f"Sends to   {where}" in check_diagnostics(json.dumps(_bundle(**reminder)))


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("not json", "not valid JSON"),
        ("[" * 100000, "not valid JSON"),
        ("```json", "not valid JSON"),
        (json.dumps({"copied": _utc(COPIED)}), "not reminder diagnostics"),
        (json.dumps({"copied": _utc(COPIED), "reminder": {"when": "daily"}}), "not reminder diagnostics"),
        # Copy times must be dates and times with their offset
        (json.dumps({"copied": COPIED, "reminder": {"when": DAILY}}), "not reminder diagnostics"),
        (json.dumps({"copied": "2026-11-18T20:53:20", "reminder": {"when": DAILY}}), "not reminder diagnostics"),
        (json.dumps({"copied": "yesterday", "reminder": {"when": DAILY}}), "not reminder diagnostics"),
    ],
)
def test_text_that_is_not_diagnostics_is_refused(text, error):
    """Anything but reminder diagnostics is refused with why."""
    with pytest.raises(ValueError, match=error):
        check_diagnostics(text)


def test_what_stops_a_server_sending_is_found():
    """Each server-side reason a reminder is not sent is named."""
    bundle = _bundle(urls=["json://"], attach=[{"size": 5}], tag="")
    bundle["server"].update(scheduler_running=False, urls_allowed=False, attach_allowed=False, idle_days=30)
    bundle["config"].update(saved=False, access="disabled", last_used=_utc(COPIED - 31 * 86400))
    bundle["versions"]["apprise"] = "0.0"
    assert _findings(bundle) == [
        "No scheduler was running, so no reminder was being sent.",
        "Its configuration is disabled, so its reminders are not sent.",
        "It sends through a configuration, but none is saved.",
        "The server no longer allows URLs in reminders, so its own URLs are left out.",
        "Attachments are turned off on the server, so its sends fail.",
        "Its configuration went unused for over 30 days, so its reminders are paused.",
        f"Versions differ, so times worked out here may too: apprise {apprise.__version__} here, 0.0 on the server.",
    ]

    # An access record that cannot be read, and parts left out
    bundle = _bundle()
    bundle["config"]["access"] = "unreadable"
    bundle["server"] = "damaged"
    assert _findings(bundle) == [
        "No scheduler was running, so no reminder was being sent.",
        "Its configuration's access could not be read, so its reminders were held.",
    ]


def test_a_server_clock_out_of_step_with_its_timezone_is_found():
    """A server whose clock reads a different offset than its TZ setting gives is named."""
    bundle = _bundle()
    bundle["server"]["time"] = "2026-11-18T06:06:40-05:00"
    assert _findings(bundle) == [
        "The server's clock read 2026-11-18T06:06:40-05:00, which does not match its timezone Etc/UTC; "
        "check how TZ is set."
    ]

    # A clock or timezone that cannot be read is left alone
    for time, tz in (("noon", "Etc/UTC"), (_utc(COPIED), "Not/AZone")):
        bundle["server"].update(time=time, tz=tz)
        assert "check how TZ is set" not in " ".join(_findings(bundle))


def test_why_a_reminder_has_nothing_left_is_explained():
    """A reminder with no next time says whether its count, end date or schedule ran out."""
    for reminder, why in (
        ({"count": 3, "runs": 3}, "it was due the 3 times it was meant to be"),
        ({"until": "2000-01-01"}, "its end date has passed"),
        ({"when": {"tz": "Etc/UTC", "at": "2000-01-01T09:00:00"}, "runs": 1}, "it was sent once, and only once"),
        ({}, "its schedule has no times left"),
    ):
        assert f"It has nothing left to send: {why}." in _findings(_bundle(next=None, **reminder))


def test_late_paused_and_mismatched_times_are_found():
    """An overdue, paused or differently worked out reminder is named, with what happens next."""
    assert _findings(_bundle(next=COPIED - 60)) == [
        "It was due 60 seconds before this was copied and was not sent yet."
    ]
    assert _findings(_bundle(next=COPIED - 7200)) == [
        "It was due 7200 seconds before this was copied and was not sent yet; being past the grace time, "
        "it is skipped as late."
    ]
    assert _findings(_bundle(enabled=False)) == ["It is paused."]

    expected = next_run({"when": DAILY}, datetime.fromtimestamp(COPIED, UTC))
    bundle = _bundle(next=expected + 60)
    bundle["upcoming"] = [_utc(expected + 60)]
    report = check_diagnostics(json.dumps(bundle))
    assert report[7] == f"           {log_time(expected + 60, 'Etc/UTC', 'Etc/UTC')} (on the server)"
    assert _findings(bundle) == [
        "The upcoming times worked out here differ from the server's; compare the versions.",
        f"Its saved next time differs from the {log_time(expected, 'Etc/UTC', 'Etc/UTC')} worked out here; "
        "compare the versions.",
    ]

    # A time that cannot be worked out here
    findings = _findings(_bundle(when={"tz": "Not/AZone", "cron": "0 0 9"}, next=COPIED + 60))
    assert findings[0].startswith("Its time cannot be worked out: ZoneInfoNotFoundError")


def test_times_that_cannot_be_read_are_left_out():
    """A damaged time reads as none, and a damaged skipped time is passed over."""
    bundle = _bundle(last={"at": COPIED - 10, "ok": True})
    bundle["reminder"].update(skip=["noon", _utc(COPIED + 60)], occurrence="noon")
    bundle["reminder"]["last"]["at"] = "noon"
    report = check_diagnostics(json.dumps(bundle))
    assert "Limits     runs 0; 1 skipped times" in report
    assert "Last       none sent" in report

    bundle["reminder"]["skip"] = "damaged"
    assert "Limits     runs 0; 0 skipped times" in check_diagnostics(json.dumps(bundle))


def test_the_last_send_is_explained():
    """The last send reads as sent, or as not sent with why."""
    sent = check_diagnostics(json.dumps(_bundle(last={"at": COPIED - 10, "ok": True})))
    assert f"Last       {log_time(COPIED - 10, 'Etc/UTC', 'Etc/UTC')} sent" in sent

    assert _findings(_bundle(last={"at": COPIED - 10, "ok": False, "reason": "config"})) == [
        "Its last send was not made: it sends through a configuration, but none was found."
    ]
    assert _findings(_bundle(last={"at": COPIED - 10, "ok": False})) == [
        f"Its last send was not made: {REASON_SERVICES}."
    ]

    # A damaged reason, even one that is not text, reads the same way
    assert _findings(_bundle(last={"at": COPIED - 10, "ok": False, "reason": {"late": 1}})) == [
        f"Its last send was not made: {REASON_SERVICES}."
    ]


def test_control_characters_are_escaped():
    """Pasted text cannot send control characters, such as colour changes, to the terminal."""
    report = check_diagnostics(json.dumps(_bundle(id="abc\x1b[31m", name="new\nline")))
    assert report[0] == "Reminder   abc\\x1b[31m (new\\nline)"
    assert not any(char in line for line in report for char in "\x1b\n")


def test_command_explains_a_file_or_pasted_text(tmp_path):
    """reminder_check reads a file, or pasted text, and refuses anything else."""
    text = json.dumps(_bundle())
    saved = tmp_path / "diagnostics.json"
    saved.write_text(text)

    out = io.StringIO()
    management.call_command("reminder_check", str(saved), stdout=out)
    assert "Nothing found that would stop it being sent." in out.getvalue()

    out = io.StringIO()
    with patch.object(sys, "stdin", io.StringIO(text)):
        management.call_command("reminder_check", stdout=out)
    assert out.getvalue().startswith("Reminder   abc")

    with pytest.raises(CommandError, match="Could not read"):
        management.call_command("reminder_check", str(tmp_path / "missing.json"), stdout=io.StringIO())

    saved.write_text("not json")
    with pytest.raises(CommandError, match="not valid JSON"):
        management.call_command("reminder_check", str(saved), stdout=io.StringIO())


def _last(store, key, event, config=True):
    """Send one reminder through the scheduler and return how its last send was recorded."""
    if config:
        assert store.put(key, "json://localhost", "text")

    reminder_file = store.reminder_path(key)
    assert store.write_reminders(reminder_file, [event])
    scheduler = Scheduler(workers=1, grace=60)
    try:
        scheduler.send(reminder_file, event)

    finally:
        scheduler.pool.shutdown(wait=True)

    return store.read_reminders(reminder_file)[0]["last"]


def test_a_failed_send_records_why(store):
    """A send that could not be made records why, so the reminder card and diagnostics can say."""
    event = {"id": "a", "body": "Hi", "when": DAILY, "next": COPIED}
    assert _last(store, "none", event, config=False)["reason"] == "config"

    with override_settings(APPRISE_REMINDER_URLS_MAX=0):
        assert _last(store, "urls", {**event, "urls": ["json://localhost"]}, config=False)["reason"] == "urls"

    with override_settings(APPRISE_ATTACH_SIZE=0):
        assert _last(store, "attach", {**event, "attach": [{"url": "https://example.com/a.png"}]})["reason"] == "attach"

    with patch.object(apprise.Apprise, "notify", side_effect=apprise.exception.AppriseException("down")):
        assert _last(store, "error", event)["reason"] == "error"

    with patch.object(scheduler_module, "deliver", side_effect=RuntimeError("broken")):
        assert _last(store, "broken", event)["reason"] == "error"

    # Services that did not take it are not a reason of their own; the log says which
    with patch.object(apprise.Apprise, "notify", return_value=False):
        last = _last(store, "refused", event)

    assert last["ok"] is False
    assert "reason" not in last
