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
# Runs one scheduled Django prune command under supervisord.
# Both prune jobs share this script and cannot run at the same time.
#
# Usage: pruner-loop.sh <django-command> <interval-env-var> <default-interval-seconds> <initial-delay-seconds>
#
#   <django-command>            storeprune or authprune
#   <interval-env-var>          environment variable that sets the interval
#   <default-interval-seconds>  fallback interval
#   <initial-delay-seconds>     delay before the first run
set -u

COMMAND="$1"
INTERVAL_VAR="$2"
DEFAULT_INTERVAL="$3"
INITIAL_DELAY="$4"

# One lock prevents the two prune jobs from overlapping.
LOCK_FILE="/tmp/apprise/pruner.lock"

# Reject invalid values and zero, which would create a busy loop.
_positive_int() {
   [[ "$1" =~ ^[1-9][0-9]*$ ]]
}

# Indirect expansion: reads the env var *named* by $INTERVAL_VAR.
raw_interval="${!INTERVAL_VAR:-$DEFAULT_INTERVAL}"
if _positive_int "$raw_interval"; then
   interval="$raw_interval"
else
   echo "pruner-loop: ${INTERVAL_VAR}='${raw_interval}' is not a positive integer, using default ${DEFAULT_INTERVAL}s"
   interval="$DEFAULT_INTERVAL"
fi

raw_timeout="${APPRISE_PRUNE_TIMEOUT_SECONDS:-28800}"
if _positive_int "$raw_timeout"; then
   timeout_seconds="$raw_timeout"
else
   echo "pruner-loop: APPRISE_PRUNE_TIMEOUT_SECONDS='${raw_timeout}' is not a positive integer, using default 28800s"
   timeout_seconds=28800
fi

echo "pruner-loop: scheduling '${COMMAND}' every ${interval}s (first run in ${INITIAL_DELAY}s, timeout ${timeout_seconds}s)"
sleep "$INITIAL_DELAY"

while true; do
   # Match the common enabled values accepted by the application.
   case "${APPRISE_PRUNE_ENABLED:-yes}" in
      [Yy1Tt]* | [Ee][Nn]* | [Aa][Cc]* | +)
         # Run at low I/O and CPU priority with a time limit.
         # Skip this cycle if the other prune job holds the lock.
         flock -n "$LOCK_FILE" ionice -c3 nice -n 19 timeout "$timeout_seconds" python3 manage.py "$COMMAND"
         rc=$?
         if [ "$rc" -ne 0 ]; then
            echo "pruner-loop: '${COMMAND}' did not complete this cycle" \
                 "(exit ${rc} -- failed, timed out, or another prune was already running)"
         fi
         ;;
      *)
         echo "pruner-loop: skipped '${COMMAND}' (APPRISE_PRUNE_ENABLED=${APPRISE_PRUNE_ENABLED:-yes})"
         ;;
   esac
   sleep "$interval"
done
