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
import ipaddress
import re
from urllib.parse import unquote, urlsplit

from apprise.utils.http import HTTPPolicy, is_public_ip_address
from apprise.utils.parse import parse_url

from .exceptions import AppriseAPIImproperlyConfigured

# A reserved deny-list token; see the "internal" entry in the
# AppriseURLFilter class docstring below for what it does.
INTERNAL_TOKEN = "internal"

# Reuse Apprise's bounded resolver for early URL checks.
_HTTP_RESOLVER = HTTPPolicy()

# Keep administrator patterns reasonably sized even though matching is bounded.
_MAX_WILDCARDS_PER_RULE = 8

# Reject unusually long candidates before matching. The URL limit leaves room
# for a normal hostname, path, and query string.
_MAX_HOST_LENGTH = 255
_MAX_URL_LENGTH = 4096

# Reject malformed or repeatedly encoded input instead of guessing how a
# destination server will interpret it.
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9a-f]{2})", re.IGNORECASE)
_ENCODED_PERCENT_ESCAPE = re.compile(r"%[0-9a-f]{2}", re.IGNORECASE)


class _TooManyWildcardsError(AppriseAPIImproperlyConfigured):
    """Raised when a URL filter rule contains too many wildcards."""


class _InvalidURLRuleError(AppriseAPIImproperlyConfigured):
    """Raised when a URL rule cannot be compared safely."""


def _is_blocked_address(addr) -> bool:
    """Return true when Apprise does not classify an address as public."""
    return not is_public_ip_address(addr)


def _resolve_addresses(host: str):
    """Resolve a host into IP objects using Apprise's bounded resolver.

    Failures return ``None`` so callers can deny an unclassified target.
    """
    # A literal IP (optionally bracketed, e.g. "[::1]") needs no lookup.
    literal = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        return [ipaddress.ip_address(literal)]

    except ValueError:
        # Not a literal address; fall through to DNS resolution below.
        pass

    try:
        results = _HTTP_RESOLVER.resolve(literal, None)
    except Exception:
        # DNS resolution failed.
        return None

    # One hostname can legitimately have several IPv4 and IPv6 answers.
    addresses = []
    for _family, _type, _proto, _canonname, sockaddr in results:
        try:
            addresses.append(ipaddress.ip_address(sockaddr[0]))

        except ValueError:
            # Unexpected address shape; skip rather than fail closed on
            # the whole lookup over one bad record.
            continue

    return addresses or None


def _decode_url_component(value):
    """Decode one URL component or reject ambiguous input."""
    if _INVALID_PERCENT_ESCAPE.search(value):
        raise ValueError("malformed percent escape")

    decoded = unquote(value, encoding="utf-8", errors="strict")

    # A remaining escape would require another decode with server-specific
    # behavior, so fail closed rather than risk inconsistent authorization.
    if _ENCODED_PERCENT_ESCAPE.search(decoded):
        raise ValueError("nested percent escape")

    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in decoded):
        raise ValueError("control character in URL")

    return decoded


def _canonicalize_path(path):
    """Decode and collapse URL path separators and dot segments."""
    decoded = _decode_url_component(path).replace("\\", "/")
    trailing_slash = decoded.endswith(("/", "/.", "/.."))
    absolute = decoded.startswith("/")
    segments = []

    for segment in decoded.split("/"):
        if not segment or segment == ".":
            continue
        if segment == "..":
            if segments:
                segments.pop()
            continue
        segments.append(segment)

    canonical = ("/" if absolute else "") + "/".join(segments)
    if trailing_slash and canonical not in ("", "/"):
        canonical += "/"

    return canonical.lower()


def _glob_match(pattern, value, host=False, prefix=False, query=False):
    """Match ``*`` and ``?`` in predictable O(pattern x value) time."""
    previous = [False] * (len(value) + 1)
    previous[0] = True

    for token in pattern:
        current = [False] * (len(value) + 1)
        if token == "*":
            # Host and query stars may be empty; path stars stay in one segment.
            current[0] = previous[0] if host or query else False
            for index, char in enumerate(value, 1):
                if host or query:
                    current[index] = previous[index] or current[index - 1]
                elif char != "/":
                    current[index] = previous[index - 1] or current[index - 1]

        elif token == "?":
            for index, char in enumerate(value, 1):
                valid = char.isascii() and (char.isalnum() or char in "-_") if host else query or char != "/"
                current[index] = previous[index - 1] and valid

        else:
            for index, char in enumerate(value, 1):
                current[index] = previous[index - 1] and token == char

        previous = current

    if previous[-1]:
        return True

    # URL paths may match descendants, but only across a slash boundary.
    return prefix and any(matched and value[index] == "/" for index, matched in enumerate(previous[:-1]))


