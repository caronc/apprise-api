# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
# All rights reserved.
#
# This code is licensed under the MIT License.
"""Run the scheduler that sends reminders."""

import time

from api.scheduler import RECHECK, Scheduler
from api.utils import reminders_enabled
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """Send reminders until stopped."""

    help = "Send reminders when they are due"

    def handle(self, *args, **options):
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
