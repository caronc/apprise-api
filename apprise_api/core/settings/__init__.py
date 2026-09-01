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
import base64
import logging
import os

import apprise
from core.settings.env import env_bool, env_choice, env_int, env_optional_bool
from core.themes import SiteTheme
from core.utils import parse_log_level
from django.core.exceptions import ImproperlyConfigured

# Register apprise's custom log levels before Django's dictConfig() runs.
if not hasattr(logging, "TRACE"):
    logging.TRACE = logging.DEBUG - 1
    logging.addLevelName(logging.TRACE, "TRACE")

# Project metadata for templates
APP_AUTHOR = "Chris Caron"
APP_COPYRIGHT = "Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>"
APP_LICENSE = "MIT"
APP_URL = "https://github.com/caronc/apprise-api"
APP_VERSION = "1.5.4"

# Mirror the container's TZ environment variable so Django does not
# override the process timezone with its own default (America/Chicago).
# Django unconditionally calls os.environ["TZ"] = TIME_ZONE + time.tzset()
# during settings load, which would otherwise clobber any TZ set before
# gunicorn workers are forked.
TIME_ZONE = os.environ.get("TZ", "Etc/UTC")

# Disable Timezones
USE_TZ = False

# Base Directory (relative to settings)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# SECURITY WARNING: keep the secret key used in production secret!
DEFAULT_SECRET_KEY = "+reua88v8rs4j!bcfdtinb-f0edxazf!$x_q1g7jtgckxd7gi="
SECRET_KEY = os.environ.get("SECRET_KEY", DEFAULT_SECRET_KEY)

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
DEBUG = env_bool("DEBUG")

# allow all hosts by default otherwise read from the
# ALLOWED_HOSTS environment variable
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(" ")

# Application definition
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    # Apprise API
    "api",
    # Error Responses
    "error",
    # Prometheus
    "django_prometheus",
]

MIDDLEWARE = [
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    "django.middleware.common.CommonMiddleware",
    "core.middleware.csrf.OriginValidationMiddleware",
    "core.middleware.auth.GlobalAuthMiddleware",
    "core.middleware.theme.AutoThemeMiddleware",
    "core.middleware.config.DetectConfigMiddleware",
    "django_prometheus.middleware.PrometheusAfterMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "core.context_processors.base_url",
                "api.context_processors.default_config_id",
                "api.context_processors.unique_config_id",
                "api.context_processors.stateful_mode",
                "api.context_processors.stateless_mode",
                "api.context_processors.config_lock",
                "api.context_processors.admin_enabled",
                "api.context_processors.authentication",
                "api.context_processors.apprise_metadata",
            ],
        },
    },
]

# Keep Django startup safe when LOG_LEVEL is empty or unsupported.
_LOG_LEVEL = logging.getLevelName(
    parse_log_level(
        os.environ.get("LOG_LEVEL"),
        "DEBUG" if DEBUG else "INFO",
    )
)

# Default notification log level when a request does not provide one.
APPRISE_LOG_LEVEL = _LOG_LEVEL

LOGGING = {
    "version": 1,
    # preserve loggers created by third-party libraries
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            # Keep console output at the configured global level.
            "level": _LOG_LEVEL,
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": _LOG_LEVEL,
            # Prevent double-logging
            "propagate": False,
        },
        "apprise": {
            "handlers": ["console"],
            # Allow request-specific capture before the console filters output.
            "level": "TRACE",
            "propagate": False,
        },
    },
}


WSGI_APPLICATION = "core.wsgi.application"

# Define our base URL
#
# Prefer APPRISE_BASE_URL for documentation and new deployments, but
# continue to support the legacy BASE_URL environment variable for
# backward compatibility. APPRISE_BASE_URL takes precedence when both are
# defined.
#
# Examples:
#   APPRISE_BASE_URL=/apprise
#   BASE_URL=/apprise
#
# A blank value means the application is hosted at the site root.

# Normalize the base path so that:
#   /apprise/ -> /apprise
#   apprise   -> /apprise
#   /         -> ''
# Fetch the environment variable and strip whitespace
_raw_base = os.environ.get("APPRISE_BASE_URL", os.environ.get("BASE_URL", "")).strip(" /")

# Prepend exactly one slash if a path exists, otherwise leave it empty
BASE_URL = f"/{_raw_base}" if _raw_base else ""

# Define our default configuration ID to use
APPRISE_DEFAULT_CONFIG_ID = os.environ.get("APPRISE_DEFAULT_CONFIG_ID", "apprise")

# Define our Prometheus Namespace
PROMETHEUS_METRIC_NAMESPACE = "apprise"

# Make all reverse() / {% url %} prefixed
FORCE_SCRIPT_NAME = BASE_URL or None

