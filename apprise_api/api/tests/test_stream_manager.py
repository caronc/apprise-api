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
import threading

from django.test import SimpleTestCase

from ..stream_manager import StreamManager

# Maximum wait for threaded tests; completed events return immediately.
_WAIT_TIMEOUT = 10


class StreamManagerTests(SimpleTestCase):
    """Validate live-stream admission and active limits."""

    def test_capacity_must_be_at_least_one(self):
        """A capacity below one is rejected."""
        with self.assertRaisesRegex(ValueError, "capacity"):
            StreamManager(capacity=0, queue_size=0)

        # A valid capacity of exactly one is accepted.
        StreamManager(capacity=1, queue_size=0)

    def test_queue_size_must_not_be_negative(self):
        """A negative queue size is rejected."""
        with self.assertRaisesRegex(ValueError, "queue_size"):
            StreamManager(capacity=1, queue_size=-1)

        # A queue size of exactly zero is accepted.
        StreamManager(capacity=1, queue_size=0)

    def test_admits_to_total_limit(self):
        """Every slot up to the total ceiling can be admitted."""
        manager = StreamManager(capacity=2, queue_size=3)

        # Fill every active and waiting slot.
        for _ in range(5):
            assert manager.try_admit() is True

    def test_rejects_over_total_limit(self):
        """A stream beyond the total ceiling is rejected immediately."""
        manager = StreamManager(capacity=1, queue_size=1)

        # The third stream is beyond one active and one waiting slot.
        assert manager.try_admit() is True
        assert manager.try_admit() is True
        assert manager.try_admit() is False

    def test_release_frees_admission(self):
        """Releasing a reserved slot allows a new stream to be admitted."""
        manager = StreamManager(capacity=1, queue_size=0)

        assert manager.try_admit() is True
        assert manager.try_admit() is False

        # Releasing the first stream makes room for another.
        manager.release_admission()
        assert manager.try_admit() is True

    def test_active_slot_waits_for_release(self):
        """A second active request waits for the first to release its slot."""
        manager = StreamManager(capacity=1, queue_size=1)
        manager.acquire_worker()

        second_acquired = threading.Event()

        def acquire_second():
            # This call waits until the test releases the first slot.
            manager.acquire_worker()
            second_acquired.set()

        worker = threading.Thread(target=acquire_second, daemon=True)
        worker.start()
        try:
            # The first slot is still held, so the second request must wait.
            assert not second_acquired.wait(0.1)

            manager.release_worker()
            assert second_acquired.wait(_WAIT_TIMEOUT)
        finally:
            worker.join(_WAIT_TIMEOUT)

    def test_limits_are_independent(self):
        """More streams can be admitted and queued than can run at once."""
        manager = StreamManager(capacity=1, queue_size=2)

        # All three admission slots are available even though only one
        # stream may actively run notify() at a time.
        assert manager.try_admit() is True
        assert manager.try_admit() is True
        assert manager.try_admit() is True
        assert manager.try_admit() is False

        manager.acquire_worker()

        second_acquired = threading.Event()

        def acquire_second():
            # The waiting limit can be larger than the active limit.
            manager.acquire_worker()
            second_acquired.set()

        worker = threading.Thread(target=acquire_second, daemon=True)
        worker.start()
        try:
            assert not second_acquired.wait(0.1)

            manager.release_worker()
            assert second_acquired.wait(_WAIT_TIMEOUT)
        finally:
            worker.join(_WAIT_TIMEOUT)
            manager.release_worker()

    def test_concurrent_admission_stays_bounded(self):
        """Many threads racing try_admit() admit exactly the total ceiling."""
        capacity, queue_size = 3, 7
        ceiling = capacity + queue_size
        manager = StreamManager(capacity=capacity, queue_size=queue_size)

        attempts = ceiling * 20
        admitted = []
        admitted_lock = threading.Lock()
        start = threading.Event()

        def attempt():
            # Release every thread together to exercise admission races.
            start.wait(_WAIT_TIMEOUT)
            if manager.try_admit():
                with admitted_lock:
                    admitted.append(True)

        workers = [threading.Thread(target=attempt, daemon=True) for _ in range(attempts)]
        for worker in workers:
            worker.start()
        start.set()
        for worker in workers:
            worker.join(_WAIT_TIMEOUT)

        # The race must admit exactly the configured limit.
        assert len(admitted) == ceiling

    def test_concurrent_release_stays_balanced(self):
        """Interleaved concurrent admits and releases never exceed the ceiling."""
        capacity, queue_size = 2, 2
        ceiling = capacity + queue_size
        manager = StreamManager(capacity=capacity, queue_size=queue_size)
        stop = threading.Event()
        errors = []

        def churn():
            try:
                while not stop.is_set():
                    if manager.try_admit():
                        manager.release_admission()
            except Exception as error:  # pragma: no cover - failure path only
                errors.append(error)

        workers = [threading.Thread(target=churn, daemon=True) for _ in range(8)]
        for worker in workers:
            worker.start()
        stop.wait(0.2)
        stop.set()
        for worker in workers:
            worker.join(_WAIT_TIMEOUT)

        assert errors == []
        # Every slot was released, so the limit can be filled again.
        for _ in range(ceiling):
            assert manager.try_admit() is True
        assert manager.try_admit() is False
