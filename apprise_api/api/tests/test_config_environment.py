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

from api import views
from api.utils import AppriseConfigCache, AppriseStoreMode
import apprise
import pytest


@pytest.fixture
def local_config(tmp_path, monkeypatch, settings):
    settings.APPRISE_CONFIG_LOCK = True
    cache = AppriseConfigCache(str(tmp_path), mode=AppriseStoreMode.SIMPLE)
    monkeypatch.setattr(views, "ConfigCache", cache)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_token")
    return cache


@pytest.mark.parametrize(
    ("extension", "content"),
    [
        ("cfg", "notifications = tgram://123456789:${TELEGRAM_BOT_TOKEN}/-1001234567890"),
        ("yml", "urls:\n  - tgram://123456789:${TELEGRAM_BOT_TOKEN}/-1001234567890:\n      tag: notifications\n"),
    ],
)
def test_notify_mounted_config(client, local_config, tmp_path, monkeypatch, extension, content):
    """A mounted template delivers through the API without rewriting credentials to disk."""
    path = tmp_path / f"apprise.{extension}"
    path.write_text(content, encoding="utf-8")

    with mock.patch("apprise.plugins.telegram.requests.post") as post:
        post.return_value.status_code = 200
        for token in ("test_token", "rotated_token"):
            monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
            response = client.post("/notify/apprise", {"body": "test notification", "tag": "notifications"})
            assert response.status_code == 200
            post.assert_called_once()
            assert post.call_args.args[0] == f"https://api.telegram.org/bot123456789:{token}/sendMessage"
            payload = json.loads(post.call_args.kwargs["data"])
            assert str(payload["chat_id"]) == "-1001234567890"
            assert payload["text"] == "test notification"
            assert path.read_text(encoding="utf-8") == content
            post.reset_mock()

        response = client.post("/notify/apprise", {"body": "test notification", "tag": "other"})
        assert response.status_code == 424
        post.assert_not_called()


def test_missing_environment_prevents_delivery(client, local_config, tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    (tmp_path / "apprise.cfg").write_text(
        "tgram://123456789:literal_token/-1001234567890\ntgram://123456789:${TELEGRAM_BOT_TOKEN}/-1001234567890\n",
        encoding="utf-8",
    )
    with mock.patch("apprise.plugins.telegram.requests.post") as post:
        assert client.post("/notify/apprise", {"body": "test"}).status_code == 424
        post.assert_not_called()


def test_locked_config_endpoints(client, local_config, tmp_path):
    content = "notifications = tgram://123456789:${TELEGRAM_BOT_TOKEN}/-1001234567890"
    path = tmp_path / "apprise.cfg"
    path.write_text(content, encoding="utf-8")
    for endpoint in ("add", "del", "get", "cfg"):
        assert client.post(f"/{endpoint}/apprise", {"config": "json://example.com"}).status_code == 403

    response = client.get("/json/urls/apprise?privacy=no")
    assert response.status_code == 200
    assert response.json()["tags"] == ["notifications"]
    assert len(response.json()["urls"]) == 1
    assert b"test_token" not in response.content
    assert b"TELEGRAM_BOT_TOKEN" not in response.content
    assert path.read_text(encoding="utf-8") == content


@pytest.mark.parametrize(("mode", "locked"), [("simple", False), ("hash", False), ("hash", True)])
def test_other_storage_remains_literal(client, tmp_path, monkeypatch, settings, mode, locked):
    settings.APPRISE_CONFIG_LOCK = locked
    monkeypatch.setenv("APPRISE_TEST_PATH", "resolved")
    cache = AppriseConfigCache(str(tmp_path), mode=mode)
    monkeypatch.setattr(views, "ConfigCache", cache)
    content = "json://example.com/${APPRISE_TEST_PATH}"
    if not locked:
        response = client.post("/add/apprise", {"config": content, "format": "text"})
        assert response.status_code == 200
    else:
        assert cache.put("apprise", content, "text")

    with mock.patch("apprise.plugins.custom_json.requests.request") as post:
        post.return_value.status_code = 200
        assert client.post("/notify/apprise", {"body": "test"}).status_code == 200
        post.assert_called_once()
        assert "resolved" not in post.call_args.args[1]
        assert "APPRISE_TEST_PATH" in post.call_args.args[1]


def test_stateless_urls_remain_literal(client, local_config, monkeypatch):
    monkeypatch.setenv("APPRISE_TEST_PATH", "resolved")
    with mock.patch("apprise.plugins.custom_json.requests.request") as post:
        post.return_value.status_code = 200
        response = client.post("/notify", {"urls": "json://example.com/${APPRISE_TEST_PATH}", "body": "test"})
        assert response.status_code == 200
        post.assert_called_once()
        assert "resolved" not in post.call_args.args[1]
        assert "APPRISE_TEST_PATH" in post.call_args.args[1]


def test_large_local_config_preserves_asset(local_config, tmp_path):
    content = "# comment\n" * 20000 + "json://example.com"
    (tmp_path / "apprise.cfg").write_text(content, encoding="utf-8")
    asset = apprise.AppriseAsset(app_id="Mounted config test")
    config = local_config.load("apprise", content, "text", asset=asset)
    servers = config.servers()
    assert len(servers) == 1
    assert servers[0].asset is asset
