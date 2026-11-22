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
"""Read and validate environment-backed Django settings.

Values are not cached so tests and commands can replace the environment before
loading the settings module.
"""

import codecs
from collections.abc import Sequence
from enum import Enum
import json
import os
from typing import Any, get_args, get_origin, get_type_hints

import apprise
from apprise.utils.parse import parse_bool as apprise_parse_bool
from core.utils import parse_bool
from django.core.exceptions import ImproperlyConfigured


def env_bool(name, default=False):
    """Read a value using Apprise API's first-character boolean rules."""
    # Missing variables inherit the documented default.
    return parse_bool(os.environ.get(name), default=default)


def env_optional_bool(name):
    """Read a boolean, or return ``None`` to preserve a library default."""
    return None if name not in os.environ else env_bool(name)


def env_int(name, default, *, minimum=None, maximum=None, absolute=False):
    """Read a whole number within the optional inclusive bounds.

    ``absolute`` preserves settings that historically accepted negative input.
    """
    # Keep the original text for a clear validation error during startup.
    raw_value = os.environ.get(name, default)
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        raise ImproperlyConfigured(f"{name} must be a whole number.") from None

    if absolute:
        value = abs(value)

    if minimum is not None and value < minimum:
        raise ImproperlyConfigured(f"{name} must be at least {minimum}.")
    if maximum is not None and value > maximum:
        raise ImproperlyConfigured(f"{name} must not exceed {maximum}.")

    return value


def env_choice(name, default, choices, *, first_character=False):
    """Read a case-insensitive choice and return its canonical value.

    ``first_character`` also accepts values beginning with a unique choice
    letter. Invalid or ambiguous values stop startup with a clear error.
    """
    # Canonicalize both sides once so every setting is case-insensitive.
    normalized_choices = tuple(str(choice).strip().lower() for choice in choices)
    value = str(os.environ.get(name, default)).strip().lower()

    if value in normalized_choices:
        return value

    if first_character and value:
        matches = [choice for choice in normalized_choices if choice.startswith(value[0])]
        if len(matches) == 1:
            return matches[0]

    expected = ", ".join(normalized_choices)
    raise ImproperlyConfigured(f"{name} must be one of: {expected}.")


# Every variable starting with this sets one AppriseAsset option.
ASSET_PREFIX = "APPRISE_ASSET_"

# Options Apprise gives no type hint for, whose default of None does not
# show what they hold.
ASSET_HINTS = {
    "interpret_emojis": bool | None,
    "body_format": apprise.NotifyFormat | None,
}


def _asset_value(key: str, raw: str, hints: dict[str, Any]) -> Any:
    """Convert an environment string into the type an asset option expects.

    - Types come from the ``AppriseAsset()`` type hints, or else from the
      option's default value.
    - Booleans must be a word Apprise knows, such as yes or no.
    - Lists are split on commas, and dictionaries are read as JSON.
    - Raises ValueError when the text does not fit the type.
    """
    if key == "encoding":
        # Apprise only uses it once it encodes something, so an unknown
        # name would otherwise fail on the first notification.
        codecs.lookup(raw)

    hint = hints.get(key)
    types = (get_args(hint) or (hint,)) if hint else (type(getattr(apprise.AppriseAsset, key, None)),)
    if str in types:
        # An option that takes text or False, such as image_path_mask, is
        # turned off with no or false.
        return False if bool in types and raw.strip().lower() in ("no", "false") else raw

    if bool in types:
        # Unknown words are refused rather than read as False, which could
        # quietly turn off an option such as secure_logging.
        value = apprise_parse_bool(raw, None)
        if value is None:
            raise ValueError(f"{raw!r} is not a yes or no value")

        return value

    if float in types:
        return float(raw)

    if int in types:
        return int(raw)

    if any(get_origin(t) is Sequence for t in types):
        return raw.split(",")

    for kind in types:
        if isinstance(kind, type) and issubclass(kind, Enum):
            # Only one of the listed choices is accepted.
            return kind(raw)

        if kind is dict:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("expects a JSON object")

            return value

    return raw


def env_asset() -> dict[str, Any]:
    """Read every ``APPRISE_ASSET_<NAME>`` variable as an ``AppriseAsset`` option.

    - ``<NAME>`` is lowercased, so ``APPRISE_ASSET_APP_ID`` sets ``app_id``.
    - Names starting with ``_`` are internal to Apprise and refused.
    - A name or value the asset refuses stops startup.
    """
    hints = get_type_hints(apprise.AppriseAsset.__init__) | ASSET_HINTS
    options = {}
    for name, raw in os.environ.items():
        if not name.startswith(ASSET_PREFIX) or name == ASSET_PREFIX:
            continue

        key = name[len(ASSET_PREFIX) :].lower()
        if key.startswith("_"):
            # These carry per-request values, such as the recursion count
            # that stops notification loops.
            raise ImproperlyConfigured(f"{name} is not a valid Apprise asset setting: {key} is internal.")

        if callable(getattr(apprise.AppriseAsset, key, None)):
            # Apprise would replace one of its own functions with the text.
            raise ImproperlyConfigured(f"{name} is not a valid Apprise asset setting: {key} is a function.")

        try:
            options[key] = _asset_value(key, raw, hints)
            # Build with every option read so far, so a mistake, or two
            # options that clash, is reported at startup.
            apprise.AppriseAsset(**options)

        except (AttributeError, LookupError, TypeError, ValueError) as e:
            # AttributeError covers a read-only option such as tzinfo, and
            # LookupError an unknown encoding.
            raise ImproperlyConfigured(f"{name} is not a valid Apprise asset setting: {e}") from None

    return options
