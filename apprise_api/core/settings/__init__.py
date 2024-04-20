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
import os
from core.themes import SiteTheme

# Disable Timezones
USE_TZ = False

# Base Directory (relative to settings)
BASE_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get(
    'SECRET_KEY', '+reua88v8rs4j!bcfdtinb-f0edxazf!$x_q1g7jtgckxd7gi=')

# SECURITY WARNING: don't run with debug turned on in production!
# If you want to run this app in DEBUG mode, run the following:
#
#    ./manage.py runserver --settings=core.settings.debug
#
# Or alternatively run:
#
#    export DJANGO_SETTINGS_MODULE=core.settings.debug
#    ./manage.py runserver
#
# Support 'yes', '1', 'true', 'enable', 'active', and +
DEBUG = os.environ.get("DEBUG", 'No')[0].lower() in (
    'a', 'y', '1', 't', 'e', '+')

# allow all hosts by default otherwise read from the
# ALLOWED_HOSTS environment variable
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '*').split(' ')

# Application definition
INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.staticfiles',

    # Apprise API
    'api',
]

MIDDLEWARE = [
    'django.middleware.common.CommonMiddleware',
    'core.middleware.theme.AutoThemeMiddleware',
    'core.middleware.config.DetectConfigMiddleware',
]

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'core.context_processors.base_url',
                'api.context_processors.default_config_id',
                'api.context_processors.unique_config_id',
                'api.context_processors.stateful_mode',
                'api.context_processors.config_lock',
                'api.context_processors.apprise_version',
            ],
        },
    },
]

LOGGING = {
    'version': 1,
    'disable_existing_loggers': True,
    'formatters': {
        'standard': {
            'format': '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'standard'
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
        },
        'apprise': {
            'handlers': ['console'],
            'level': os.environ.get(
                'LOG_LEVEL', 'debug' if DEBUG else 'info').upper(),
        },
    }
}


WSGI_APPLICATION = 'core.wsgi.application'

# Define our base URL
# The default value is to be a single slash
BASE_URL = os.environ.get('BASE_URL', '')

# Define our default configuration ID to use
APPRISE_DEFAULT_CONFIG_ID = \
    os.environ.get('APPRISE_DEFAULT_CONFIG_ID', 'apprise')

# Static files relative path (CSS, JavaScript, Images)
STATIC_URL = BASE_URL + '/s/'

# Default theme can be either 'light' or 'dark'
APPRISE_DEFAULT_THEME = \
    os.environ.get('APPRISE_DEFAULT_THEME', SiteTheme.LIGHT)

# Webhook that is posted to upon executed results
# Set it to something like https://myserver.com/path/
# Requets are done as a POST
APPRISE_WEBHOOK_URL = os.environ.get('APPRISE_WEBHOOK_URL', '')

# The location to store Apprise configuration files
APPRISE_CONFIG_DIR = os.environ.get(
    'APPRISE_CONFIG_DIR', os.path.join(BASE_DIR, 'var', 'config'))

# The location to place file attachments
APPRISE_ATTACH_DIR = os.environ.get(
    'APPRISE_ATTACH_DIR', os.path.join(BASE_DIR, 'var', 'attach'))

# The maximum file attachment size allowed by the API (defined in MB)
APPRISE_ATTACH_SIZE = int(os.environ.get('APPRISE_ATTACH_SIZE', 200)) * 1048576

# The maximum size in bytes that a request body may be before raising an error
# (defined in MB)
DATA_UPLOAD_MAX_MEMORY_SIZE = abs(int(os.environ.get(
    'APPRISE_UPLOAD_MAX_MEMORY_SIZE', 3))) * 1048576

# When set Apprise API Locks itself down so that future (configuration)
# changes can not be made or accessed.  It disables access to:
# - the configuration screen: /cfg/{token}
#    - this in turn makes it so the Apprise CLI tool can not use it's
#        --config= (-c) options against this server.
# - All notifications (both persistent and non persistent) continue to work
#   as they did before. This includes both /notify/{token}/ and /notify/
# - Certain API calls no longer work such as:
#    - /del/{token}/
#    - /add/{token}/
# - the /json/urls/{token} API location will continue to work but will always
#   enforce it's privacy mode.
#
# The idea here is that someone has set up the configuration they way they want
# and do not want this information exposed any more then it needs to be.
# it's a lock down mode if you will.
APPRISE_CONFIG_LOCK = \
    os.environ.get("APPRISE_CONFIG_LOCK", 'no')[0].lower() in (
        'a', 'y', '1', 't', 'e', '+')

# Stateless posts to /notify/ will resort to this set of URLs if none
# were otherwise posted with the URL request.
APPRISE_STATELESS_URLS = os.environ.get('APPRISE_STATELESS_URLS', '')

# Defines the stateful mode; possible values are:
# - hash (default): content is hashed and zipped
# - simple: content is just written straight to disk 'as-is'
# - disabled: disable all stateful functionality
APPRISE_STATEFUL_MODE = os.environ.get('APPRISE_STATEFUL_MODE', 'hash')

# Our Apprise Deny List
# - By default we disable all non-remote calling services
# - You do not need to identify every schema supported by the service you
#   wish to disable (only one).  For example, if you were to specify
#   xml, that would include the xmls entry as well (or vs versa)
APPRISE_DENY_SERVICES = os.environ.get('APPRISE_DENY_SERVICES', ','.join((
    'windows', 'dbus', 'gnome', 'macosx', 'syslog')))

# Our Apprise Exclusive Allow List
#  - anything not identified here is denied/disabled)
#  - this list trumps the APPRISE_DENY_SERVICES identified above
APPRISE_ALLOW_SERVICES = os.environ.get('APPRISE_ALLOW_SERVICES', '')

# Define the number of recursive calls your system will allow users to make
# The idea here is to prevent people from defining apprise:// URL's triggering
# a call to the same server again, and again and again. By default we allow
# 1 level of recursion
APPRISE_RECURSION_MAX = int(os.environ.get('APPRISE_RECURSION_MAX', 1))

# Provided optional plugin paths to scan for custom schema definitions
APPRISE_PLUGIN_PATHS = os.environ.get(
    'APPRISE_PLUGIN_PATHS', os.path.join(BASE_DIR, 'var', 'plugin')).split(',')

# Define the number of attachments that can exist as part of a payload
# Setting this to zero disables the limit
APPRISE_MAX_ATTACHMENTS = int(os.environ.get('APPRISE_MAX_ATTACHMENTS', 6))
