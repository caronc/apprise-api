# -*- coding: utf-8 -*-
#
# Copyright (C) 2023 Chris Caron <lead2gold@gmail.com>
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
import re
from apprise.utils.parse import parse_url


class AppriseURLFilter:
    """
    A URL filtering class that uses pre-parsed and pre-compiled allow/deny lists.

    Deny rules are always processed before allow rules. If a URL matches any deny rule,
    it is immediately rejected. If no deny rule matches, then the URL is allowed only
    if it matches an allow rule; otherwise, it is rejected.

    Each entry in the allow/deny lists can be provided as:
      - A full URL (with http:// or https://)
      - A URL without a scheme (e.g. "localhost/resources")
      - A plain hostname or IP

    Wildcards:
      - '*' will match any sequence of characters.
      - '?' will match a single alphanumeric/dash/underscore character.

    A trailing '*' is implied if not already present so that rules operate as a prefix match.
    """

    def __init__(self, allow_list: str, deny_list: str):
        # Pre-compile our rules.
        # Each rule is stored as a tuple (compiled_regex, is_url_based)
        # where `is_url_based` indicates if the token included "http://" or "https://"
        self.allow_rules = self._parse_list(allow_list)
        self.deny_rules = self._parse_list(deny_list)

    def _parse_list(self, list_str: str):
        """
        Split the list (tokens separated by whitespace or commas) and compile each token.
        Tokens are classified as follows:
          - URL‐based tokens: if they start with “http://” or “https://” (explicit)
            or if they contain a “/” (implicit; no scheme given).
          - Host‐based tokens: those that do not contain a “/”.
        Returns a list of tuples (compiled_regex, is_url_based).
        """
        tokens = re.split(r'[\s,]+', list_str.strip().lower())
        rules = []
        for token in tokens:
            if not token:
                continue

            if token.startswith("http://") or token.startswith("https://"):
                # Explicit URL token.
                compiled = self._compile_url_token(token)
                is_url_based = True

            elif "/" in token:
                # Implicit URL token: prepend a scheme pattern.
                compiled = self._compile_implicit_token(token)
                is_url_based = True

            else:
                # Host-based token.
                compiled = self._compile_host_token(token)
                is_url_based = False

            rules.append((compiled, is_url_based))
        return rules

    def _compile_url_token(self, token: str):
        """
        Compiles a URL‐based token (explicit token that starts with a scheme) into a regex.
        An implied trailing wildcard is added to the path:
          - If no path is given (or just “/”) then “(/.*)?” is appended.
          - If a nonempty path is given that does not end with “/” or “*”, then “($|/.*)” is appended.
          - If the path ends with “/”, then the trailing slash is removed and “(/.*)?” is appended,
            so that “/resources” and “/resources/” are treated equivalently.
        Also, if no port is specified in the host part, the regex ensures that no port is present.
        """
        # Determine the scheme.
        scheme_regex = ""
        if token.startswith("http://"):
            scheme_regex = r'http'
            # drop http://
            token = token[7:]

        elif token.startswith("https://"):
            scheme_regex = r'https'
            # drop https://
            token = token[8:]

        else:  # https?
            # Used for implicit tokens; our _compile_implicit_token ensures this.
            scheme_regex = r'https?'
            # strip https?://
            token = token[9:]

        # Split token into host (and optional port) and path.
        if "/" in token:
            netloc, path = token.split("/", 1)
            path = "/" + path
        else:
            netloc = token
            path = ""

        # Process netloc and port.
        if ":" in netloc:
            host, port = netloc.split(":", 1)
            port_specified = True

        else:
            host = netloc
            port_specified = False

        regex = "^" + scheme_regex + "://"
        regex += self._wildcard_to_regex(host, is_host=True)
        if port_specified:
            regex += ":" + re.escape(port)

        else:
            # Ensure no port is present.
            regex += r"(?!:)"

        # Process the path.
        if path in ("", "/"):
            regex += r"(/.*)?"

        else:
            if path.endswith("*"):
                # Remove the trailing "*" and append .*
                regex += self._wildcard_to_regex(path[:-1]) + "([^/]+/?)"

            elif path.endswith("/"):
                # Remove the trailing "/" and allow an optional slash with extra path.
                norm = self._wildcard_to_regex(path.rstrip("/"))
                regex += norm + r"(/.*)?"

            else:
                # For a nonempty path that does not end with "/" or "*",
                # match either an exact match or a prefix (with a following slash).
                norm = self._wildcard_to_regex(path)
                regex += norm + r"($|/.*)"

        regex += "$"
        return re.compile(regex, re.IGNORECASE)

    def _compile_implicit_token(self, token: str):
        """
        For an implicit token (one that does not start with a scheme but contains a “/”),
        prepend “https?://” so that it matches both http and https, then compile it.
        """
        new_token = "https?://" + token
        return self._compile_url_token(new_token)

    def _compile_host_token(self, token: str):
        """
        Compiles a host‐based token (one with no “/”) into a regex.
        Note: When matching host‐based tokens, we require that the URL’s scheme is exactly “http”.
        """
        regex = "^" + self._wildcard_to_regex(token) + "$"
        return re.compile(regex, re.IGNORECASE)

    def _wildcard_to_regex(self, pattern: str, is_host: bool = True) -> str:
        """
        Converts a pattern containing wildcards into a regex.
          - '*' becomes '.*' if host or [^/]+/? if path
          - '?' becomes '[A-Za-z0-9_-]'
          - Other characters are escaped.
        Special handling: if the pattern starts with "https?://", that prefix is preserved
        (so it can match either http:// or https://).
        """
        regex = ""
        for char in pattern:
            if char == '*':
                regex += r"[^/]+/?" if not is_host else r'.*'

            elif char == '?':
                regex += r'[^/]' if not is_host else r"[A-Za-z0-9_-]"

            else:
                regex += re.escape(char)

        return regex

    def is_allowed(self, url: str) -> bool:
        """
        Checks a given URL against the deny list first, then the allow list.
        For URL-based rules (explicit or implicit), the full URL is tested.
        For host-based rules, the URL’s netloc (which includes the port) is tested.
        """
        parsed = parse_url(url, strict_port=True, simple=True)
        if not parsed:
            return False

        # includes port if present
        netloc = '%s:%d' % (parsed['host'], parsed.get('port')) if parsed.get('port') else parsed['host']

        # Check deny rules first.
        for pattern, is_url_based in self.deny_rules:
            if is_url_based:
                if pattern.match(url):
                    return False

            elif pattern.match(netloc):
                return False

        # Then check allow rules.
        for pattern, is_url_based in self.allow_rules:
            if is_url_based:
                if pattern.match(url):
                    return True
            elif pattern.match(netloc):
                return True

        return False
