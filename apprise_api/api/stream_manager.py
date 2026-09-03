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
"""Manage how many live notification streams a worker process accepts."""

import threading


class StreamManager:
    """
    Limit running and queued live notification streams.

    - ``capacity`` limits streams actively sending notifications.
    - ``queue_size`` allows additional streams to remain open.

    Open streams remain admitted until their responses close.
    """

    def __init__(self, capacity, queue_size):
        """Create limits for running and waiting streams."""
        # At least one stream must be able to run.
        if capacity < 1:
            raise ValueError("capacity must be at least 1")

        # A zero-sized queue rejects streams as soon as every worker is busy.
        if queue_size < 0:
            raise ValueError("queue_size must not be negative")

        # Limit streams that are actively sending notifications.
        self._worker_semaphore = threading.BoundedSemaphore(capacity)

        # Bound all open streams, including active, waiting, and draining ones.
        self._admission_semaphore = threading.BoundedSemaphore(capacity + queue_size)

    def try_admit(self):
        """Reserve one admission slot without waiting.

        Return ``False`` when full. A successful caller must release the slot.
        """
        # Never make the request wait just to learn that the queue is full.
        return self._admission_semaphore.acquire(blocking=False)

    def release_admission(self):
        """Release one previously reserved admission slot."""
        # Return the total stream slot for the next request.
        self._admission_semaphore.release()

    def acquire_worker(self):
        """Wait for an active slot after successful admission."""
        # Waiting is safe because admission already limited the queue size.
        self._worker_semaphore.acquire()

    def release_worker(self):
        """Release one previously acquired active slot."""
        # Allow the next waiting stream to begin its notification work.
        self._worker_semaphore.release()
