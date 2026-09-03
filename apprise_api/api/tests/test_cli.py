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

import io
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

from django.core import management
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings


class CommandTests(SimpleTestCase):
    def test_command_style(self):
        out = io.StringIO()
        management.call_command("storeprune", days=40, stdout=out)

    def test_authprune_command_style(self):
        out = io.StringIO()
        management.call_command("authprune", seconds=2592000, stdout=out)
        self.assertIn("pruned", out.getvalue())

    def test_authprune_rejects_negative_seconds(self):
        """Reject negative ages that would make every lock eligible."""
        with self.assertRaises(CommandError):
            management.call_command("authprune", seconds=-1, stdout=io.StringIO())

    def test_combined_prune_runs_both_cleanups(self):
        """The container command prunes state and unused locks together."""
        out = io.StringIO()
        with (
            patch("api.management.commands.prune.apprise.PersistentStore.disk_prune") as disk_prune,
            patch("api.management.commands.prune.ConfigCache.prune_unused_locks", return_value=2) as lock_prune,
        ):
            management.call_command("prune", stdout=out)

        disk_prune.assert_called_once()
        lock_prune.assert_called_once()
        self.assertIn("2 unused lock(s)", out.getvalue())

    @override_settings(APPRISE_AUTH_PRUNE_SECONDS=-1)
    def test_combined_prune_rejects_negative_lock_age(self):
        """The scheduled command never turns a negative age into prune-all."""
        with self.assertRaises(CommandError):
            management.call_command("prune", stdout=io.StringIO())

    def test_container_pruner_has_timeout_and_supervisor_watchdog(self):
        """The scheduler is syntax checked, bounded, and restarted if it exits."""
        package_dir = Path(__file__).resolve().parents[2]
        loop = package_dir / "etc" / "pruner-loop.sh"
        supervisor = (package_dir / "etc" / "supervisord.conf").read_text()
        loop_content = loop.read_text()

        subprocess.run(["bash", "-n", loop], check=True)
        self.assertIn('if [ "$timeout_seconds" -ge "$interval" ]', loop_content)
        self.assertIn("--kill-after=", loop_content)
        self.assertIn("autorestart=true", supervisor)
        self.assertIn("stopasgroup=true", supervisor)
        self.assertIn("killasgroup=true", supervisor)

    def test_container_selects_nginx_without_rewriting_packaged_files(self):
        """Normal and strict mode render Supervisor configuration in /tmp."""
        package_dir = Path(__file__).resolve().parents[2]
        startup = package_dir / "supervisord-startup"
        content = startup.read_text()

        subprocess.run(["bash", "-n", startup], check=True)
        self.assertIn('RUNTIME_SUPERVISORD_CONF="/tmp/apprise/supervisord.conf"', content)
        self.assertIn("nginx-strict\\.conf|${NGINX_CONF}|g", content)
        self.assertIn("nginx\\.conf|${NGINX_CONF}|g", content)
        self.assertIn('"$SUPERVISORD_CONF" > "$RUNTIME_SUPERVISORD_CONF"', content)
        self.assertIn('SUPERVISORD_CONF="$RUNTIME_SUPERVISORD_CONF"', content)
        self.assertNotIn('sed -i -e "s/nginx\\.conf/nginx-strict.conf/g"', content)

    def test_container_stream_timeout(self):
        """The container accepts safe stream timeouts and rejects invalid ones."""
        package_dir = Path(__file__).resolve().parents[2]
        startup = package_dir / "supervisord-startup"
        content = startup.read_text()

        # Run only the timeout function so the full container startup is not invoked.
        function_start = content.index("apply_connection_timeout_to_nginx() {")
        function_end = content.index("\n}\n", function_start) + 3
        function = content[function_start:function_end]

        with tempfile.TemporaryDirectory() as temp_dir:
            timeout_conf = Path(temp_dir) / "nginx-timeout.conf"
            function = function.replace(
                'local timeout_conf="/tmp/apprise/nginx-connection-timeout.conf"',
                f'local timeout_conf="{timeout_conf}"',
            )
            script = f"{function}\napply_connection_timeout_to_nginx"

            # Check the documented boundaries and the default value.
            for value in ("30", "600", "3600"):
                with self.subTest(value=value):
                    result = subprocess.run(
                        ["bash", "-c", script],
                        check=False,
                        capture_output=True,
                        text=True,
                        env={**os.environ, "APPRISE_CONNECTION_TIMEOUT": value},
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(f"proxy_read_timeout {value}s", timeout_conf.read_text())
                    self.assertIn(f"proxy_send_timeout {value}s", timeout_conf.read_text())

            # Reject malformed values and values outside the supported range.
            for value in ("many", "29", "3601"):
                with self.subTest(value=value):
                    result = subprocess.run(
                        ["bash", "-c", script],
                        check=False,
                        capture_output=True,
                        text=True,
                        env={**os.environ, "APPRISE_CONNECTION_TIMEOUT": value},
                    )
                    self.assertNotEqual(result.returncode, 0)

        # Both nginx modes apply the generated timeout to notification routes.
        for nginx_name in ("nginx.conf", "nginx-strict.conf"):
            nginx = (package_dir / "etc" / nginx_name).read_text()
            self.assertEqual(
                nginx.count("include /tmp/apprise/nginx-connection-timeout.conf;"),
                2,
            )
            self.assertEqual(
                nginx.count("proxy_next_upstream error timeout http_502 http_504;"),
                2,
            )
