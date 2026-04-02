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
from django.test import SimpleTestCase, override_settings

from ..payload_mapper import remap_fields, _get_nested


class NotifyPayloadMapper(SimpleTestCase):
    """
    Test Payload Mapper
    """

    def test_remap_fields(self):
        """
        Test payload re-mapper
        """

        #
        # No rules defined
        #
        rules = {}
        payload = {
            "format": "markdown",
            "title": "title",
            "body": "# body",
        }
        payload_orig = payload.copy()

        # Map our fields
        remap_fields(rules, payload)

        # no change is made
        assert payload == payload_orig

        #
        # rules defined - test 1
        #
        rules = {
            # map 'as' to 'format'
            "as": "format",
            # map 'subject' to 'title'
            "subject": "title",
            # map 'content' to 'body'
            "content": "body",
            # 'missing' is an invalid entry so this will be skipped
            "unknown": "missing",
            # Empty field
            "attachment": "",
            # Garbage is an field that can be removed since it doesn't
            # conflict with the form
            "garbage": "",
            # Tag
            "tag": "test",
        }
        payload = {
            "as": "markdown",
            "subject": "title",
            "content": "# body",
            "tag": "",
            "unknown": "hmm",
            "attachment": "",
            "garbage": "",
        }

        # Map our fields
        remap_fields(rules, payload)

        # Our field mappings have taken place
        assert payload == {
            "tag": "test",
            "unknown": "missing",
            "format": "markdown",
            "title": "title",
            "body": "# body",
        }

        #
        # rules defined - test 2
        #
        rules = {
            #
            # map 'content' to 'body'
            "content": "body",
            # a double mapping to body will trigger an error
            "message": "body",
            # Swapping fields
            "body": "another set of data",
        }
        payload = {
            "as": "markdown",
            "subject": "title",
            "content": "# content body",
            "message": "# message body",
            "body": "another set of data",
        }

        # Map our fields
        remap_fields(rules, payload)

        # Our information gets swapped
        assert payload == {
            "as": "markdown",
            "subject": "title",
            "body": "another set of data",
        }

        #
        # swapping fields - test 3
        #
        rules = {
            #
            # map 'content' to 'body'
            "title": "body",
        }
        payload = {
            "format": "markdown",
            "title": "body",
            "body": "# title",
        }

        # Map our fields
        remap_fields(rules, payload)

        # Our information gets swapped
        assert payload == {
            "format": "markdown",
            "title": "# title",
            "body": "body",
        }

        #
        # swapping fields - test 4
        #
        rules = {
            #
            # map 'content' to 'body'
            "title": "body",
        }
        payload = {
            "format": "markdown",
            "title": "body",
        }

        # Map our fields
        remap_fields(rules, payload)

        # Our information gets swapped
        assert payload == {
            "format": "markdown",
            "body": "body",
        }

        #
        # swapping fields - test 5
        #
        rules = {
            #
            # map 'content' to 'body'
            "content": "body",
        }
        payload = {
            "format": "markdown",
            "content": "the message",
            "body": "to-be-replaced",
        }

        # Map our fields
        remap_fields(rules, payload)

        # Our information gets swapped
        assert payload == {
            "format": "markdown",
            "body": "the message",
        }

        #
        # mapping of fields don't align - test 6
        #
        rules = {
            "payload": "body",
            "fmt": "format",
            "extra": "tag",
        }
        payload = {
            "format": "markdown",
            "type": "info",
            "title": "",
            "body": "## test notifiction",
            "attachment": None,
            "tag": "general",
            "tags": "",
        }

        # Make a copy of our original payload
        payload_orig = payload.copy()

        # Map our fields
        remap_fields(rules, payload)

        # There are no rules applied since nothing aligned
        assert payload == payload_orig

    def test_remap_fields_subfields(self):
        """
        Test dot-notation (subfield) payload mapping
        """

        #
        # Basic subfield mapping: event.title -> title, event.state -> type,
        # component.name -> body  (mirrors the issue-306 use-case)
        #
        rules = {
            "event.title": "title",
            "event.state": "type",
            "component.name": "body",
        }
        payload = {
            "event": {
                "title": "CPU spike",
                "state": "critical",
            },
            "component": {
                "name": "web-server-01",
            },
        }

        result = remap_fields(rules, payload)

        assert result is True
        assert payload["title"] == "CPU spike"
        assert payload["type"] == "critical"
        assert payload["body"] == "web-server-01"

        #
        # Deeply nested path (3 levels)
        #
        rules = {"a.b.c": "body"}
        payload = {"a": {"b": {"c": "deep value"}}}

        assert remap_fields(rules, payload) is True
        assert payload["body"] == "deep value"

        #
        # Missing intermediate key — warning logged, returns False
        #
        rules = {"missing.field": "body"}
        payload = {"other": "data"}

        with self.assertLogs("django", level="WARNING") as cm:
            result = remap_fields(rules, payload)

        assert result is False
        assert "missing.field" in "\n".join(cm.output)
        assert "body" not in payload

        #
        # Missing leaf key — warning logged, returns False
        #
        rules = {"event.missing_leaf": "title"}
        payload = {"event": {"state": "ok"}}

        with self.assertLogs("django", level="WARNING") as cm:
            result = remap_fields(rules, payload)

        assert result is False
        assert "event.missing_leaf" in "\n".join(cm.output)
        assert "title" not in payload

        #
        # Intermediate node is not a dict (e.g. it's a string) — warning logged, returns False
        #
        rules = {"event.title.extra": "body"}
        payload = {"event": {"title": "flat string"}}

        with self.assertLogs("django", level="WARNING") as cm:
            result = remap_fields(rules, payload)

        assert result is False
        assert "event.title.extra" in "\n".join(cm.output)
        assert "body" not in payload

        #
        # Path exceeds APPRISE_WEBHOOK_MAPPING_MAX_DEPTH — warning logged, returns False
        #
        rules = {"a.b.c": "body"}
        payload = {"a": {"b": {"c": "too deep"}}}

        with self.assertLogs("django", level="WARNING") as cm, override_settings(APPRISE_WEBHOOK_MAPPING_MAX_DEPTH=2):
            result = remap_fields(rules, payload)

        assert result is False
        assert "exceeds the maximum depth" in "\n".join(cm.output)
        assert "body" not in payload

        #
        # Dot-notation source with empty value (no-op — path resolved, but no
        # valid target).  Returns True because path resolution succeeded.
        #
        rules = {"event.title": ""}
        payload = {"event": {"title": "hello"}, "body": "existing"}

        assert remap_fields(rules, payload) is True
        assert payload["body"] == "existing"
        assert "title" not in payload

        #
        # Dot-notation source pointing to a non-expected apprise field — no-op
        # Returns True because path resolution succeeded.
        #
        rules = {"event.title": "nonexistent_apprise_field"}
        payload = {"event": {"title": "hello"}}

        assert remap_fields(rules, payload) is True
        assert "nonexistent_apprise_field" not in payload

    def test_get_nested(self):
        """
        Test the _get_nested helper directly
        """

        # Happy path
        payload = {"a": {"b": {"c": 42}}}
        value, found = _get_nested(payload, ["a", "b", "c"], "a.b.c")
        assert found is True
        assert value == 42

        # Missing key at first level
        with self.assertLogs("django", level="WARNING") as cm:
            value, found = _get_nested({}, ["missing"], "missing")
        assert found is False
        assert value is None
        assert "missing" in "\n".join(cm.output)

        # Intermediate value is not a dict
        payload = {"a": "not-a-dict"}
        with self.assertLogs("django", level="WARNING") as cm:
            value, found = _get_nested(payload, ["a", "b"], "a.b")
        assert found is False
        assert "a.b" in "\n".join(cm.output)
