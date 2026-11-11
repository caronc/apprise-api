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
"""Test the reminder API."""

import base64
from datetime import UTC, date, datetime
import hashlib
from json import dumps, loads
import os
import re
import time
from unittest.mock import patch
from zoneinfo import ZoneInfo

import apprise
from dateblock import DRIFT_MAX, DRIFT_MIN
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http.request import RequestDataTooBig
from django.test import SimpleTestCase
from django.test.client import BOUNDARY, encode_multipart
from django.test.utils import override_settings

from ..schedule import HOLIDAY_YEARS
from ..utils import ConfigCache
from ..views import REMINDER_DAYS
from .helpers import notify_result


def _basic(username, password):
    """Build one Basic Auth header value."""
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


_MASTER_TOKEN = base64.b64encode(b"master:pass").decode()
_MASTER = {"authorization": _basic("master", "pass")}
_USER = {"authorization": _basic("alice", "secret")}

# A reminder that repeats every day at 9:00 in UTC.
_DAILY = {"name": "Standup", "body": "Time to meet", "when": {"tz": "Etc/UTC", "cron": "0 0 9"}}

# Christmas Day in Canada at 9:00.
_CHRISTMAS = {"country": "CA", "name": "Christmas Day"}


class ReminderTestCase(SimpleTestCase):
    """Shared helpers for the reminder tests."""

    key = "reminder-api"

    def setUp(self):
        """Save a configuration to hold reminders."""
        self.assertTrue(ConfigCache.put(self.key, "json://localhost", "text"))

    def tearDown(self):
        """Remove the configuration and everything beside it."""
        ConfigCache.clear(self.key)
        ConfigCache.clear_auth(self.key)
        ConfigCache.set_limits(self.key, {})
        with ConfigCache.reminder_guard():
            ConfigCache.write_reminders(ConfigCache.reminder_path(self.key), [])

    def send(self, method, path, payload=None, **kwargs):
        """Send JSON to a reminder route and return the response."""
        return getattr(self.client, method)(
            path,
            data=dumps(payload) if payload is not None else None,
            content_type="application/json",
            **kwargs,
        )

    def add(self, payload=None, **kwargs):
        """Add a reminder, returning the response."""
        return self.send("post", f"/reminder/{self.key}/", _DAILY if payload is None else payload, **kwargs)

    def stored(self):
        """Return the reminders saved on disk."""
        return ConfigCache.read_reminders(ConfigCache.reminder_path(self.key))


