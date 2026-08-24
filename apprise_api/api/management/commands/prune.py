# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
# All rights reserved.
#
# This code is licensed under the MIT License.
"""Prune persistent state and unused authentication locks together."""

from api.utils import ConfigCache
import apprise
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Run every routine disk cleanup used by the container."""

    help = "Prune expired persistent state and unused authentication locks"

    def handle(self, *args, **options):
        # Remove expired notification state using Apprise's storage policy.
        apprise.PersistentStore.disk_prune(
            path=settings.APPRISE_STORAGE_DIR,
            expires=settings.APPRISE_STORAGE_PRUNE_DAYS * 86400,
            action=True,
        )

        # Locks attached to configurations are never removed by this method.
        locks = ConfigCache.prune_unused_locks(settings.APPRISE_AUTH_PRUNE_SECONDS)
        self.stdout.write(self.style.SUCCESS(f"Successfully completed pruning ({locks} unused lock(s) removed)"))
