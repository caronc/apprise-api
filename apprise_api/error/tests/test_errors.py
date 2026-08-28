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

from pathlib import Path

from api.utils import CONFIG_KEY_MAX_LENGTH
from django.test import SimpleTestCase
from django.test.utils import override_settings


class ErrorTests(SimpleTestCase):
    def test_accept_selects_response_format(self):
        """Error responses use Accept instead of request Content-Type."""
        for url in ("/_/401", "/_/403", "/_/404", "/_/421", "/_/429", "/_/50x"):
            with self.subTest(url=url):
                response = self.client.get(
                    url,
                    CONTENT_TYPE="application/json",
                    HTTP_ACCEPT="text/html",
                )
                assert response["Content-Type"].startswith("text/html")

                response = self.client.get(url, HTTP_ACCEPT="application/json")
                assert response["Content-Type"].startswith("application/json")

                response = self.client.get(
                    url,
                    CONTENT_TYPE="application/json",
                )
                assert response["Content-Type"].startswith("application/json")

                response = self.client.get(
                    url,
                    CONTENT_TYPE="application/json",
                    HTTP_ACCEPT="*/*",
                )
                assert response["Content-Type"].startswith("application/json")

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN="admin-token")
    def test_auth_error_pages_bypass_authentication(self):
        """Internal error pages remain reachable while authentication is on."""
        response = self.client.get("/_/401", HTTP_ACCEPT="text/html")
        assert response.status_code == 401
        assert response["WWW-Authenticate"] == 'Basic realm="Apprise API"'
        assert b"Authentication Required" in response.content

        response = self.client.get("/_/403", HTTP_ACCEPT="text/html")
        assert response.status_code == 403
        assert b"Permission Denied" in response.content

        response = self.client.get("/_/429", HTTP_ACCEPT="text/html")
        assert response.status_code == 429
        assert response["Retry-After"] == "60"
        assert b"Too Many Requests" in response.content

    def test_nginx_maps_auth_error_pages(self):
        """Nginx keeps local pages while preserving Django auth responses."""
        etc_dir = Path(__file__).resolve().parents[2] / "etc"
        error_pages = (
            "error_page 401 = /_/401/;",
            "error_page 403 = /_/403/;",
            "error_page 404 = /_/404/;",
            "error_page 421 = /_/421/;",
            "error_page 429 = /_/429/;",
            "error_page 500 = /_/50x/;",
            "error_page 502 503 504 /50x.html;",
        )
        for filename in ("nginx.conf", "nginx-strict.conf"):
            config = (etc_dir / filename).read_text(encoding="utf-8")
            with self.subTest(filename=filename):
                for error_page in error_pages:
                    assert error_page in config
                assert "proxy_intercept_errors off;" in config
                assert "proxy_set_header X-Original-URI $request_uri;" in config
                assert "proxy_set_header X-Original-Method $request_method;" in config
                assert "location = /50x.html {" in config
                assert "root /usr/share/nginx/html/s;" in config
                assert 'location ~ "^/login/?$"' in config
                assert "limit_req_status 429;" in config
                assert "proxy_set_header X-Real-IP $remote_addr;" in config
                assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in config
                assert 'location ~ "^/notify/?$"' in config
                assert 'location ~ "^/cfg/?$"' in config
                assert 'location ~ "^/cfg/([\\w_-]{{1,{}}}|@)/?$"'.format(CONFIG_KEY_MAX_LENGTH) in config
                assert (
                    'location ~ "^/(status(/([\\w_-]{{1,{}}}|@))?|metrics)/?$"'.format(CONFIG_KEY_MAX_LENGTH) in config
                )

        fallback = Path(__file__).resolve().parents[2] / "static" / "50x.html"
        assert fallback.is_file()
        assert "Service Temporarily Unavailable" in fallback.read_text(encoding="utf-8")

        strict = (etc_dir / "nginx-strict.conf").read_text(encoding="utf-8")
        regular = (etc_dir / "nginx.conf").read_text(encoding="utf-8")
        assert "limit_req_zone $auth_limit_key zone=auth:10m rate=1r/s;" in strict
        assert "limit_req_zone $binary_remote_addr zone=key_auth:10m rate=10r/s;" in strict
        assert "limit_req zone=key_auth burst=20 nodelay;" in strict
        assert "zone=auth:10m" not in regular
        assert "zone=key_auth:10m" not in regular
        assert 'location ~ "^/logout/?$"' in strict
        assert 'location ~ "^/auth(/([\\w_-]{{1,{}}}|@))?/?$"'.format(CONFIG_KEY_MAX_LENGTH) in strict
        assert "if ($request_method !~ ^(GET|POST|DELETE)$)" in strict

    def test_get_401(self):
        """The static authentication page includes its challenge header."""
        response = self.client.get("/_/401")
        assert response.status_code == 401
        assert response["WWW-Authenticate"] == 'Basic realm="Apprise API"'

    def test_get_404(self):
        """
        Test 404
        """
        response = self.client.get("/_/404")
        assert response.status_code == 404

    def test_get_403(self):
        """The permission page returns the forbidden status."""
        response = self.client.get("/_/403")
        assert response.status_code == 403

    def test_get_421(self):
        """
        Test 421
        """
        response = self.client.get("/_/421")
        assert response.status_code == 421

    def test_get_429(self):
        """The static rate-limit page tells clients when to retry."""
        response = self.client.get("/_/429")
        assert response.status_code == 429
        assert response["Retry-After"] == "60"

    def test_get_50x(self):
        """
        Test 50x
        """
        response = self.client.get("/_/50x")
        assert response.status_code == 500
