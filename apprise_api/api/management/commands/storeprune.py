# -*- coding: utf-8 -*-
#
# Copyright (C) 2023 Chris Caron <lead2gold@gmail.com>
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

from django.core.management.base import BaseCommand
from django.conf import settings
import apprise


class Command(BaseCommand):
    help = f"Prune all persistent content older then {settings.APPRISE_STORAGE_PRUNE_DAYS} days()"

    def add_arguments(self, parser):
        parser.add_argument("-d", "--days", type=int, default=settings.APPRISE_STORAGE_PRUNE_DAYS)

    def handle(self, *args, **options):
        # Persistent Storage cleanup
        apprise.PersistentStore.disk_prune(
            path=settings.APPRISE_STORAGE_DIR,
            expires=options["days"] * 86400, action=True,
        )
        self.stdout.write(
            self.style.SUCCESS('Successfully pruned persistent storeage (days: %d)' % options["days"])
        )
