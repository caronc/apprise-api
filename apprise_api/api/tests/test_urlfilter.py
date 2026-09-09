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
import socket
from unittest import mock

from django.test import SimpleTestCase

from ..urlfilter import AppriseURLFilter


class AttachmentTests(SimpleTestCase):
    def test_apprise_url_filter(self):
        """
        Test the apprise url filter
        """
        # empty allow and deny lists
        af = AppriseURLFilter("", "")

        # Test garbage entries
        self.assertFalse(af.is_allowed("$"))
        self.assertFalse(af.is_allowed(b"13"))
        self.assertFalse(af.is_allowed("Mālō e Lelei"))
        self.assertFalse(af.is_allowed(""))
        self.assertFalse(af.is_allowed(None))
        self.assertFalse(af.is_allowed(True))
        self.assertFalse(af.is_allowed(42))

        # These ar blocked too since we have no allow list
        self.assertFalse(af.is_allowed("http://localhost"))
        self.assertFalse(af.is_allowed("http://localhost"))

        #
        # We have a wildcard for accept all in our allow list
        #
        af = AppriseURLFilter("*", "")

        # We still block junk
        self.assertFalse(af.is_allowed("$"))
        self.assertFalse(af.is_allowed(b"13"))
        self.assertFalse(af.is_allowed("Mālō e Lelei"))
        self.assertFalse(af.is_allowed(""))
        self.assertFalse(af.is_allowed(None))
        self.assertFalse(af.is_allowed(True))
        self.assertFalse(af.is_allowed(42))

        # We however allow localhost now (caught with *)
        self.assertTrue(af.is_allowed("http://localhost"))
        self.assertTrue(af.is_allowed("http://localhost/resources"))
        self.assertTrue(af.is_allowed("http://localhost/images"))

        #
        # Allow list accepts all, except we want to explicitely block https://localhost/resources
        #
        af = AppriseURLFilter("*", "https://localhost/resources")

        # We still block junk
        self.assertFalse(af.is_allowed("$"))
        self.assertFalse(af.is_allowed(b"13"))
        self.assertFalse(af.is_allowed("Mālō e Lelei"))
        self.assertFalse(af.is_allowed(""))
        self.assertFalse(af.is_allowed(None))
        self.assertFalse(af.is_allowed(True))
        self.assertFalse(af.is_allowed(42))

        # Takeaway is https:// was blocked to resources, but not http:// request
        # because it was explicitly identify as so:
        self.assertTrue(af.is_allowed("http://localhost"))
        self.assertTrue(af.is_allowed("http://localhost/resources"))
        self.assertTrue(af.is_allowed("http://localhost/resources/sub/path/"))
        self.assertFalse(af.is_allowed("https://localhost/resources"))
        self.assertFalse(af.is_allowed("https://localhost/resources?view=1"))
        self.assertFalse(af.is_allowed("https://localhost/resources/sub/path/"))
        self.assertTrue(af.is_allowed("http://localhost/images"))

        #
        # Allow list accepts all, except we want to explicitely block both
        #   https://localhost/resources and http://localhost/resources
        #
        af = AppriseURLFilter("*", "localhost/resources")

        # We still block junk
        self.assertFalse(af.is_allowed("$"))
        self.assertFalse(af.is_allowed(b"13"))
        self.assertFalse(af.is_allowed("Mālō e Lelei"))
        self.assertFalse(af.is_allowed(""))
        self.assertFalse(af.is_allowed(None))
        self.assertFalse(af.is_allowed(True))
        self.assertFalse(af.is_allowed(42))

        # Takeaway is https:// was blocked to resources, but not http:// request
        # because it was explicitly identify as so:
        self.assertTrue(af.is_allowed("http://localhost"))
        self.assertFalse(af.is_allowed("http://localhost/resources"))
        self.assertFalse(af.is_allowed("http://localhost/resources/sub/path"))
        self.assertFalse(af.is_allowed("https://localhost/resources"))
        self.assertFalse(af.is_allowed("https://localhost/resources/sub/path/"))
        self.assertTrue(af.is_allowed("http://localhost/images"))

        #
        # A more restrictive allow/block list
        #   https://localhost/resources and http://localhost/resources
        #
        af = AppriseURLFilter("https://localhost, http://myserver.*", "localhost/resources")

        # We still block junk
        self.assertFalse(af.is_allowed("$"))
        self.assertFalse(af.is_allowed(b"13"))
        self.assertFalse(af.is_allowed("Mālō e Lelei"))
        self.assertFalse(af.is_allowed(""))
        self.assertFalse(af.is_allowed(None))
        self.assertFalse(af.is_allowed(True))
        self.assertFalse(af.is_allowed(42))

        # Explicitly only allows https
        self.assertFalse(af.is_allowed("http://localhost"))
        self.assertTrue(af.is_allowed("https://localhost"))
        self.assertFalse(af.is_allowed("https://localhost:8000"))
        self.assertTrue(af.is_allowed("https://localhost/images"))
        self.assertFalse(af.is_allowed("https://localhost/resources"))
        self.assertFalse(af.is_allowed("https://localhost/resources/sub/path/"))
        self.assertFalse(af.is_allowed("http://localhost/resources"))
        self.assertFalse(af.is_allowed("http://localhost/resources/sub/path"))
        self.assertFalse(af.is_allowed("http://not-in-list"))

        # Explicitly definition of allowed hostname prohibits the below from working:
        self.assertFalse(af.is_allowed("localhost"))

        #
        # Testing of hostnames only and ports
        #
        af = AppriseURLFilter("localhost, myserver:3000", "localhost/resources")

        # We still block junk
        self.assertFalse(af.is_allowed("$"))
        self.assertFalse(af.is_allowed(b"13"))
        self.assertFalse(af.is_allowed("Mālō e Lelei"))
        self.assertFalse(af.is_allowed(""))
        self.assertFalse(af.is_allowed(None))
        self.assertFalse(af.is_allowed(True))
        self.assertFalse(af.is_allowed(42))

        # all forms of localhost is allowed (provided there is no port)
        self.assertTrue(af.is_allowed("http://localhost"))
        self.assertTrue(af.is_allowed("https://localhost"))
        self.assertFalse(af.is_allowed("https://localhost:8000"))
        self.assertFalse(af.is_allowed("https://localhost:80"))
        self.assertFalse(af.is_allowed("https://localhost:443"))
        self.assertTrue(af.is_allowed("https://localhost/images"))
        self.assertFalse(af.is_allowed("https://localhost/resources"))
        self.assertFalse(af.is_allowed("https://localhost/resources/sub/path"))
        self.assertFalse(af.is_allowed("http://localhost/resources"))
        self.assertTrue(af.is_allowed("http://localhost/resourcesssssssss"))
        self.assertFalse(af.is_allowed("http://localhost/resources/sub/path/"))
        self.assertFalse(af.is_allowed("http://not-in-list"))

        # myserver is only allowed if port is provided
        self.assertFalse(af.is_allowed("http://myserver"))
        self.assertFalse(af.is_allowed("https://myserver"))
        self.assertTrue(af.is_allowed("http://myserver:3000"))
        self.assertTrue(af.is_allowed("https://myserver:3000"))

        # Open range of hosts allows these to be accepted:
        self.assertTrue(af.is_allowed("localhost"))
        self.assertTrue(af.is_allowed("myserver:3000"))
        self.assertTrue(af.is_allowed("https://myserver:3000"))
        self.assertTrue(af.is_allowed("http://myserver:3000"))

        #
        # Testing of hostnames only and ports but via URLs (explicit http://)
        # Also tests path ending with `/` (slash)
        #
        af = AppriseURLFilter(
            "http://localhost, http://myserver:3000",
            "http://localhost/resources/",
        )

        # We still block junk
        self.assertFalse(af.is_allowed("$"))
        self.assertFalse(af.is_allowed(b"13"))
        self.assertFalse(af.is_allowed("Mālō e Lelei"))
        self.assertFalse(af.is_allowed(""))
        self.assertFalse(af.is_allowed(None))
        self.assertFalse(af.is_allowed(True))
        self.assertFalse(af.is_allowed(42))

        # http://localhost acceptance only
        self.assertTrue(af.is_allowed("http://localhost"))
        self.assertFalse(af.is_allowed("https://localhost"))
        self.assertFalse(af.is_allowed("http://localhost:8000"))
        self.assertFalse(af.is_allowed("http://localhost:80"))
        self.assertTrue(af.is_allowed("http://localhost/images"))
        self.assertFalse(af.is_allowed("https://localhost/images"))
        self.assertFalse(af.is_allowed("https://localhost/resources"))
        self.assertFalse(af.is_allowed("https://localhost/resources/sub/path"))
        self.assertFalse(af.is_allowed("http://localhost/resources"))
        self.assertFalse(af.is_allowed("http://not-in-list"))

        # myserver is only allowed if port is provided and http://
        self.assertFalse(af.is_allowed("http://myserver"))
        self.assertFalse(af.is_allowed("https://myserver"))
        self.assertTrue(af.is_allowed("http://myserver:3000"))
        self.assertFalse(af.is_allowed("https://myserver:3000"))
        self.assertTrue(af.is_allowed("http://myserver:3000/path/"))

        # Open range of hosts is no longer allowed due to explicit http:// reference
        self.assertFalse(af.is_allowed("localhost"))
        self.assertFalse(af.is_allowed("myserver:3000"))
        self.assertFalse(af.is_allowed("https://myserver:3000"))

        #
        # Testing of hostnames only and ports but via URLs (explicit https://)
        # Also tests path ending with `/` (slash)
        #
        af = AppriseURLFilter(
            "https://localhost, https://myserver:3000",
            "https://localhost/resources/",
        )

        # We still block junk
        self.assertFalse(af.is_allowed("$"))
        self.assertFalse(af.is_allowed(b"13"))
        self.assertFalse(af.is_allowed("Mālō e Lelei"))
        self.assertFalse(af.is_allowed(""))
        self.assertFalse(af.is_allowed(None))
        self.assertFalse(af.is_allowed(True))
        self.assertFalse(af.is_allowed(42))

        # http://localhost acceptance only
        self.assertTrue(af.is_allowed("https://localhost"))
        self.assertFalse(af.is_allowed("http://localhost"))
        self.assertFalse(af.is_allowed("localhost"))
        self.assertFalse(af.is_allowed("https://localhost:8000"))
        self.assertFalse(af.is_allowed("https://localhost:80"))
        self.assertTrue(af.is_allowed("https://localhost/images"))
        self.assertFalse(af.is_allowed("http://localhost/images"))
        self.assertFalse(af.is_allowed("http://localhost/resources"))
        self.assertFalse(af.is_allowed("http://localhost/resources/sub/path"))
        self.assertFalse(af.is_allowed("https://localhost/resources"))
        self.assertFalse(af.is_allowed("https://not-in-list"))

        # myserver is only allowed if port is provided and http://
        self.assertFalse(af.is_allowed("https://myserver"))
        self.assertFalse(af.is_allowed("http://myserver"))
        self.assertFalse(af.is_allowed("myserver"))
        self.assertTrue(af.is_allowed("https://myserver:3000"))
        self.assertFalse(af.is_allowed("http://myserver:3000"))
        self.assertTrue(af.is_allowed("https://myserver:3000/path/"))

        # Open range of hosts is no longer allowed due to explicit http:// reference
        self.assertFalse(af.is_allowed("localhost"))
        self.assertFalse(af.is_allowed("myserver:3000"))
        self.assertFalse(af.is_allowed("http://myserver:3000"))

        #
        # Testing Regular Expressions
        #
        af = AppriseURLFilter("https://localhost/incoming/*/*", "https://localhost/*/*/var")

        # We still block junk
        self.assertFalse(af.is_allowed("$"))
        self.assertFalse(af.is_allowed(b"13"))
        self.assertFalse(af.is_allowed("Mālō e Lelei"))
        self.assertFalse(af.is_allowed(""))
        self.assertFalse(af.is_allowed(None))
        self.assertFalse(af.is_allowed(True))
        self.assertFalse(af.is_allowed(42))

        # Very specific paths are supported now in https://localhost only:
        self.assertFalse(af.is_allowed("https://localhost"))
        self.assertFalse(af.is_allowed("http://localhost"))
        self.assertFalse(af.is_allowed("https://localhost/incoming"))
        self.assertFalse(af.is_allowed("https://localhost/incoming/dir1"))
        self.assertFalse(af.is_allowed("https://localhost/incoming/dir1/"))

        self.assertTrue(af.is_allowed("https://localhost/incoming/dir1/dir2"))
        self.assertTrue(af.is_allowed("https://localhost/incoming/dir1/dir2/"))
        self.assertFalse(af.is_allowed("http://localhost/incoming/dir1/dir2"))
        self.assertFalse(af.is_allowed("http://localhost/incoming/dir1/dir2/"))

        # our incoming directory we restricted
        self.assertFalse(af.is_allowed("https://localhost/incoming/dir1/var"))
        self.assertFalse(af.is_allowed("https://localhost/incoming/dir1/var/"))
        self.assertFalse(af.is_allowed("https://localhost/incoming/dir1/var/sub/dir"))
        self.assertFalse(af.is_allowed("https://localhost/incoming/dir1/var/sub"))

        # Test the ? out
        af = AppriseURLFilter("localhost?", "")

        # Test garbage entries
        self.assertFalse(af.is_allowed("$"))
        self.assertFalse(af.is_allowed(b"13"))
        self.assertFalse(af.is_allowed("Mālō e Lelei"))
        self.assertFalse(af.is_allowed(""))
        self.assertFalse(af.is_allowed(None))
        self.assertFalse(af.is_allowed(True))
        self.assertFalse(af.is_allowed(42))

        # These are blocked too since we have no allow list
        self.assertFalse(af.is_allowed("http://localhost"))
        self.assertTrue(af.is_allowed("http://localhost1"))
        self.assertTrue(af.is_allowed("https://localhost1"))
        self.assertFalse(af.is_allowed("http://localhost%"))
        self.assertFalse(af.is_allowed("http://localhost10"))

        # conflicting elements cancel one another
        af = AppriseURLFilter("localhost", "localhost")

        # These are blocked too since we have no allow list
        self.assertFalse(af.is_allowed("localhost"))

    def test_internal_token_blocks_literal_addresses(self):
        """
        The "internal" deny token resolves and IP-classifies the
        destination instead of string-matching it. Literal IP addresses
        need no DNS resolution, so these cases are deterministic and
        network-free.
        """
        af = AppriseURLFilter("*", "internal")

        # Loopback, in a few different spellings/encodings
        self.assertFalse(af.is_allowed("http://127.0.0.1/x"))
        self.assertFalse(af.is_allowed("http://2130706433/x"))  # decimal 127.0.0.1
        self.assertFalse(af.is_allowed("http://[::1]/x"))  # IPv6 loopback
        self.assertFalse(af.is_allowed("http://[::ffff:127.0.0.1]/x"))  # IPv4-mapped

        # RFC1918 private ranges
        self.assertFalse(af.is_allowed("http://10.0.0.5/x"))
        self.assertFalse(af.is_allowed("http://172.16.0.5/x"))
        self.assertFalse(af.is_allowed("http://192.168.1.1/x"))

        # Link-local / cloud metadata, unspecified, multicast, CGN shared space
        self.assertFalse(af.is_allowed("http://169.254.169.254/x"))
        self.assertFalse(af.is_allowed("http://0.0.0.0/x"))
        self.assertFalse(af.is_allowed("http://224.0.0.1/x"))
        self.assertFalse(af.is_allowed("http://100.64.0.1/x"))

        # A real public address is unaffected
        self.assertTrue(af.is_allowed("http://8.8.8.8/x"))

    def test_internal_token_resolves_hostnames(self):
        """
        A hostname that resolves to an internal address must be blocked
        the same way a literal internal IP is -- this is the exact
        bypass class (an internal DNS name) that a wildcard host/spelling
        deny list can't catch.
        """
        af = AppriseURLFilter("*", "internal")

        with mock.patch(
            "socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.97.3", 0))],
        ):
            self.assertFalse(af.is_allowed("http://ssrf-marker.internal.example/x"))

        with mock.patch(
            "socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 0))],
        ):
            self.assertTrue(af.is_allowed("http://example.com/x"))

    def test_internal_token_fails_closed_on_unresolvable_host(self):
        """
        A host that can't be resolved at all can't be proven safe, so it
        must be treated as blocked rather than allowed.
        """
        af = AppriseURLFilter("*", "internal")

        with mock.patch("socket.getaddrinfo", side_effect=socket.gaierror("name not known")):
            self.assertFalse(af.is_allowed("http://this-does-not-resolve.invalid/x"))

    def test_internal_token_fails_closed_on_resolution_timeout(self):
        """A resolver timeout is treated as blocked."""
        af = AppriseURLFilter("*", "internal")
        with mock.patch(
            "apprise_api.api.urlfilter._HTTP_RESOLVER.resolve",
            side_effect=socket.gaierror("timed out"),
        ):
            self.assertFalse(af.is_allowed("http://slow-dns.example/x"))

    def test_dns_resolution_failure_is_closed(self):
        """A failed shared resolver cannot make a host appear public."""
        from ..urlfilter import _resolve_addresses

        with mock.patch(
            "apprise_api.api.urlfilter._HTTP_RESOLVER.resolve",
            side_effect=RuntimeError("resolver unavailable"),
        ):
            self.assertIsNone(_resolve_addresses("busy.example"))

    def test_internal_token_skips_unparseable_resolved_records(self):
        """
        A malformed/unexpected address record from the resolver is
        skipped rather than crashing the whole lookup; the destination is
        still correctly classified using any remaining valid records.
        """
        af = AppriseURLFilter("*", "internal")

        with mock.patch(
            "socket.getaddrinfo",
            return_value=[
                # Malformed entry: not a parseable IP literal
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("not-an-ip", 0)),
                # Valid public address
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0)),
            ],
        ):
            self.assertTrue(af.is_allowed("http://mixed-records.example/x"))

        with mock.patch(
            "socket.getaddrinfo",
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("not-an-ip", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.1", 0)),
            ],
        ):
            self.assertFalse(af.is_allowed("http://mixed-records-internal.example/x"))

    def test_internal_token_in_allow_list_is_a_noop(self):
        """
        "internal" positive match handling.
        """
        af = AppriseURLFilter("internal", "")
        self.assertFalse(af.is_allowed("http://8.8.8.8/x"))
        self.assertFalse(af.is_allowed("http://example.com/x"))

    def test_is_host_denied_matches_host_rules_only(self):
        """Bare-host checks apply host rules without URL or port details."""
        af = AppriseURLFilter("*", "localhost* evil.example.com")

        self.assertTrue(af.is_host_denied("localhost"))
        self.assertTrue(af.is_host_denied("localhost.localdomain"))
        self.assertTrue(af.is_host_denied("evil.example.com"))
        self.assertTrue(af.is_host_denied("evil.example.com."))
        self.assertFalse(af.is_host_denied("example.com"))

    def test_is_host_denied_ignores_url_kind_deny_rules(self):
        """Bare-host checks ignore rules that require a full URL."""
        af = AppriseURLFilter("*", "https://example.com/blocked")

        self.assertFalse(af.is_host_denied("example.com"))

    def test_is_host_denied_blocks_missing_host(self):
        """A missing hostname can't be proven safe, so it is denied."""
        af = AppriseURLFilter("*", "internal")

        self.assertTrue(af.is_host_denied(""))
        self.assertTrue(af.is_host_denied(None))

    def test_malformed_url_does_not_raise(self):
        """
        handling of malformed urls.
        """
        af = AppriseURLFilter("*", "127.0.* localhost* internal")

        # Unbalanced IPv6 bracket: apprise's parse_url() raises here.
        self.assertFalse(af.is_allowed("http://[/x"))
        self.assertFalse(af.is_allowed("http://[::1/x"))

    def test_url_rule_rejects_non_string_value(self):
        """A URL rule safely rejects values outside normal filter input."""
        from ..urlfilter import _URLRule

        self.assertFalse(_URLRule("https://example.com").match(None))

    def test_query_rules_match_candidate_queries(self):
        """Configured query patterns participate in URL matching."""
        af = AppriseURLFilter(
            "https://example.com/path?view=*",
            "https://example.com/path?token=*",
        )

        self.assertFalse(af.is_allowed("https://example.com/path?token=secret"))
        self.assertTrue(af.is_allowed("https://example.com/path?view=summary"))
        self.assertTrue(af.is_allowed("https://example.com/path?view="))
        self.assertFalse(af.is_allowed("https://example.com/path?other=value"))

        encoded = AppriseURLFilter(
            "*",
            "https://example.com/path?token=secret",
        )
        self.assertFalse(encoded.is_allowed("https://example.com/path?token=%73ecret"))

        single = AppriseURLFilter(
            "https://example.com/path?id=?",
            "",
        )
        self.assertTrue(single.is_allowed("https://example.com/path?id=a"))
        self.assertFalse(single.is_allowed("https://example.com/path?id=ab"))

    def test_query_wildcards_accept_query_punctuation(self):
        """A query wildcard may span URL values containing separators."""
        af = AppriseURLFilter(
            "https://example.com/path?next=*",
            "",
        )

        self.assertTrue(af.is_allowed("https://example.com/path?next=/one/two&mode=full"))

    def test_path_rules_use_canonical_paths(self):
        """Encoded names, separators, and dot segments cannot bypass rules."""
        deny_admin = AppriseURLFilter("*", "https://example.com/admin")
        self.assertFalse(deny_admin.is_allowed("https://example.com/a%64min"))
        self.assertFalse(deny_admin.is_allowed("https://example.com/../admin"))
        self.assertFalse(deny_admin.is_allowed("https://example.com/x/../admin"))
        self.assertFalse(deny_admin.is_allowed("https://example.com/x%2f..%2fadmin"))
        self.assertFalse(deny_admin.is_allowed("https://example.com/x\\..\\admin"))

        allow_public = AppriseURLFilter("https://example.com/public", "")
        self.assertFalse(allow_public.is_allowed("https://example.com/public/%2e%2e/admin"))
        self.assertTrue(allow_public.is_allowed("https://example.com/public/./images"))

    def test_ambiguous_url_encoding_fails_closed(self):
        """Malformed, nested, invalid UTF-8, and control escapes are denied."""
        af = AppriseURLFilter("*", "")

        self.assertFalse(af.is_allowed("https://example.com/bad%escape"))
        self.assertFalse(af.is_allowed("https://example.com/%252e%252e/admin"))
        self.assertFalse(af.is_allowed("https://example.com/%ff"))
        self.assertFalse(af.is_allowed("https://example.com/path?value=%00"))

    def test_unsafe_configured_rule_is_rejected(self):
        """Ambiguous administrator URL rules fail during configuration."""
        from ..exceptions import AppriseAPIImproperlyConfigured

        with self.assertRaises(AppriseAPIImproperlyConfigured):
            AppriseURLFilter("https://example.com/%252e", "")

    def test_unresolvable_host_does_not_raise(self):
        """
        A hostname whose IDNA encoding is invalid or too long raises
        UnicodeError from socket.getaddrinfo(); verify we handle this.
        """
        af = AppriseURLFilter("*", "internal")

        # 300 raw characters is long enough to fail IDNA encoding
        # regardless of whether it also happens to get rejected earlier
        # by apprise's own URL parsing.
        self.assertFalse(af.is_allowed("http://" + "a" * 300 + "/x"))

    def test_resolve_addresses_never_raises_on_bad_host(self):
        """
        _resolve_addresses() resolution failure handling.
        """
        from ..urlfilter import _resolve_addresses

        self.assertIsNone(_resolve_addresses("a" * 300))
        self.assertIsNone(_resolve_addresses("xn--" + "a" * 70))

    def test_empty_parsed_host_is_blocked_not_crashed(self):
        """
        handling of unparsable hosts
        """
        af = AppriseURLFilter("*", "internal")

        for host_value in ("", None):
            with mock.patch("apprise_api.api.urlfilter.parse_url", return_value={"host": host_value}):
                self.assertFalse(af.is_allowed("http://whatever/x"))

    def test_parse_url_value_error_is_blocked_not_crashed(self):
        """Malformed URLs are blocked when Apprise cannot parse them."""
        af = AppriseURLFilter("*", "internal")

        with mock.patch("apprise_api.api.urlfilter.parse_url", side_effect=ValueError):
            self.assertFalse(af.is_allowed("http://whatever/x"))


