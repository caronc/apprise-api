# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
# All rights reserved.
#
# This code is licensed under the MIT License.
"""Prune persistent state, unused authentication locks and reminder files together."""

from api.utils import ConfigCache
import apprise
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """Run every routine disk cleanup used by the container."""

    help = "Prune expired persistent state, unused authentication locks and unused reminder files"

    def handle(self, *args, **options):
        """Prune expired notification data and unused access records."""
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
