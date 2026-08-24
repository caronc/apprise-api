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
from pathlib import Path
import subprocess
from unittest.mock import patch

from django.core import management
from django.core.management.base import CommandError
from django.test import SimpleTestCase


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
