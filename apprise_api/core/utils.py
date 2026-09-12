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
import logging

# Keep accepted names and their logging values in one shared lookup.
_LOG_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "TRACE": logging.DEBUG - 1,
}

# Existing query options accept these familiar truthy first characters.
_TRUE_PREFIXES = frozenset(("a", "y", "1", "t", "e", "+"))


def parse_bool(value, default=False):
    """Parse a loose boolean from its first non-whitespace character."""
    # A missing option should behave exactly like the caller's default.
    if value is None:
        return default

    # Empty strings also fall back instead of causing an index error.
    value = str(value).strip().lower()
    return value[:1] in _TRUE_PREFIXES if value else default


def parse_log_level(value, default="WARNING"):
    """Return a supported log level, using the default for invalid values."""
    # Normalize request values before comparing them with known names.
    name = str(value).strip().upper()

    if name not in _LOG_LEVELS:
        # A bad configured default safely settles on WARNING below.
        name = str(default).strip().upper()

    # WARNING is the final safe choice when both supplied names are invalid.
    return _LOG_LEVELS.get(name, logging.WARNING)
