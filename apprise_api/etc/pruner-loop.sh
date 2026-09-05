#!/bin/bash
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
#
# Runs the combined Django prune command under supervisord.
set -u

# The lock also prevents an accidental second loop from overlapping this one.
LOCK_FILE="/tmp/apprise/pruner.lock"

# Keep values within a range Bash can compare safely. The interval must leave
# at least one second for a shorter prune timeout.
_positive_int() {
   [[ "$1" =~ ^[1-9][0-9]{0,8}$ ]]
}

raw_interval="${APPRISE_PRUNE_INTERVAL_SECONDS:-86400}"
if _positive_int "$raw_interval" && [ "$raw_interval" -ge 2 ]; then
   interval="$raw_interval"
else
   echo "pruner-loop: APPRISE_PRUNE_INTERVAL_SECONDS='${raw_interval}' must be an integer of at least 2, using default 86400s"
   interval=86400
fi

raw_timeout="${APPRISE_PRUNE_TIMEOUT_SECONDS:-28800}"
if _positive_int "$raw_timeout"; then
   timeout_seconds="$raw_timeout"
else
   echo "pruner-loop: APPRISE_PRUNE_TIMEOUT_SECONDS='${raw_timeout}' is not a positive integer, using default 28800s"
   timeout_seconds=28800
fi

# A prune must end before another cycle is due. Keep custom intervals safe
# even when the administrator leaves the timeout at its longer default.
if [ "$timeout_seconds" -ge "$interval" ]; then
   timeout_seconds=$((interval - 1))
   echo "pruner-loop: limiting prune timeout to ${timeout_seconds}s so it remains shorter than the interval"
fi

# Give Python a short chance to exit cleanly, then stop it unconditionally.
kill_grace=30
if [ "$timeout_seconds" -lt "$kill_grace" ]; then
   kill_grace="$timeout_seconds"
fi

echo "pruner-loop: scheduling pruning every ${interval}s (timeout ${timeout_seconds}s)"

while true; do
   # Match the common enabled values accepted by the application.
   case "${APPRISE_PRUNE_ENABLED:-yes}" in
      [Yy1Tt]* | [Ee][Nn]* | [Aa][Cc]* | +)
         # Run at low I/O and CPU priority. A second signal forcibly ends a
         # prune that does not respond when its time limit expires.
         flock -n "$LOCK_FILE" ionice -c3 nice -n 19 \
            timeout --signal=TERM --kill-after="${kill_grace}s" "${timeout_seconds}s" \
            python3 manage.py prune
         rc=$?
         if [ "$rc" -ne 0 ]; then
            echo "pruner-loop: pruning did not complete this cycle" \
                 "(exit ${rc} -- failed, timed out, or another prune was already running)"
         fi
         ;;
      *)
         echo "pruner-loop: skipped pruning (APPRISE_PRUNE_ENABLED=${APPRISE_PRUNE_ENABLED:-yes})"
         ;;
   esac
   sleep "$interval"
done
