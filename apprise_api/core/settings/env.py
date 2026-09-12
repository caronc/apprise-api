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

import os

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
