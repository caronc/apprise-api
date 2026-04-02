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

from api.forms import NotifyForm
from django.conf import settings

# Get an instance of a logger
logger = logging.getLogger("django")


def _get_nested(payload, path_parts, source_key):
    """
    Traverse nested dicts following path_parts.

    Returns a ``(value, found)`` tuple.  Logs a WARNING when the path cannot
    be fully resolved so the operator can diagnose misconfigured mapping rules.
    """
    current = payload
    for i, part in enumerate(path_parts):
        if not isinstance(current, dict) or part not in current:
            partial = ".".join(path_parts[: i + 1])
            logger.warning(
                "Payload mapping path '%s' not found in payload (stopped at '%s')",
                source_key,
                partial,
            )
            return None, False
        current = current[part]
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

    The maximum traversal depth is controlled by the
    ``APPRISE_WEBHOOK_MAPPING_MAX_DEPTH`` Django setting (default: 5).

    """

    # Prepare our Form (identifies our expected keys)
    form = NotifyForm() if form is None else form

    max_depth = getattr(settings, "APPRISE_WEBHOOK_MAPPING_MAX_DEPTH", 5)

    # First generate our expected keys; only these can be mapped
    expected_keys = set(form.fields.keys())
    for key, value in rules.items():
        # ------------------------------------------------------------------
        # Dot-notation (subfield) path handling
        # ------------------------------------------------------------------
        if "." in key:
            path_parts = key.split(".")

            if len(path_parts) > max_depth:
                logger.warning(
                    "Payload mapping path '%s' exceeds the maximum depth of %d; skipping",
                    key,
                    max_depth,
                )
                return False

            nested_value, found = _get_nested(payload, path_parts, key)
            if not found:
                # Warning already emitted by _get_nested
                return False

            if value in expected_keys:
                # Map the nested value to the flat Apprise field
                payload[value] = nested_value
            # Any other combination (empty value, non-expected target) is a
            # no-op for dot-notation sources — skip silently.
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
