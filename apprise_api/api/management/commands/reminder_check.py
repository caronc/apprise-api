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
"""Explain a reminder's copied diagnostics."""

from pathlib import Path
import sys
from typing import Any

from api.diagnostics import check_diagnostics
from django.core.management.base import BaseCommand, CommandError, CommandParser


class Command(BaseCommand):
    """Read the diagnostics copied from a reminder and say what they show."""

    help = (
        "Explain the diagnostics copied from a reminder: when it is next due, worked out again here, "
        "and anything that would stop it being sent"
    )

    def add_arguments(self, parser: CommandParser) -> None:
        """Take the file the diagnostics were saved in."""
        parser.add_argument(
            "source",
            nargs="?",
            default="-",
            help="A file holding the copied diagnostics, or - (the default) to paste them in",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Print the report."""
        source = options["source"]
        try:
            text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")

        except (OSError, UnicodeDecodeError) as e:
            raise CommandError(f"Could not read {source}: {e}") from None

        try:
            self.stdout.write("\n".join(check_diagnostics(text)))

        except ValueError as e:
            raise CommandError(str(e)) from None
