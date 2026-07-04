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
# import the logging library
import logging
import re

from api.forms import NotifyForm
from django.conf import settings

# Get an instance of a logger
logger = logging.getLogger("django")

# Matches a single [N] subscript at the start of a string.
_SUBSCRIPT_RE = re.compile(r"^\[(\d+)\]")


def _parse_path(key):
    """
    Parse a mapping key into a flat, ordered list of traversal steps.

    Each step is a ``('key', name, parse_json)`` or ``('index', N, parse_json)`` tuple:

    * ``('key', name, parse_json)``   - perform a dict lookup for *name*.
    * ``('index', N, parse_json)``    - dereference integer index *N* of the current list.

    Supported forms::

        "title"                 → [('key', 'title', False)]
        "event.title"           → [('key', 'event', False), ('key', 'title', False)]
        "items[0]"              → [('key', 'items', False), ('index', 0, False)]
        "items[0].objectURI"    → [('key', 'items', False), ('index', 0, False),
                                    ('key', 'objectURI', False)]
        "a[0][2][2].b[3]"       → [('key', 'a', False), ('index', 0, False), ('index', 2, False),
                                    ('index', 2, False), ('key', 'b', False), ('index', 3, False)]

    Returns ``(steps, None)`` on success.  Returns ``(None, reason)`` when
    the key contains malformed bracket notation — any of:

    * Missing closing bracket  e.g. ``items[0``
    * Missing opening bracket  e.g. ``items0]``
    * Stray ``]`` before ``[`` e.g. ``a]b[0]``
    * Non-integer index        e.g. ``items[abc]``
    """
    steps = []
    for segment in key.split("."):
        if not segment:
            return None, f"empty segment in path '{key}'"

        if "::json" in segment:
            parts = segment.rsplit("::json", 1)
            left, right = parts[0], parts[1]
        else:
            left, right = segment, ""

        segment_steps = []

        # Helper to parse a part of a segment (like left or right)
        def parse_part(part, parse_json_on_last, steps_out):
            if not part:
                return True

            if "[" not in part and "]" not in part:
                # Plain dict key — no bracket characters at all
                steps_out.append(("key", part, parse_json_on_last))
                return True

            # At least one bracket character is present.
            # A stray ']' before any '[' is immediately malformed.
            bracket_pos = part.find("[")
            if bracket_pos == -1:
                return False

            key_name = part[:bracket_pos]
            remaining = part[bracket_pos:]

            # A ']' appearing before the first '[' is malformed (e.g. 'a]b[0]').
            if "]" in key_name:
                return False

            part_steps = []
            if key_name:
                part_steps.append(("key", key_name))

            # Consume every [N] subscript greedily; any leftover chars are malformed.
            while remaining:
                m = _SUBSCRIPT_RE.match(remaining)
                if not m:
                    return False
                part_steps.append(("index", int(m.group(1))))
                remaining = remaining[m.end() :]

            for i, step in enumerate(part_steps):
                is_last = i == len(part_steps) - 1
                steps_out.append((step[0], step[1], parse_json_on_last if is_last else False))

            return True

        if not parse_part(left, "::json" in segment, segment_steps):
            return None, f"malformed segment '{segment}'"

        if not parse_part(right, False, segment_steps):
            return None, f"malformed segment '{segment}'"

        if not segment_steps:
            continue

        steps.extend(segment_steps)

    return steps, None


def _get_nested(payload, steps, source_key):
    """
    Traverse *payload* using the pre-parsed *steps* produced by
    :func:`_parse_path`.

    Each step is a ``(step_type, step_val, parse_json)`` tuple.

    Returns ``(value, True)`` on success.  Logs a WARNING and returns
    ``(None, False)`` when traversal cannot be completed (missing key,
    not-indexable node, or out-of-range index).
    """
    current = payload
    for step_type, step_val, parse_json in steps:
        if step_type == "key":
            if not isinstance(current, dict) or step_val not in current:
                logger.warning(
                    "Payload mapping path '%s' not found in payload (stopped at key '%s')",
                    source_key,
                    step_val,
                )
                return None, False
            current = current[step_val]

        else:  # 'index'
            if not isinstance(current, (list, tuple)):
                logger.warning(
                    "Payload mapping path '%s': [%d] is not indexable (expected a list/array, got %s)",
                    source_key,
                    step_val,
                    type(current).__name__,
                )
                return None, False
            try:
                current = current[step_val]
            except IndexError:
                logger.warning(
                    "Payload mapping path '%s': index [%d] is out of range (length %d)",
                    source_key,
                    step_val,
                    len(current),
                )
                return None, False

        if parse_json and isinstance(current, str):
            try:
                import json

                current = json.loads(current)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

    return current, True


