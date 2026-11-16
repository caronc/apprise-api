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
"""Run the scheduler that sends reminders."""

import time
from typing import Any

from api.scheduler import RECHECK, Scheduler
from api.utils import reminders_enabled
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """Send reminders until stopped."""

    help = "Send reminders when they are due"

    def handle(self, *args: Any, **options: Any) -> None:
        """Run the scheduler, or exit cleanly when it is turned off."""
        if not reminders_enabled():
            # A clean exit tells the process manager to leave it stopped.
            self.stdout.write("The scheduler is disabled")
            return

        # Runs until stopped. While another instance sharing this directory
        # runs its scheduler, this one waits quietly to take over.
        if not Scheduler(settings.APPRISE_REMINDER_WORKERS, settings.APPRISE_REMINDER_GRACE).run(wait=True):
            # The lock file could not be used, such as in a read-only folder.
            # Waiting first keeps the process manager from restarting it
            # over and over without a pause.
            time.sleep(RECHECK)
            raise CommandError("Could not take the scheduler lock")
