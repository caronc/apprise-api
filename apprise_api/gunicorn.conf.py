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
import multiprocessing
import os
import time

# This file is launched with the call:
# gunicorn --config <this file> core.wsgi:application

# Apply the TZ environment variable before workers are forked so that Python's
# time functions (used by logging formatters) use the same timezone as every
# other process in the container (nginx, supervisord, etc.).
if hasattr(time, "tzset"):
    time.tzset()

raw_env = [
    "LANG={}".format(os.environ.get("LANG", "en_US.UTF-8")),
    "DJANGO_SETTINGS_MODULE=core.settings",
    # Carry TZ into every worker environment.
    "TZ={}".format(os.environ.get("TZ", "Etc/UTC")),
]

# This is the path as prepared in the docker compose
pythonpath = "/opt/apprise/webapp"

# Bind path
bind = ["unix:/tmp/apprise/gunicorn.sock"]

# Define our umask
umask = 0o117

# Workers are relative to the number of CPU's provided by hosting server
workers = int(os.environ.get("APPRISE_WORKER_COUNT", multiprocessing.cpu_count() * 2 + 1))

# Increase worker timeout value to give upstream services time to
# respond.
timeout = int(os.environ.get("APPRISE_WORKER_TIMEOUT", 300))

# Our worker type to use; over-ride the default `sync`
worker_class = "gevent"

# Get workers memory consumption under control by leveraging gunicorn
# worker recycling. Workers are restarted after handling this many requests,
# which forces Python to release accumulated memory. Lower values help
# in low-traffic deployments where workers would otherwise run indefinitely.
max_requests = int(os.environ.get("APPRISE_WORKER_MAX_REQUESTS", 1000))
max_requests_jitter = int(os.environ.get("APPRISE_WORKER_MAX_REQUESTS_JITTER", 50))

# Logging
# '-' means log to stdout.
errorlog = "-"
# Access logging is handled entirely by nginx, which sits in front of
# gunicorn and already logs every request
# Enabling gunicorn's own access log produces duplicate entries
accesslog = None
loglevel = "warn"


def post_fork(_server, _worker):
    # Re-apply TZ in each worker after the fork to ensure each freshly forked
    # worker initialises from the TZ env var rather than from whatever cached
    # timezone state it inherited from the parent process.
    if hasattr(time, "tzset"):
        time.tzset()