def remap_fields(rules, payload, form=None):
    """
    Remaps fields in the payload provided based on the rules provided

    The key value of the dictionary identifies the payload key type you
    wish to alter.  If there is no value defined, then the entry is removed

    If there is a value provided, then it's key is swapped into the new key
    provided.

    The purpose of this function is to allow people to re-map the fields
    that are being posted to the Apprise API before hand.  Mapping them
    can allow 3rd party programs that post 'subject' and 'content' to
    be remapped to say 'title' and 'body' respectively

    Dot-notation keys (e.g. ``event.title``) are interpreted as paths into
    nested dictionaries within the payload.  The resolved value is then
    mapped to the target Apprise field.  A WARNING is logged when the path
    cannot be found, so operators can diagnose misconfigured rules.

    Array-index notation is also supported and may be combined freely with
    dot-notation.  Multiple consecutive subscripts are allowed::

        items[0].objectURI          # list -> dict
        data[0][2][2].value[3]      # list of lists, then dict -> list

    Each ``key[N]`` segment performs a dict lookup for *key* then dereferences
    index *N* of the resulting list.  Invalid notation (missing bracket,
    non-integer index, out-of-range index, or a non-list node) is handled
    gracefully: a WARNING is logged and ``False`` is returned.

    **Empty target and nested paths:** setting an empty target value (e.g.
    ``:event.title=``) for a nested-path source is a no-op — the path is
    resolved but nothing is removed or written.  Only flat top-level keys
    support deletion via an empty target (``?:key=``).

    The maximum traversal depth is controlled by the
    ``APPRISE_WEBHOOK_MAPPING_MAX_DEPTH`` Django setting (default: 5).
    Depth is counted as the total number of individual traversal steps —
    each dict key lookup *and* each array index dereference counts as one
    step — so ``a[0][1].b[2]`` has a depth of 5.

    """

    # Prepare our Form (identifies our expected keys)
    form = NotifyForm() if form is None else form

    max_depth = getattr(settings, "APPRISE_WEBHOOK_MAPPING_MAX_DEPTH", 5)

    # First generate our expected keys; only these can be mapped
    expected_keys = set(form.fields.keys())
    for key, value in rules.items():
        # ------------------------------------------------------------------
        # If the key contains "::json" but exists literally in the payload,
        # we treat it as a flat field mapping to preserve backward
        # compatibility for literal key names containing "::json".
        # ------------------------------------------------------------------
        if "::json" in key and key in payload:
            if not value:
                del payload[key]
                continue

            if value in expected_keys:
                if key not in expected_keys or value not in payload:
                    payload[value] = payload[key]
                    del payload[key]
                else:
                    _tmp = payload[value]
                    payload[value] = payload[key]
                    payload[key] = _tmp
            elif key in expected_keys or key in payload:
                payload[key] = value
            continue

        # ------------------------------------------------------------------
        # Dot-notation and/or array-index path handling.
        # Any bracket character (either '[' or ']') triggers this branch so
        # that stray/unmatched brackets are caught and rejected as malformed
        # rather than silently falling through to flat-field handling.
        # ------------------------------------------------------------------
        if "." in key or "[" in key or "]" in key or "::json" in key:
            steps, err = _parse_path(key)
            if steps is None:
                logger.warning(
                    "Payload mapping path '%s': %s",
                    key,
                    err,
                )
                return False

            if len(steps) > max_depth:
                logger.warning(
                    "Payload mapping path '%s' exceeds the maximum depth of %d; skipping",
                    key,
                    max_depth,
                )
                return False

            nested_value, found = _get_nested(payload, steps, key)
            if not found:
                # Warning already emitted by _get_nested
                return False

            if value in expected_keys:
                # Map the nested value to the flat Apprise field
                payload[value] = nested_value
            # Any other combination (empty value, non-expected target) is a
            # no-op for dot/index sources — skip silently.
            continue

        # ------------------------------------------------------------------
        # Flat field handling (original behaviour)
        # ------------------------------------------------------------------
        if key in payload and not value:
            # Remove element
            del payload[key]
            continue

        if value in expected_keys and key in payload:
            if key not in expected_keys or value not in payload:
                # replace
                payload[value] = payload[key]
                del payload[key]

            else:
                # swap
                _tmp = payload[value]
                payload[value] = payload[key]
                payload[key] = _tmp

        elif key in expected_keys or key in payload:
            # assignment
            payload[key] = value

    return True