# Static files relative path (CSS, JavaScript, Images)
STATIC_URL = f"{BASE_URL}/s/"

# Default theme can be either 'light' or 'dark'. Values are not case
# sensitive, and the first letter is enough because each option starts with a
# different letter. For example, 'd', 'dark', and 'dakr' all select dark mode.
APPRISE_DEFAULT_THEME = env_choice(
    "APPRISE_DEFAULT_THEME",
    SiteTheme.LIGHT,
    (SiteTheme.LIGHT, SiteTheme.DARK),
    first_character=True,
)

# Webhook that is posted to upon executed results
# Set it to something like https://myserver.com/path/
# Requets are done as a POST
APPRISE_WEBHOOK_URL = os.environ.get("APPRISE_WEBHOOK_URL", "")

# The location to store Apprise configuration files
APPRISE_CONFIG_DIR = os.environ.get("APPRISE_CONFIG_DIR", os.path.join(BASE_DIR, "var", "config"))

# The location to store Apprise Persistent Storage files
APPRISE_STORAGE_DIR = os.environ.get("APPRISE_STORAGE_DIR", os.path.join(APPRISE_CONFIG_DIR, "store"))

# Default number of days to prune persistent storage
APPRISE_STORAGE_PRUNE_DAYS = env_int("APPRISE_STORAGE_PRUNE_DAYS", 30, minimum=0)

# Prune unused authentication locks after this age in seconds.
# The default is 30 days; locks with configuration are always retained.
APPRISE_AUTH_PRUNE_SECONDS = env_int("APPRISE_AUTH_PRUNE_SECONDS", 30 * 86400, minimum=0)

# The default URL ID Length
APPRISE_STORAGE_UID_LENGTH = env_int(
    "APPRISE_STORAGE_UID_LENGTH",
    8,
    minimum=2,
    maximum=64,
)

# The default storage mode; options are:
# - memory  : Disables persistent storage (this is also automatically set
#             if the APPRISE_STORAGE_DIR is empty reguardless of what is
#             defined below.
# - auto    : Writes to storage after each notifications execution (default)
# - flush   : Writes to storage constantly (as much as possible).  This
#             produces more i/o but can allow multiple calls to the same
#             notification to be in sync more
# Values are not case sensitive. Since each mode begins with a different
# letter, 'a', 'f', or 'm' is enough to select the intended mode. This also
# makes a small spelling mistake harmless when the first letter is correct.
APPRISE_STORAGE_MODE = env_choice(
    "APPRISE_STORAGE_MODE",
    "auto",
    ("auto", "flush", "memory"),
    first_character=True,
)

# The location to place file attachments
APPRISE_ATTACH_DIR = os.environ.get("APPRISE_ATTACH_DIR", os.path.join(BASE_DIR, "var", "attach"))

# The maximum file attachment size allowed by the API (defined in MB)
APPRISE_ATTACH_SIZE = env_int("APPRISE_ATTACH_SIZE", 200) * 1048576

# A provided list that identify all of the URLs/Hosts/IPs that Apprise can
# retrieve remote attachments from.
#
# Processing Order:
#  - The DENY list is always processed before the ALLOW list.
#     * If a match is found, processing stops, and the URL attachment is ignored
#  - The ALLOW list is ONLY processed if there was no match found on the DENY list
#  - If there is no match found on the ALLOW List, then the Attachment URL is ignored
#     * Not matching anything on the ALLOW list is effectively treated as a DENY
#
#  Lists are both processed from top down (stopping on first match)

# Use the following rules when constructing your ALLOW/DENY entries:
#  - Entries are separated with either a comma (,) and/or a space
#  - Entries can start with http:// or https:// (enforcing URL security HTTPS as part of check)
#  - IPs or hostnames provided is the preferred approach if it doesn't matter if the entry is
#     https:// or http://
#  - * wildcards are allowed. * means nothing or anything else that follows.
#  - ? placeholder wildcards are allowed (identifying the explicit placeholder
#    of an alpha/numeric/dash/underscore character)
#
#  Notes of interest:
#  - If the list is empty, then attachments can not be retrieved via URL at all.
#  - If the URL to be attached is not found or matched against an entry in this list then
#     the URL based attachment is ignored and is not retrieved at all.
#  - Set the list to * (a single astrix) to match all URLs and accepting all provided
#    matches
#  - The special token "internal" rejects loopback, private, link-local,
#     reserved, unspecified, multicast, and CGN addresses -- including ones
#     reached via DNS or alternate IP encodings, not just literal matches.
#     It also blocks ordinary LAN devices (e.g. 192.168.*), so it's opt-in,
#     not part of the default below. Add it to your own
#     APPRISE_ATTACH_REJECT_URL for that hardening.
APPRISE_ATTACH_DENY_URLS = os.environ.get("APPRISE_ATTACH_REJECT_URL", "127.0.* localhost*").lower()

