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
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import ipaddress
import re
import socket
import threading

from apprise.utils.parse import parse_url

# A reserved deny-list token; see the "internal" entry in the
# AppriseURLFilter class docstring below for what it does.
INTERNAL_TOKEN = "internal"

# Slow DNS Handling
_RESOLVE_TIMEOUT_SEC = 5
_RESOLVE_MAX_PENDING = 16

# A shared pool and admission limit for DNS resolution. ThreadPoolExecutor's
# internal queue is unbounded, so the semaphore also caps queued work.
_RESOLVE_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="apprise-urlfilter-resolve")
_RESOLVE_SLOTS = threading.BoundedSemaphore(_RESOLVE_MAX_PENDING)

# 100.64.0.0/10 - RFC 6598 - Carrier-Grade NAT shared address space
_CGN_SHARED_V4 = ipaddress.ip_network("100.64.0.0/10")


def _release_resolve_slot(_future):
    """Release one DNS admission slot after its future stops running."""
    _RESOLVE_SLOTS.release()


def _is_blocked_address(addr) -> bool:
    """
    Return True if the given ipaddress.IPv4Address/IPv6Address should
    never be reachable from an attachment fetch: loopback, private,
    link-local, reserved, unspecified, multicast, or CGN shared space.
    """
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_unspecified
        or addr.is_multicast
        or (isinstance(addr, ipaddress.IPv4Address) and addr in _CGN_SHARED_V4)
    )


def _resolve_addresses(host: str):
    """
    Resolve a host to its ipaddress.IPv4Address/IPv6Address objects.

    Returns None if the host could not be resolved (including on
    timeout) -- callers must treat that the same as "blocked", not
    "allowed", since a destination that can't be classified can't be
    proven safe.
    """
    # A literal IP (optionally bracketed, e.g. "[::1]") needs no lookup.
    literal = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        return [ipaddress.ip_address(literal)]

    except ValueError:
        # Not a literal address; fall through to DNS resolution below.
        pass

    # Fail closed when every bounded DNS slot is busy. This prevents slow or
    # malicious resolvers from filling ThreadPoolExecutor's unbounded queue.
    if not _RESOLVE_SLOTS.acquire(blocking=False):
        return None

    try:
        future = _RESOLVE_POOL.submit(socket.getaddrinfo, literal, None)

    except Exception:
        _RESOLVE_SLOTS.release()
        return None

    # A running getaddrinfo() call cannot be forcibly killed safely. Keep its
    # slot until it really completes (or a queued future is cancelled).
    future.add_done_callback(_release_resolve_slot)
    try:
        results = future.result(timeout=_RESOLVE_TIMEOUT_SEC)

    except FutureTimeoutError:
        future.cancel()
        return None

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
      - The special token "internal" (see INTERNAL_TOKEN below)

    Wildcards:
      - '*' will match any sequence of characters.
      - '?' will match a single alphanumeric/dash/underscore character.

    A trailing '*' is implied if not already present so that rules operate as a prefix match.
    """

    def __init__(self, allow_list: str, deny_list: str):
        # Parsing once at startup keeps request-time checks small and predictable.
        # Pre-compile our rules.
        # Each rule is stored as a tuple (compiled_regex, kind) where kind is
        # one of "url", "host", or "internal". compiled_regex is None for
        # "internal" rules since they resolve/classify instead of matching.
        self.allow_rules = self._parse_list(allow_list)
        self.deny_rules = self._parse_list(deny_list)

    def _parse_list(self, list_str: str):
        """
        Split the list (tokens separated by whitespace or commas) and compile each token.
        Tokens are classified as follows:
          - The reserved "internal" token (resolve + IP-class check).
          - URL-based tokens: if they start with “http://” or “https://” (explicit)
            or if they contain a “/” (implicit; no scheme given).
          - Host-based tokens: those that do not contain a “/”.
        Returns a list of tuples (compiled_regex, kind).
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

            if token.startswith("http://") or token.startswith("https://"):
                # Explicit URL token.
                compiled = self._compile_url_token(token)
                kind = "url"

            elif "/" in token:
                # Implicit URL token: prepend a scheme pattern.
                compiled = self._compile_implicit_token(token)
                kind = "url"

            else:
                # Host-based token.
                compiled = self._compile_host_token(token)
                kind = "host"

            rules.append((compiled, kind))
        return rules

    def _compile_url_token(self, token: str):
        """
        Compiles a URL-based token (explicit token that starts with a scheme) into a regex.
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
            scheme_regex = r"http"
            # drop http://
            token = token[7:]

        elif token.startswith("https://"):
            scheme_regex = r"https"
            # drop https://
            token = token[8:]

        else:  # https?
            # Used for implicit tokens; our _compile_implicit_token ensures this.
            scheme_regex = r"https?"
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
        Compiles a host-based token (one with no "/") into a regex.
        Note: When matching host-based tokens, we require that the URL's scheme is exactly "http".
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
            if char == "*":
                regex += r"[^/]+/?" if not is_host else r".*"

            elif char == "?":
                regex += r"[^/]" if not is_host else r"[A-Za-z0-9_-]"

            else:
                regex += re.escape(char)

        return regex

    def _is_internal_target(self, host: str) -> bool:
        """
        Resolves the given host and returns True if any resulting address is
        loopback, private, link-local, reserved, unspecified, multicast, or
        CGN shared space. A host that can't be resolved (including on
        timeout) is treated as internal/blocked because it can't be proven safe.
        """
        addresses = _resolve_addresses(host)
        if not addresses:
            return True

        return any(_is_blocked_address(addr) for addr in addresses)

    def is_allowed(self, url: str) -> bool:
        """
        Checks a given URL against the deny list first, then the allow list.
        """
        try:
            parsed = parse_url(url, strict_port=True, simple=True)

        except ValueError:
            # apprise's parse_url() can raise on certain malformed input
            # (e.g. an unbalanced IPv6 bracket) rather than returning None
            # like it does for other garbage; treat it the same way.
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

        # Check deny rules first.
        for pattern, kind in self.deny_rules:
            if kind == "internal":
                if self._is_internal_target(host):
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
