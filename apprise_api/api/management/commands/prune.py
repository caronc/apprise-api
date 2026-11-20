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
"""Prune persistent state, unused authentication locks and reminder files together."""

from typing import Any

from api.utils import ConfigCache
import apprise
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """Run every routine disk cleanup used by the container."""

    help = "Prune expired persistent state, unused authentication locks and unused reminder files"

    def handle(self, *args: Any, **options: Any) -> None:
        """Prune expired notification data, unused access records, finished reminders and their files."""
        if settings.APPRISE_AUTH_PRUNE_SECONDS < 0:
            # A negative age would make every unused lock eligible.
            raise CommandError("APPRISE_AUTH_PRUNE_SECONDS must not be negative")

        # Remove expired notification state using Apprise's storage policy.
        apprise.PersistentStore.disk_prune(
            path=settings.APPRISE_STORAGE_DIR,
            expires=settings.APPRISE_STORAGE_PRUNE_DAYS * 86400,
            action=True,
        )

        # Locks attached to configurations are never removed by this method.
        locks = ConfigCache.prune_unused_locks(settings.APPRISE_AUTH_PRUNE_SECONDS)

        # Finish any configuration removal a crash left part way, so the
        # reminders it set aside are not stranded.
        ConfigCache.recover_removed_reminders()

        # Reminders with nothing left to send. Done first, so the attachments
        # only they used are removed below.
        finished = ConfigCache.prune_finished_reminders(settings.APPRISE_REMINDER_PRUNE_DAYS * 86400)

        # Attachments left behind by deleted reminders, or by an upload whose
        # reminder was never saved.
        files = ConfigCache.prune_reminder_files()
        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully completed pruning ({locks} unused lock(s), {finished} finished reminder(s) and "
                f"{files} unused reminder file(s) removed)"
            )
        )
