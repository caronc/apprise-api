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
import json
from unittest import mock

import apprise
from django.test import SimpleTestCase, override_settings

from ..forms import NotifyForm

# Grant access to our Notification Manager Singleton
N_MGR = apprise.manager_plugins.NotificationManager()


class NotifyWithDefaultFormatTests(SimpleTestCase):
    """
    Test that APPRISE_DEFAULT_FORMAT is wired correctly to AppriseAsset,
    same as APPRISE_HTTP_REDIRECTS (see test_redirects.py).

    APPRISE_DEFAULT_FORMAT only ever applies when the `format` field is
    missing from the payload entirely. That can only happen through a
    JSON payload -- the web form always submits an explicit `format`
    value (even the default "IGNORE" choice is an explicit selection),
    so the server default never applies there. A `format` field that is
    present but blank or null is also an explicit choice -- it forces
    pass-through, overriding any configured server default.
    """

    def _make_asset_spy(self):
        """Return a mock that patches AppriseAsset and captures its kwargs."""
        real_asset_cls = apprise.AppriseAsset

        captured = {}

        class SpyAsset(real_asset_cls):
            def __init__(self, **kwargs):
                # Capture what was passed before forwarding
                captured.update(kwargs)
                super().__init__(**kwargs)

        return SpyAsset, captured

    def test_stateful_form_submission_never_applies_default_format(self):
        """
        The web form always submits an explicit `format` value, so
        APPRISE_DEFAULT_FORMAT never applies via /notify/{key} when
        posted as a form -- even when a server default is configured.
        """
        key = "test_default_format_stateful_form"

        spy_cls, captured = self._make_asset_spy()
        with mock.patch("apprise.AppriseAsset", spy_cls):
            self.client.post("/add/{}".format(key), {"urls": "json://user:pass@localhost"})

        form_data = {"title": "Test", "body": "Body"}
        form = NotifyForm(data=form_data)
        assert form.is_valid()
        del form.cleaned_data["attachment"]
        if not form.cleaned_data.get("format") and "format" in form.cleaned_data:
            # format is optional; None cannot be encoded as POST data
            del form.cleaned_data["format"]

        # No server default configured: pass-through, as expected
        spy_cls, captured = self._make_asset_spy()
        with mock.patch("apprise.AppriseAsset", spy_cls):
            self.client.post("/notify/{}".format(key), form.cleaned_data)
        assert captured.get("body_format") is None

        # A server default is configured, but the form's explicit (blank)
        # selection still forces pass-through -- the default is ignored
        spy_cls, captured = self._make_asset_spy()
        with (
            mock.patch("apprise.AppriseAsset", spy_cls),
            override_settings(APPRISE_DEFAULT_FORMAT="markdown"),
        ):
            self.client.post("/notify/{}".format(key), form.cleaned_data)
        assert captured.get("body_format") is None

        # An explicitly declared format still always wins
        declared_form_data = {**form_data, "format": "html"}
        declared_form = NotifyForm(data=declared_form_data)
        assert declared_form.is_valid()
        del declared_form.cleaned_data["attachment"]

        spy_cls, captured = self._make_asset_spy()
        with (
            mock.patch("apprise.AppriseAsset", spy_cls),
            override_settings(APPRISE_DEFAULT_FORMAT="markdown"),
        ):
            self.client.post("/notify/{}".format(key), declared_form.cleaned_data)
        assert captured.get("body_format") == "html"

    def test_stateless_json_absent_format_uses_default(self):
        """
        /notify (stateless, JSON) applies APPRISE_DEFAULT_FORMAT only
        when the `format` key is missing from the payload entirely.
        """
        json_data = {
            "urls": "json://user:pass@localhost",
            "title": "Test",
            "body": "Body",
        }

        # No server default configured: pass-through
        spy_cls, captured = self._make_asset_spy()
        with mock.patch("apprise.AppriseAsset", spy_cls):
            self.client.post(
                "/notify",
                data=json.dumps(json_data),
                content_type="application/json",
            )
        assert captured.get("body_format") is None

        # A configured server default is used since "format" is absent
        spy_cls, captured = self._make_asset_spy()
        with (
            mock.patch("apprise.AppriseAsset", spy_cls),
            override_settings(APPRISE_DEFAULT_FORMAT="markdown"),
        ):
            self.client.post(
                "/notify",
                data=json.dumps(json_data),
                content_type="application/json",
            )
        assert captured.get("body_format") == "markdown"

    def test_stateless_json_blank_or_null_format_forces_passthrough(self):
        """
        A `format` field that is present but blank, whitespace-only, or
        null is an explicit caller choice -- it forces pass-through even
        when a server default is configured.
        """
        base = {
            "urls": "json://user:pass@localhost",
            "title": "Test",
            "body": "Body",
        }

        for explicit_blank in (None, "", "   "):
            spy_cls, captured = self._make_asset_spy()
            with (
                mock.patch("apprise.AppriseAsset", spy_cls),
                override_settings(APPRISE_DEFAULT_FORMAT="markdown"),
            ):
                self.client.post(
                    "/notify",
                    data=json.dumps({**base, "format": explicit_blank}),
                    content_type="application/json",
                )
            assert captured.get("body_format") is None, repr(explicit_blank)

    def test_stateless_json_declared_format_wins_over_default(self):
        """
        An explicitly declared, non-blank format always wins over a
        configured server default.
        """
        json_data = {
            "urls": "json://user:pass@localhost",
            "title": "Test",
            "body": "Body",
            "format": "html",
        }

        spy_cls, captured = self._make_asset_spy()
        with (
            mock.patch("apprise.AppriseAsset", spy_cls),
            override_settings(APPRISE_DEFAULT_FORMAT="markdown"),
        ):
            self.client.post(
                "/notify",
                data=json.dumps(json_data),
                content_type="application/json",
            )
        assert captured.get("body_format") == "html"

    def test_stateful_json_absent_format_uses_default(self):
        """
        /notify/{key} (stateful, JSON) applies APPRISE_DEFAULT_FORMAT
        only when the `format` key is missing from the payload entirely
        -- mirrors the stateless behavior since the view implements the
        same logic independently.
        """
        key = "test_default_format_stateful_json"

        spy_cls, captured = self._make_asset_spy()
        with mock.patch("apprise.AppriseAsset", spy_cls):
            self.client.post("/add/{}".format(key), {"urls": "json://user:pass@localhost"})

        json_data = {"title": "Test", "body": "Body"}

        # A configured server default is used since "format" is absent
        spy_cls, captured = self._make_asset_spy()
        with (
            mock.patch("apprise.AppriseAsset", spy_cls),
            override_settings(APPRISE_DEFAULT_FORMAT="markdown"),
        ):
            self.client.post(
                "/notify/{}".format(key),
                data=json.dumps(json_data),
                content_type="application/json",
            )
        assert captured.get("body_format") == "markdown"

        # An explicit null forces pass-through, overriding the default
        spy_cls, captured = self._make_asset_spy()
        with (
            mock.patch("apprise.AppriseAsset", spy_cls),
            override_settings(APPRISE_DEFAULT_FORMAT="markdown"),
        ):
            self.client.post(
                "/notify/{}".format(key),
                data=json.dumps({**json_data, "format": None}),
                content_type="application/json",
            )
        assert captured.get("body_format") is None
