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
"""Exceptions raised by Apprise API.

Each error also inherits from its related built-in exception so existing
handlers remain compatible.
"""

import errno


class AppriseAPIError(Exception):
    """Base class for exceptions raised by apprise-api itself."""

    def __init__(self, message, error_code=0):
        super().__init__(message)
        self.error_code = error_code


class AppriseAPIImproperlyConfigured(AppriseAPIError, TypeError, ValueError, AttributeError):
    """Raised for missing, invalid, or conflicting input and settings."""

    def __init__(self, message, error_code=errno.EINVAL):
        super().__init__(message, error_code=error_code)


class AppriseAPIStorageError(AppriseAPIError, OSError):
    """Raised when an Apprise API storage operation fails."""

    def __init__(self, message, error_code=errno.EIO):
        super().__init__(message, error_code=error_code)
        # Keep standard OSError details available to existing handlers.
        self.errno = error_code
        self.strerror = message

    def __str__(self):
        """Return the original message while exposing standard I/O details."""
        return str(self.args[0])