class ReminderCrudTests(ReminderTestCase):
    """Add, list, change and remove reminders."""

    def test_reminder_round_trip(self):
        """A reminder is added, listed, read, changed and removed."""
        response = self.add()
        self.assertEqual(response.status_code, 200)
        added = loads(response.content)["reminder"]
        self.assertEqual(len(added["id"]), 32)
        self.assertEqual(added["owner"], "admin")
        self.assertEqual(added["name"], "Standup")
        self.assertEqual(added["type"], "info")
        self.assertGreater(added["next"], time.time())
        self.assertEqual(self.stored(), [added])

        listed = loads(self.client.get(f"/reminder/{self.key}/").content)["reminders"]
        self.assertEqual(listed, [added])

        path = f"/reminder/{self.key}/{added['id']}/"
        self.assertEqual(loads(self.client.get(path).content)["reminder"], added)

        # A partial change keeps the other fields and the next due time
        response = self.send("patch", path, {"name": "Daily standup"})
        self.assertEqual(response.status_code, 200)
        changed = loads(response.content)["reminder"]
        self.assertEqual(changed["name"], "Daily standup")
        self.assertEqual(changed["body"], "Time to meet")
        self.assertEqual(changed["next"], added["next"])

        # A full replacement needs every field again
        response = self.send("put", path, {"name": "Only a name"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(loads(response.content)["field"], "body")

        # Removing answers the same way /del does: a 204 with no body
        response = self.send("delete", path, headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertEqual(self.stored(), [])

        for method in ("get", "patch", "delete"):
            response = self.send(method, path, {} if method == "patch" else None)
            self.assertEqual(response.status_code, 404)

    def test_header_and_current_key_routes(self):
        """The Config ID header and the '@' route reach the same reminders."""
        response = self.send("post", "/reminder/", _DAILY, headers={"X-Apprise-Config-ID": self.key})
        self.assertEqual(response.status_code, 200)
        rid = loads(response.content)["reminder"]["id"]

        response = self.client.get(f"/reminder/@/{rid}/", headers={"X-Apprise-Config-ID": self.key})
        self.assertEqual(loads(response.content)["reminder"]["id"], rid)

        # The browser's remembered key is used when there is no header
        self.client.cookies["key"] = self.key
        response = self.client.get("/reminder/@/")
        self.assertEqual([event["id"] for event in loads(response.content)["reminders"]], [rid])

        # A bare route needs the header, and a bad header is refused
        del self.client.cookies["key"]
        self.assertEqual(self.client.get("/reminder/").status_code, 400)
        response = self.client.get("/reminder/", headers={"X-Apprise-Config-ID": "bad key!"})
        self.assertEqual(response.status_code, 400)

    def test_changing_the_time_starts_over(self):
        """A new time resets the run count, and turning it back on plans it again."""
        rid = loads(self.add().content)["reminder"]["id"]
        path = f"/reminder/{self.key}/{rid}/"
        with ConfigCache.reminder_guard():
            events = self.stored()
            events[0].update({"runs": 4, "enabled": False, "next": 1})
            ConfigCache.write_reminders(ConfigCache.reminder_path(self.key), events)

        # Turning it back on works out a new next time
        event = loads(self.send("patch", path, {"enabled": True}).content)["reminder"]
        self.assertGreater(event["next"], time.time())
        self.assertEqual(event["runs"], 4)

        # Changing only the end keeps the run count
        event = loads(self.send("patch", path, {"until": "2099-01-01T00:00:00"}).content)["reminder"]
        self.assertEqual(event["runs"], 4)

        event = loads(self.send("patch", path, {"when": {"tz": "Etc/UTC", "cron": "0 30 9"}}).content)["reminder"]
        self.assertEqual(event["runs"], 0)

        # A holiday sent once, moved to a later year, is sent again
        once = {**_DAILY, "when": {"tz": "Etc/UTC", "holiday": _CHRISTMAS}, "start": "2026-12-25 09:00", "count": 1}
        rid = loads(self.add(once).content)["reminder"]["id"]
        with ConfigCache.reminder_guard():
            events = self.stored()
            events[-1].update({"runs": 1, "next": None})
            ConfigCache.write_reminders(ConfigCache.reminder_path(self.key), events)

        event = loads(self.send("patch", f"/reminder/{self.key}/{rid}/", {"start": "2098-12-25 09:00"}).content)
        self.assertEqual(event["reminder"]["runs"], 0)
        self.assertEqual(event["reminder"]["next"], int(datetime(2098, 12, 25, 9, tzinfo=UTC).timestamp()))

        # A time that would never send is refused; 30 February never comes
        response = self.send("patch", path, {"when": {"tz": "Etc/UTC", "cron": "0 0 9 30 2"}})
        self.assertEqual(response.status_code, 400)

    def test_patch_keeps_changes_made_meanwhile(self):
        """Two changes to different fields of one reminder at once both stay."""
        rid = loads(self.add().content)["reminder"]["id"]
        saved = self.stored()
        # Another request changes the title after this one first reads the reminder
        answers = [saved, [{**saved[0], "title": "Changed meanwhile"}]]
        read = ConfigCache.read_reminders
        with patch(
            "api.views.ConfigCache.read_reminders", side_effect=lambda path: answers.pop(0) if answers else read(path)
        ):
            response = self.send("patch", f"/reminder/{self.key}/{rid}/", {"body": "New body"})

        event = loads(response.content)["reminder"]
        self.assertEqual((event["title"], event["body"]), ("Changed meanwhile", "New body"))
        self.assertEqual((self.stored()[0]["title"], self.stored()[0]["body"]), ("Changed meanwhile", "New body"))

    def test_open_access_is_held_to_the_limits(self):
        """Without logins nobody is the administrator, so every caller is held to the limits."""
        with override_settings(APPRISE_REMINDER_MAX=1):
            self.assertEqual(self.add().status_code, 200)
            self.assertEqual(self.add().status_code, 409)

        every_second = {"tz": "Etc/UTC", "at": "2099-01-01T09:00:00", "every": {"unit": "seconds"}}
        self.assertEqual(self.add({**_DAILY, "when": every_second}).status_code, 400)

        # Nor can they change the limits
        self.assertEqual(self.send("post", f"/reminder/{self.key}/limits/", {"reminders": 500}).status_code, 403)

    def test_missing_configuration(self):
        """A reminder needs a saved configuration to send through."""
        ConfigCache.clear(self.key)
        self.assertEqual(self.add().status_code, 404)

        with patch("api.views.ConfigCache.get", return_value=(None, None)):
            self.assertEqual(self.add().status_code, 500)

    def test_storage_failures(self):
        """Damaged or unwritable reminder files are reported."""
        rid = loads(self.add().content)["reminder"]["id"]
        path = f"/reminder/{self.key}/{rid}/"

        with patch("api.views.ConfigCache.write_reminders", return_value=False):
            self.assertEqual(self.add().status_code, 500)
            self.assertEqual(self.send("patch", path, {"name": "x"}).status_code, 500)
            self.assertEqual(self.send("delete", path).status_code, 500)

        with patch("api.views.ConfigCache.read_reminders", return_value=None):
            self.assertEqual(self.client.get(f"/reminder/{self.key}/").status_code, 500)
            self.assertEqual(self.add().status_code, 500)
            self.assertEqual(self.client.get(path).status_code, 500)
            self.assertEqual(self.send("patch", path, {"name": "x"}).status_code, 500)
            self.assertEqual(self.send("delete", path).status_code, 500)
            self.assertEqual(self.send("post", path + "test/").status_code, 500)

    def test_json_is_required(self):
        """Changes must be sent as a JSON object."""
        response = self.client.post(f"/reminder/{self.key}/", data={"name": "x"})
        self.assertEqual(response.status_code, 400)

        response = self.client.post(f"/reminder/{self.key}/", data="[1]", content_type="application/json")
        self.assertEqual(response.status_code, 400)

        rid = loads(self.add().content)["reminder"]["id"]
        response = self.client.patch(f"/reminder/{self.key}/{rid}/", data="name=x")
        self.assertEqual(response.status_code, 400)

    def test_reminders_turned_off(self):
        """With reminders off, the routes do not exist and status leaves them out."""
        self.assertTrue(
            loads(self.client.get("/status", headers={"Accept": "application/json"}).content)["reminders_enabled"]
        )

        for changes in ({"APPRISE_REMINDER_ENABLED": False}, {"APPRISE_STATEFUL_MODE": "disabled"}):
            with override_settings(**changes):
                self.assertEqual(self.client.get(f"/reminder/{self.key}/").status_code, 404)
                status = loads(self.client.get("/status", headers={"Accept": "application/json"}).content)
                self.assertNotIn("reminders_enabled", status)


class ReminderNotifyFieldTests(ReminderTestCase):
    """Reminders read their message fields the same way /notify does."""

    def test_names_and_aliases_match_notify(self):
        """The same field names and aliases as /notify are accepted."""
        payload = {"body": "Rent is due\nPay it", "tags": [["home", "bills"], "me"], "when": _DAILY["when"]}
        event = loads(self.add(payload).content)["reminder"]
        # Without a name, the first line of the message names it
        self.assertEqual(event["name"], "Rent is due")
        # A list inside the tag list needs all of its tags
        self.assertEqual(event["tag"], "home bills, me")
        self.assertIsNone(event["template"])

        # "attachment" is the same as "attach"
        event = loads(self.add({**_DAILY, "attachment": "https://example.com/a.png"}).content)["reminder"]
        self.assertEqual(event["attach"], [{"url": "https://example.com/a.png"}])

        # With no title or message either, it is simply called Reminder
        payload = {"body": "", "attach": "https://example.com/b.png", "when": _DAILY["when"]}
        self.assertEqual(loads(self.add(payload).content)["reminder"]["name"], "Reminder")

    def test_headers_and_query_fill_in_like_notify(self):
        """X-Apprise-* headers, then the query string, fill in what the payload left out."""
        path = f"/reminder/{self.key}/?format=markdown&tag=ops"
        payload = {"body": "Hi", "when": _DAILY["when"]}
        headers = {"X-Apprise-Title": "Heads up", "X-Apprise-Template": "room=ops"}
        event = loads(self.send("post", path, payload, headers=headers).content)["reminder"]
        self.assertEqual(
            (event["name"], event["title"], event["format"], event["tag"]), ("Heads up", "Heads up", "markdown", "ops")
        )
        self.assertEqual(event["template"], {"room": "ops"})

        # The payload wins over a header
        response = self.send("post", path, {**payload, "title": "Mine"}, headers=headers)
        self.assertEqual(loads(response.content)["reminder"]["title"], "Mine")

        # A bad template header is refused the same way /notify refuses it
        response = self.send("post", path, payload, headers={"X-Apprise-Template": "%zz"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(loads(response.content)["field"], "template")

    def test_answers_follow_accept(self):
        """Saving answers like /add: JSON when asked for, plain text otherwise."""
        form = encode_multipart(BOUNDARY, {"reminder": dumps(_DAILY)})
        response = self.client.post(
            f"/reminder/{self.key}/",
            data=form,
            content_type=f"multipart/form-data; boundary={BOUNDARY}",
            headers={"Accept": "text/plain"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "Successfully saved reminder")
        rid = response["X-Apprise-Reminder-ID"]
        self.assertEqual([event["id"] for event in self.stored()], [rid])

        # JSON callers get the reminder itself, with the same header
        path = f"/reminder/{self.key}/{rid}/"
        response = self.send("patch", path, {"title": "New"})
        self.assertEqual(loads(response.content)["reminder"]["title"], "New")
        self.assertEqual(response["X-Apprise-Reminder-ID"], rid)

        # Removing answers like /del, a 204 with no body, whatever was asked for
        response = self.client.delete(path, headers={"Accept": "text/plain"})
        self.assertEqual((response.status_code, response.content), (204, b""))


class ReminderValidationTests(ReminderTestCase):
    """Refuse reminder fields that cannot be used."""

    def assert_refused(self, changes, field):
        """Adding the reminder with these changes fails on ``field``."""
        response = self.add({**_DAILY, **changes})
        self.assertEqual(response.status_code, 400, changes)
        self.assertEqual(loads(response.content)["field"], field, changes)

    def test_times_may_leave_out_parts(self):
        """Dates and times are stored to the second, whatever parts were sent."""
        once = {**_DAILY, "when": {"tz": "Etc/UTC", "at": "2099-01-01 09"}, "start": None}
        self.assertEqual(loads(self.add(once).content)["reminder"]["when"]["at"], "2099-01-01T09:00:00")

        timed = {**_DAILY, "start": "2099-01-01", "until": "2099-02-01 17:30"}
        stored = loads(self.add(timed).content)["reminder"]
        self.assertEqual((stored["start"], stored["until"]), ("2099-01-01T00:00:00", "2099-02-01T17:30:00"))
        self.assertEqual(stored["next"], int(datetime(2099, 1, 1, 9, tzinfo=UTC).timestamp()))

        # A holiday is sent at its start's time of day, which may carry seconds
        holiday = {**_DAILY, "when": {"tz": "Etc/UTC", "holiday": _CHRISTMAS}, "start": "2098-12-25 9"}
        stored = loads(self.add(holiday).content)["reminder"]
        self.assertEqual(stored["start"], "2098-12-25T09:00:00")
        self.assertEqual(stored["next"], int(datetime(2098, 12, 25, 9, tzinfo=UTC).timestamp()))
        stored = loads(self.add({**holiday, "start": "2098-12-25 09:00:30"}).content)["reminder"]
        self.assertEqual(stored["next"], int(datetime(2098, 12, 25, 9, 0, 30, tzinfo=UTC).timestamp()))

    def test_holiday_start_may_be_a_time_of_day(self):
        """A holiday's start may be just a time of day, stored on the holiday's next date."""
        rule = {"country": "CA", "region": "NS", "name": "Remembrance Day"}
        holiday = {**_DAILY, "when": {"tz": "Etc/UTC", "holiday": rule}}
        for given, clock in (("10:45", "10:45:00"), ("10:45:30", "10:45:30")):
            stored = loads(self.add({**holiday, "start": given}).content)["reminder"]
            self.assertRegex(stored["start"], rf"^\d{{4}}-11-11T{clock}$")
            start = datetime.fromisoformat(stored["start"]).replace(tzinfo=UTC)
            self.assertEqual(stored["next"], int(start.timestamp()))

        # The same works in a preview
        response = self.client.get(
            f"/reminder/{self.key}/preview/?holiday=Remembrance+Day&country=CA&region=NS&tz=Etc/UTC&start=10:45",
            headers={"Accept": "application/json"},
        )
        self.assertEqual(time.gmtime(loads(response.content)["next"][0])[1:5], (11, 11, 10, 45))

    def test_holiday_start_uses_only_its_year(self):
        """A holiday's start sets the year it begins in; the holiday sets the month and day."""
        holiday = {**_DAILY, "when": {"tz": "Etc/UTC", "holiday": _CHRISTMAS}, "start": "2098-12-26 09:00"}
        stored = loads(self.add(holiday).content)["reminder"]
        self.assertEqual(stored["next"], int(datetime(2098, 12, 25, 9, tzinfo=UTC).timestamp()))

        # An end in the start's year is fine, even before the start's month and day
        self.assertEqual(self.add({**holiday, "until": "2098-12-25 23:00"}).status_code, 200)

        # An end in an earlier year is not
        response = self.add({**holiday, "until": "2097-12-31"})
        self.assertEqual(loads(response.content)["field"], "until")

    def test_end_date_that_leaves_nothing_is_named(self):
        """When only the end date stops every send, the error names that field."""
        # Mondays only, from a Thursday to the Saturday after it
        mondays = {**_DAILY, "when": {"tz": "Etc/UTC", "cron": "0 0 9 * * Mon"}, "start": "2099-01-01"}
        response = self.add({**mondays, "until": "2099-01-03"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(loads(response.content)["field"], "until")

        query = "cron=0+0+9+*+*+Mon&tz=Etc/UTC&start=2099-01-01"
        json_only = {"Accept": "application/json"}
        response = self.client.get(f"/reminder/{self.key}/preview/?{query}&until=2099-01-03", headers=json_only)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(loads(response.content)["field"], "until")

        # A rule that can never match is still blamed on its time
        never = "every=months&months=2&at=2099-01-30T09:00:00&tz=Etc/UTC"
        response = self.client.get(f"/reminder/{self.key}/preview/?{never}&until=2100-01-01", headers=json_only)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(loads(response.content)["field"], "when")

    def test_end_before_start_is_refused(self):
        """An end before the start is refused, whether the start is its own field or the rule's time."""
        weekly = {"tz": "Etc/UTC", "at": "2099-01-01 09:00", "every": {"unit": "weeks"}}
        for payload in (
            {**_DAILY, "when": weekly, "until": "2098-12-31"},
            {**_DAILY, "start": "2099-01-01", "until": "2098-01-01"},
        ):
            response = self.add(payload)
            self.assertEqual(response.status_code, 400)
            content = loads(response.content)
            self.assertEqual((content["field"], content["error"]), ("until", "The end date is before the start"))

        # The same end as the start is fine
        self.assertEqual(self.add({**_DAILY, "start": "2099-01-01", "until": "2099-01-01 09:00"}).status_code, 200)

    def test_passed_times_are_kept_but_not_sent(self):
        """A one-off in the past, or a repeating reminder past its end date, is saved but never sent."""
        starting_now = {"tz": "Etc/UTC", "every": {"unit": "days"}}
        for payload in (
            {**_DAILY, "when": {"tz": "Etc/UTC", "at": "2000-01-01T09:00:00"}},
            {**_DAILY, "until": "2000-01-01T00:00:00"},
            # Starting now with an end already passed is not an end before its start
            {**_DAILY, "when": starting_now, "until": "2000-01-01T00:00:00"},
        ):
            response = self.add(payload)
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(loads(response.content)["reminder"]["next"])

        # The preview lists nothing for them, without an error
        json_only = {"Accept": "application/json"}
        for query in ("at=2000-01-01T09:00:00&tz=Etc/UTC", "cron=0+0+9&tz=Etc/UTC&until=2000-01-01"):
            response = self.client.get(f"/reminder/{self.key}/preview/?{query}", headers=json_only)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(loads(response.content)["next"], [])

        # Moved to the future, a passed one-off is sent again
        rid = loads(self.add({**_DAILY, "when": {"tz": "Etc/UTC", "at": "2000-01-01T09:00:00"}}).content)["reminder"]
        changed = self.send("patch", f"/reminder/{self.key}/{rid['id']}/", {"when": {"at": "2099-01-01T09:00:00"}})
        self.assertGreater(loads(changed.content)["reminder"]["next"], time.time())

    def test_never_sent_stays_on_the_time(self):
        """When even without its end date nothing could be sent, the error stays on the time."""
        for effect in ([None, None], [None, ValueError()]):
            with patch("api.views.next_run", side_effect=effect):
                response = self.add({**_DAILY, "until": "2099-01-01"})

            self.assertEqual(loads(response.content)["field"], "when")

    def test_repeating_rule_without_a_start_counts_from_now(self):
        """An every rule with no time starts at its start, or else when it is saved."""
        every = {**_DAILY, "when": {"tz": "Etc/UTC", "every": {"unit": "days"}}}
        before = int(time.time())
        stored = loads(self.add(every).content)["reminder"]
        anchor = datetime.fromisoformat(stored["when"]["at"]).replace(tzinfo=UTC).timestamp()
        self.assertLessEqual(abs(anchor - before), 5)

        stored = loads(self.add({**every, "start": "2099-03-04 05:06"}).content)["reminder"]
        self.assertEqual(stored["when"]["at"], "2099-03-04T05:06:00")

    def test_invalid_fields(self):
        """Each unusable value names the field it is in."""
        for changes, field in (
            ({"name": 5}, "name"),
            ({"name": "x" * 101}, "name"),
            ({"template": "token=abc"}, "template"),
            ({"template": {"bad name!": "abc"}}, "template"),
            ({"title": 5}, "title"),
            ({"title": "x" * 251}, "title"),
            ({"body": " "}, "body"),
            ({"body": "x" * 32769}, "body"),
            ({"tag": "a," * 501}, "tag"),
            ({"when": {"cron": "0 " * 128}}, "when"),
            ({"type": "loud"}, "type"),
            ({"type": ["info"]}, "type"),
            ({"format": "fancy"}, "format"),
            ({"format": 5}, "format"),
            ({"tag": "bad tag!"}, "tag"),
            ({"tag": ["ok", 5]}, "tag"),
            ({"tag": [[]]}, "tag"),
            ({"tag": 5}, "tag"),
            ({"enabled": "yes"}, "enabled"),
            ({"when": "tomorrow"}, "when"),
            ({"when": {"at": "2099-01-01T00:00:00", "cron": "0 0 9"}}, "when"),
            ({"when": {"tz": "Etc/UTC"}}, "when"),
            ({"when": {"cron": " "}}, "when"),
            ({"when": {"cron": 5}}, "when"),
            ({"when": {"cron": "0 0 9", "isc": "yes"}}, "when"),
            ({"when": {"cron": "0 0 9", "tz": "Mars/Base"}}, "when"),
            ({"when": {"at": "soon"}}, "when"),
            ({"when": {"cron": "garbage"}}, "when"),
            ({"when": {"holiday": "Christmas Day"}}, "when"),
            ({"when": {"holiday": _CHRISTMAS, "cron": "0 0 9"}}, "when"),
            ({"when": {"holiday": {**_CHRISTMAS, "name": None}}}, "when"),
            ({"when": {"holiday": {**_CHRISTMAS, "region": 5}}}, "when"),
            ({"when": {"holiday": {**_CHRISTMAS, "country": "ZZ"}}}, "when"),
            ({"when": {"holiday": {**_CHRISTMAS, "region": "ZZ"}}}, "when"),
            ({"when": {"holiday": {**_CHRISTMAS, "name": "Not A Holiday"}}}, "when"),
            ({"until": 5}, "until"),
            ({"until": "later"}, "until"),
            ({"start": "later"}, "start"),
            # A time of day alone only suits a holiday, and must exist
            ({"start": "09:00"}, "start"),
            ({"when": {"holiday": _CHRISTMAS}, "start": "25:00"}, "start"),
            ({"count": True}, "count"),
            ({"count": "2"}, "count"),
            ({"count": 0}, "count"),
            ({"count": 1000001}, "count"),
            ({"skip": [253402300800]}, "skip"),
            ({"template": {f"t{index}": "x" for index in range(257)}}, "template"),
            ({"when": {"cron": "0 0 9", "every": {"unit": "days"}}}, "when"),
            ({"when": {"at": "2099-01-01T09:00:00", "every": "daily"}}, "when"),
            ({"when": {"at": "2099-01-01T09:00:00", "every": {"unit": "fortnights"}}}, "when"),
            ({"when": {"at": "2099-01-01T09:00:00", "every": {"unit": "days", "interval": 0}}}, "when"),
            ({"when": {"at": "2099-01-01T09:00:00", "every": {"unit": "days", "interval": 1000}}}, "when"),
            ({"when": {"at": "2099-01-01T09:00:00", "every": {"unit": "days", "interval": True}}}, "when"),
            ({"when": {"at": "2099-01-01T09:00:00", "every": {"unit": "days", "months": [13]}}}, "when"),
            ({"when": {"at": "2099-01-01T09:00:00", "every": {"unit": "days", "months": "Jan"}}}, "when"),
            ({"when": {"at": "2099-01-01T09:00:00", "every": {"unit": "years", "months": [1]}}}, "when"),
            ({"when": {"at": "2099-01-01T09:00:00", "every": {"unit": "days", "days": ["Funday"]}}}, "when"),
            ({"when": {"at": "2099-01-01T09:00:00", "every": {"unit": "days", "days": "Mon"}}}, "when"),
            ({"when": {"at": "2099-01-01T09:00:00", "every": {"unit": "months", "days": ["Mon"]}}}, "when"),
            ({"skip": "soon"}, "skip"),
            ({"skip": [True]}, "skip"),
            ({"skip": ["4102444800"]}, "skip"),
            ({"skip": list(range(201))}, "skip"),
        ):
            self.assert_refused(changes, field)

    def test_oversized_reminders_are_refused(self):
        """A reminder whose saved form would be too large is refused, even when each field fits."""
        template = {f"t{index}": "x" * 1024 for index in range(256)}
        response = self.add({**_DAILY, "template": template})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(loads(response.content)["error"], "The reminder is too large to save")

        rid = loads(self.add().content)["reminder"]["id"]
        response = self.send("patch", f"/reminder/{self.key}/{rid}/", {"template": template})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.stored()), 1)

    def test_skipping_the_last_time_is_refused(self):
        """A repeating reminder cannot skip the only time it has left before its end date."""
        first = loads(self.add().content)["reminder"]["next"]
        until = datetime.fromtimestamp(first + 3600, UTC).strftime("%Y-%m-%d %H:%M")
        event = loads(self.add({**_DAILY, "until": until}).content)["reminder"]
        path = f"/reminder/{self.key}/{event['id']}/"
        response = self.send("patch", path, {"skip": [event["next"]]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(loads(response.content)["field"], "until")
        self.assertEqual(self.stored()[1]["next"], event["next"])

    def test_values_are_tidied(self):
        """Lists of tags, empty formats and crontab layouts are stored plainly."""
        event = loads(
            self.add(
                {
                    **_DAILY,
                    "tag": ["ops", "qa"],
                    "format": " ",
                    "when": {"cron": " 0 9 * * * ", "isc": True},
                    "count": 2,
                    "skip": [4102444800, 1000, 4102444800],
                }
            ).content
        )["reminder"]
        self.assertEqual(event["tag"], "ops, qa")
        self.assertIsNone(event["format"])
        self.assertEqual(event["when"], {"tz": settings.TIME_ZONE, "cron": "0 9 * * *", "isc": True})
        self.assertEqual(event["count"], 2)
        # Skipped times that have passed are dropped, and the rest kept once
        self.assertEqual(event["skip"], [4102444800])

        # A tag with nothing in it is the same as no tag
        self.assertIsNone(loads(self.add({**_DAILY, "tag": ","}).content)["reminder"]["tag"])

        rule = {"country": " ca ", "region": " ON ", "name": "Christmas Day"}
        event = loads(self.add({**_DAILY, "when": {"tz": "America/Toronto", "holiday": rule}}).content)["reminder"]
        self.assertEqual(event["when"]["holiday"], {**_CHRISTMAS, "region": "ON"})
        self.assertGreater(event["next"], time.time())

        # A repeating rule keeps only the limits that leave something out
        every = {"unit": "days", "days": ["Wed", "Mon", "Wed"], "months": list(range(1, 13)), "interval": None}
        event = loads(self.add({**_DAILY, "when": {"at": "2026-01-05T09:00:00", "every": every}}).content)["reminder"]
        self.assertEqual(event["when"]["every"], {"interval": 1, "unit": "days", "days": ["Mon", "Wed"]})

        # Weeks keep every day when all are chosen, as no days means the start's weekday
        every = {"unit": "weeks", "days": list(REMINDER_DAYS)}
        event = loads(self.add({**_DAILY, "when": {"at": "2026-01-05T09:00:00", "every": every}}).content)["reminder"]
        self.assertEqual(event["when"]["every"]["days"], list(REMINDER_DAYS))
        self.assertGreater(event["next"], time.time())

        # Without a region the whole country's holidays are used
        rule = {**_CHRISTMAS, "region": None}
        event = loads(self.add({**_DAILY, "when": {"holiday": rule}}).content)["reminder"]
        self.assertEqual(event["when"]["holiday"], _CHRISTMAS)


class ReminderTestSendTests(ReminderTestCase):
    """Send a reminder on demand."""

    def test_send_now(self):
        """A test send goes out, and is answered, the same way as /notify."""
        event = loads(self.add({**_DAILY, "template": {"room": "ops"}}).content)["reminder"]
        path = f"/reminder/{self.key}/{event['id']}/test/"

        with patch.object(apprise.Apprise, "notify", return_value=notify_result()) as notify:
            response = self.send("post", path)
            body = loads(b"".join(response.streaming_content))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["error"], None)
        self.assertIn("details", body)
        self.assertEqual(notify.call_args.kwargs["template"], {"room": "ops"})
        # The schedule is not changed by a test send
        self.assertEqual(self.stored(), [event])

        # Without JSON, the answer is plain text, as from /notify
        with patch.object(apprise.Apprise, "notify", return_value=notify_result(success=False)):
            response = self.client.post(path, headers={"Accept": "text/plain"})
            b"".join(response.streaming_content)

        self.assertEqual(response.status_code, 424)
        self.assertTrue(response["Content-Type"].startswith("text/plain"))

        missing = f"/reminder/{self.key}/{'0' * 32}/test/"
        self.assertEqual(self.send("post", missing).status_code, 404)

    def test_send_now_with_a_live_log(self):
        """A test send can stream each service's log and the result, like /notify."""
        event = loads(self.add().content)["reminder"]
        path = f"/reminder/{self.key}/{event['id']}/test/"

        with patch.object(apprise.Apprise, "notify", return_value=notify_result()) as notify:
            response = self.client.post(path, headers={"Accept": "text/event-stream"})
            body = b"".join(response.streaming_content).decode()

        self.assertTrue(response["Content-Type"].startswith("text/event-stream"))
        self.assertIn("event: result", body)
        self.assertIn('"status": "SUCCESS"', body)
        self.assertEqual(notify.call_args.args[0], "Time to meet")
        self.assertEqual(self.stored(), [event])

        # Nothing it could send through
        with patch("api.scheduler.prepare_send", return_value=None):
            self.assertEqual(self.client.post(path + "?stream=1").status_code, 424)

        # No room for another live stream
        with patch("api.views._STREAM_MANAGER.try_admit", return_value=False):
            response = self.client.post(path + "?stream=1")

        self.assertEqual(response.status_code, 503)
        self.assertIn("Retry-After", response)


class ReminderPreviewTests(ReminderTestCase):
    """Preview upcoming times without saving."""

    def preview(self, query):
        """Return the preview response for a query string."""
        return self.client.get(f"/reminder/{self.key}/preview/?{query}", headers={"Accept": "application/json"})

    def test_preview_lists_upcoming_times(self):
        """A cron lists its next runs, and a one-off that has passed lists nothing."""
        times = loads(self.preview("cron=0+0+9&tz=Etc/UTC&limit=3").content)["next"]
        self.assertEqual(len(times), 3)
        self.assertEqual(times[1] - times[0], 86400)

        times = loads(self.preview("cron=0+9+*+*+*&isc=yes&count=2").content)["next"]
        self.assertEqual(len(times), 2)

        self.assertEqual(loads(self.preview("at=2000-01-01T00:00:00").content)["next"], [])
        self.assertEqual(len(loads(self.preview("at=2099-01-01T00:00:00").content)["next"]), 1)

        query = "at=2026-01-05T09:00:00&tz=Etc/UTC&every=weeks&interval=2&days=Mon,Wed&months=1,2,3,4,5,6,7,8,9,10,11"
        times = loads(self.preview(query + "&limit=4").content)["next"]
        self.assertEqual(len(times), 4)
        self.assertTrue(all(time.gmtime(due).tm_wday in (0, 2) and time.gmtime(due).tm_mon != 12 for due in times))
        self.assertEqual(self.preview("at=2026-01-05T09:00:00&every=days&interval=two").status_code, 400)

        query = "holiday=Christmas+Day&country=CA&region=ON&tz=Etc/UTC&start=2026-01-01T09:00:00&limit=2"
        times = loads(self.preview(query).content)["next"]
        self.assertEqual(len(times), 2)
        self.assertEqual(time.gmtime(times[0])[1:4], (12, 25, 9))
        self.assertEqual(self.preview("holiday=Christmas+Day&country=ZZ").status_code, 400)

    def test_preview_refuses_bad_values(self):
        """Unusable query values name the field they are in."""
        for query, field in (
            ("cron=0+0+9&count=two", "count"),
            ("cron=0+0+9&limit=two", "limit"),
            ("cron=0+0+9&limit=0", "limit"),
            ("cron=0+0+9&limit=26", "limit"),
            ("tz=Etc/UTC", "when"),
        ):
            response = self.preview(query)
            self.assertEqual(response.status_code, 400, query)
            self.assertEqual(loads(response.content)["field"], field, query)

        # A cron that dateblock cannot read is refused too
        self.assertEqual(self.preview("cron=garbage").status_code, 400)

    def test_drift_is_checked_and_kept(self):
        """Drift is a whole number of seconds within what dateblock accepts."""
        times = loads(self.preview("cron=0+0+9&tz=Etc/UTC&drift=90&limit=1").content)["next"]
        self.assertEqual(times[0] % 86400, 9 * 3600 + 90)
        for drift in ("two", str(DRIFT_MIN - 1), str(DRIFT_MAX + 1)):
            response = self.preview(f"cron=0+0+9&drift={drift}")
            self.assertEqual(response.status_code, 400, drift)
            self.assertEqual(loads(response.content)["field"], "drift", drift)

        event = loads(self.add({**_DAILY, "when": {**_DAILY["when"], "drift": DRIFT_MAX}}).content)["reminder"]
        self.assertEqual(event["when"]["drift"], DRIFT_MAX)
        # No drift is the same as leaving it out
        event = loads(self.add({**_DAILY, "when": {**_DAILY["when"], "drift": 0}}).content)["reminder"]
        self.assertNotIn("drift", event["when"])
        for drift in (True, "90", 1.5):
            response = self.add({**_DAILY, "when": {**_DAILY["when"], "drift": drift}})
            self.assertEqual(loads(response.content)["field"], "drift", drift)

        # A negative drift sends each time early, down to dateblock's DRIFT_MIN
        event = loads(self.add({**_DAILY, "when": {**_DAILY["when"], "drift": DRIFT_MIN}}).content)["reminder"]
        self.assertEqual(event["when"]["drift"], DRIFT_MIN)
        event = loads(self.add({**_DAILY, "when": {**_DAILY["when"], "drift": -3600}}).content)["reminder"]
        times = loads(self.preview("cron=0+0+9&tz=Etc/UTC&drift=-3600&limit=1").content)["next"]
        self.assertEqual(event["when"]["drift"], -3600)
        self.assertEqual(times[0] % 86400, 8 * 3600)


class ReminderSeriesTests(ReminderTestCase):
    """Change or skip one time of a repeating reminder."""

    def stand_in(self, repeating, at, **changes):
        """Add a one-off in place of the time ``at`` of ``repeating``, returning the response."""
        payload = {
            **_DAILY,
            "name": "Moved",
            "when": {"tz": "Etc/UTC", "at": "2099-01-01T10:00:00"},
            "series": repeating["id"],
            "occurrence": at,
        }
        return self.add({**payload, **changes})

    def test_one_off_and_repeating_switch_back_and_forth(self):
        """Saving a one-off as repeating, and back again, changes only its schedule."""
        once = {**_DAILY, "when": {"tz": "Etc/UTC", "at": "2099-01-01 09:00"}}
        event = loads(self.add(once).content)["reminder"]
        path = f"/reminder/{self.key}/{event['id']}/"

        repeating = {**once, "when": {**once["when"], "every": {"unit": "days", "interval": 2}}}
        changed = loads(self.send("put", path, repeating).content)["reminder"]
        self.assertEqual(changed["when"]["every"], {"interval": 2, "unit": "days"})
        self.assertEqual(changed["next"], event["next"])

        changed = loads(self.send("put", path, once).content)["reminder"]
        self.assertNotIn("every", changed["when"])
        self.assertEqual((changed["id"], changed["next"], changed["body"]), (event["id"], event["next"], event["body"]))

    def test_stand_in_made_repeating_stands_alone(self):
        """A one-off that replaced one time stops being part of its series once it repeats."""
        series = loads(self.add().content)["reminder"]
        first = series["next"]
        stand_in = loads(self.stand_in(series, first).content)["reminder"]
        path = f"/reminder/{self.key}/{stand_in['id']}/"
        weekly = {"tz": "Etc/UTC", "at": "2099-01-01 10:00", "every": {"unit": "weeks"}}
        repeating = {**_DAILY, "name": "Moved", "when": weekly}
        changed = loads(self.send("put", path, repeating).content)["reminder"]
        self.assertNotIn("series", changed)
        self.assertNotIn("occurrence", changed)

        # The series still skips the time it replaced, even when saved again with it
        stored = {event["id"]: event for event in self.stored()}
        self.assertEqual(stored[series["id"]]["skip"], [first])
        path = f"/reminder/{self.key}/{series['id']}/"
        saved = loads(self.send("put", path, {**_DAILY, "skip": [first]}).content)["reminder"]
        self.assertEqual(saved["skip"], [first])

        # Removing the series leaves it in place
        self.assertEqual(self.client.delete(path).status_code, 204)
        self.assertEqual([event["id"] for event in self.stored()], [stand_in["id"]])

    def test_one_time_is_replaced(self):
        """The replaced time is skipped, and the one-off names its series."""
        series = loads(self.add().content)["reminder"]
        first = series["next"]
        response = self.stand_in(series, first)
        self.assertEqual(response.status_code, 200)
        stand_in = loads(response.content)["reminder"]
        self.assertEqual((stand_in["series"], stand_in["occurrence"]), (series["id"], first))

        stored = {event["id"]: event for event in self.stored()}
        self.assertEqual(stored[series["id"]]["skip"], [first])
        self.assertEqual(stored[series["id"]]["next"], first + 86400)

        # Replacing the whole skip list still keeps the replaced time skipped
        path = f"/reminder/{self.key}/{series['id']}/"
        changed = loads(self.send("patch", path, {"skip": [first + 86400]}).content)["reminder"]
        self.assertEqual(changed["skip"], [first, first + 86400])
        self.assertEqual(changed["next"], first + 86400 * 2)

        # Removing the series also removes what stands in for its times
        other = loads(self.add().content)["reminder"]
        self.assertEqual(self.send("delete", path).status_code, 204)
        self.assertEqual([event["id"] for event in self.stored()], [other["id"]])

    def test_repeating_rule_times_can_be_replaced(self):
        """A reminder that repeats with an ``every`` rule can have one time replaced."""
        weekly = {"tz": "Etc/UTC", "at": "2026-01-05T09:00:00", "every": {"unit": "weeks"}}
        series = loads(self.add({**_DAILY, "when": weekly}).content)["reminder"]
        response = self.stand_in(series, series["next"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.stored()[0]["next"], series["next"] + 7 * 86400)

    def test_stand_in_keeps_the_series_attachments(self):
        """A one-off standing in for a time may keep the series' stored files."""
        stored = {"file": "f" * 64, "filename": "notes.txt", "size": 1}
        series = loads(self.add().content)["reminder"]
        path = ConfigCache.reminder_path(self.key)
        self.assertTrue(ConfigCache.write_reminders(path, [{**series, "attach": [stored]}]))

        # A file no longer in the store, such as one pruned meanwhile, cannot be kept
        response = self.stand_in(series, series["next"], attach=[{"file": "f" * 64}])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(loads(response.content)["field"], "attach")

        stored_path = ConfigCache.reminder_file_path("f" * 64)
        os.makedirs(os.path.dirname(stored_path), exist_ok=True)
        with open(stored_path, "w") as handle:
            handle.write("x")

        # Removing the configuration afterwards removes the file too, once nothing uses it.
        response = self.stand_in(series, series["next"], attach=[{"file": "f" * 64}])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(loads(response.content)["reminder"]["attach"], [stored])

    def test_unusable_replacements_are_refused(self):
        """Unknown series, one-off series and times the series is not due name the field."""
        series = loads(self.add().content)["reminder"]
        once = loads(self.add({**_DAILY, "when": {"at": "2099-01-01T10:00:00"}}).content)["reminder"]
        first = series["next"]
        weekly = {"at": "2099-01-01T10:00:00", "every": {"unit": "weeks"}}
        for changes, field in (
            ({"when": weekly}, "series"),
            ({"series": "0" * 32}, "series"),
            ({"series": once["id"]}, "series"),
            ({"series": 5}, "series"),
            ({"when": {"cron": "0 0 10"}}, "series"),
            ({"occurrence": None}, "occurrence"),
            ({"occurrence": True}, "occurrence"),
            ({"occurrence": 1000}, "occurrence"),
            ({"occurrence": first + 60}, "occurrence"),
        ):
            response = self.stand_in(series, first, **changes)
            self.assertEqual(response.status_code, 400, changes)
            self.assertEqual(loads(response.content)["field"], field, changes)

        # A time without the series it belongs to is refused too
        response = self.add({**_DAILY, "when": {"at": "2099-01-01T10:00:00"}, "occurrence": first})
        self.assertEqual(loads(response.content)["field"], "series")

        # A series whose saved time cannot be read cannot be matched
        path = ConfigCache.reminder_path(self.key)
        self.assertTrue(ConfigCache.write_reminders(path, [{**series, "when": {"cron": "garbage"}}]))
        self.assertEqual(loads(self.stand_in(series, first).content)["field"], "occurrence")
        self.assertEqual(len(self.stored()), 1)


class ReminderOverviewTests(ReminderTestCase):
    """List every Config ID's reminders for the administrator."""

    def overview(self, **headers):
        """Return the reminder list as JSON."""
        return self.client.get("/reminders/", headers={"Accept": "application/json", **headers})

    @override_settings(APPRISE_ADMIN=True, APPRISE_STATEFUL_MODE="simple")
    def test_every_reminder_with_its_key(self):
        """Reminders from every Config ID are listed soonest first."""
        later = loads(self.add({**_DAILY, "when": {"at": "2099-01-01T10:00:00"}}).content)["reminder"]
        sooner = loads(self.add().content)["reminder"]
        with patch("api.views.ConfigCache.keys", return_value=[self.key, "no-reminders"]):
            found = loads(self.overview().content)["reminders"]

        # The menu link shows in simple mode, which also moves where files are looked for
        with patch("api.context_processors.ConfigCache.mode", "simple"):
            html = self.client.get("/reminders/").content.decode()

        listed = [(event["key"], event["id"]) for event in found]
        self.assertEqual(listed, [(self.key, sooner["id"]), (self.key, later["id"])])
        self.assertIn('id="reminder-overview-data"', html)
        self.assertIn('id="reminder-overview"', html)
        # One eye shows or hides every Config ID, the same as the configuration list
        self.assertRegex(html, r'<button type="button"\s+id="reminder-list-toggle-all"')
        self.assertIn("appriseWireConfigIdToggleAll(reminderToggleAll", html)
        # Send Now shows the same live log dialog as the Notifications form
        self.assertIn("function sendNotifyStream(", html)
        self.assertIn("sendNotifyStream(url, {method: 'POST'", html)
        # With nothing listed, the page points to the configuration list
        self.assertRegex(html, r'Open a configuration from the <a href="/cfg/?" class="tab-chip tab-chip-link">')

        # A reminder file that cannot be read is left out
        with (
            patch("api.views.ConfigCache.keys", return_value=[self.key]),
            patch("api.views.ConfigCache.read_reminders", return_value=None),
        ):
            self.assertEqual(loads(self.overview().content)["reminders"], [])

    def test_only_where_the_configuration_list_is_allowed(self):
        """The list follows the configuration list's rules."""
        self.assertEqual(self.overview().status_code, 403)
        with override_settings(APPRISE_ADMIN=True, APPRISE_STATEFUL_MODE="hash"):
            self.assertEqual(self.overview().status_code, 403)

        with override_settings(APPRISE_ADMIN=True, APPRISE_STATEFUL_MODE="simple", APPRISE_REMINDER_ENABLED=False):
            self.assertEqual(self.overview().status_code, 404)

        with override_settings(APPRISE_ADMIN=True, APPRISE_STATEFUL_MODE="simple", APPRISE_API_ONLY=True):
            self.assertEqual(self.client.get("/reminders/").status_code, 421)
            self.assertEqual(self.overview().status_code, 200)

    @override_settings(
        APPRISE_ADMIN=True,
        APPRISE_STATEFUL_MODE="simple",
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_USER="master",
    )
    def test_every_configuration_is_listed(self):
        """The administrator sees reminders whatever each configuration's access, disabled too."""
        self.add(headers=_MASTER)
        with patch("api.views.ConfigCache.keys", return_value=[self.key]):
            for access in ("public", "disabled"):
                self.assertTrue(ConfigCache.set_access(self.key, access))
                self.assertEqual(len(loads(self.overview(**_MASTER).content)["reminders"]), 1, access)

    @override_settings(
        APPRISE_ADMIN=True,
        APPRISE_STATEFUL_MODE="simple",
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_USER="master",
    )
    def test_logins_need_the_administrator(self):
        """With logins required, only the administrator sees the list."""
        self.assertEqual(self.overview().status_code, 401)
        self.assertEqual(self.overview(**_MASTER).status_code, 200)


class ReminderHolidayTests(ReminderTestCase):
    """List the holidays a reminder can be sent on."""

    def holidays(self, query="", **headers):
        """Return the holidays response for a query string."""
        return self.client.get(
            f"/reminder/{self.key}/holidays/?{query}", headers={"Accept": "application/json", **headers}
        )

    def test_countries_and_their_regions(self):
        """Without a country, every country is listed with its regions."""
        countries = loads(self.holidays().content)["countries"]
        canada = next(country for country in countries if country["code"] == "CA")
        self.assertIn({"code": "ON", "name": "Ontario"}, canada["regions"])

        # The '@' route reads the key from the header
        response = self.client.get(
            "/reminder/@/holidays/", headers={"Accept": "application/json", "X-Apprise-Config-ID": self.key}
        )
        self.assertEqual(loads(response.content), {"countries": countries})

    def test_a_countrys_holidays(self):
        """A country's holidays cover the next year, named for saving and for showing."""
        found = loads(self.holidays("country=ca&region=QC", **{"Accept-Language": "fr"}).content)["holidays"]
        christmas = next(entry for entry in found if entry["name"] == "Christmas Day")
        self.assertEqual(christmas["label"], "Jour de Noël")
        self.assertEqual(christmas["date"][5:], "12-25")
        self.assertEqual(len([entry for entry in found if entry["name"] == "Christmas Day"]), 1)

        # A page language the country has no names for shows the saved names
        found = loads(self.holidays("country=CA", **{"Accept-Language": "pl"}).content)["holidays"]
        self.assertTrue(all(entry["label"] == entry["name"] for entry in found))

        year = time.gmtime().tm_year + 1
        found = loads(self.holidays(f"country=CA&year={year}").content)["holidays"]
        self.assertEqual({entry["date"][:4] for entry in found}, {str(year)})

    def test_names_used_when_labels_differ(self):
        """A day listed differently in the page language shows the saved names."""
        day = date(time.gmtime().tm_year, 12, 31)

        def dates(country, region, year, language=None):
            return ((day, ("Eve",) if language is None else ("Veille", "Autre")),)

        with patch("api.views.holiday_dates", side_effect=dates):
            found = loads(self.holidays(f"country=CA&year={day.year}", **{"Accept-Language": "fr"}).content)

        self.assertEqual(found["holidays"], [{"date": day.isoformat(), "name": "Eve", "label": "Eve"}])

    def test_bad_values_are_refused(self):
        """Unknown countries, regions and years name the field at fault."""
        for query, field in (
            ("country=ZZ", "country"),
            ("country=CA&region=ZZ", "region"),
            ("country=CA&year=1999", "year"),
            ("country=CA&year=soon", "year"),
        ):
            response = self.holidays(query)
            self.assertEqual(response.status_code, 400, query)
            self.assertIn(field, loads(response.content)["error"], query)


@override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
class ReminderAccessTests(ReminderTestCase):
    """Only the administrator and the configuration's user may manage reminders."""

    def login(self, access):
        """Give the configuration a user with this access setting."""
        self.assertTrue(ConfigCache.set_auth(self.key, "alice", "secret", access="user"))
        if access != "user":
            self.assertTrue(ConfigCache.set_access(self.key, access))

    def test_user_access(self):
        """The configuration's user may add reminders, which they then own."""
        self.login("user")
        self.assertEqual(self.add().status_code, 401)

        response = self.add(headers=_USER)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(loads(response.content)["reminder"]["owner"], "user")

    def test_every_route_needs_credentials(self):
        """Callers without credentials are refused on every reminder route."""
        self.login("user")
        rid = loads(self.add(headers=_MASTER).content)["reminder"]["id"]
        path = f"/reminder/{self.key}/{rid}/"
        header = {"X-Apprise-Config-ID": self.key}
        for method, route, kwargs in (
            ("get", f"/reminder/{self.key}/", {}),
            ("post", f"/reminder/{self.key}/", {}),
            ("get", "/reminder/", {"headers": header}),
            ("post", "/reminder/", {"headers": header}),
            ("get", "/reminder/@/", {"headers": header}),
            ("get", path, {}),
            ("put", path, {}),
            ("patch", path, {}),
            ("delete", path, {}),
            ("get", f"/reminder/@/{rid}/", {"headers": header}),
            ("post", f"{path}test/", {}),
            ("get", f"/reminder/{self.key}/preview/?cron=0+0+9", {}),
            ("get", f"/reminder/{self.key}/holidays/", {}),
            ("get", f"/reminder/{self.key}/limits/", {}),
            ("post", f"/reminder/{self.key}/limits/", {}),
        ):
            with self.subTest(method=method, route=route):
                payload = _DAILY if method in {"post", "put", "patch"} else None
                self.assertEqual(self.send(method, route, payload, **kwargs).status_code, 401)

        # Nothing was changed or sent along the way
        self.assertEqual([event["id"] for event in self.stored()], [rid])

        # A login for one configuration cannot reach another's reminders
        other = "reminder-api-other"
        self.assertTrue(ConfigCache.put(other, "json://localhost", "text"))
        try:
            self.assertEqual(self.client.get(f"/reminder/{other}/", headers=_USER).status_code, 401)
            response = self.client.get("/reminder/", headers={**_USER, "X-Apprise-Config-ID": other})
            self.assertEqual(response.status_code, 401)

        finally:
            ConfigCache.clear(other)

    def test_reminders_stay_with_their_configuration(self):
        """A reminder, stored file or series of another Config ID is unknown under this one."""
        self.login("user")
        other = "reminder-api-other"
        stored = {"file": "f" * 64, "filename": "notes.txt", "size": 1}
        other_file = ConfigCache.reminder_path(other)
        self.assertTrue(ConfigCache.put(other, "json://localhost", "text"))
        try:
            theirs = loads(self.send("post", f"/reminder/{other}/", _DAILY, headers=_MASTER).content)["reminder"]
            theirs = {**theirs, "attach": [stored]}
            self.assertTrue(ConfigCache.write_reminders(other_file, [theirs]))

            # Their reminder ID is not found under this configuration
            path = f"/reminder/{self.key}/{theirs['id']}/"
            for method in ("get", "put", "patch", "delete"):
                payload = _DAILY if method in {"put", "patch"} else None
                self.assertEqual(self.send(method, path, payload, headers=_USER).status_code, 404, method)

            self.assertEqual(self.send("post", f"{path}test/", headers=_USER).status_code, 404)

            # A new reminder cannot keep their files or stand in for their times
            once = {"tz": "Etc/UTC", "at": "2099-01-01T10:00:00"}
            for changes in (
                {"attach": [{"file": stored["file"]}]},
                {"when": once, "series": theirs["id"], "occurrence": theirs["next"]},
            ):
                self.assertEqual(self.add({**_DAILY, **changes}, headers=_USER).status_code, 400, changes)

            self.assertFalse(self.stored())
            self.assertEqual(ConfigCache.read_reminders(other_file), [theirs])

        finally:
            ConfigCache.clear(other)
            with ConfigCache.reminder_guard():
                ConfigCache.write_reminders(other_file, [])

    def test_status_says_whether_the_caller_may_add_reminders(self):
        """/status reports reminders_enabled for the caller and Config ID asked about."""

        def allowed(**headers):
            response = self.client.get("/status", headers={"Accept": "application/json", **headers})
            return loads(response.content)["reminders_enabled"]

        keyed = {"X-Apprise-Config-ID": self.key}
        # Without a Config ID, the administrator may
        self.assertTrue(allowed(**_MASTER))

        # A configuration's own login may while its access is "user"
        self.login("user")
        self.assertTrue(allowed(**keyed, **_USER))
        self.assertTrue(allowed(**keyed, **_MASTER))

        # Locked: only the administrator may add them
        self.assertTrue(ConfigCache.set_access(self.key, "locked"))
        self.assertFalse(allowed(**keyed, **_USER))
        self.assertTrue(allowed(**keyed, **_MASTER))

        # The administrator always may, whatever the access
        for access in ("public", "disabled"):
            self.assertTrue(ConfigCache.set_access(self.key, access))
            self.assertTrue(allowed(**keyed, **_MASTER), access)

        # Turned off, the field is left out as on an older server
        with override_settings(APPRISE_REMINDER_ENABLED=False):
            response = self.client.get("/status", headers={"Accept": "application/json", **_MASTER})
            self.assertNotIn("reminders_enabled", loads(response.content))

    def test_locked_user_cannot_use_reminders(self):
        """A locked configuration's user cannot reach its reminders; the administrator can."""
        self.login("locked")
        rid = loads(self.add(headers=_MASTER).content)["reminder"]["id"]
        path = f"/reminder/{self.key}/{rid}/"

        for method, route in (("get", path), ("get", f"/reminder/{self.key}/"), ("post", f"/reminder/{self.key}/")):
            payload = _DAILY if method == "post" else None
            self.assertEqual(self.send(method, route, payload, headers=_USER).status_code, 403)

        self.assertEqual(self.send("delete", path, headers=_USER).status_code, 403)
        self.assertEqual(self.client.get(path, headers=_MASTER).status_code, 200)

    def test_public_and_disabled_configurations(self):
        """The administrator manages reminders on public and disabled configurations; nobody else can."""
        for access in ("public", "disabled"):
            self.assertTrue(ConfigCache.set_access(self.key, access))
            response = self.add(headers=_MASTER)
            self.assertEqual(response.status_code, 200, access)
            path = f"/reminder/{self.key}/{loads(response.content)['reminder']['id']}/"
            self.assertEqual(self.client.get(path, headers=_MASTER).status_code, 200, access)
            self.assertEqual(self.send("patch", path, {"title": "New"}, headers=_MASTER).status_code, 200, access)

            # Nobody without the administrator's login can reach them
            self.assertEqual(self.client.get(f"/reminder/{self.key}/").status_code, 401, access)

            self.assertEqual(self.send("delete", path, headers=_MASTER).status_code, 204, access)

    def test_browser_user_reaches_current_reminders(self):
        """A signed-in configuration user manages reminders through '@' routes."""
        self.login("user")
        login = self.client.post(
            "/login",
            {"username": "alice", "password": "secret", "key": self.key},
            headers={"accept": "text/html"},
        )
        self.assertEqual(login.status_code, 302)

        # Browser scripts mark their requests with the web header
        web = {"X-Apprise-Web-Auth": "1"}
        response = self.send("post", "/reminder/@/", _DAILY, headers=web)
        self.assertEqual(response.status_code, 200)
        rid = loads(response.content)["reminder"]["id"]

        response = self.client.get("/reminder/@/", headers=web)
        self.assertEqual([event["id"] for event in loads(response.content)["reminders"]], [rid])
        self.assertEqual(self.send("delete", f"/reminder/@/{rid}/", headers=web).status_code, 204)

        # The login covers only its own configuration
        response = self.client.get("/reminder/@/", headers={**web, "X-Apprise-Config-ID": "another-key"})
        self.assertEqual(response.status_code, 401)


class ReminderAttachmentTests(ReminderTestCase):
    """Attach files and web addresses to reminders."""

    def upload(self, method, path, payload, *names, headers=None):
        """Send a reminder with uploaded files, returning the response."""
        data = {"reminder": dumps(payload), "attach": [SimpleUploadedFile(name, name.encode()) for name in names]}
        return getattr(self.client, method)(
            path,
            data=encode_multipart(BOUNDARY, data),
            content_type=f"multipart/form-data; boundary={BOUNDARY}",
            headers={"Accept": "application/json", **(headers or {})},
        )

    def test_uploads_are_stored_and_kept(self):
        """Uploaded files are stored once, kept by reference, and replaced on request."""
        response = self.upload("post", f"/reminder/{self.key}/", {**_DAILY, "body": ""}, "a.txt", "b.txt")
        self.assertEqual(response.status_code, 200)
        event = loads(response.content)["reminder"]
        self.assertEqual([entry["filename"] for entry in event["attach"]], ["a.txt", "b.txt"])
        self.assertEqual(event["attach"][0]["size"], 5)
        path = f"/reminder/{self.key}/{event['id']}/"

        # A change that leaves out the attachments keeps them
        changed = loads(self.send("patch", path, {"name": "Renamed"}).content)["reminder"]
        self.assertEqual(changed["attach"], event["attach"])

        # A multipart change adds to the ones it keeps
        keep = [{"file": event["attach"][0]["file"]}]
        changed = loads(self.upload("patch", path, {"attach": keep}, "c.txt").content)["reminder"]
        self.assertEqual([entry["filename"] for entry in changed["attach"]], ["a.txt", "c.txt"])

        # A replacement without them drops them
        changed = loads(self.send("put", path, _DAILY).content)["reminder"]
        self.assertEqual(changed["attach"], [])

        # Only files this configuration's reminders already have can be kept
        response = self.send("patch", path, {"attach": [{"file": "0" * 64}]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(loads(response.content)["field"], "attach")

    def test_upload_patch_keeps_attachments_changed_meanwhile(self):
        """Files uploaded by a change are added to the attachments the reminder has by then."""
        event = loads(self.upload("post", f"/reminder/{self.key}/", _DAILY, "a.txt").content)["reminder"]
        path = f"/reminder/{self.key}/{event['id']}/"
        read = ConfigCache.read_reminders

        def patched(answers):
            # The first read sees ``answers[0]``, the read under the lock ``answers[1]``.
            return patch(
                "api.views.ConfigCache.read_reminders",
                side_effect=lambda path: answers.pop(0) if answers else read(path),
            )

        # Another request swaps a.txt for a web address after this one first reads the reminder
        saved = self.stored()
        with patched([saved, [{**saved[0], "attach": [{"url": "https://example.com/b.png"}]}]]):
            response = self.upload("patch", path, {}, "c.txt")

        attach = loads(response.content)["reminder"]["attach"]
        names = [entry.get("filename") or entry["url"] for entry in attach]
        self.assertEqual(names, ["https://example.com/b.png", "c.txt"])

        # Too many once added to the ones there by then is refused
        saved = self.stored()
        with override_settings(APPRISE_MAX_ATTACHMENTS=2), patched([[{**saved[0], "attach": attach[:1]}], saved]):
            response = self.upload("patch", path, {}, "d.txt")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.stored()[0]["attach"], attach)

    def test_copy_keeps_stored_files(self):
        """A new reminder, such as a copy, may keep files another reminder of the configuration stores."""
        original = loads(self.upload("post", f"/reminder/{self.key}/", _DAILY, "a.txt").content)["reminder"]
        copy = {**_DAILY, "name": "Copy", "attach": [{"file": original["attach"][0]["file"]}]}
        response = self.add(copy)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(loads(response.content)["reminder"]["attach"], original["attach"])

    def test_body_may_be_left_out_with_an_attachment(self):
        """A reminder with an attachment needs no body at all."""
        payload = {key: value for key, value in _DAILY.items() if key != "body"}
        response = self.add({**payload, "attach": ["https://example.com/a.png"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(loads(response.content)["reminder"]["body"], "")

    def test_web_addresses_are_saved_without_downloading(self):
        """Web addresses are checked when saved and downloaded only when sent."""
        attach = ["https://example.com/a.png", {"url": "https://example.com/b.png", "filename": "b.png"}, " "]
        event = loads(self.add({**_DAILY, "attach": attach}).content)["reminder"]
        self.assertEqual(
            event["attach"],
            [{"url": "https://example.com/a.png"}, {"url": "https://example.com/b.png", "filename": "b.png"}],
        )

        for bad in (
            "http://localhost/x.png",
            "http://127.0.0.1/x.png",
            "/etc/passwd",
            "file:///etc/passwd",
            {"url": "file:///etc/passwd"},
            {"file": "../../etc/passwd"},
            {"url": "https://example.com/c.png", "filename": 5},
        ):
            response = self.add({**_DAILY, "attach": bad})
            self.assertEqual(response.status_code, 400, bad)

        # Changing a reminder can replace its attachments entirely
        path = f"/reminder/{self.key}/{event['id']}/"
        changed = loads(self.send("put", path, {**_DAILY, "attach": ["https://example.com/new.png"]}).content)
        self.assertEqual(changed["reminder"]["attach"], [{"url": "https://example.com/new.png"}])

        # Embedded content is stored like an upload
        event = loads(self.add({**_DAILY, "attach": [{"base64": "aGVsbG8=", "filename": "hi.txt"}]}).content)
        self.assertEqual(event["reminder"]["attach"][0]["size"], 5)

    def test_attachment_rules(self):
        """The attachment count, the global switch and storage failures are respected."""
        with override_settings(APPRISE_MAX_ATTACHMENTS=1):
            response = self.upload("post", f"/reminder/{self.key}/", _DAILY, "a.txt", "b.txt")
            self.assertEqual(response.status_code, 400)

        with override_settings(APPRISE_ATTACH_SIZE=0):
            response = self.add({**_DAILY, "attach": "https://example.com/a.png"})
            self.assertEqual(response.status_code, 400)

        with patch("api.views.ConfigCache.store_reminder_file", return_value=None):
            response = self.upload("post", f"/reminder/{self.key}/", _DAILY, "a.txt")
            self.assertEqual(response.status_code, 500)

        # When a later file cannot be stored, the ones stored before it are removed
        store = ConfigCache.store_reminder_file
        calls = []

        def first_only(path):
            calls.append(path)
            return store(path) if len(calls) == 1 else None

        with patch("api.views.ConfigCache.store_reminder_file", side_effect=first_only):
            response = self.upload("post", f"/reminder/{self.key}/", _DAILY, "first-of-two.txt", "second.txt")
            self.assertEqual(response.status_code, 500)

        self.assertFalse(ConfigCache.reminder_file_stored(hashlib.sha256(b"first-of-two.txt").hexdigest()))

        # A file that must be stored again under the lock, but cannot be, is refused too
        calls.clear()
        with (
            patch("api.views.ConfigCache.reminder_file_stored", return_value=False),
            patch("api.views.ConfigCache.store_reminder_file", side_effect=first_only),
        ):
            response = self.upload("post", f"/reminder/{self.key}/", _DAILY, "stored-again.txt")
            self.assertEqual(response.status_code, 500)

        with patch("api.views.ConfigCache.reminder_file_digest", return_value=None):
            response = self.upload("post", f"/reminder/{self.key}/", _DAILY, "a.txt")
            self.assertEqual(response.status_code, 500)

        # A change whose new file cannot be stored is not saved
        rid = loads(self.add().content)["reminder"]["id"]
        with patch("api.views.ConfigCache.store_reminder_file", return_value=None):
            response = self.upload("patch", f"/reminder/{self.key}/{rid}/", {}, "a.txt")
            self.assertEqual(response.status_code, 500)

    def test_bad_multipart_payloads(self):
        """The reminder field of an upload must hold a JSON object."""
        path = f"/reminder/{self.key}/"
        for value in ("not json", "[1]"):
            response = self.client.post(path, data={"reminder": value})
            self.assertEqual(response.status_code, 400, value)

        with patch("api.views.json.loads", side_effect=RequestDataTooBig):
            self.assertEqual(self.client.post(path, data={"reminder": "{}"}).status_code, 431)


@override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
class ReminderLimitsTests(ReminderTestCase):
    """Set limits, which hold back everyone but the administrator."""

    def setUp(self):
        """Give the configuration a user."""
        super().setUp()
        self.assertTrue(ConfigCache.set_auth(self.key, "alice", "secret", access="user"))

    def limits(self, payload=None, headers=_MASTER):
        """Read the limits, or save them when a payload is given."""
        path = f"/reminder/{self.key}/limits/"
        if payload is None:
            return self.client.get(path, headers=headers)

        return self.send("post", path, payload, headers=headers)

    def test_limits_round_trip(self):
        """Limits are saved, reported with their use, and reset with null."""
        self.assertEqual(
            loads(self.limits().content)["limits"],
            {"reminders": 100, "reminders_used": 0, "attach_size": 50 * 1048576, "attach_used": 0},
        )

        saved = loads(self.limits({"reminders": 2, "attach_size": 10}).content)["limits"]
        self.assertEqual(saved["reminders"], 2)
        self.assertEqual(saved["attach_size"], 10)

        # Leaving a field out keeps it, and null puts it back to its default
        saved = loads(self.limits({"reminders": None}).content)["limits"]
        self.assertEqual((saved["reminders"], saved["attach_size"]), (100, 10))
        saved = loads(self.limits({"attach_size": None}).content)["limits"]
        self.assertEqual(saved["attach_size"], 50 * 1048576)

        # A server quota of 0 turns reminder uploads off, except for the administrator
        with override_settings(APPRISE_REMINDER_ATTACH_QUOTA=0):
            self.limits({"attach_size": None})
            self.assertEqual(loads(self.limits().content)["limits"]["attach_size"], 0)
            path = f"/reminder/{self.key}/"
            response = ReminderAttachmentTests.upload(self, "post", path, _DAILY, "no-quota.txt", headers=_USER)
            self.assertEqual(response.status_code, 413)
            response = ReminderAttachmentTests.upload(self, "post", path, _DAILY, "no-quota.txt", headers=_MASTER)
            self.assertEqual(response.status_code, 200)

        for payload, field in (
            ({"reminders": 0}, "reminders"),
            ({"attach_size": -1}, "attach_size"),
            ({"attach_size": True}, "attach_size"),
            ({"reminders": "2"}, "reminders"),
        ):
            response = self.limits(payload)
            self.assertEqual(response.status_code, 400, payload)
            self.assertEqual(loads(response.content)["field"], field)

        # Limits are sent as JSON
        response = self.client.post(f"/reminder/{self.key}/limits/", data={"reminders": 3}, headers=_MASTER)
        self.assertEqual(response.status_code, 400)

    def test_reminder_count_limit(self):
        """The user is held to the server default, or to the configuration's own limit."""
        with override_settings(APPRISE_REMINDER_MAX=1):
            self.assertEqual(self.add(headers=_USER).status_code, 200)
            self.assertEqual(self.add(headers=_USER).status_code, 409)

        self.limits({"reminders": 2})
        self.assertEqual(self.add(headers=_USER).status_code, 200)
        self.assertEqual(self.add(headers=_USER).status_code, 409)
        self.assertEqual(loads(self.limits().content)["limits"]["reminders_used"], 2)

        # The administrator is never limited
        self.assertEqual(self.add(headers=_MASTER).status_code, 200)

    def test_attachment_quota(self):
        """Stored files count once against the quota, and a lowered quota never blocks other edits."""

        def upload(method, path, payload, name, headers=_USER):
            return ReminderAttachmentTests.upload(self, method, path, payload, name, headers=headers)

        self.limits({"attach_size": 10})
        first = loads(upload("post", f"/reminder/{self.key}/", _DAILY, "aaaa.txt").content)["reminder"]

        # The same file on a second reminder takes no more room
        self.assertEqual(upload("post", f"/reminder/{self.key}/", _DAILY, "aaaa.txt").status_code, 200)
        self.assertEqual(loads(self.limits().content)["limits"]["attach_used"], 8)

        response = upload("post", f"/reminder/{self.key}/", _DAILY, "bbbb.txt")
        self.assertEqual(response.status_code, 413)
        self.assertEqual(loads(response.content)["field"], "attach")
        # A refused upload is not stored
        self.assertFalse(ConfigCache.reminder_file_stored(hashlib.sha256(b"bbbb.txt").hexdigest()))

        # The administrator is never held to the quota
        self.assertEqual(upload("post", f"/reminder/{self.key}/", _DAILY, "bbbb.txt", _MASTER).status_code, 200)

        self.limits({"attach_size": 0})
        response = self.send("patch", f"/reminder/{self.key}/{first['id']}/", {"name": "Still fine"}, headers=_USER)
        self.assertEqual(response.status_code, 200)
        response = upload("patch", f"/reminder/{self.key}/{first['id']}/", {}, "cc.txt")
        self.assertEqual(response.status_code, 413)
        self.assertFalse(ConfigCache.reminder_file_stored(hashlib.sha256(b"cc.txt").hexdigest()))

    def test_zero_quota_turns_attachments_off(self):
        """A quota of 0 refuses new attachments, web addresses included; ones already there may stay."""
        with_url = {**_DAILY, "attach": "https://example.com/a.png"}

        # The administrator may still add one
        rid = loads(self.add(with_url, headers=_MASTER).content)["reminder"]["id"]
        self.limits({"attach_size": 0})

        response = self.add(with_url, headers=_USER)
        self.assertEqual(response.status_code, 413)
        message = loads(response.content)["error"]
        self.assertEqual(message, "Attachments are turned off for this configuration's reminders")

        # Changing a reminder that already has one is fine
        path = f"/reminder/{self.key}/{rid}/"
        self.assertEqual(self.send("patch", path, {"name": "Renamed"}, headers=_USER).status_code, 200)

    def test_server_attachment_limit(self):
        """New files must fit the space left on the server, except for the administrator."""

        def upload(method, path, payload, name, headers=_USER):
            return ReminderAttachmentTests.upload(self, method, path, payload, name, headers=headers)

        # Room for one more 18 byte file than is stored now
        with override_settings(APPRISE_REMINDER_STORE_MAX=ConfigCache.reminder_store_size() + 20):
            first = loads(upload("post", f"/reminder/{self.key}/", _DAILY, "server-limit-a.txt").content)["reminder"]
            response = upload("post", f"/reminder/{self.key}/", _DAILY, "server-limit-b.txt")
            self.assertEqual(response.status_code, 413)
            message = loads(response.content)["error"]
            self.assertEqual(message, "There is not enough attachment space left on this server")

            # A file already stored takes no more room, and changes without new files are fine
            self.assertEqual(upload("post", f"/reminder/{self.key}/", _DAILY, "server-limit-a.txt").status_code, 200)
            path = f"/reminder/{self.key}/{first['id']}/"
            self.assertEqual(self.send("patch", path, {"name": "Renamed"}, headers=_USER).status_code, 200)
            self.assertEqual(upload("patch", path, {}, "server-limit-c.txt").status_code, 413)

            # Saves that each fit alone, but not together, are caught under the lock
            name = "server-limit-d.txt"
            stored = hashlib.sha256(name.encode()).hexdigest()
            with patch("api.views.ConfigCache.reminder_store_size", side_effect=[0, 10**12]):
                self.assertEqual(upload("post", f"/reminder/{self.key}/", _DAILY, name).status_code, 413)

            self.assertFalse(ConfigCache.reminder_file_stored(stored))

            # A store whose size cannot be read has no room
            with patch("api.views.ConfigCache.reminder_store_size", return_value=None):
                self.assertEqual(upload("post", f"/reminder/{self.key}/", _DAILY, name).status_code, 413)

            # The administrator is never held to it
            response = upload("post", f"/reminder/{self.key}/", _DAILY, "server-limit-b.txt", _MASTER)
            self.assertEqual(response.status_code, 200)

    def test_changes_made_meanwhile_are_checked_again(self):
        """A save checks again under the lock, in case the reminders changed after the first check."""
        rid = loads(self.add(headers=_USER).content)["reminder"]["id"]
        saved = self.stored()
        # Another reminder that takes all of the default attachment space
        big = {**saved[0], "id": "b" * 32, "attach": [{"file": "9" * 64, "filename": "big", "size": 50 * 1048576}]}

        read = ConfigCache.read_reminders

        def reads(second):
            # The first read sees the reminders as saved; the one under the lock sees
            # ``second``; any later read, such as when tidying files, sees what is saved.
            answers = [saved, second]
            return patch(
                "api.views.ConfigCache.read_reminders",
                side_effect=lambda path: answers.pop(0) if answers else read(path),
            )

        path = f"/reminder/{self.key}/{rid}/"
        for second, status in ((None, 500), ([], 404), ([*saved, big], 413)):
            with reads(second):
                response = ReminderAttachmentTests.upload(self, "patch", path, {}, "x.txt", headers=_USER)
                self.assertEqual(response.status_code, status, second)

        # A file stored for a change that was then refused is removed again
        self.assertFalse(ConfigCache.reminder_file_stored(hashlib.sha256(b"x.txt").hexdigest()))

        with reads(None):
            self.assertEqual(self.add(headers=_USER).status_code, 500)

        with reads([*saved, big]):
            response = ReminderAttachmentTests.upload(
                self, "post", f"/reminder/{self.key}/", _DAILY, "x.txt", headers=_USER
            )
            self.assertEqual(response.status_code, 413)

        # A file kept from before must still be in the store
        with patch("api.views.ConfigCache.reminder_file_stored", return_value=False):
            response = ReminderAttachmentTests.upload(self, "patch", path, {}, "x.txt", headers=_USER)
            self.assertEqual(response.status_code, 400)

        self.assertEqual(len(self.stored()), 1)

    def test_limit_storage_failures(self):
        """Limits that cannot be read or saved are reported."""
        rid = loads(self.add(headers=_USER).content)["reminder"]["id"]
        with patch("api.views.ConfigCache.get_limits", return_value=None):
            self.assertEqual(self.limits().status_code, 500)
            self.assertEqual(self.limits({"reminders": 3}).status_code, 500)
            self.assertEqual(self.add(headers=_USER).status_code, 500)
            response = self.send("patch", f"/reminder/{self.key}/{rid}/", {"name": "x"}, headers=_USER)
            self.assertEqual(response.status_code, 500)

        with patch("api.views.ConfigCache.set_limits", return_value=False):
            self.assertEqual(self.limits({"reminders": 3}).status_code, 500)

        with patch("api.views.ConfigCache.read_reminders", return_value=None):
            self.assertEqual(self.limits().status_code, 500)
            self.assertEqual(self.limits({"reminders": 3}).status_code, 500)

    def test_only_the_administrator_sets_limits(self):
        """A configuration's user can read its limits but not change them."""
        self.assertEqual(self.limits(headers={}).status_code, 401)
        self.assertEqual(self.limits({"reminders": 3}, headers={}).status_code, 401)
        self.assertEqual(self.limits(headers=_USER).status_code, 200)
        self.assertEqual(self.limits({"reminders": 3}, headers=_USER).status_code, 403)
        self.assertEqual(self.limits({"reminders": 3}, headers=_MASTER).status_code, 200)


@override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
class ReminderAccountTests(ReminderTestCase):
    """Reminders follow the configuration's login."""

    def seed(self):
        """Add one reminder as the administrator and one as the user."""
        self.assertTrue(ConfigCache.set_auth(self.key, "alice", "secret", access="user"))
        self.assertEqual(self.add(headers=_MASTER).status_code, 200)
        self.assertEqual(self.add(headers=_USER).status_code, 200)

    def test_reset_access_removes_the_users_reminders(self):
        """Removing the login removes what its user added."""
        self.seed()
        before = self.stored()

        # A login that cannot be removed keeps its user's reminders
        with patch("api.views.ConfigCache.clear_auth", return_value=False):
            self.assertEqual(self.client.delete(f"/auth/{self.key}", headers=_MASTER).status_code, 500)

        self.assertEqual(self.stored(), before)

        # So does a reminder file that cannot be read first
        with patch("api.views.ConfigCache.read_reminders", return_value=None):
            self.assertEqual(self.client.delete(f"/auth/{self.key}", headers=_MASTER).status_code, 500)

        # A change made meanwhile is kept, and the user's reminder still comes back
        path = ConfigCache.reminder_path(self.key)
        added = {"id": "c" * 32, "owner": "admin"}

        def change_then_fail(key):
            with ConfigCache.reminder_guard():
                (admin,) = ConfigCache.read_reminders(path)
                ConfigCache.write_reminders(path, [{**admin, "name": "Changed"}, added])

            return False

        with patch("api.views.ConfigCache.clear_auth", side_effect=change_then_fail):
            self.assertEqual(self.client.delete(f"/auth/{self.key}", headers=_MASTER).status_code, 500)

        restored = self.stored()
        self.assertEqual([event["id"] for event in restored], [before[0]["id"], added["id"], before[1]["id"]])
        self.assertEqual(restored[0]["name"], "Changed")
        self.assertEqual(restored[2], before[1])

        # Nothing is put back when the reminders cannot be read then
        def fail(key):
            return False

        read = ConfigCache.read_reminders
        with (
            patch("api.views.ConfigCache.clear_auth", side_effect=fail),
            patch("api.views.ConfigCache.read_reminders", side_effect=[read(path), None]),
        ):
            self.assertEqual(self.client.delete(f"/auth/{self.key}", headers=_MASTER).status_code, 500)

        with ConfigCache.reminder_guard():
            ConfigCache.write_reminders(path, before)

        self.assertEqual(self.client.delete(f"/auth/{self.key}", headers=_MASTER).status_code, 200)
        self.assertEqual([event["owner"] for event in self.stored()], ["admin"])

    def test_reset_access_tidies_replaced_times(self):
        """Removing the user's reminders gives back times they replaced, and drops what replaced theirs."""
        self.assertTrue(ConfigCache.set_auth(self.key, "alice", "secret", access="user"))
        admin_series = loads(self.add(headers=_MASTER).content)["reminder"]
        user_series = loads(self.add({**_DAILY, "name": "Mine"}, headers=_USER).content)["reminder"]
        moved = {**_DAILY, "name": "Moved", "when": {"tz": "Etc/UTC", "at": "2099-01-01T10:00:00"}}
        for series, headers in ((admin_series, _USER), (user_series, _MASTER)):
            payload = {**moved, "series": series["id"], "occurrence": series["next"]}
            self.assertEqual(self.add(payload, headers=headers).status_code, 200)

        self.assertEqual(self.client.delete(f"/auth/{self.key}", headers=_MASTER).status_code, 200)
        (stored,) = self.stored()
        self.assertEqual(stored["id"], admin_series["id"])
        self.assertEqual(stored["skip"], [])
        self.assertEqual(stored["next"], admin_series["next"])

    def test_reset_access_can_keep_the_users_reminders(self):
        """With keep_reminders the administrator takes over the user's reminders."""
        self.seed()
        response = self.client.delete(f"/auth/{self.key}?keep_reminders=yes", headers=_MASTER)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([event["owner"] for event in self.stored()], ["admin", "admin"])

        # A failure to update them leaves the login in place
        self.assertTrue(ConfigCache.set_auth(self.key, "alice", "secret", access="user"))
        with patch("api.views.ConfigCache.release_user_reminders", return_value=None):
            self.assertEqual(self.client.delete(f"/auth/{self.key}", headers=_MASTER).status_code, 500)

        self.assertIsNotNone(ConfigCache.get_auth(self.key))

    def test_admin_delete_removes_limits(self):
        """Deleting a configuration as the administrator removes its limits too."""
        self.assertTrue(ConfigCache.set_limits(self.key, {"reminders": 3}))
        self.assertEqual(self.client.post(f"/del/{self.key}", headers=_MASTER).status_code, 204)
        self.assertEqual(ConfigCache.get_limits(self.key), {})

        self.assertTrue(ConfigCache.put(self.key, "json://localhost", "text"))
        with patch("api.views.ConfigCache.set_limits", return_value=False):
            self.assertEqual(self.client.post(f"/del/{self.key}", headers=_MASTER).status_code, 204)

    def test_deleting_the_configuration_removes_its_reminders(self):
        """Whoever deletes the configuration, its reminders go with it."""
        for headers in (_USER, _MASTER):
            self.seed()
            self.assertEqual(self.client.post(f"/del/{self.key}", headers=headers).status_code, 204)
            self.assertFalse(self.stored())
            self.assertTrue(ConfigCache.put(self.key, "json://localhost", "text"))

        # A failure to remove them leaves the configuration in place
        self.seed()
        with patch("os.replace", side_effect=OSError("denied")):
            self.assertEqual(self.client.post(f"/del/{self.key}", headers=_MASTER).status_code, 500)

        self.assertEqual(ConfigCache.get(self.key)[0], "json://localhost")
        self.assertEqual(len(self.stored()), 2)

    @override_settings(APPRISE_REMINDER_IDLE_DAYS=30)
    def test_use_is_recorded(self):
        """A request that passes the access check records the configuration as used."""
        self.seed()
        seen = ConfigCache.seen_path(self.key)
        if os.path.exists(seen):
            os.remove(seen)

        self.assertEqual(self.client.get(f"/reminder/{self.key}/", headers=_USER).status_code, 200)
        self.assertTrue(os.path.exists(seen))


@override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
class ReminderIntervalTests(ReminderTestCase):
    """A configuration's user cannot set a reminder to repeat too often."""

    def setUp(self):
        """Give the configuration a user."""
        super().setUp()
        self.assertTrue(ConfigCache.set_auth(self.key, "alice", "secret", access="user"))

    @staticmethod
    def every(interval, unit="seconds"):
        """Return a reminder that repeats every ``interval`` units."""
        when = {"tz": "Etc/UTC", "at": "2099-01-01T09:00:00", "every": {"unit": unit, "interval": interval}}
        return {**_DAILY, "when": when}

    def test_user_is_held_to_the_interval(self):
        """A user's reminder may not repeat more often than the setting allows."""
        response = self.add(self.every(30), headers=_USER)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(loads(response.content)["field"], "when")
        self.assertEqual(self.add(self.every(60), headers=_USER).status_code, 200)

        # A crontab firing twice a minute is held to it too
        twice = {**_DAILY, "when": {"tz": "Etc/UTC", "cron": "0,30 * * * * *"}}
        self.assertEqual(self.add(twice, headers=_USER).status_code, 400)

        once = {**_DAILY, "when": {"tz": "Etc/UTC", "at": "2099-01-01T09:00:00"}}
        self.assertEqual(self.add(once, headers=_USER).status_code, 200)

    def test_administrator_and_open_settings_have_no_limit(self):
        """The administrator may repeat every second, and so may a user once the setting is 0 or 1."""
        self.assertEqual(self.add(self.every(1), headers=_MASTER).status_code, 200)
        for limit in (0, 1):
            with override_settings(APPRISE_REMINDER_USER_MIN_INTERVAL=limit):
                self.assertEqual(self.add(self.every(1), headers=_USER).status_code, 200)

    def test_user_may_keep_or_slow_an_administrator_reminder(self):
        """A user can edit a faster reminder the administrator set, but not make it faster."""
        rid = loads(self.add(self.every(10), headers=_MASTER).content)["reminder"]["id"]
        path = f"/reminder/{self.key}/{rid}/"
        self.assertEqual(self.send("patch", path, {"body": "Changed"}, headers=_USER).status_code, 200)
        self.assertEqual(self.send("patch", path, self.every(20), headers=_USER).status_code, 200)
        self.assertEqual(self.send("patch", path, self.every(15), headers=_USER).status_code, 400)
        self.assertEqual(self.send("patch", path, self.every(2, "minutes"), headers=_USER).status_code, 200)
        self.assertEqual(self.send("patch", path, self.every(20), headers=_USER).status_code, 400)

    def test_unusable_times_are_refused(self):
        """A time whose later runs cannot be worked out is refused, and so is a damaged saved one."""
        with patch("api.views.shortest_gap", side_effect=ValueError):
            response = self.add(self.every(30), headers=_USER)

        self.assertEqual(loads(response.content)["field"], "when")

        rid = loads(self.add(self.every(10), headers=_MASTER).content)["reminder"]["id"]
        with patch("api.views.shortest_gap", side_effect=[20, ValueError]):
            response = self.send("patch", f"/reminder/{self.key}/{rid}/", self.every(20), headers=_USER)

        self.assertEqual(response.status_code, 400)


class ReminderCalendarTests(ReminderTestCase):
    """List when each reminder is due, for the calendar."""

    def test_occurrences_between_two_times(self):
        """Each reminder lists the times it is due inside the window, from now on."""
        daily = loads(self.add().content)["reminder"]
        paused = loads(self.add({**_DAILY, "name": "Off", "enabled": False}).content)["reminder"]
        start = daily["next"] - 1
        response = self.client.get(f"/reminder/{self.key}/?start={start}&end={start + 3 * 86400}")
        found = {event["id"]: event["occurrences"] for event in loads(response.content)["reminders"]}
        self.assertEqual(found[daily["id"]], [daily["next"], daily["next"] + 86400, daily["next"] + 2 * 86400])
        self.assertEqual(found[paused["id"]], [])

        # A window in the past shows nothing, since sends already made are not repeated
        response = self.client.get(f"/reminder/{self.key}/?start=0&end=1000")
        self.assertEqual([event["occurrences"] for event in loads(response.content)["reminders"]], [[], []])

    def test_paused_times_listed_on_request(self):
        """A paused reminder lists its times only when ``disabled=show`` asks for them."""
        paused = loads(self.add({**_DAILY, "name": "Off", "enabled": False}).content)["reminder"]
        start = paused["next"] - 1
        window = f"start={start}&end={start + 3 * 86400}"
        listed = loads(self.client.get(f"/reminder/{self.key}/?{window}").content)["reminders"]
        self.assertEqual(listed[0]["occurrences"], [])

        listed = loads(self.client.get(f"/reminder/{self.key}/?{window}&disabled=show").content)["reminders"]
        self.assertEqual(listed[0]["occurrences"], [paused["next"] + day * 86400 for day in range(3)])

    def test_occurrences_skip_unusable_times(self):
        """A reminder whose time cannot be worked out lists nothing."""
        self.add()
        with ConfigCache.reminder_guard():
            events = self.stored()
            events[0]["when"] = {"cron": "garbage"}
            ConfigCache.write_reminders(ConfigCache.reminder_path(self.key), events)

        response = self.client.get(f"/reminder/{self.key}/?start=0&end={2**31}")
        self.assertEqual(loads(response.content)["reminders"][0]["occurrences"], [])

    def test_window_must_be_numbers(self):
        """Both ends of the window must be UNIX timestamps."""
        response = self.client.get(f"/reminder/{self.key}/?start=soon", headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(loads(response.content)["field"], "start")

        # A start too far off to be a date is refused the same way
        response = self.client.get(
            f"/reminder/{self.key}/?start={10**20}&end={10**20 + 1}", headers={"Accept": "application/json"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(loads(response.content)["field"], "start")


class ReminderPageTests(ReminderTestCase):
    """The configuration page offers reminders when they are turned on."""

    def page(self):
        """Return the configuration page's HTML."""
        return self.client.get(f"/cfg/{self.key}", headers={"Accept": "text/html"}).content.decode()

    def sign_in(self, username, password, key=None):
        """Sign the browser in, as the administrator or as the configuration's user."""
        self.client.cookies.clear()
        form = {"username": username, "password": password, **({"key": key} if key else {})}
        response = self.client.post("/login", form, headers={"Accept": "text/html"})
        self.assertEqual(response.status_code, 302)

    def test_reminder_tab_and_section(self):
        """The tab lists reminders; the Notifications form holds when to send one."""
        html = self.page()
        for marker in (
            '<a href="#reminders" aria-label=',
            'id="reminder-calendar"',
            'id="reminder-toggle"',
            'id="reminder-form"',
            'id="notify-add-reminder"',
            'id="reminder-holiday"',
            # A holiday's start moves a year at a time
            'id="reminder-holiday-previous"',
            'id="reminder-holiday-next"',
            # It goes as far ahead as a reminder looks for a holiday
            f'data-last-year="{datetime.now(ZoneInfo(settings.TIME_ZONE)).year + HOLIDAY_YEARS - 1}"',
            # A holiday sent once is not a series
            "reminderIsOneOff(reminder.when) || reminder.count === 1",
            # Days and months can be picked all, none or inverted at once
            'data-reminder-pick="reminder-days"',
            'data-reminder-pick="reminder-months"',
            f"/reminder/{self.key}/holidays'",
            "fullcalendar.global.min.js",
        ):
            self.assertIn(marker, html)

        # The schedule sits after the message and before the buttons; the list is on its own tab
        self.assertLess(html.index('id="id_body"'), html.index('id="notify-reminder"'))
        self.assertLess(html.index('id="notify-reminder"'), html.index('id="notify-add-reminder"'))
        self.assertLess(html.index('id="donotify"'), html.index('id="reminders"'))
        self.assertLess(html.index("</form>", html.index('id="donotify"')), html.index('id="reminder-calendar"'))
        # Limits are set on the access page instead
        self.assertNotIn('id="reminder-limits-form"', html)
        # Make recurring uses the same switch as the Review tab's filter.
        self.assertRegex(html, r'<span class="review-selected-filter">\s*<input id="reminder-recurring"')

        with override_settings(APPRISE_REMINDER_ENABLED=False):
            html = self.page()
        for marker in (
            '<a href="#reminders" aria-label=',
            'id="notify-reminder"',
            'id="notify-add-reminder"',
            "fullcalendar.global.min.js",
        ):
            self.assertNotIn(marker, html)

    def test_empty_tab_points_to_notifications(self):
        """With no reminders, the tab explains how to add one and links to Notifications."""
        html = self.page()
        self.assertRegex(html, r'id="reminder-empty"\s+class="notify-target-guidance reminder-empty"')
        self.assertIn("No Reminders Yet", html)
        self.assertIn("All pre-saved notifications set to be sent at a later time are listed here.", html)
        self.assertRegex(html, r'have been added. Use the <a href="#notify" class="tab-chip tab-chip-link">')
        # Like Notifications, the tab stays off until the configuration has a URL
        self.assertIn('li a[href="#notify"], .config-overview li a[href="#reminders"]', html)
        # The All view shows the same empty box as the calendar views
        self.assertIn("'fc-list reminder-all-empty'", html)
        self.assertIn("No reminders have been added yet.", html)

    def test_reminders_tab_lists_all_and_picks_a_timezone(self):
        """The tab has an All view of every reminder and a timezone to show times in."""
        html = self.page()
        self.assertRegex(html, r'<label for="reminder-view-zone">Show times in:</label>')
        self.assertIn('aria-controls="reminder-view-zone-list"', html)
        self.assertRegex(html, r'id="reminder-view-zone-reset"[^>]+aria-label="Use my timezone"')
        # Option text has no spaces around it, or the dropdown shows them
        self.assertRegex(html, r'<option value="seconds"[^>]*>Seconds</option>')
        self.assertIn("listMonth,timeGridWeek,dayGridMonth,reminderAll", html)
        self.assertIn("buttonText: 'All'", html)
        # Calendar times show seconds only for a reminder whose times need them.
        self.assertIn("reminderShowsSeconds(props.reminder.when, props.reminder.start)", html)
        # List rows show the tags and mark a failed send; finished reminders keep their buttons
        self.assertIn("None (untagged services only)", html)
        self.assertIn("' is-failed'", html)
        self.assertIn("props.past && !reminder.next", html)
        # The start picks seconds, including a holiday's time of day; the end time does not
        self.assertEqual(len(re.findall(r"data-reminder-seconds\s+aria-haspopup", html)), 1)
        self.assertIn("reminder-picker-second", html)
        # An edit link opens its reminder even after the tabs change the address
        self.assertIn("reminderFromHash(wanted)", html)
        # Pausing a repeating reminder says it pauses the whole series
        self.assertIn("return paused ? 'Resume Series' : 'Pause Series';", html)
        self.assertEqual(html.count("reminderPauseLabel(reminder, paused), async"), 2)
        # Fields Chrome takes for a person or an address get a value it does not know
        self.assertRegex(html, r'<input id="reminder-name"[^>]*autocomplete="apprise-none"')
        self.assertRegex(html, r'<select id="reminder-region"[^>]*autocomplete="apprise-none"')
        self.assertRegex(html, r'<input[^>]*name="title"[^>]*autocomplete="apprise-none"')
        self.assertIn("keyInput.autocomplete = 'apprise-none';", html)
        # One saved timezone, kept in a cookie, is shared by both timezone fields
        self.assertIn("'apprise_reminder_zone'", html)
        self.assertIn("const zone = selected || reminderViewZone;", html)
        self.assertRegex(html, r'<label for="reminder-drift">Drift:</label>')
        # Drift is entered in a unit and saved in seconds
        self.assertRegex(html, r'<select id="reminder-drift-unit"[^>]*>\s*<option value="1">Seconds</option>')
        self.assertIn('<option value="604800">Weeks</option>', html)
        self.assertIn("Number(drift) * reminderDriftSize()", html)
        self.assertIn(f'min="{DRIFT_MIN}"', html)
        self.assertIn(f'max="{DRIFT_MAX}"', html)
        # A holiday shows the drift without the repeat choices
        self.assertIn('<div class="col s12 reminder-every" data-reminder-until>', html)
        self.assertIn('<div class="input-field reminder-every-main" data-reminder-repeat>', html)
        # A negative drift keeps its minus sign
        self.assertIn(r"/^-?\d+$/.test(drift)", html)
        self.assertIn("'Finished'", html)
        # Saving opens the tab at the reminder's first time
        self.assertIn("reminderReveal(saved);", html)
        # The tab shows how many reminders there are, the same as All
        self.assertIn('<span class="reminder-tab-count"></span>', html)

    def test_date_picker_is_served_locally(self):
        """The date picker and its styles come from this server, not a third party."""
        html = self.page()
        self.assertIn("/s/js/air-datepicker.js?v=3.6.0", html)
        self.assertIn("/s/css/air-datepicker.css?v=3.6.0", html)
        self.assertIn('data-reminder-date="reminder-date"', html)
        self.assertNotRegex(html, r'<(script|link)[^>]+(src|href)="https?://')

    def test_attachments_have_their_own_section(self):
        """Files and web addresses are added in one Attachments section, used by sending and by reminders."""
        html = self.page()
        for marker in ('id="notify-attach-rows"', 'id="notify-attach-add"', f"/attach/{self.key}'"):
            self.assertIn(marker, html)

        # Below the message and above the Reminder section
        self.assertLess(html.index('id="id_body"'), html.index('id="notify-attach-rows"'))
        self.assertLess(html.index('id="notify-attach-rows"'), html.index('id="notify-reminder"'))
        # Send Notification passes web addresses on as text for the server to fetch
        self.assertIn("form.append('attach', entry.url);", html)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_page_offers_only_what_the_caller_may_do(self):
        """Users see how much of their limits is used; locked users see no reminders; the administrator always does."""
        self.assertTrue(ConfigCache.set_auth(self.key, "alice", "secret", access="user"))
        self.sign_in("alice", "secret", self.key)
        html = self.page()
        self.assertIn('id="notify-add-reminder"', html)
        self.assertIn('id="reminder-usage-count"', html)
        # The full attachment space alert waits, hidden, on the tab and in the Reminder section
        self.assertEqual(html.count('class="notify-target-guidance reminder-space-full"'), 2)

        # Locked: no tab, form, buttons or scripts for its own user
        self.assertTrue(ConfigCache.set_access(self.key, "locked"))
        html = self.page()
        for marker in ('<a href="#reminders"', 'id="reminder-form"', 'id="notify-add-reminder"', "remindersInit()"):
            self.assertNotIn(marker, html)

        # The administrator still has them on a locked configuration
        self.sign_in("master", "pass")
        self.assertIn('<a href="#reminders"', self.page())

        # The administrator has them whatever the access, but no usage line
        for access in ("public", "disabled"):
            self.assertTrue(ConfigCache.set_access(self.key, access))
            html = self.page()
            self.assertIn('<a href="#reminders"', html)
            self.assertIn('id="notify-add-reminder"', html)
            self.assertNotIn('id="reminder-usage-count"', html)
            self.assertNotIn('class="notify-target-guidance reminder-space-full"', html)

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_limits_are_set_on_the_access_page(self):
        """The administrator sets limits on the access page once the configuration has a login."""
        path = f"/auth/{self.key}"
        headers = {"Accept": "text/html"}
        self.sign_in("master", "pass")
        self.assertNotIn('id="reminder-limits-form"', self.client.get(path, headers=headers).content.decode())

        self.assertTrue(ConfigCache.set_auth(self.key, "alice", "secret", access="user"))
        html = self.client.get(path, headers=headers).content.decode()
        self.assertIn('id="reminder-limits-form"', html)
        self.assertIn(f"/reminder/{self.key}/limits", html)

        # The user does not see them
        self.sign_in("alice", "secret", self.key)
        self.assertNotIn('id="reminder-limits-form"', self.client.get(path, headers=headers).content.decode())

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_seconds_are_offered_only_when_allowed(self):
        """Seconds are hidden from a user held to a minute or more."""
        locked = re.compile(r'<option value="seconds"\s+data-reminder-seconds-locked>')
        self.assertTrue(ConfigCache.set_auth(self.key, "alice", "secret", access="user"))
        self.sign_in("alice", "secret", self.key)
        self.assertRegex(self.page(), locked)
        with override_settings(APPRISE_REMINDER_USER_MIN_INTERVAL=30):
            self.assertNotRegex(self.page(), locked)

        self.sign_in("master", "pass")
        self.assertNotRegex(self.page(), locked)

    def test_scheduler_notice(self):
        """The tab says when no scheduler is running to send reminders."""
        notice = "The scheduler is not running, so reminders are saved but not sent."
        self.assertIn(notice, self.page())
        with patch("api.views.ConfigCache.scheduler_running", return_value=True):
            self.assertNotIn(notice, self.page())

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN, APPRISE_USER="master")
    def test_reset_access_offers_to_keep_reminders(self):
        """The Reset Access dialog can keep the user's reminders when reminders are on."""
        self.assertTrue(ConfigCache.set_auth(self.key, "alice", "secret", access="user"))
        self.sign_in("master", "pass")
        path = f"/auth/{self.key}"
        headers = {"Accept": "text/html"}
        self.assertIn('id="swal-keep-reminders"', self.client.get(path, headers=headers).content.decode())
        with override_settings(APPRISE_REMINDER_ENABLED=False):
            self.assertNotIn('id="swal-keep-reminders"', self.client.get(path, headers=headers).content.decode())
