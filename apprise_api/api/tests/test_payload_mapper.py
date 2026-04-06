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

from ..payload_mapper import _get_nested, _parse_path, remap_fields


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
        # Path exceeds APPRISE_WEBHOOK_MAPPING_MAX_DEPTH — warning logged, returns False.
        # "a.b.c" produces 3 steps; setting max_depth=2 triggers the guard.
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

    def test_remap_fields_array_index(self):
        """
        Test bracket/array-index notation in remap_fields:
        ``items[N]``, ``items[N].subfield``, and chained ``a[N][M][P]`` paths.
        """

        #
        # Simple array index: items[0] -> body
        #
        rules = {"items[0]": "body"}
        payload = {"items": ["hello world"]}
        assert remap_fields(rules, payload) is True
        assert payload["body"] == "hello world"

        #
        # Array index + subfield: items[0].objectURI -> body
        # (the motivating example from issue #209)
        #
        rules = {"items[0].objectURI": "body", "items[0].title": "title"}
        payload = {
            "items": [
                {"objectURI": "https://example.com/q/1234", "title": "New post"},
            ]
        }
        assert remap_fields(rules, payload) is True
        assert payload["body"] == "https://example.com/q/1234"
        assert payload["title"] == "New post"

        #
        # Index into a later element
        #
        rules = {"alerts[2]": "body"}
        payload = {"alerts": ["a", "b", "critical failure"]}
        assert remap_fields(rules, payload) is True
        assert payload["body"] == "critical failure"

        #
        # Chained subscripts: data[0][1] -> body
        #
        rules = {"data[0][1]": "body"}
        payload = {"data": [["first", "second"], ["third", "fourth"]]}
        assert remap_fields(rules, payload) is True
        assert payload["body"] == "second"

        #
        # Full chained path: key[0][1][2].value[3] -> body
        # Steps: ('key','key') + [0] + [1] + [2] + ('key','value') + [3] = 6 total.
        # Default max_depth=5 should reject; max_depth=6 should pass.
        #
        # Data layout so that key[0][1][2] resolves to inner:
        #   payload["key"][0][1][2] == inner
        #
        inner = {"value": ["v0", "v1", "v2", "target"]}
        payload = {"key": [[[None, None], [None, None, inner]]]}

        rules = {"key[0][1][2].value[3]": "body"}
        with self.assertLogs("django", level="WARNING") as cm, override_settings(APPRISE_WEBHOOK_MAPPING_MAX_DEPTH=5):
            result = remap_fields(rules, payload)
        assert result is False
        assert "exceeds the maximum depth" in "\n".join(cm.output)

        # Same path succeeds when max_depth is raised to accommodate 6 steps
        inner = {"value": ["v0", "v1", "v2", "target"]}
        payload = {"key": [[[None, None], [None, None, inner]]]}

        with override_settings(APPRISE_WEBHOOK_MAPPING_MAX_DEPTH=6):
            result = remap_fields(rules, payload)
        assert result is True
        assert payload["body"] == "target"

        #
        # Malformed: missing closing bracket -> returns False, warning logged
        #
        rules = {"items[0": "body"}
        payload = {"items": ["hello"]}
        with self.assertLogs("django", level="WARNING") as cm:
            result = remap_fields(rules, payload)
        assert result is False
        assert "malformed" in "\n".join(cm.output)
        assert "body" not in payload

        #
        # Malformed: missing opening bracket (stray ']') -> returns False
        #
        rules = {"items0]": "body"}
        payload = {"items0]": ["hello"]}
        with self.assertLogs("django", level="WARNING") as cm:
            result = remap_fields(rules, payload)
        assert result is False
        assert "malformed" in "\n".join(cm.output)
        assert "body" not in payload

        #
        # Malformed: stray ']' before '[' within a segment (e.g. a]b[0])
        #
        rules = {"a]b[0]": "body"}
        payload = {"a]b": [["x"]]}
        with self.assertLogs("django", level="WARNING") as cm:
            result = remap_fields(rules, payload)
        assert result is False
        assert "malformed" in "\n".join(cm.output)
        assert "body" not in payload

        #
        # Malformed: non-integer index -> returns False, warning logged
        #
        rules = {"items[abc]": "body"}
        payload = {"items": ["hello"]}
        with self.assertLogs("django", level="WARNING") as cm:
            result = remap_fields(rules, payload)
        assert result is False
        assert "malformed" in "\n".join(cm.output)

        #
        # Out-of-range index -> returns False, warning logged
        #
        rules = {"items[99]": "body"}
        payload = {"items": ["only one"]}
        with self.assertLogs("django", level="WARNING") as cm:
            result = remap_fields(rules, payload)
        assert result is False
        assert "out of range" in "\n".join(cm.output)

        #
        # Target is not a list -> returns False, warning logged
        #
        rules = {"items[0]": "body"}
        payload = {"items": {"key": "value"}}
        with self.assertLogs("django", level="WARNING") as cm:
            result = remap_fields(rules, payload)
        assert result is False
        assert "not indexable" in "\n".join(cm.output)

        #
        # Array index with no-op target (empty value) — path resolves but
        # nothing is written; returns True
        #
        rules = {"items[0]": ""}
        payload = {"items": ["hello"], "body": "keep me"}
        assert remap_fields(rules, payload) is True
        assert payload["body"] == "keep me"

        #
        # Array index pointing to a non-expected apprise field — no-op,
        # returns True
        #
        rules = {"items[0]": "nonexistent_field"}
        payload = {"items": ["hello"]}
        assert remap_fields(rules, payload) is True
        assert "nonexistent_field" not in payload

    def test_parse_path(self):
        """
        Test the _parse_path helper directly
        """

        # Plain key
        steps, err = _parse_path("title")
        assert err is None
        assert steps == [("key", "title")]

        # Dot-notation
        steps, err = _parse_path("a.b.c")
        assert err is None
        assert steps == [("key", "a"), ("key", "b"), ("key", "c")]

        # Single subscript
        steps, err = _parse_path("items[0]")
        assert err is None
        assert steps == [("key", "items"), ("index", 0)]

        # Subscript + subfield
        steps, err = _parse_path("items[0].objectURI")
        assert err is None
        assert steps == [("key", "items"), ("index", 0), ("key", "objectURI")]

        # Chained subscripts
        steps, err = _parse_path("a[0][2][2]")
        assert err is None
        assert steps == [("key", "a"), ("index", 0), ("index", 2), ("index", 2)]

        # Full complex path
        steps, err = _parse_path("key[0][2][2].value[3]")
        assert err is None
        assert steps == [
            ("key", "key"),
            ("index", 0),
            ("index", 2),
            ("index", 2),
            ("key", "value"),
            ("index", 3),
        ]

        # Malformed: missing closing bracket
        steps, err = _parse_path("items[0")
        assert steps is None
        assert "malformed" in err

        # Malformed: stray closing bracket (no opening bracket)
        steps, err = _parse_path("items0]")
        assert steps is None
        assert "malformed" in err

        # Malformed: non-integer index
        steps, err = _parse_path("items[abc]")
        assert steps is None
        assert "malformed" in err

        # Malformed: partial subscript mid-chain e.g. key[0][abc]
        steps, err = _parse_path("key[0][abc]")
        assert steps is None
        assert "malformed" in err

        # Malformed: stray ']' before '[' within a segment e.g. a]b[0]
        steps, err = _parse_path("a]b[0]")
        assert steps is None
        assert "malformed" in err

        # Malformed: double-dot produces an empty segment
        steps, err = _parse_path("a..b")
        assert steps is None
        assert "empty segment" in err

        # Subscript-only segment (no key name before '[') — valid when the
        # previous step already resolved to a list.  e.g. "items.[0]" is
        # equivalent to "items[0]".
        steps, err = _parse_path("items.[0]")
        assert err is None
        assert steps == [("key", "items"), ("index", 0)]

    def test_get_nested(self):
        """
        Test the _get_nested helper directly (steps tuple format)
        """

        # Happy path — nested dicts
        payload = {"a": {"b": {"c": 42}}}
        steps, _ = _parse_path("a.b.c")
        value, found = _get_nested(payload, steps, "a.b.c")
        assert found is True
        assert value == 42

        # Missing key at first level
        steps, _ = _parse_path("missing")
        with self.assertLogs("django", level="WARNING") as cm:
            value, found = _get_nested({}, steps, "missing")
        assert found is False
        assert value is None
        assert "missing" in "\n".join(cm.output)

        # Intermediate value is not a dict
        payload = {"a": "not-a-dict"}
        steps, _ = _parse_path("a.b")
        with self.assertLogs("django", level="WARNING") as cm:
            value, found = _get_nested(payload, steps, "a.b")
        assert found is False
        assert "a.b" in "\n".join(cm.output)

        # Array index: happy path
        payload = {"items": ["first", "second", "third"]}
        steps, _ = _parse_path("items[1]")
        value, found = _get_nested(payload, steps, "items[1]")
        assert found is True
        assert value == "second"

        # Chained subscripts: data[0][1]
        payload = {"data": [["x", "y"], ["a", "b"]]}
        steps, _ = _parse_path("data[0][1]")
        value, found = _get_nested(payload, steps, "data[0][1]")
        assert found is True
        assert value == "y"

        # Array index: out of range
        payload = {"items": ["only"]}
        steps, _ = _parse_path("items[5]")
        with self.assertLogs("django", level="WARNING") as cm:
            value, found = _get_nested(payload, steps, "items[5]")
        assert found is False
        assert "out of range" in "\n".join(cm.output)

        # Array index: target key is not a list
        payload = {"items": {"nested": "dict"}}
        steps, _ = _parse_path("items[0]")
        with self.assertLogs("django", level="WARNING") as cm:
            value, found = _get_nested(payload, steps, "items[0]")
        assert found is False
        assert "not indexable" in "\n".join(cm.output)

        # Array index: dict key not found
        payload = {"other": ["x"]}
        steps, _ = _parse_path("missing[0]")
        with self.assertLogs("django", level="WARNING") as cm:
            value, found = _get_nested(payload, steps, "missing[0]")
        assert found is False
        assert "missing[0]" in "\n".join(cm.output)

        # Chained subscripts: second index out of range
        payload = {"data": [["x"]]}
        steps, _ = _parse_path("data[0][5]")
        with self.assertLogs("django", level="WARNING") as cm:
            value, found = _get_nested(payload, steps, "data[0][5]")
        assert found is False
        assert "out of range" in "\n".join(cm.output)

        # Chained subscripts: intermediate is not a list
        payload = {"data": ["scalar"]}
        steps, _ = _parse_path("data[0][1]")
        with self.assertLogs("django", level="WARNING") as cm:
            value, found = _get_nested(payload, steps, "data[0][1]")
        assert found is False
        assert "not indexable" in "\n".join(cm.output)