# The Allow list which is processed after the Deny list above
APPRISE_ATTACH_ALLOW_URLS = os.environ.get("APPRISE_ATTACH_ALLOW_URL", "*").lower()


# The maximum size in bytes that a request body may be before raising an error
# (defined in MB)
APPRISE_UPLOAD_MAX_MEMORY_SIZE = env_int("APPRISE_UPLOAD_MAX_MEMORY_SIZE", 3, absolute=True) * 1048576


# Live logs use memory first, then one temporary file for a slow client.
# These same limits are placed on AppriseAsset for completed result logs.
# Environment values arrive as text, while defaults are whole MB. Django and
# the buffering code work with bytes, so convert the validated value here.
APPRISE_STREAM_MEMORY_SIZE = env_int("APPRISE_STREAM_MEMORY_SIZE", 2, minimum=0) * 1048576

# This allowance bounds the temporary file shared by captured logs.
APPRISE_STREAM_DISK_SIZE = env_int("APPRISE_STREAM_DISK_SIZE", 256, minimum=0) * 1048576

# Bound active notification work that may outlive disconnected clients.
APPRISE_STREAM_WORKER_COUNT = env_int("APPRISE_STREAM_WORKER_COUNT", 4, minimum=1)

# The maximum configuration payload size (in bytes) accepted by form/API
# configuration updates. This value is configured in KB and converted to bytes
# (KB * 1024). It is capped by APPRISE_UPLOAD_MAX_MEMORY_SIZE (bytes).
APPRISE_CONFIG_MAX_LENGTH = min(
    env_int("APPRISE_CONFIG_MAX_LENGTH", 512, absolute=True) * 1024,
    APPRISE_UPLOAD_MAX_MEMORY_SIZE,
)

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
APPRISE_CONFIG_LOCK = env_bool("APPRISE_CONFIG_LOCK")

# Authentication stays off unless it is explicitly requested. Credentials are
# ignored while it is off, which preserves the behavior of older deployments.
APPRISE_AUTH_REQUIRED = env_bool("APPRISE_AUTH_REQUIRED")

# Usernames ignore accidental surrounding whitespace. Passwords remain exact.
APPRISE_USER = (os.environ.get("APPRISE_USER") or "").strip() if APPRISE_AUTH_REQUIRED else None
APPRISE_PASSWORD = os.environ.get("APPRISE_PASSWORD") if APPRISE_AUTH_REQUIRED else None

# Label shown when a client asks for Basic Auth credentials.
APPRISE_BASIC_AUTH_REALM = os.environ.get("APPRISE_BASIC_AUTH_REALM", "Apprise API")

# Prepare the optional administrator login once at startup. Configuration
# logins can still be used when authentication is required without an admin.
APPRISE_BASIC_AUTH_TOKEN = None
if not APPRISE_AUTH_REQUIRED:
    logging.info("Authentication Mode: Disabled")
elif not APPRISE_PASSWORD:
    if APPRISE_USER and not APPRISE_PASSWORD:
        logging.warning("APPRISE_USER was set without APPRISE_PASSWORD; the administration account is disabled.")
    APPRISE_USER = None
    APPRISE_PASSWORD = None
    logging.info("Authentication Mode: Enabled - Administration Account Disabled")
else:
    # Basic Auth uses a colon between the username and password.
    if APPRISE_USER and ":" in APPRISE_USER:
        raise ImproperlyConfigured("APPRISE_USER cannot contain ':'.")
    APPRISE_BASIC_AUTH_TOKEN = base64.b64encode(f"{APPRISE_USER or ''}:{APPRISE_PASSWORD}".encode()).decode()
    logging.info("Authentication Mode: Enabled - Administration Account Enabled")

# Browser logins use their own setting and built-in default. This keeps browser
# sessions independent from the SECRET_KEY used by HASH-mode configurations.
DEFAULT_WEB_AUTH_SECRET = "Sw`rFTu3~4dq#hua:daY#T5^d;`#Z5:cE~mf.h`ZCKCP:AZMKZ"
APPRISE_WEB_AUTH_SECRET = os.environ.get("APPRISE_WEB_AUTH_SECRET") or DEFAULT_WEB_AUTH_SECRET

# Active browser sessions renew this 24-hour login window on each request.
APPRISE_WEB_AUTH_MAX_AGE = 24 * 60 * 60

# Optional browser-origin allow-list using ``scheme://host[:port]``.
# Without it, Origin validation compares host and port only because bundled
# nginx does not forward the original scheme. HTTPS deployments should set it.
APPRISE_TRUSTED_ORIGINS = [
    origin.strip().lower() for origin in os.environ.get("APPRISE_TRUSTED_ORIGINS", "").split(",") if origin.strip()
]