class _HostRule:
    """A deterministic host glob with the same ``match`` interface as regex."""

    def __init__(self, pattern):
        if pattern.count(":") == 1:
            host, separator, port = pattern.rpartition(":")
            self.pattern = f"{host.rstrip('.')}{separator}{port}"
        else:
            self.pattern = pattern.rstrip(".")

    def match(self, value):
        value = value.lower()

        # Strip an FQDN's trailing dot without disturbing an explicit port.
        if value.count(":") == 1:
            host, separator, port = value.rpartition(":")
            value = f"{host.rstrip('.')}{separator}{port}"
        else:
            value = value.rstrip(".")

        return _glob_match(self.pattern, value, host=True)


class _URLRule:
    """Match a URL rule by scheme, authority, and path."""

    def __init__(self, token, implicit=False):
        parsed = urlsplit(token)
        self.schemes = ("http", "https") if implicit else (parsed.scheme,)
        self.has_query = "?" in token.split("#", 1)[0]

        try:
            self.path = _canonicalize_path(parsed.path)
            self.query = _decode_url_component(parsed.query).lower()
        except (UnicodeError, ValueError) as exc:
            raise _InvalidURLRuleError(f"URL filter rule cannot be normalized safely: {token}") from exc

        # A missing rule port must not match an explicit candidate port.
        authority = parsed.netloc
        self.host, separator, self.port = authority.rpartition(":")
        if not separator or ":" in self.host:
            self.host = authority
            self.port = None
        self.host = self.host.rstrip(".")

    def match(self, value):
        try:
            candidate = urlsplit(value.lower())
            candidate_path = _canonicalize_path(candidate.path)
            candidate_query = _decode_url_component(candidate.query).lower()
        except (AttributeError, TypeError, UnicodeError, ValueError):
            return False

        if candidate.scheme not in self.schemes:
            return False

        authority = candidate.netloc

        candidate_host, port_separator, candidate_port = authority.rpartition(":")
        if not port_separator or ":" in candidate_host:
            candidate_host = authority
            candidate_port = None

        # Normalize a fully-qualified hostname's optional trailing dot.
        candidate_host = candidate_host.rstrip(".")
        if self.port != candidate_port or not _glob_match(
            self.host,
            candidate_host,
            host=True,
        ):
            return False

        if self.path in ("", "/"):
            path_matches = True
        elif self.path.endswith("/"):
            # A trailing slash means the path itself and all descendants.
            prefix = self.path.rstrip("/")
            path_matches = _glob_match(
                prefix,
                candidate_path.rstrip("/"),
                prefix=True,
            )
        elif self.path.endswith("*"):
            # A trailing star accepts the path with an optional final slash.
            path_matches = _glob_match(
                self.path,
                candidate_path.rstrip("/"),
            )
        else:
            # Other paths match exactly or at a directory boundary.
            path_matches = _glob_match(
                self.path,
                candidate_path,
                prefix=True,
            )

        if not path_matches:
            return False

        # A rule without a query continues to accept any candidate query.
        return not self.has_query or _glob_match(
            self.query,
            candidate_query,
            query=True,
        )


