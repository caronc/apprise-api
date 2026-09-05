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
import base64
from json import loads
import os
import re
import shutil
import subprocess
import tempfile

from django.conf import settings
from django.http import HttpResponse
from django.test import Client, RequestFactory, SimpleTestCase
from django.test.utils import override_settings

from ..auth import Authentication
from ..utils import ConfigCache
from ..views import _build_apprise_mobile_url


def _basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


_MASTER_TOKEN = base64.b64encode(b"master:pass").decode()
_GOOD_MASTER = {"authorization": _basic("master", "pass")}


class BuildAppriseMobileUrlTests(SimpleTestCase):
    """Test the apprise[s]:// URL builder used by the Apprise Mobile QR code."""

    def setUp(self):
        self.factory = RequestFactory()

    def test_no_credentials_when_auth_disabled(self):
        """No auth means no credentials, only host and config ID."""
        request = self.factory.get("/qr/mykey")
        with override_settings(APPRISE_AUTH_REQUIRED=False):
            url = _build_apprise_mobile_url(request, "mykey")
        self.assertEqual(url, "apprise://testserver/mykey")

    def test_secure_request_uses_apprises_scheme(self):
        """An HTTPS request builds an apprises:// URL."""
        request = self.factory.get("/qr/mykey", secure=True)
        with override_settings(APPRISE_AUTH_REQUIRED=False):
            url = _build_apprise_mobile_url(request, "mykey")
        self.assertEqual(url, "apprises://testserver/mykey")

    @override_settings(BASE_URL="/apprise")
    def test_base_url_prefix_is_included(self):
        """A reverse-proxy base path sits between the host and the config ID."""
        request = self.factory.get("/qr/mykey")
        with override_settings(APPRISE_AUTH_REQUIRED=False):
            url = _build_apprise_mobile_url(request, "mykey")
        self.assertEqual(url, "apprise://testserver/apprise/mykey")

    @override_settings(
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_USER="admin",
        APPRISE_PASSWORD="s3cr3t",
    )
    def test_admin_session_includes_username_without_password(self):
        """An unassigned configuration falls back to the administrator login."""
        request = self.factory.get("/qr/mykey")
        request.globally_authenticated = True
        url = _build_apprise_mobile_url(request, "mykey")
        self.assertEqual(url, "apprise://:admin@testserver/mykey")

    @override_settings(
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_USER="admin",
        APPRISE_PASSWORD="s3cr3t",
    )
    def test_admin_session_prefers_assigned_config_username(self):
        """An assigned configuration login takes priority for an administrator."""
        key = "qr_url_admin_assigned_key"
        self.addCleanup(ConfigCache.clear_auth, key)
        ConfigCache.set_auth(key, "alice", "alice-secret")

        request = self.factory.get("/qr/{}".format(key))
        request.globally_authenticated = True
        url = _build_apprise_mobile_url(request, key)
        self.assertEqual(url, "apprise://:alice@testserver/{}".format(key))

    @override_settings(
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_USER="",
        APPRISE_PASSWORD="s3cr3t",
    )
    def test_admin_session_with_no_username_requires_password_only(self):
        """A password-only administrator login keeps its required-password marker."""
        request = self.factory.get("/qr/mykey")
        request.globally_authenticated = True
        url = _build_apprise_mobile_url(request, "mykey")
        self.assertEqual(url, "apprise://@testserver/mykey")

    @override_settings(
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_USER="admin",
        APPRISE_PASSWORD="p@ss:word/weird",
    )
    def test_admin_password_is_never_returned(self):
        """Even unusual administrator passwords remain absent from regular QR URLs."""
        request = self.factory.get("/qr/mykey")
        request.globally_authenticated = True
        url = _build_apprise_mobile_url(request, "mykey")
        self.assertEqual(url, "apprise://:admin@testserver/mykey")

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_USER="admin", APPRISE_PASSWORD="s3cr3t")
    def test_non_admin_session_includes_username_marker(self):
        """A saved username is included without its one-way-hashed password."""
        key = "qr_url_assigned_key"
        self.addCleanup(ConfigCache.clear_auth, key)
        ConfigCache.set_auth(key, "alice", "alice-secret")

        request = self.factory.get("/qr/{}".format(key))
        request.globally_authenticated = False
        url = _build_apprise_mobile_url(request, key)
        self.assertEqual(url, "apprise://:alice@testserver/{}".format(key))

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_USER="admin", APPRISE_PASSWORD="s3cr3t")
    def test_non_admin_session_requires_password_only(self):
        """A password-only configuration keeps its required-password marker."""
        key = "qr_url_password_only_key"
        self.addCleanup(ConfigCache.clear_auth, key)
        ConfigCache.set_auth(key, "", "shared-secret")

        request = self.factory.get("/qr/{}".format(key))
        request.globally_authenticated = False
        url = _build_apprise_mobile_url(request, key)
        self.assertEqual(url, "apprise://@testserver/{}".format(key))


