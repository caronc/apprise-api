# -*- coding: utf-8 -*-
#
# Copyright (C) 2019 Chris Caron <lead2gold@gmail.com>
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

# This file is launched with the call:
# gunicorn --config <this file> core.wsgi:application

raw_env = [
    'LANG=en_US.UTF-8',
    'DJANGO_SETTINGS_MODULE=core.settings',
]

# This is the path as prepared in the docker compose
pythonpath = '/opt/apprise/webapp'

# bind to port 8000
bind = [
    '0.0.0.0:8000',
]

# Workers are relative to the number of CPU's provided by hosting server
workers = multiprocessing.cpu_count() * 2 + 1

# Increase worker timeout value to give upstream services time to
# respond
timeout = 90

# Logging
# '-' means log to stdout.
errorlog = '-'
accesslog = '-'
loglevel = 'warn'