# Stateless posts to /notify/ will resort to this set of URLs if none
# were otherwise posted with the URL request.
APPRISE_STATELESS_URLS = os.environ.get("APPRISE_STATELESS_URLS", "")

# Allow stateless URLS to generate and/or work with persistent storage
# By default this is set to no
APPRISE_STATELESS_STORAGE = env_bool("APPRISE_STATELESS_STORAGE")

# Defines the stateful mode; possible values are:
# - hash (default): content is hashed and zipped
# - simple: content is just written straight to disk 'as-is'
# - disabled: disable all stateful functionality
# Values are not case sensitive. The unique first letters 'h', 's', and 'd'
# are accepted too, so a small spelling mistake after the first letter still
# selects the intended mode. Any other first letter stops startup with a clear
# configuration error instead of silently choosing a different mode.
APPRISE_STATEFUL_MODE = env_choice(
    "APPRISE_STATEFUL_MODE",
    "hash",
    ("hash", "simple", "disabled"),
    first_character=True,
)

# Defines the stateless mode; possible values are:
# - enabled (default): stateless /notify/ calls are accepted
# - disabled: stateless /notify/ calls are rejected
# Keep this as a mode string so more choices can be added later.
# parse_bool() accepts common values such as yes/no, true/false, and 1/0.
APPRISE_STATELESS_MODE = "enabled" if env_bool("APPRISE_STATELESS_MODE", default=True) else "disabled"

# Our Apprise Deny List
# - By default we disable all non-remote calling services
# - You do not need to identify every schema supported by the service you
#   wish to disable (only one).  For example, if you were to specify
#   xml, that would include the xmls entry as well (or vs versa)
APPRISE_DENY_SERVICES = os.environ.get(
    "APPRISE_DENY_SERVICES",
    ",".join(("windows", "dbus", "gnome", "macosx", "syslog")),
)

# Our Apprise Exclusive Allow List
#  - anything not identified here is denied/disabled)
#  - this list trumps the APPRISE_DENY_SERVICES identified above
APPRISE_ALLOW_SERVICES = os.environ.get("APPRISE_ALLOW_SERVICES", "")

# Define the number of recursive calls your system will allow users to make
# The idea here is to prevent people from defining apprise:// URL's triggering
# a call to the same server again, and again and again. By default we allow
# 1 level of recursion
APPRISE_RECURSION_MAX = env_int("APPRISE_RECURSION_MAX", 1, minimum=0)

# Provided optional plugin paths to scan for custom schema definitions
APPRISE_PLUGIN_PATHS = os.environ.get("APPRISE_PLUGIN_PATHS", os.path.join(BASE_DIR, "var", "plugin")).split(",")

# Define the number of attachments that can exist as part of a payload
# Setting this to zero disables the limit
APPRISE_MAX_ATTACHMENTS = env_int("APPRISE_MAX_ATTACHMENTS", 6, minimum=0)

# The maximum depth allowed when traversing nested (dot-notation) subfields in
# third-party webhook payload mapping rules.  For example, `:event.title=title`
# has a depth of 2.  Raising this value too high could permit deeply recursive
# traversal; keep it low to avoid abuse.
APPRISE_WEBHOOK_MAPPING_MAX_DEPTH = env_int("APPRISE_WEBHOOK_MAPPING_MAX_DEPTH", 5, absolute=True)

# Apprise API Only mode:
# - Disable entire Web Page and only allow the API interface to work
# - Website requests returns 421 (Misdirected Request) for what would otherwise
#   have been part of the Apprise Website host if this is set to 'no'.
# - The default value of this is 'no'
APPRISE_API_ONLY = env_bool("APPRISE_API_ONLY")

# Allow Admin mode:
# - showing a list of configuration keys (when STATEFUL_MODE is set to simple)
APPRISE_ADMIN = env_bool("APPRISE_ADMIN")

# Allow Interpret Emojis override
APPRISE_INTERPRET_EMOJIS = env_optional_bool("APPRISE_INTERPRET_EMOJIS")

# Allow HTTP Redirects override
# By default Apprise follows HTTP 3xx redirects, matching the behaviour of
# the underlying requests library.  Set APPRISE_HTTP_REDIRECTS=no to disable
# redirect following globally across all plugins without touching individual
# URLs.
APPRISE_HTTP_REDIRECTS = env_bool("APPRISE_HTTP_REDIRECTS", default=True)

# Optional default for requests that omit ``format``. Blank or unknown values
# leave message content unchanged. Explicit request values always take priority.
_apprise_default_format = os.environ.get("APPRISE_DEFAULT_FORMAT", "").strip().lower()
APPRISE_DEFAULT_FORMAT = (
    _apprise_default_format if _apprise_default_format in {f.value for f in apprise.NotifyFormat} else None
)