class MobileQrViewTests(SimpleTestCase):
    """Test the /qr endpoints backing the Apprise Mobile QR code popup."""

    def tearDown(self):
        for key in ("qr_view_key", "qr_view_assigned_key"):
            ConfigCache.clear(key)
            ConfigCache.clear_auth(key)

    def test_missing_key_returns_bad_request(self):
        """A request with no config ID at all is rejected."""
        response = self.client.get("/qr/", headers={"accept": "application/json"})
        self.assertEqual(response.status_code, 400)

    def test_disabled_auth_returns_url_without_credentials(self):
        """With auth disabled, the endpoint is reachable and returns a bare URL."""
        response = self.client.get("/qr/qr_view_key", headers={"accept": "application/json"})
        self.assertEqual(response.status_code, 200)
        payload = loads(response.content)
        self.assertTrue(payload["url"].startswith("apprise://"))
        self.assertTrue(payload["url"].endswith("/qr_view_key"))

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_requires_authentication_when_enabled(self):
        """A protected key rejects a request with no credentials."""
        response = self.client.get("/qr/qr_view_key", headers={"accept": "application/json"})
        self.assertEqual(response.status_code, 401)

    @override_settings(
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_USER="master",
        APPRISE_PASSWORD="pass",
    )
    def test_admin_request_requires_saved_password(self):
        """The normal QR endpoint never returns the administrator password."""
        response = self.client.get(
            "/qr/qr_view_key",
            headers={"accept": "application/json", **_GOOD_MASTER},
        )
        self.assertEqual(response.status_code, 200)
        payload = loads(response.content)
        self.assertEqual(payload["url"], "apprise://:master@testserver/qr_view_key")
        self.assertTrue(payload["uses_admin_credentials"])

    @override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
    def test_assigned_key_request_includes_username_marker(self):
        """A configuration login pre-fills its username but still requires a password."""
        key = "qr_view_assigned_key"
        ConfigCache.set_auth(key, "alice", "alice-secret")
        response = self.client.get(
            "/qr/{}".format(key),
            headers={"accept": "application/json", "authorization": _basic("alice", "alice-secret")},
        )
        self.assertEqual(response.status_code, 200)
        payload = loads(response.content)
        self.assertEqual(payload["url"], "apprise://:alice@testserver/{}".format(key))
        self.assertFalse(payload["uses_admin_credentials"])

    @override_settings(
        APPRISE_AUTH_REQUIRED=True,
        APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN,
        APPRISE_USER="master",
        APPRISE_PASSWORD="pass",
    )
    def test_admin_request_uses_assigned_config_login(self):
        """The administrator receives the configuration's assigned login."""
        key = "qr_view_assigned_key"
        ConfigCache.set_auth(key, "alice", "alice-secret")
        response = self.client.get(
            "/qr/{}".format(key),
            headers={"accept": "application/json", **_GOOD_MASTER},
        )
        self.assertEqual(response.status_code, 200)
        payload = loads(response.content)
        self.assertEqual(payload["url"], "apprise://:alice@testserver/{}".format(key))
        self.assertFalse(payload["uses_admin_credentials"])

    def test_current_alias_without_key_returns_bad_request(self):
        """The cookie-based alias fails cleanly with no remembered configuration."""
        response = self.client.get("/qr/@", headers={"accept": "application/json"})
        self.assertEqual(response.status_code, 400)


