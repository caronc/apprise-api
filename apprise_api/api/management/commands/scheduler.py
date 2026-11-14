# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
# All rights reserved.
#
# This code is licensed under the MIT License.
"""Run the scheduler that sends reminders."""

from api.scheduler import Scheduler
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

        # Runs until stopped; False means another scheduler holds the lock.
        if not Scheduler(settings.APPRISE_REMINDER_WORKERS, settings.APPRISE_REMINDER_GRACE).run():
            raise CommandError("Another scheduler is already running")