class WildcardBacktrackingHardeningTests(SimpleTestCase):
    """Prevent wildcard rules from causing costly attachment URL matching."""

    def test_excessive_wildcards_are_rejected_at_compile_time(self):
        """A pattern past the wildcard cap rejects invalid configuration."""
        from ..urlfilter import (
            _MAX_WILDCARDS_PER_RULE,
            _TooManyWildcardsError,
        )

        # One more wildcard than the cap allows.
        pattern = "*" + ("a*" * (_MAX_WILDCARDS_PER_RULE + 1))
        with self.assertRaises(_TooManyWildcardsError):
            AppriseURLFilter("", pattern)

    def test_wildcard_count_at_the_cap_still_compiles_and_matches(self):
        """The cap does not reject legitimate patterns at or under the limit."""
        from ..urlfilter import _MAX_WILDCARDS_PER_RULE

        # Exactly _MAX_WILDCARDS_PER_RULE wildcards, not one more.
        pattern = "a*" * _MAX_WILDCARDS_PER_RULE
        af = AppriseURLFilter(pattern, "")
        self.assertTrue(af.is_allowed("http://" + "a" * 20 + "/"))

    def test_excessive_wildcard_pattern_stays_fast(self):
        """Allowed wildcard patterns match in bounded time."""
        import time

        from ..urlfilter import _MAX_WILDCARDS_PER_RULE

        pattern = "*" + ("a*" * (_MAX_WILDCARDS_PER_RULE - 1)) + "b"
        af = AppriseURLFilter("", pattern)
        hostile = "http://" + "a" * 253 + "/"

        start = time.perf_counter()
        result = af.is_allowed(hostile)
        elapsed = time.perf_counter() - start

        self.assertFalse(result)
        self.assertLess(elapsed, 1.0, "Matching took too long; wildcard backtracking may be unbounded again")

    def test_path_prefix_matching_stays_fast(self):
        """Many path boundaries do not cause repeated glob evaluation."""
        import time

        af = AppriseURLFilter("https://example.com/a*a*a*a*a*a*a*b", "")
        hostile = "https://example.com/" + ("a/" * 1500)

        start = time.perf_counter()
        result = af.is_allowed(hostile)
        elapsed = time.perf_counter() - start

        self.assertFalse(result)
        self.assertLess(elapsed, 1.0)

    def test_overlong_url_is_rejected_before_matching(self):
        """A URL past the length ceiling is denied without ever being parsed."""
        from ..urlfilter import _MAX_URL_LENGTH

        af = AppriseURLFilter("*", "")
        overlong = "http://example.com/" + ("a" * _MAX_URL_LENGTH)
        self.assertFalse(af.is_allowed(overlong))

    def test_overlong_host_is_rejected_before_matching(self):
        """A host past the length ceiling is denied even inside an otherwise-short URL."""
        from ..urlfilter import _MAX_HOST_LENGTH

        af = AppriseURLFilter("*", "")
        overlong_host = "http://" + ("a" * (_MAX_HOST_LENGTH + 1)) + "/x"
        self.assertFalse(af.is_allowed(overlong_host))

        # Keep the post-parse guard covered independently of parser limits.
        with mock.patch(
            "apprise_api.api.urlfilter.parse_url",
            return_value={"host": "a" * (_MAX_HOST_LENGTH + 1)},
        ):
            self.assertFalse(af.is_allowed("http://example.com/x"))
