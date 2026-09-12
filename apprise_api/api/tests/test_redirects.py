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


class NotifyWithRedirectTests(SimpleTestCase):
    """
    Test that APPRISE_HTTP_REDIRECTS is wired correctly to AppriseAsset.

    The apprise package installed in the test environment may pre-date the
    http_redirects attribute, so these tests mock AppriseAsset directly to
    verify that views.py passes the correct value rather than running the
    full notification stack.
    """

    def _make_asset_spy(self):
        """Return a mock that patches AppriseAsset and captures its kwargs."""
        real_asset_cls = apprise.AppriseAsset

        captured = {}

        class SpyAsset(real_asset_cls):
            def __init__(self, **kwargs):
                # Capture what was passed before forwarding
                captured.update(kwargs)
                # Remove our new kwarg if the installed apprise doesn't
                # know about it yet so the real __init__ doesn't raise
                kwargs.pop("http_redirects", None)
                super().__init__(**kwargs)

        return SpyAsset, captured

    def test_stateful_notify_passes_redirects_to_asset(self):
        """
        Stateful /notify/{key} passes APPRISE_HTTP_REDIRECTS to AppriseAsset.
        """
        spy_cls, captured = self._make_asset_spy()

        key = "test_redirect_stateful"

        # Seed stateful config
        with mock.patch("apprise.AppriseAsset", spy_cls):
            self.client.post("/add/{}".format(key), {"urls": "json://user:pass@localhost"})

        form_data = {"title": "Test", "body": "Body"}
        form = NotifyForm(data=form_data)
        assert form.is_valid()
        del form.cleaned_data["attachment"]
        if not form.cleaned_data.get("format") and "format" in form.cleaned_data:
            # format is optional; None cannot be encoded as POST data
            del form.cleaned_data["format"]

        # Redirects disabled (default)
        spy_cls, captured = self._make_asset_spy()
        with mock.patch("apprise.AppriseAsset", spy_cls), override_settings(APPRISE_HTTP_REDIRECTS=False):
            self.client.post("/notify/{}".format(key), form.cleaned_data)
        assert captured.get("http_redirects") is False

        # Redirects enabled
        spy_cls, captured = self._make_asset_spy()
        with mock.patch("apprise.AppriseAsset", spy_cls), override_settings(APPRISE_HTTP_REDIRECTS=True):
            self.client.post("/notify/{}".format(key), form.cleaned_data)
        assert captured.get("http_redirects") is True

    def test_stateless_notify_passes_redirects_to_asset(self):
        """
        Stateless /notify passes APPRISE_HTTP_REDIRECTS to AppriseAsset.
        """
        json_data = {
            "urls": "json://user:pass@localhost",
            "title": "Test",
            "body": "Body",
        }

        # Redirects disabled (default)
        spy_cls, captured = self._make_asset_spy()
        with mock.patch("apprise.AppriseAsset", spy_cls), override_settings(APPRISE_HTTP_REDIRECTS=False):
            self.client.post(
                "/notify",
                data=json.dumps(json_data),
                content_type="application/json",
            )
        assert captured.get("http_redirects") is False

        # Redirects enabled
        spy_cls, captured = self._make_asset_spy()
        with mock.patch("apprise.AppriseAsset", spy_cls), override_settings(APPRISE_HTTP_REDIRECTS=True):
            self.client.post(
                "/notify",
                data=json.dumps(json_data),
                content_type="application/json",
            )
        assert captured.get("http_redirects") is True
