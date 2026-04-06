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

    Each step is a ``('key', name)`` or ``('index', N)`` tuple:

    * ``('key', name)``  - perform a dict lookup for *name*.
    * ``('index', N)``   - dereference integer index *N* of the current list.

    Supported forms::

        "title"                 → [('key', 'title')]
        "event.title"           → [('key', 'event'), ('key', 'title')]
        "items[0]"              → [('key', 'items'), ('index', 0)]
        "items[0].objectURI"    → [('key', 'items'), ('index', 0),
                                    ('key', 'objectURI')]
        "a[0][2][2].b[3]"       → [('key', 'a'), ('index', 0), ('index', 2),
                                    ('index', 2), ('key', 'b'), ('index', 3)]

    Returns ``(steps, None)`` on success.  Returns ``(None, reason)`` when
    the key contains malformed bracket notation — any of:

    * Missing closing bracket  e.g. ``items[0``
    * Missing opening bracket  e.g. ``items0]``
    * Non-integer index        e.g. ``items[abc]``
    """
    steps = []
    for segment in key.split("."):
        if not segment:
            return None, f"empty segment in path '{key}'"

        if "[" not in segment and "]" not in segment:
            # Plain dict key — no bracket characters at all
            steps.append(("key", segment))
            continue

        # At least one bracket character is present.
        # A stray ']' before any '[' is immediately malformed.
        bracket_pos = segment.find("[")
        if bracket_pos == -1:
            return None, (f"malformed bracket notation at '{segment}' (unexpected ']' with no matching '[')")

        key_name = segment[:bracket_pos]
        remaining = segment[bracket_pos:]

        if key_name:
            steps.append(("key", key_name))

        # Consume every [N] subscript greedily; any leftover chars are malformed.
        while remaining:
            m = _SUBSCRIPT_RE.match(remaining)
            if not m:
                return None, (
                    f"malformed bracket notation at '{segment}'; expected [N] where N is a non-negative integer"
                )
            steps.append(("index", int(m.group(1))))
            remaining = remaining[m.end() :]

    return steps, None


def _get_nested(payload, steps, source_key):
    """
    Traverse *payload* using the pre-parsed *steps* produced by
    :func:`_parse_path`.

    Each step is a ``('key', name)`` or ``('index', N)`` tuple.

    Returns ``(value, True)`` on success.  Logs a WARNING and returns
    ``(None, False)`` when traversal cannot be completed (missing key,
    not-indexable node, or out-of-range index).
    """
    current = payload
    for step_type, step_val in steps:
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

        items[0].objectURI          # list → dict
        data[0][2][2].value[3]      # list of lists, then dict → list

    Each ``key[N]`` segment performs a dict lookup for *key* then dereferences
    index *N* of the resulting list.  Invalid notation (missing bracket,
    non-integer index, out-of-range index, or a non-list node) is handled
    gracefully: a WARNING is logged and ``False`` is returned.

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
        # Dot-notation and/or array-index path handling.
        # Any bracket character (either '[' or ']') triggers this branch so
        # that stray/unmatched brackets are caught and rejected as malformed
        # rather than silently falling through to flat-field handling.
        # ------------------------------------------------------------------
        if "." in key or "[" in key or "]" in key:
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