def _web_cookie_client(mode, username, key=None):
    """Return a client with a signed browser login cookie."""
    request = RequestFactory().get("/", HTTP_HOST="testserver")
    response = HttpResponse()
    Authentication.set_web_cookie(response, request, mode, username, key)
    client = Client()
    client.cookies[Authentication.WEB_COOKIE] = response.cookies[Authentication.WEB_COOKIE].value
    return client


# Catch invalid JavaScript from embedded SVGs or template tags in comments.
@override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN)
class AuthPageQrButtonRenderingTests(SimpleTestCase):
    """Test that the Auth page keeps rendering valid HTML and JavaScript."""

    def tearDown(self):
        for key in ("qr_render_admin_key", "qr_render_assigned_key"):
            ConfigCache.clear(key)
            ConfigCache.clear_auth(key)

    def _script_blocks(self, html):
        return re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, re.S)

    def test_auth_page_renders_for_an_administrator(self):
        """The real editor (not a template error) renders for an admin."""
        client = _web_cookie_client(Authentication.ROLE_ADMIN, "master")
        response = client.get("/auth/qr_render_admin_key", headers={"accept": "text/html"})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('id="auth-form"', html)
        self.assertIn("auth-save-qr", html)

    def test_auth_page_renders_for_a_configuration_user(self):
        """The real editor also renders for a configuration's own login."""
        key = "qr_render_assigned_key"
        ConfigCache.set_auth(key, "alice", "alice-secret")
        client = _web_cookie_client(Authentication.ROLE_USER, "alice", key)
        response = client.get("/auth/{}".format(key), headers={"accept": "text/html"})
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="auth-form"', response.content.decode())

    def test_config_page_renders_with_the_qr_icon(self):
        """The configuration page includes both QR entry points."""
        client = _web_cookie_client(Authentication.ROLE_ADMIN, "master")
        response = client.get("/cfg/qr_render_admin_key", headers={"accept": "text/html"})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("data-config-id-qr", html)
        self.assertIn("data-mobile-setup-card", html)
        self.assertIn("overview-mobile-qr is-concealed", html)
        self.assertIn("Click here to reveal QR Code", html)
        self.assertNotIn("data-mobile-qr-toggle", html)
        self.assertNotIn("Mobile Setup QR Code", html)
        self.assertIn("data-mobile-beta-open", html)

    def test_config_page_qr_script_is_valid_javascript(self):
        """The embedded QR card initialization remains valid JavaScript."""
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not available to check JavaScript syntax")

        client = _web_cookie_client(Authentication.ROLE_ADMIN, "master")
        response = client.get("/cfg/qr_render_admin_key", headers={"accept": "text/html"})
        scripts = [block for block in self._script_blocks(response.content.decode()) if "initOverviewMobileQr" in block]
        self.assertEqual(len(scripts), 2)

        for script in scripts:
            result = subprocess.run(
                [node, "--check"],
                input=script,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_auth_page_script_is_syntactically_valid_javascript(self):
        """Ensure the rendered Save handler is valid JavaScript."""
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not available to check JavaScript syntax")

        client = _web_cookie_client(Authentication.ROLE_ADMIN, "master")
        response = client.get("/auth/qr_render_admin_key", headers={"accept": "text/html"})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()

        scripts = [block for block in self._script_blocks(html) if "authForm" in block]
        self.assertEqual(len(scripts), 1, "Expected exactly one script block defining authForm")

        with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as handle:
            handle.write(scripts[0])
            script_path = handle.name

        try:
            result = subprocess.run(
                [node, "--check", script_path],
                capture_output=True,
                text=True,
                timeout=10,
            )
        finally:
            os.unlink(script_path)

        self.assertEqual(
            result.returncode, 0, "Auth page script has a JavaScript syntax error:\n{}".format(result.stderr)
        )

    def test_qr_icon_include_uses_a_template_literal(self):
        """Keep the multiline QR icon inside a JavaScript template literal."""
        client = _web_cookie_client(Authentication.ROLE_ADMIN, "master")
        response = client.get("/auth/qr_render_admin_key", headers={"accept": "text/html"})
        html = response.content.decode()

        button_open = '<button type="button" id="auth-save-qr" '
        self.assertIn(
            "`" + button_open,
            html,
            "The auth-save-qr button markup must be inside a backtick template literal, "
            "since its included SVG icon can span multiple lines",
        )
        self.assertNotIn(
            "'" + button_open,
            html,
            "The auth-save-qr button markup must not be inside a single-quoted JS string, "
            "since its included SVG icon spans multiple lines and would break it",
        )

    def test_qr_script_masks_visible_passwords(self):
        """Visible setup URLs conceal passwords and shorten Config IDs."""
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not available to check JavaScript behavior")

        script_path = os.path.join(settings.BASE_DIR, "static", "js", "apprise-qr.js")
        program = """
global.window = global;
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
const redact = global.AppriseQr.redactMobileUrl;
const actual = [
  redact('apprise://chris:pass@host:8080/apprise'),
  redact('apprises://secret@host/key'),
  redact('apprises://@host/key'),
  redact('apprises://:alice@host/key'),
  redact('apprises://host/key')
];
const expected = [
  'apprise://chris:*****@host:8080/a...e',
  'apprises://*****@host/k...y',
  'apprises://*****@host/k...y',
  'apprises://alice:*****@host/k...y',
  'apprises://host/k...y'
];
if (JSON.stringify(actual) !== JSON.stringify(expected)) process.exit(1);
"""
        result = subprocess.run(
            [node, "-e", program, script_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_qr_popup_can_explicitly_reveal_the_complete_url(self):
        """The visible URL starts concealed and offers an explicit eye toggle."""
        script_path = os.path.join(settings.BASE_DIR, "static", "js", "apprise-qr.js")
        with open(script_path, encoding="utf-8") as script_file:
            script = script_file.read()

        self.assertIn('class="btn-flat btn-small apprise-qr-visibility"', script)
        self.assertIn("reveal ? options.url : redactMobileUrl(options.url)", script)
        self.assertIn('aria-pressed="false"', script)

    def test_oversized_qr_payload_rejects_without_throwing(self):
        """An oversized payload rejects its promise so the popup can recover.

        The QR library throws when data exceeds its largest supported size;
        callers need a rejected promise they can handle instead.
        """
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not available to check JavaScript behavior")

        qrcode_path = os.path.join(settings.BASE_DIR, "static", "js", "qrcode.min.js")
        apprise_qr_path = os.path.join(settings.BASE_DIR, "static", "js", "apprise-qr.js")
        program = """
global.window = global;
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
eval(require('fs').readFileSync(process.argv[2], 'utf8'));

function makeFakeCanvas() {
  const ctx = {
    fillStyle: '',
    fillRect: function () {},
    beginPath: function () {},
    arc: function () {},
    fill: function () {},
    save: function () {},
    restore: function () {},
    drawImage: function () {}
  };
  return { width: 0, height: 0, getContext: function () { return ctx; } };
}

const hugePassword = 'x'.repeat(3000);
const oversizedUrl = 'apprises://admin:' + hugePassword + '@host/apprise/key';

global.AppriseQr.drawQrToCanvas(makeFakeCanvas(), oversizedUrl, {})
  .then(function () { process.exit(1); }, function () { return null; })
  .then(function () {
    // A normal, small payload must still resolve after the change.
    return global.AppriseQr.drawQrToCanvas(makeFakeCanvas(), 'apprise://host/key', {});
  })
  .then(function () { process.exit(0); }, function () { process.exit(1); });
"""
        result = subprocess.run(
            [node, "-e", program, qrcode_path, apprise_qr_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(
            result.returncode,
            0,
            "Expected the oversized payload to reject and the normal payload to still resolve:\n{}".format(
                result.stderr
            ),
        )


class AdminCredentialsWarningConsolidationTests(SimpleTestCase):
    """Keep the admin-login warning identical in every QR display.

    The translated text is declared once in base.html and reused by the popup
    and Overview card.
    """

    WARNING_TEXT = (
        "This Config ID has no assigned user. The QR code will use the "
        "administrator login, and Apprise Mobile will prompt for its password."
    )

    def test_source_templates_declare_the_warning_copy_exactly_once(self):
        """Guard against the copy being re-declared in a template again."""
        import glob

        template_dir = os.path.join(settings.BASE_DIR, "api", "templates")
        occurrences = []
        for path in glob.glob(os.path.join(template_dir, "**", "*.html"), recursive=True):
            with open(path, encoding="utf-8") as handle:
                if self.WARNING_TEXT in handle.read():
                    occurrences.append(path)

        self.assertEqual(
            occurrences,
            [os.path.join(template_dir, "base.html")],
            "The admin-credentials warning text must be declared in base.html only; found it in: {}".format(
                occurrences
            ),
        )

    def test_hidden_source_element_renders_on_every_page(self):
        """The shared data element is present regardless of which page loads it."""
        response = self.client.get("/cfg/warning_source_key", headers={"accept": "text/html"})
        html = response.content.decode()
        self.assertIn('id="apprise-mobile-admin-warning-copy"', html)
        self.assertIn('data-title="Administrator Credentials"', html)
        self.assertIn(self.WARNING_TEXT, html)

    def test_helper_functions_read_the_shared_source_correctly(self):
        """Exercise AppriseQr.usesAdminCredentials/adminCredentialsWarning for real."""
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not available to check JavaScript behavior")

        response = self.client.get("/cfg/warning_source_key", headers={"accept": "text/html"})
        html = response.content.decode()
        match = re.search(
            r'<span id="apprise-mobile-admin-warning-copy"[^>]*data-title="([^"]*)"[^>]*data-text="([^"]*)"',
            html,
        )
        self.assertIsNotNone(match, "Could not find the hidden warning-copy element in the rendered page")

        apprise_qr_path = os.path.join(settings.BASE_DIR, "static", "js", "apprise-qr.js")
        program = """
const fakeDataset = {{ title: {title!r}, text: {text!r} }};
const fakeDocument = {{
  getElementById: function (id) {{
    return id === 'apprise-mobile-admin-warning-copy' ? {{ dataset: fakeDataset }} : null;
  }}
}};
global.window = global;
global.document = fakeDocument;
eval(require('fs').readFileSync(process.argv[1], 'utf8'));

if (AppriseQr.usesAdminCredentials({{uses_admin_credentials: true}}) !== true) process.exit(1);
if (AppriseQr.usesAdminCredentials({{uses_admin_credentials: false}}) !== false) process.exit(1);
if (AppriseQr.usesAdminCredentials({{}}) !== false) process.exit(1);
if (AppriseQr.usesAdminCredentials(null) !== false) process.exit(1);

const warning = AppriseQr.adminCredentialsWarning();
if (warning.title !== {title!r}) process.exit(1);
if (warning.text !== {text!r}) process.exit(1);
process.exit(0);
""".format(title=match.group(1), text=match.group(2))

        result = subprocess.run(
            [node, "-e", program, apprise_qr_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_raw_comment_syntax_leaks_into_rendered_pages(self):
        """Django's short comment syntax must not leak into rendered pages."""
        # Create the cookie under this token so requests reach the tested page.
        with override_settings(APPRISE_AUTH_REQUIRED=True, APPRISE_BASIC_AUTH_TOKEN=_MASTER_TOKEN):
            admin_client = _web_cookie_client(Authentication.ROLE_ADMIN, "master")
            for path in ("/cfg/no_leaked_comment_key", "/auth/no_leaked_comment_key"):
                response = admin_client.get(path, headers={"accept": "text/html"})
                self.assertEqual(
                    response.status_code, 200, "Expected a real page render, not a redirect, at {}".format(path)
                )
                html = response.content.decode()
                self.assertNotIn("{#", html, "A raw Django comment leaked into the rendered page at {}".format(path))
                self.assertNotIn("#}", html, "A raw Django comment leaked into the rendered page at {}".format(path))
