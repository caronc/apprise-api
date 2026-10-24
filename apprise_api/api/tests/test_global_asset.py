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
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from .test_settings import _load_settings


class GlobalAssetTests(SimpleTestCase):
    def test_settings(self):
        assert _load_settings().APPRISE_ASSET == {}
        value = {"app_id": "Operations", "async_mode": False}
        assert value == _load_settings({"APPRISE_ASSET": json.dumps(value)}).APPRISE_ASSET
        for invalid in ("[", "[]", "null", '{"async_mode":"false"}', '{"_recursion":2}', '{"allow_templates":true}'):
            with self.subTest(invalid=invalid), self.assertRaises(ImproperlyConfigured):
                _load_settings({"APPRISE_ASSET": invalid})

    @override_settings(APPRISE_ASSET={"app_id": "Operations", "interpret_escapes": True})
    def test_stateful_and_stateless_delivery(self):
        assets = []
        real_asset = apprise.AppriseAsset

        class CaptureAsset(real_asset):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                assets.append(self)

        key = "global-asset-test"
        self.client.post(f"/add/{key}", {"urls": "json://localhost"})
        for endpoint, payload in (
            ("/notify", {"urls": "json://localhost", "body": "message"}),
            (f"/notify/{key}", {"body": "message"}),
        ):
            with (
                self.subTest(endpoint=endpoint),
                mock.patch("apprise.AppriseAsset", CaptureAsset),
                mock.patch.object(apprise.Apprise, "notify", return_value=True),
            ):
                response = self.client.post(endpoint, data=json.dumps(payload), content_type="application/json")
                assert response.status_code == 200
                assert assets[-1].app_id == "Operations"
                assert assets[-1].interpret_escapes is True
        assert _load_settings().APPRISE_ASSET == {}