class AppriseURLFilter:
    """Match attachment URLs against parsed allow and deny lists.

    Deny rules take priority. A URL not denied must match an allow rule.

    Each entry in the allow/deny lists can be provided as:
      - A full URL (with http:// or https://)
      - A URL without a scheme (e.g. "localhost/resources")
      - A plain hostname or IP
      - The special token "internal" (see INTERNAL_TOKEN below)

    Wildcards:
      - '*' will match any sequence of characters.
      - '?' will match a single alphanumeric/dash/underscore character.

    URL paths also match their descendants at a directory boundary.
    """

    def __init__(self, allow_list: str, deny_list: str):
        # Parse once so request-time checks remain small and predictable.
        self.allow_rules = self._parse_list(allow_list)
        self.deny_rules = self._parse_list(deny_list)

    def _parse_list(self, list_str: str):
        """Parse comma- or whitespace-separated filter rules.

        Tokens are classified as follows:
          - The reserved "internal" token (resolve + IP-class check).
          - URL tokens with a scheme or path.
          - Host-based tokens: those that do not contain a “/”.
        """
        tokens = re.split(r"[\s,]+", list_str.strip().lower())
        rules = []
        for token in tokens:
            if not token:
                continue

            if token == INTERNAL_TOKEN:
                # Resolved/classified at match time; nothing to compile.
                rules.append((None, "internal"))
                continue

            if token.count("*") > _MAX_WILDCARDS_PER_RULE:
                # Invalid administrator policy should stop startup clearly.
                raise _TooManyWildcardsError(
                    f"URL filter rules may contain at most {_MAX_WILDCARDS_PER_RULE} wildcards: {token}"
                )

            if token.startswith("http://") or token.startswith("https://"):
                # Explicit URL tokens retain their selected scheme.
                compiled = self._compile_url_token(token)
                kind = "url"

            elif "/" in token:
                # Implicit URL tokens match HTTP and HTTPS.
                compiled = self._compile_implicit_token(token)
                kind = "url"

            else:
                compiled = self._compile_host_token(token)
                kind = "host"

            rules.append((compiled, kind))
        return rules

    def _compile_url_token(self, token: str):
        """Prepare an explicit URL rule for deterministic matching."""
        return _URLRule(token)

    def _compile_implicit_token(self, token: str):
        """Prepare a URL rule that accepts either HTTP scheme."""
        return _URLRule("http://" + token, implicit=True)

    def _compile_host_token(self, token: str):
        """Prepare a scheme-independent hostname and optional-port rule."""
        return _HostRule(token)

    def _is_internal_target(self, host: str) -> bool:
        """Return true if any address is internal or resolution fails."""
        addresses = _resolve_addresses(host)
        if not addresses:
            return True

        return any(_is_blocked_address(addr) for addr in addresses)

    def is_host_denied(self, host: str) -> bool:
        """Return whether a hostname matches a host-only deny rule.

        Connection checks lack URL and port details, so URL rules are ignored.
        Address safety is checked separately.
        """
        if not host:
            return True

        return any(pattern.match(host) for pattern, kind in self.deny_rules if kind == "host")

    @property
    def blocks_internal(self) -> bool:
        """Return whether the deny list enables internal-address blocking."""
        return any(kind == "internal" for _pattern, kind in self.deny_rules)

    def is_address_allowed(self, address: str) -> bool:
        """Apply the configured internal-address policy to one DNS answer."""
        if not self.blocks_internal:
            return True

        return is_public_ip_address(address)

    def is_allowed(self, url: str, resolve=True) -> bool:
        """
        Checks a given URL against the deny list first, then the allow list.
        """
        # Reject invalid or unusually long values before parsing or matching.
        if not isinstance(url, str) or len(url) > _MAX_URL_LENGTH:
            return False

        try:
            parsed = parse_url(url, strict_port=True, simple=True)

            # Validate the raw components even when only a host rule applies.
            raw_url = urlsplit(url)
            _canonicalize_path(raw_url.path)
            _decode_url_component(raw_url.query)

        except (UnicodeError, ValueError):
            # Treat malformed URLs that raise, such as broken IPv6, as denied.
            return False

        if not parsed:
            return False

        # A parsed result with no usable host can't be matched against
        # anything meaningfully -- treat it as blocked rather than let an
        # empty/None host reach string formatting or DNS resolution below.
        host = parsed.get("host")
        if not host:
            return False

        # includes port if present
        port = parsed.get("port")
        netloc = f"{host}:{port}" if port is not None else host
        if len(netloc) > _MAX_HOST_LENGTH:
            return False

        # Check deny rules first.
        for pattern, kind in self.deny_rules:
            if kind == "internal":
                if resolve and self._is_internal_target(host):
                    return False

            elif kind == "url":
                if pattern.match(url):
                    return False

            elif pattern.match(netloc):
                return False

        # Then check allow rules. "internal" has no meaning as a positive
        # match (there's nothing bounded to allow), so it's ignored here.
        for pattern, kind in self.allow_rules:
            if kind == "internal":
                continue

            if kind == "url":
                if pattern.match(url):
                    return True

            elif pattern.match(netloc):
                return True

        return False
