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

"""The scheduler's wake-up signal, and files it cannot read for now."""

import signal
import time
from unittest.mock import patch

import pytest

from .. import scheduler as scheduler_module
from ..scheduler import Scheduler
from ..utils import AppriseConfigCache, AppriseStoreMode


@pytest.fixture
def store(tmpdir):
    """Point the scheduler at an empty configuration directory."""
    cache = AppriseConfigCache(str(tmpdir), mode=AppriseStoreMode.HASH)
    with patch.object(scheduler_module, "ConfigCache", cache):
        yield cache


@pytest.fixture
def restore_signals():
    """Put back the signal handlers the scheduler replaces."""
    saved = {signum: signal.getsignal(signum) for signum in (signal.SIGUSR1, signal.SIGTERM, signal.SIGINT)}
    yield
    for signum, handler in saved.items():
        signal.signal(signum, handler)


def test_a_wake_signal_before_the_lock_is_held_is_only_noted(store, restore_signals):
    """SIGUSR1 is handled before the scheduler records itself, so an early wake-up never ends it."""
    signal.signal(signal.SIGUSR1, signal.SIG_DFL)
    scheduler = Scheduler(workers=1, grace=60)
    seen = []

    def busy(*_args, **_kwargs):
        # Another scheduler runs, so this one gives up; the handler is in place already
        seen.append(signal.getsignal(signal.SIGUSR1))
        return iter([False])

    with patch.object(store, "scheduler_lock", side_effect=lambda wait: _yielding(busy())):
        assert scheduler.run() is False

    assert seen == [scheduler._on_signal]
    # SIGTERM still ends the process while it waits for the lock
    assert signal.getsignal(signal.SIGTERM) is not scheduler._on_signal


def _yielding(values):
    """Return a context manager that yields the first of ``values``."""

    class Holder:
        def __enter__(self):
            return next(values)

        def __exit__(self, *_exc):
            return False

    return Holder()


def test_a_file_unreadable_under_the_lock_is_tried_again(store):
    """A reminder file that cannot be read when claimed is tried again later, not forgotten."""
    scheduler = Scheduler(workers=1, grace=60)
    reminder_file = store.reminder_path("key")
    with patch.object(store, "read_reminders", return_value=None):
        assert scheduler.claim(reminder_file, int(time.time())) is None

    # A file with nothing in it has nothing to send
    with patch.object(store, "read_reminders", return_value=[]):
        assert scheduler.claim(reminder_file, int(time.time())) == []
