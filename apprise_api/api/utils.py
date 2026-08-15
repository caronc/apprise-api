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
import binascii
from collections.abc import Mapping
from contextlib import suppress
from datetime import datetime
import errno
import gzip
import hashlib
import hmac
from json import dumps, loads

# import the logging library
import logging
import os
import re
import shutil
import tempfile

import apprise
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core import signing
from django.core.cache import cache
from django.http import HttpRequest
import requests

from .urlfilter import AppriseURLFilter

# Get an instance of a logger
logger = logging.getLogger("django")

# Support JSON formats
# text/json
# text/x-json
# application/json
# application/x-json
MIME_IS_JSON = re.compile(r"(text|application)/(x-)?json", re.I)
MIME_IS_HTML = re.compile(r"(^|,)\s*text/html(?:\s*;|\s*,|$)", re.I)

# Parsing of Accept; the following amounts to Accept All
# */*
# <blank>
ACCEPT_ALL = re.compile(r"^\s*([*]/[*]|)\s*$", re.I)


def is_json_response(request: HttpRequest) -> bool:
    """Return whether the request prefers a JSON response.

    Accept takes priority. Missing or wildcard Accept falls back to the
    request Content-Type for backward compatibility.
    """
    accept = request.headers.get("accept", "")
    content_type = request.content_type or request.headers.get("content-type", "")
    return MIME_IS_JSON.match(accept) is not None or (
        ACCEPT_ALL.match(accept) is not None and MIME_IS_JSON.match(content_type) is not None
    )


def is_html_response(request: HttpRequest) -> bool:
    """Return whether the client explicitly accepts a browser page."""
    return MIME_IS_HTML.search(request.headers.get("accept", "")) is not None


def is_authenticated(request: HttpRequest) -> bool:
    """Return whether the request satisfies the global Basic Auth gate.

    Authentication is optional. When enabled, the supplied token is compared
    safely with the token prepared at startup.
    """
    if not settings.APPRISE_AUTH_REQUIRED:
        return True

    # Authentication may be enabled without an administrator account.
    if settings.APPRISE_BASIC_AUTH_TOKEN is None:
        return False

    provided = request.headers.get("authorization", "")
    # RFC 7235: the auth-scheme token ("Basic") is case-insensitive.
    if provided[:6].lower() != "basic ":
        return False

    return hmac.compare_digest(provided[6:], settings.APPRISE_BASIC_AUTH_TOKEN)


def global_credentials_ok(username: str, password: str) -> bool:
    """Return whether a username and password match the global login."""
    if settings.APPRISE_BASIC_AUTH_TOKEN is None:
        return False

    try:
        provided = base64.b64encode("{}:{}".format(username, password).encode()).decode()
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(provided, settings.APPRISE_BASIC_AUTH_TOKEN)


class AppriseStoreMode:
    """
    Defines the store modes of configuration
    """

    # This is the default option. Content is cached and written by
    # it's key
    HASH = "hash"

    # Content is written straight to disk using it's key
    # there is nothing further done
    SIMPLE = "simple"

    # When set to disabled; stateful functionality is disabled
    DISABLED = "disabled"


class AttachmentPayload:
    """
    Defines the supported Attachment Payload Types
    """

    # BASE64
    BASE64 = "base64"

    # URL request
    URL = "url"


STORE_MODES = (
    AppriseStoreMode.HASH,
    AppriseStoreMode.SIMPLE,
    AppriseStoreMode.DISABLED,
)

# Access our Attachment Manager Singleton
A_MGR = apprise.manager_attachment.AttachmentManager()

# Access our Notification Manager Singleton
N_MGR = apprise.manager_plugins.NotificationManager()

# Let the API unload optional modules that no enabled service still needs.
# Other applications embedding Apprise keep loaded modules by default.
N_MGR.evict_on_disable = True

# Prepare our Attachment URL Filter
ATTACH_URL_FILTER = AppriseURLFilter(settings.APPRISE_ATTACH_ALLOW_URLS, settings.APPRISE_ATTACH_DENY_URLS)


class Attachment(A_MGR["file"]):
    """
    A Light Weight Attachment Object for Auto-cleanup that wraps the Apprise
    Attachments
    """

    def __init__(self, filename, path=None, delete=True, **kwargs):
        """
        Initialize our attachment
        """
        self._filename = filename
        self.delete = delete
        self._path = None
        try:
            os.makedirs(settings.APPRISE_ATTACH_DIR, exist_ok=True)

        except OSError:
            # Permission error
            raise ValueError("Could not create directory {}".format(settings.APPRISE_ATTACH_DIR)) from None

        if not path:
            try:
                d, path = tempfile.mkstemp(dir=settings.APPRISE_ATTACH_DIR)
                # Close our file descriptor
                os.close(d)

            except FileNotFoundError:
                raise ValueError(
                    "Could not prepare {} attachment in {}".format(filename, settings.APPRISE_ATTACH_DIR)
                ) from None

        self._path = path

        # Prepare our item
        super().__init__(path=self._path, name=filename, **kwargs)

        # Update our file size based on the settings value
        self.max_file_size = settings.APPRISE_ATTACH_SIZE

    @property
    def filename(self):
        return self._filename

    @property
    def size(self):
        """
        Return filesize
        """
        return os.stat(self._path).st_size

    def __del__(self):
        """
        De-Construtor is used to tidy up files during garbage collection
        """
        if self.delete and self._path:
            # no problem if file is missing
            with suppress(FileNotFoundError):
                os.remove(self._path)


class HTTPAttachment(A_MGR["http"]):
    """
    A Light Weight Attachment Object for Auto-cleanup that wraps the Apprise
    Web Attachments
    """

    def __init__(self, filename=None, delete=True, **kwargs):
        """
        Initialize our attachment
        """
        # Pop any name that parse_url() extracted from a ?name= query
        # parameter.  We must remove it from kwargs before passing to
        # AttachBase to avoid "multiple values for keyword argument 'name'".
        # Priority: explicit filename arg > URL ?name= > None (auto-detect).
        url_name = kwargs.pop("name", None)
        effective_name = filename if filename is not None else url_name

        self._filename = effective_name
        self.delete = delete
        self._path = None
        try:
            os.makedirs(settings.APPRISE_ATTACH_DIR, exist_ok=True)

        except OSError:
            # Permission error
            raise ValueError("Could not create directory {}".format(settings.APPRISE_ATTACH_DIR)) from None

        try:
            d, self._path = tempfile.mkstemp(dir=settings.APPRISE_ATTACH_DIR)
            # Close our file descriptor
            os.close(d)

        except FileNotFoundError:
            raise ValueError(
                "Could not prepare {} attachment in {}".format(effective_name, settings.APPRISE_ATTACH_DIR)
            ) from None

        # Prepare our item
        super().__init__(name=effective_name, **kwargs)

        # Update our file size based on the settings value
        self.max_file_size = settings.APPRISE_ATTACH_SIZE

    @property
    def filename(self):
        return self._filename

    @property
    def size(self):
        """
        Return filesize
        """
        return 0 if not self else os.stat(self._path).st_size

    def __del__(self):
        """
        De-Construtor is used to tidy up files during garbage collection
        """
        if self.delete and self._path:
            # no problem if file is missing
            with suppress(FileNotFoundError):
                os.remove(self._path)


def touchdir(path, mode=0o770, **kwargs):
    """
    Acts like a Linux touch and updates a dir with a current timestamp
    """
    try:
        os.makedirs(path, mode=mode, exist_ok=False)

    except FileExistsError:
        # Update the mtime of the directory
        try:
            os.utime(path, None)
        except OSError:
            return False

    except OSError:
        return False

    return True


def touch(fname, mode=0o666, dir_fd=None, **kwargs):
    """
    Acts like a Linux touch and updates a file with a current timestamp
    """
    flags = os.O_CREAT | os.O_APPEND
    try:
        with os.fdopen(os.open(fname, flags=flags, mode=mode, dir_fd=dir_fd)) as f:
            os.utime(
                f.fileno() if os.utime in os.supports_fd else fname,
                dir_fd=None if os.supports_fd else dir_fd,
                **kwargs,
            )

    except OSError:
        return False

    return True


def parse_attachments(attachment_payload, files_request):
    """
    Takes the payload provided in a `/notify` call and extracts the
    attachments out of it.

    Content is written to a temporary directory until the garbage
    collection kicks in.
    """
    attachments = []

    if settings.APPRISE_ATTACH_SIZE <= 0:
        if not (attachment_payload or files_request):
            # No further processing required
            return []

        # Otherwise we need to raise an error
        raise ValueError("Attachment support has been disabled")

    # Determine how many files we have in the request.FILES
    file_count = 0
    if hasattr(files_request, "lists"):
        file_count = sum(len(v) for _, v in files_request.lists())
    elif isinstance(files_request, dict):
        # conservative fallback
        file_count = len(files_request)

    # Attachment Count
    count = (len(attachment_payload) if isinstance(attachment_payload, (set, tuple, list)) else 0) + file_count

    if isinstance(attachment_payload, dict | str | bytes):
        # Convert and adjust counter
        attachment_payload = (attachment_payload,)
        count += 1

    if settings.APPRISE_MAX_ATTACHMENTS > 0 and count > settings.APPRISE_MAX_ATTACHMENTS:
        raise ValueError(f"There is a maximum of {settings.APPRISE_MAX_ATTACHMENTS} attachments")

    if isinstance(attachment_payload, tuple | list | set):
        for no, entry in enumerate(attachment_payload, start=1):
            if isinstance(entry, str | bytes):
                filename = f"attachment.{no:03d}"

            elif isinstance(entry, dict):
                try:
                    filename = entry.get("filename", "").strip()

                    # Max filename size is 250
                    if len(filename) > 250:
                        raise ValueError(f"The filename associated with attachment {no} is too long")

                    elif not filename:
                        filename = f"attachment.{no:03d}"

                except AttributeError:
                    # not a string that was provided
                    raise ValueError(f"An invalid filename was provided for attachment {no}") from None

            else:
                # you must pass in a base64 string, or a dict containing our
                # required parameters
                raise ValueError(f"An invalid filename was provided for attachment {no}")

            #
            # Prepare our Attachment
            #
            if isinstance(entry, str):
                if not entry.strip():
                    # ignore blank entries; these can come from using the
                    # api/website and submitting without an element defined.
                    # There is no need have a bad outcome; just decrement our
                    # counter and move along
                    count -= 1
                    continue

                if not re.match(r"^https?://.+", entry[:10], re.I):
                    # We failed to retrieve the product
                    raise ValueError(f"Failed to load attachment {no} (not web request): {entry}")

                if not ATTACH_URL_FILTER.is_allowed(entry):
                    # We are not allowed to use this entry
                    raise ValueError(f"Denied attachment {no} (blocked web request): {entry}")

                # apprise's own parse_url() already sanitizes ?name= (strips
                # directory components and only sets the key when non-empty)
                _parsed = A_MGR["http"].parse_url(entry)

                # ?name= wins when present; otherwise derive from the URL path
                # basename so .../6dba.jpg doesn't get renamed to attachment.001.
                # Only fall back to attachment.NNN when no name can be found.
                if "name" not in _parsed:
                    _path_name = os.path.basename(_parsed.get("fullpath", "").rstrip("/"))
                    if not _path_name:
                        _parsed["name"] = filename

                attachment = HTTPAttachment(**_parsed)
                if not attachment:
                    # We failed to retrieve the attachment
                    raise ValueError(f"Failed to retrieve attachment {no}: {entry}")

            else:  # web, base64 or raw
                attachment = Attachment(filename)
                try:
                    with open(attachment.path, "wb") as f:
                        # Write our content to disk
                        if isinstance(entry, dict) and AttachmentPayload.BASE64 in entry:
                            # BASE64
                            f.write(base64.b64decode(entry[AttachmentPayload.BASE64]))

                        elif isinstance(entry, dict) and AttachmentPayload.URL in entry:
                            if not ATTACH_URL_FILTER.is_allowed(entry[AttachmentPayload.URL]):
                                # We are not allowed to use this entry
                                raise ValueError(
                                    f"Denied attachment {no} (blocked web request): {entry[AttachmentPayload.URL]}"
                                )

                            # apprise's own parse_url() already sanitizes
                            # ?name= (same rules as the string-URL path above).
                            _parsed = A_MGR["http"].parse_url(entry[AttachmentPayload.URL])

                            # User-provided dict filename overrides all URL
                            # derived names.  If absent, prefer URL ?name=
                            # then path basename, then attachment.NNN.
                            _dict_filename = entry.get("filename", "").strip()
                            if _dict_filename:
                                _parsed["name"] = _dict_filename
                            elif "name" not in _parsed:
                                _path_name = os.path.basename(_parsed.get("fullpath", "").rstrip("/"))
                                if not _path_name:
                                    _parsed["name"] = filename

                            attachment = HTTPAttachment(**_parsed)
                            if not attachment:
                                # We failed to retrieve the attachment
                                raise ValueError(f"Failed to retrieve attachment {no}: {entry}")

                        elif isinstance(entry, bytes):
                            # RAW
                            f.write(entry)

                        else:
                            raise ValueError(f"Invalid filetype was provided for attachment {filename}")

                except binascii.Error:
                    # The file ws not base64 encoded
                    raise ValueError(f"Invalid filecontent was provided for attachment {filename}") from None

                except OSError:
                    raise ValueError(f"Could not write attachment {filename} to disk") from None

                #
                # Some Validation
                #
                if settings.APPRISE_ATTACH_SIZE > 0 and attachment.size > settings.APPRISE_ATTACH_SIZE:
                    raise ValueError(f"attachment {filename}'s filesize is to large")

            # Add our attachment
            attachments.append(attachment)

    #
    # Now handle the request.FILES
    #
    if hasattr(files_request, "lists"):
        iterable = ((k, f) for k, lst in files_request.lists() for f in lst)
    elif isinstance(files_request, dict):
        iterable = files_request.items()
    else:
        iterable = ()

    for no, (_, meta) in enumerate(iterable, start=len(attachments) + 1):
        try:
            # Filetype is presumed to be of base class
            # django.core.files.UploadedFile
            filename = meta.name.strip()

            # Max filename size is 250
            if len(filename) > 250:
                raise ValueError(f"The filename associated with attachment {no} is too long")

            elif not filename:
                filename = f"attachment.{no:03d}"

        except (AttributeError, TypeError):
            raise ValueError(f"An invalid filename was provided for attachment {no}") from None

        # lower() protects case from the Apprise case sensitive guessing:
        #  - Content-Type: Image/JPEG
        #  - APPLICATION/OCTET-STREAM
        wire_mimetype = getattr(meta, "content_type", "").strip().lower() or None

        if wire_mimetype == "application/octet-stream":
            # disabling mimetype before Attachment() object allows for guessing of type
            wire_mimetype = None

        attachment = Attachment(filename, mimetype=wire_mimetype)
        try:
            with open(attachment.path, "wb") as f:
                # Write our content to disk
                f.write(meta.read())

        except OSError:
            raise ValueError(f"Could not write attachment {filename} to disk") from None

        #
        # Some Validation
        #
        if settings.APPRISE_ATTACH_SIZE > 0 and attachment.size > settings.APPRISE_ATTACH_SIZE:
            raise ValueError(f"attachment {filename}'s filesize is to large")

        # Add our attachment
        attachments.append(attachment)

    return attachments


class SimpleFileExtension:
    """
    Defines the simple file exension lookups
    """

    # Simple Configuration file
    TEXT = "cfg"

    # YAML Configuration file
    YAML = "yml"


SIMPLE_FILE_EXTENSION_MAPPING = {
    apprise.ConfigFormat.TEXT.value: SimpleFileExtension.TEXT,
    apprise.ConfigFormat.YAML.value: SimpleFileExtension.YAML,
    SimpleFileExtension.TEXT: SimpleFileExtension.TEXT,
    SimpleFileExtension.YAML: SimpleFileExtension.YAML,
}

SIMPLE_FILE_EXTENSIONS = (SimpleFileExtension.TEXT, SimpleFileExtension.YAML)


class AppriseAuthStorageError(Exception):
    """Raised when an existing per-key authentication lock cannot be read."""


# These values describe how a configuration key is protected.
AUTH_MODE_DISABLED = "disabled"
AUTH_MODE_MASTER = "master_lock"
AUTH_MODE_SHARED = "shared_lock"

# Browser pages use a signed cookie so their Logout button can end a session.
# API clients continue to authenticate each request with Basic Auth.
WEB_AUTH_COOKIE = "apprise_web_auth"
WEB_AUTH_HEADER = "X-Apprise-Web-Auth"
_WEB_AUTH_SIGNING_SALT = "apprise-api.web-auth"

# New lock files retain the username for the GUI. Digest-only files from
# earlier versions remain supported.
_AUTH_RECORD_VERSION = 1


class AppriseConfigCache:
    """
    Designed to make it easy to store/read contact back from disk in a cache
    type structure that is fast.
    """

    def __init__(self, cache_root, salt="apprise", mode=AppriseStoreMode.HASH):
        """
        Works relative to the cache_root
        """
        self.root = cache_root
        self.salt = salt.encode()
        self.mode = mode.strip().lower()
        if self.mode not in STORE_MODES:
            self.mode = AppriseStoreMode.DISABLED
            logger.error("APPRISE_STATEFUL_MODE {} is not supported; reverted to {}.".format(mode, self.mode))

    def put(self, key, content, fmt):
        """
        Based on the key specified, content is written to disk (compressed)

        key:     is an alphanumeric string needed to write and read back this
                 file being written.
        content: the content to be written to disk
        fmt:     the content config format (of type apprise.ConfigFormat)

        """
        # There isn't a lot of error handling done here as it is presumed most
        # of the checking has been done higher up.
        if self.mode == AppriseStoreMode.DISABLED:
            # Do nothing
            return False

        # First two characters are reserved for cache level directory writing.
        path, filename = self.path(key)
        try:
            os.makedirs(path, exist_ok=True)

        except OSError:
            # Permission error
            logger.error("Could not create directory {}".format(path))
            return False

        # Write our file to a temporary file
        try:
            d, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=path)
            # Close the temporary handle before reopening and renaming it.
            os.close(d)

        except OSError:
            logger.error("Could not create a temporary file in {}".format(path))
            return False

        if self.mode == AppriseStoreMode.HASH:
            try:
                with gzip.open(tmp_path, "wb") as f:
                    # Write our content to disk
                    f.write(content.encode())

            except OSError:
                # Handle failure
                with suppress(OSError):
                    os.remove(tmp_path)
                return False

        else:  # AppriseStoreMode.SIMPLE
            # Update our file extenion based on our fmt
            fmt = SIMPLE_FILE_EXTENSION_MAPPING[fmt]
            try:
                with open(tmp_path, "wb") as f:
                    # Write our content to disk
                    f.write(content.encode())

            except OSError:
                # Handle failure
                with suppress(OSError):
                    os.remove(tmp_path)
                return False

        # If we reach here we successfully wrote the content. We now safely
        # move our configuration into place. The following writes our content
        # to disk
        try:
            shutil.move(tmp_path, os.path.join(path, "{}.{}".format(filename, fmt)))

        except OSError:
            logger.error("Could not move temporary file into place for KEY: {}".format(key))
            with suppress(OSError):
                os.remove(tmp_path)
            return False

        # perform tidy of any other lingering files of other type in case
        # configuration changed from TEXT -> YAML or YAML -> TEXT
        if self.mode == AppriseStoreMode.HASH:
            if self.clear(key, set(apprise.CONFIG_FORMATS) - {fmt}) is False:
                # We couldn't remove an existing entry; clear what we just
                # created
                self.clear(key, {fmt})
                # fail
                return False

        elif self.clear(key, set(SIMPLE_FILE_EXTENSIONS) - {fmt}) is False:
            # We couldn't remove an existing entry; clear what we just
            # created
            self.clear(key, {fmt})
            # fail
            return False

        return True

    def get(self, key):
        """
        Based on the key specified, content is written to disk (compressed)

        key:     is an alphanumeric string needed to write and read back this
                 file being written.

        The function returns a tuple of (content, fmt) where the content
        is the uncompressed content found in the file and fmt is the
        content representation (of type apprise.ConfigFormat).

        If no data was found, then (None, None) is returned.
        """

        if self.mode == AppriseStoreMode.DISABLED:
            # Do nothing
            return (None, "")

        # There isn't a lot of error handling done here as it is presumed most
        # of the checking has been done higher up.

        # First two characters are reserved for cache level directory writing.
        path, filename = self.path(key)

        # prepare our format to return
        fmt = None

        # Test the only possible hashed files we expect to find
        if self.mode == AppriseStoreMode.HASH:
            text_file = os.path.join(path, "{}.{}".format(filename, apprise.ConfigFormat.TEXT.value))
            yaml_file = os.path.join(path, "{}.{}".format(filename, apprise.ConfigFormat.YAML.value))

        else:  # AppriseStoreMode.SIMPLE
            text_file = os.path.join(path, "{}.{}".format(filename, SimpleFileExtension.TEXT))
            yaml_file = os.path.join(path, "{}.{}".format(filename, SimpleFileExtension.YAML))

        if os.path.isfile(text_file):
            fmt = apprise.ConfigFormat.TEXT.value
            path = text_file

        elif os.path.isfile(yaml_file):
            fmt = apprise.ConfigFormat.YAML.value
            path = yaml_file

        else:
            # Not found; we set the fmt to something other than none as
            # an indication for the upstream handling to know that we didn't
            # fail on error
            return (None, "")

        # Initialize our content
        content = None
        if self.mode == AppriseStoreMode.HASH:
            try:
                with gzip.open(path, "rb") as f:
                    # Write our content to disk
                    content = f.read().decode()

            except (OSError, UnicodeDecodeError):
                # Two None values distinguish a read failure from a missing file.
                return (None, None)

        else:  # AppriseStoreMode.SIMPLE
            try:
                with open(path, "rb") as f:
                    # Write our content to disk
                    content = f.read().decode()

            except (OSError, UnicodeDecodeError):
                # Two None values distinguish a read failure from a missing file.
                return (None, None)

        # return our read content
        return (content, fmt)

    def clear(self, key, formats=None):
        """
        Removes any content associated with the specified key should it
        exist.

        None is returned if there was nothing to clear
        True is returned if content was cleared
        False is returned if an internal error prevented data from being
              cleared
        """
        # Default our response None
        response = None

        if self.mode == AppriseStoreMode.DISABLED:
            # Do nothing
            return response

        if formats is None:
            formats = apprise.CONFIG_FORMATS

        path, filename = self.path(key)
        for fmt in formats:
            # Eliminate any existing content if present
            try:
                # Handle failure
                os.remove(
                    os.path.join(
                        path,
                        "{}.{}".format(
                            filename,
                            (fmt if self.mode == AppriseStoreMode.HASH else SIMPLE_FILE_EXTENSION_MAPPING[fmt]),
                        ),
                    )
                )

                # If we reach here, an element was removed
                response = True

            except OSError as e:
                if e.errno != errno.ENOENT:
                    # We were unable to remove the file
                    response = False

        return response

    def path(self, key):
        """
        returns the path and filename content should be written to based on the
        specified key
        """
        if self.mode == AppriseStoreMode.HASH:
            encoded_key = hashlib.sha224(self.salt + key.encode()).hexdigest()
            path = os.path.join(self.root, encoded_key[0:2])
            return (path, encoded_key[2:])

        else:  # AppriseStoreMode.SIMPLE
            return (self.root, key)

    def keys(self):
        """
        Returns a list of keys that are currently stored
        """
        keys = []
        if self.mode != AppriseStoreMode.SIMPLE:
            return keys

        for filename in sorted(os.listdir(self.root)):
            if filename.startswith("."):
                continue
            path = os.path.join(self.root, filename)
            if os.path.isfile(path):
                key_name = os.path.splitext(filename)[0]
                keys.append(key_name)

        return keys

    def auth_path(self, key):
        """Return the directory and hidden lock filename for a key.

        The lock name does not use the config format, so switching between
        text and YAML leaves authentication unchanged.
        """
        path, filename = self.path(key)
        return path, ".{}.lock".format(filename)

    def set_auth(self, key, username, password):
        """Save hashed credentials for a key and report whether it worked.

        Credentials are salted and written atomically through a private
        temporary file. Usernames containing a colon are rejected because
        the stored value uses ``username:password`` format.
        """
        if self.mode == AppriseStoreMode.DISABLED:
            return False

        if username and ":" in username:
            logger.error("Username cannot contain ':' for KEY: {}".format(key))
            return False

        try:
            digest = make_password("{}:{}".format(username, password))

        except (TypeError, UnicodeError):
            logger.error("Could not hash authentication credentials for KEY: {}".format(key))
            return False

        path, filename = self.auth_path(key)
        try:
            os.makedirs(path, exist_ok=True)

        except OSError:
            logger.error("Could not create directory {}".format(path))
            return False

        full_path = os.path.join(path, filename)
        try:
            fd, tmp_path = tempfile.mkstemp(prefix="." + filename, dir=path)

        except OSError:
            logger.error("Could not create a temporary file in {}".format(path))
            return False

        try:
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(
                        dumps(
                            {
                                "version": _AUTH_RECORD_VERSION,
                                "username": username,
                                "digest": digest,
                            },
                            separators=(",", ":"),
                        )
                    )

                os.replace(tmp_path, full_path)

            except OSError:
                logger.error("Could not write authentication for KEY: {}".format(key))
                return False

        finally:
            # A successful replacement consumes the temporary file.
            # Otherwise, remove whatever was left behind.
            with suppress(OSError):
                os.remove(tmp_path)

        return True

    def get_auth_record(self, key):
        """Return ``(username, digest)`` for a key, or ``None``.

        Raises ``AppriseAuthStorageError`` when an existing lock cannot be
        read. Older digest-only locks return ``(None, digest)``.
        """
        path, filename = self.auth_path(key)
        full_path = os.path.join(path, filename)
        try:
            with open(full_path) as f:
                stored = f.read().strip()

        except FileNotFoundError:
            return None

        except (OSError, UnicodeDecodeError) as e:
            logger.error("Could not read authentication for KEY: {} ({})".format(key, e))
            raise AppriseAuthStorageError(str(e)) from e

        # JSON is the current format. Anything else is a legacy digest.
        if not stored.startswith("{"):
            return None, stored

        try:
            record = loads(stored)
            username = record["username"]
            digest = record["digest"]
            if (
                record.get("version") != _AUTH_RECORD_VERSION
                or not isinstance(username, str)
                or not isinstance(digest, str)
            ):
                raise ValueError

        except (KeyError, TypeError, ValueError):
            logger.error("Could not decode authentication for KEY: {}".format(key))
            raise AppriseAuthStorageError("Invalid authentication record") from None

        return username, digest

    def get_auth(self, key):
        """Return a key's credential digest, or ``None`` when unlocked."""
        record = self.get_auth_record(key)
        return None if record is None else record[1]

    def get_auth_username(self, key):
        """Return the saved username when the lock format provides it."""
        record = self.get_auth_record(key)
        return None if record is None else record[0]

    def has_auth(self, key):
        """True when this key currently has (or may have) per-key authentication set.

        Fails closed: a lock file that exists but can't be read is treated
        as protected, not as unprotected.
        """
        try:
            return self.get_auth(key) is not None

        except AppriseAuthStorageError:
            return True

    def verify_auth(self, key, username, password):
        """Safely compare credentials with a key's stored digest.

        A key without stored credentials, or one whose lock file could not
        be read, never matches -- fails closed either way.
        """
        try:
            stored = self.get_auth(key)

        except AppriseAuthStorageError:
            return False

        if stored is None:
            return False

        return check_password("{}:{}".format(username, password), stored)

    def clear_auth(self, key):
        """Remove a key's lock file.

        Returns None when absent, True when removed, and False on error.
        """
        path, filename = self.auth_path(key)
        try:
            os.remove(os.path.join(path, filename))
            return True

        except OSError as e:
            if e.errno != errno.ENOENT:
                return False
            return None

    def prune_unused_locks(self, older_than_seconds):
        """Remove old authentication locks that have no configuration.

        HASH mode is scanned directly because original key names cannot be
        recovered. A concurrent ``/add`` may narrowly overlap pruning after
        the full retention period; this accepted tradeoff avoids disk locking.
        Returns the number of locks removed.
        """
        if self.mode == AppriseStoreMode.DISABLED:
            return 0

        # HASH mode uses a 2-character directory and 54-character filename.
        # SIMPLE mode uses the same key format accepted by the API.
        # These checks leave unrelated files under APPRISE_CONFIG_DIR alone.
        hash_prefix_pattern = re.compile(r"^[0-9a-f]{2}$")
        hash_name_pattern = re.compile(r"^[0-9a-f]{54}$")

        if self.mode == AppriseStoreMode.HASH:
            # Lock files live two levels deep:
            # root/<2-char-prefix>/.<hash-remainder>.lock
            content_extensions = (apprise.ConfigFormat.TEXT.value, apprise.ConfigFormat.YAML.value)
            name_pattern = hash_name_pattern
            lock_dirs = []
            if os.path.isdir(self.root):
                try:
                    # Keep directory metadata so symlinks can be skipped.
                    with os.scandir(self.root) as it:
                        entries = list(it)

                except OSError as e:
                    logger.warning("Could not list directory {} while pruning: {}".format(self.root, e))
                    entries = []

                for entry in entries:
                    if not hash_prefix_pattern.match(entry.name):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            lock_dirs.append(entry.path)

                    except OSError:
                        continue

        else:  # AppriseStoreMode.SIMPLE
            content_extensions = (SimpleFileExtension.TEXT, SimpleFileExtension.YAML)
            name_pattern = CONFIG_KEY_PATTERN
            lock_dirs = [self.root] if os.path.isdir(self.root) else []

        lock_suffix = ".lock"
        now = datetime.now().timestamp()
        pruned = 0
        for directory in lock_dirs:
            try:
                filenames = os.listdir(directory)

            except OSError as e:
                logger.warning("Could not list directory {} while pruning: {}".format(directory, e))
                continue

            for filename in filenames:
                if not (filename.startswith(".") and filename.endswith(lock_suffix)):
                    continue

                # Strip the leading '.' and trailing '.lock'.
                name = filename[1 : -len(lock_suffix)]
                if not name_pattern.match(name):
                    continue

                lock_path = os.path.join(directory, filename)
                try:
                    age = now - os.path.getmtime(lock_path)

                except OSError:
                    continue

                if age < older_than_seconds:
                    continue

                has_content = any(
                    os.path.isfile(os.path.join(directory, "{}.{}".format(name, ext))) for ext in content_extensions
                )
                if has_content:
                    continue

                try:
                    os.remove(lock_path)

                except OSError as e:
                    logger.error("Could not prune stale unused authentication lock: {} ({})".format(name, e))
                    continue

                logger.info("Pruned stale unused authentication lock: {}".format(name))
                pruned += 1

        return pruned


# Initialize our singleton
ConfigCache = AppriseConfigCache(
    settings.APPRISE_CONFIG_DIR,
    salt=settings.SECRET_KEY,
    mode=settings.APPRISE_STATEFUL_MODE,
)


def config_auth_mode(key: str) -> str:
    """Return how a configuration key is protected in this deployment."""
    if not settings.APPRISE_AUTH_REQUIRED:
        return AUTH_MODE_DISABLED
    return AUTH_MODE_SHARED if ConfigCache.has_auth(key) else AUTH_MODE_MASTER


def config_auth_username(key: str):
    """Return the username used in examples and the authentication form."""
    if config_auth_mode(key) == AUTH_MODE_SHARED:
        try:
            return ConfigCache.get_auth_username(key)
        except AppriseAuthStorageError:
            # The global administrator can still replace a damaged lock.
            return None
    return settings.APPRISE_USER


def basic_auth_credentials(request: HttpRequest):
    """Decode Basic Auth into ``(username, password)``.

    Missing or malformed credentials return ``(None, None)``.
    """
    header = request.headers.get("authorization", "")
    # RFC 7235: the auth-scheme token ("Basic") is case-insensitive.
    if header[:6].lower() != "basic ":
        return None, None

    try:
        decoded = base64.b64decode(header[6:]).decode()

    except (binascii.Error, UnicodeDecodeError):
        return None, None

    if ":" not in decoded:
        return None, None

    username, _, password = decoded.partition(":")
    return username, password


# This header keeps configuration keys out of URLs and access logs.
# Validate it here because headers do not pass through Django's URL regex;
# SIMPLE mode also uses the key in a filename.
CONFIG_KEY_HEADER = "X-Apprise-Config-ID"

# Shared configuration-key format for routes, middleware, and headers.
# The unanchored form can be embedded in route patterns.
# Use CONFIG_KEY_PATTERN when validating a complete value.
CONFIG_KEY_REGEX = r"[\w_-]{1,128}"
CONFIG_KEY_PATTERN = re.compile(r"^{}$".format(CONFIG_KEY_REGEX))


def resolve_config_key(request: HttpRequest, key: str) -> str:
    """Return the request's effective configuration key.

    A valid header takes precedence over the URL key. An invalid header
    returns an empty value instead of falling back to the URL.
    """
    header_key = request.headers.get(CONFIG_KEY_HEADER, "").strip()
    if not header_key:
        return key
    return header_key if CONFIG_KEY_PATTERN.match(header_key) else ""


def config_key_header_present_but_invalid(request: HttpRequest) -> bool:
    """Return whether the request supplied an invalid config ID header."""
    header_key = request.headers.get(CONFIG_KEY_HEADER, "").strip()
    return bool(header_key) and not CONFIG_KEY_PATTERN.match(header_key)


# Limit failed authentication attempts for each client and key.
# All keyed routes share this cache-backed limit.
# The default local-memory cache applies the limit per worker.
_AUTH_FAILURE_CACHE_PREFIX = "apprise-auth-fail"
_AUTH_FAILURE_WINDOW_SECONDS = 60
_AUTH_FAILURE_MAX_ATTEMPTS = 20

# Briefly cache successful checks so repeated requests avoid re-hashing.
_AUTH_SUCCESS_CACHE_PREFIX = "apprise-auth-ok"
_AUTH_SUCCESS_CACHE_SECONDS = 30


def _client_ip(request: HttpRequest) -> str:
    """Return the best available client address for throttling.

    Bundled nginx supplies ``X-Real-IP``. Direct development servers fall
    back to ``REMOTE_ADDR``. This value is never an authorization identity.
    """
    return request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR", "unknown")


def _auth_throttle_cache_key(client_ip: str, key: str) -> str:
    # Keep failures isolated to one client and configuration key.
    return "{}:{}:{}".format(_AUTH_FAILURE_CACHE_PREFIX, client_ip, key)


def _auth_rate_limited(client_ip: str, key: str) -> bool:
    """True once this client has failed too many recent authentication attempts against this key."""
    return cache.get(_auth_throttle_cache_key(client_ip, key), 0) >= _AUTH_FAILURE_MAX_ATTEMPTS


def _record_auth_failure(client_ip: str, key: str) -> None:
    """Counts one failed authentication attempt for this client against this key."""
    cache_key = _auth_throttle_cache_key(client_ip, key)
    if not cache.add(cache_key, 1, timeout=_AUTH_FAILURE_WINDOW_SECONDS):
        try:
            cache.incr(cache_key)

        except ValueError:
            # Expired between the add() and incr() calls above; start over.
            cache.add(cache_key, 1, timeout=_AUTH_FAILURE_WINDOW_SECONDS)


def _auth_success_cache_key(client_ip: str, key: str) -> str:
    return "{}:{}:{}".format(_AUTH_SUCCESS_CACHE_PREFIX, client_ip, key)


def _auth_success_fingerprint(username: str, password: str, stored: str) -> str:
    """Fingerprint credentials that already passed the password check.

    Including the stored digest invalidates the cache after a password
    change. ``SECRET_KEY`` prevents a cache value from being forged.
    """
    mac = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        "{}:{}:{}".format(username, password, stored).encode("utf-8"),
        hashlib.sha256,
    )
    return mac.hexdigest()


def key_credentials_ok(request: HttpRequest, key: str, username: str, password: str) -> bool:
    """Check supplied credentials against one configuration lock."""
    try:
        stored = ConfigCache.get_auth(key)

    except AppriseAuthStorageError:
        # Fail closed: an unreadable lock file is treated as protected.
        return False

    if stored is None:
        return False

    client_ip = _client_ip(request)
    if _auth_rate_limited(client_ip, key):
        request.apprise_auth_rate_limited = True
        return False

    success_cache_key = _auth_success_cache_key(client_ip, key)
    fingerprint = _auth_success_fingerprint(username, password, stored)
    if hmac.compare_digest(cache.get(success_cache_key, ""), fingerprint):
        request.apprise_auth_permission = AUTH_MODE_SHARED
        request.apprise_auth_username = username
        return True

    if check_password("{}:{}".format(username, password), stored):
        cache.set(success_cache_key, fingerprint, timeout=_AUTH_SUCCESS_CACHE_SECONDS)
        request.apprise_auth_permission = AUTH_MODE_SHARED
        request.apprise_auth_username = username
        return True

    _record_auth_failure(client_ip, key)
    return False


def key_auth_ok(request: HttpRequest, key: str) -> bool:
    """Return whether a request may use a protected configuration key.

    Global credentials can access every key. A browser session is limited to
    its saved key. API requests check Basic credentials on every request.
    """
    if getattr(request, "globally_authenticated", False):
        request.apprise_auth_permission = AUTH_MODE_MASTER
        return True

    # The signed browser cookie never grants access to a different key.
    if (
        getattr(request, "apprise_auth_permission", AUTH_MODE_DISABLED) == AUTH_MODE_SHARED
        and getattr(request, "apprise_web_auth_key", None) == key
    ):
        return True

    # Turning authentication off restores the original open behavior.
    if not settings.APPRISE_AUTH_REQUIRED:
        request.apprise_auth_permission = AUTH_MODE_DISABLED
        return True

    username, password = basic_auth_credentials(request)
    if username is None:
        return False
    return key_credentials_ok(request, key, username, password)


def _web_auth_proof(mode: str, key=None):
    """Return a private fingerprint of the credential backing a web login."""
    if mode == AUTH_MODE_MASTER:
        credential = settings.APPRISE_BASIC_AUTH_TOKEN
    elif mode == AUTH_MODE_SHARED and key:
        try:
            credential = ConfigCache.get_auth(key)
        except AppriseAuthStorageError:
            return None
    else:
        return None

    if not credential:
        return None
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        credential.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def set_web_auth_cookie(response, request, mode: str, username: str, key=None) -> None:
    """Store a signed, browser-session login without saving a password."""
    payload = {
        "mode": mode,
        "username": username,
        "key": key if mode == AUTH_MODE_SHARED else None,
        "proof": _web_auth_proof(mode, key),
    }
    # A trusted HTTPS origin also covers TLS terminated by a reverse proxy.
    secure = request.is_secure() or "https://{}".format(request.get_host()).lower() in settings.APPRISE_TRUSTED_ORIGINS
    response.set_cookie(
        WEB_AUTH_COOKIE,
        signing.dumps(payload, salt=_WEB_AUTH_SIGNING_SALT, compress=True),
        httponly=True,
        secure=secure,
        samesite="Lax",
        path=settings.BASE_URL or "/",
    )


def clear_web_auth_cookie(response) -> None:
    """Remove the browser login while leaving API credentials untouched."""
    response.delete_cookie(
        WEB_AUTH_COOKIE,
        path=settings.BASE_URL or "/",
        samesite="Lax",
    )


def restore_web_auth(request: HttpRequest, requested_key=None, allow_shared_without_key=False) -> bool:
    """Restore a valid signed browser login onto the current request."""
    value = request.COOKIES.get(WEB_AUTH_COOKIE)
    if not value:
        return False

    try:
        payload = signing.loads(value, salt=_WEB_AUTH_SIGNING_SALT)
    except (signing.BadSignature, TypeError):
        return False

    mode = payload.get("mode") if isinstance(payload, dict) else None
    username = payload.get("username") if isinstance(payload, dict) else None
    key = payload.get("key") if isinstance(payload, dict) else None
    proof = payload.get("proof") if isinstance(payload, dict) else None
    if not isinstance(username, str) or not isinstance(proof, str):
        return False

    if mode == AUTH_MODE_SHARED:
        # A shared login may use its key or a small set of general GUI pages.
        if requested_key and key != requested_key:
            return False
        if not requested_key and not allow_shared_without_key:
            return False

    expected = _web_auth_proof(mode, key)
    if expected is None or not hmac.compare_digest(proof, expected):
        return False

    request.apprise_auth_permission = mode
    request.apprise_auth_username = username
    request.apprise_web_auth_key = key
    request.globally_authenticated = mode == AUTH_MODE_MASTER
    return True


def apply_global_filters():
    #
    # Apply Any Global Filters (if identified)
    #
    if settings.APPRISE_ALLOW_SERVICES:
        alphanum_re = re.compile(r"^(?P<name>[a-z][a-z0-9]+)", re.IGNORECASE)
        entries = [
            alphanum_re.match(x).group("name").lower()
            for x in re.split(r"[ ,]+", settings.APPRISE_ALLOW_SERVICES)
            if alphanum_re.match(x)
        ]

        N_MGR.enable_only(*entries)

    elif settings.APPRISE_DENY_SERVICES:
        alphanum_re = re.compile(r"^(?P<name>[a-z][a-z0-9]+)", re.IGNORECASE)
        entries = [
            alphanum_re.match(x).group("name").lower()
            for x in re.split(r"[ ,]+", settings.APPRISE_DENY_SERVICES)
            if alphanum_re.match(x)
        ]

        N_MGR.disable(*entries)


def gen_unique_config_id():
    """
    Generates a unique configuration ID
    """
    # our key to use
    h = hashlib.sha256()
    h.update(datetime.now().strftime("%Y%m%d%H%M%S%f").encode("utf-8"))
    h.update(settings.SECRET_KEY.encode("utf-8"))
    return h.hexdigest()


def send_webhook(payload):
    """POST a mapping or JSON chunk iterator to the webhook."""

    # Prepare HTTP Headers
    headers = {
        "User-Agent": "Apprise-API",
        "Content-Type": "application/json",
    }

    try:
        if not apprise.utils.parse.VALID_URL_RE.match(settings.APPRISE_WEBHOOK_URL).group("schema"):
            raise AttributeError()

    except (AttributeError, TypeError):
        logger.warning("The Apprise Webhook Result URL is not a valid web based URI")
        return

    # Parse our URL
    results = apprise.URLBase.parse_url(settings.APPRISE_WEBHOOK_URL)
    if not results:
        logger.warning("The Apprise Webhook Result URL is not parseable")
        return

    if results["schema"] not in ("http", "https"):
        logger.warning("The Apprise Webhook Result URL is not using the HTTP protocol")
        return

    # Load our URL
    base = apprise.URLBase(**results)

    # Our Query String Dictionary; we use this to track arguments
    # specified that aren't otherwise part of this class
    params = {k: v for k, v in results.get("qsd", {}).items() if k not in base.template_args}

    # Prepare both forms inside the protected block below.
    body = None
    data = None

    try:
        # A mapping remains convenient for small callers and existing tests.
        data = dumps(payload) if isinstance(payload, Mapping) else None

        if data is None:
            # Build large webhook bodies on disk instead of joining every log.
            body = tempfile.TemporaryFile(mode="w+b")  # noqa: SIM115

            for chunk in payload:
                # Convert text chunks before writing to the binary file.
                value = chunk.encode("utf-8") if isinstance(chunk, str) else chunk

                # Never send JSON after an incomplete write.
                if body.write(value) != len(value):
                    raise OSError("Incomplete webhook temporary-file write.")

            # Rewind so the request reads from the beginning.
            body.seek(0)
            data = body

        response = requests.post(
            base.request_url,
            # Small mappings use text; chunked results use the file.
            data=data,
            params=params,
            headers=headers,
            auth=base.request_auth,
            verify=base.verify_certificate,
            timeout=base.request_timeout,
        )

        # Report HTTP failures even when the connection itself succeeded.
        if not 200 <= response.status_code < 300:
            logger.warning(
                "The Apprise Webhook Result URL returned HTTP %d: %s",
                response.status_code,
                base.url(privacy=True),
            )

    except requests.RequestException as e:
        logger.warning("A Connection error occurred sending the Apprise Webhook results to %s.", base.url(privacy=True))
        logger.debug("Socket Exception: %s", str(e))

    except (OSError, TypeError, ValueError) as e:
        # Preparation failures must not change the notification result.
        logger.warning(
            "The Apprise Webhook results could not be prepared for %s.",
            base.url(privacy=True),
        )
        logger.debug("Webhook preparation exception: %s", str(e))

    finally:
        if body is not None:
            try:
                # TemporaryFile removes the buffered webhook when closed.
                body.close()

            except (OSError, ValueError) as e:
                logger.warning("The Apprise Webhook temporary file could not be closed.")
                logger.debug("Webhook cleanup exception: %s", str(e))

    return


def healthcheck(lazy=True):
    """
    Runs a status check on the data and returns the statistics
    """

    # Some status variables we can flip
    response = {
        "persistent_storage": False,
        "can_write_config": False,
        "can_write_attach": False,
        "details": [],
    }

    if not (settings.APPRISE_STATEFUL_MODE == AppriseStoreMode.DISABLED or settings.APPRISE_CONFIG_LOCK):
        # Update our Configuration Check Block
        path = os.path.join(ConfigCache.root, ".tmp_hc")
        if lazy:
            try:
                modify_date = datetime.fromtimestamp(os.path.getmtime(path))
                delta = (datetime.now() - modify_date).total_seconds()
                if delta <= 30.00:  # 30s
                    response["can_write_config"] = True

            except FileNotFoundError:
                # No worries... continue with below testing
                pass

            except OSError:
                # Permission Issue or something else likely
                # We can take an early exit
                response["details"].append("CONFIG_PERMISSION_ISSUE")

        if not (response["can_write_config"] or "CONFIG_PERMISSION_ISSUE" in response["details"]):
            try:
                os.makedirs(ConfigCache.root, exist_ok=True)
                if touch(path):
                    # Toggle our status
                    response["can_write_config"] = True

                else:
                    # We can take an early exit as there is already a permission issue detected
                    response["details"].append("CONFIG_PERMISSION_ISSUE")

            except OSError:
                # We can take an early exit as there is already a permission issue detected
                response["details"].append("CONFIG_PERMISSION_ISSUE")

    if settings.APPRISE_ATTACH_SIZE > 0:
        # Test our ability to access write attachments

        # Update our Configuration Check Block
        path = os.path.join(settings.APPRISE_ATTACH_DIR, ".tmp_hc")
        if lazy:
            try:
                modify_date = datetime.fromtimestamp(os.path.getmtime(path))
                delta = (datetime.now() - modify_date).total_seconds()
                if delta <= 30.00:  # 30s
                    response["can_write_attach"] = True

            except FileNotFoundError:
                # No worries... continue with below testing
                pass

            except OSError:
                # We can take an early exit as there is already a permission issue detected
                response["details"].append("ATTACH_PERMISSION_ISSUE")

        if not (response["can_write_attach"] or "ATTACH_PERMISSION_ISSUE" in response["details"]):
            # No lazy mode set or content require a refresh
            try:
                os.makedirs(settings.APPRISE_ATTACH_DIR, exist_ok=True)
                if touch(path):
                    # Toggle our status
                    response["can_write_attach"] = True

                else:
                    # We can take an early exit as there is already a permission issue detected
                    response["details"].append("ATTACH_PERMISSION_ISSUE")

            except OSError:
                # We can take an early exit
                response["details"].append("ATTACH_PERMISSION_ISSUE")

    if settings.APPRISE_STORAGE_DIR:
        #
        # Persistent Storage Check
        #
        store = apprise.PersistentStore(
            path=settings.APPRISE_STORAGE_DIR,
            namespace="tmp_hc",
            mode=settings.APPRISE_STORAGE_MODE,
        )

        if store.mode != settings.APPRISE_STORAGE_MODE:
            # Persistent storage not as configured
            response["details"].append("STORE_PERMISSION_ISSUE")

        elif store.mode != apprise.PersistentStoreMode.MEMORY:
            # G
            path = settings.APPRISE_STORAGE_DIR
            if lazy:
                try:
                    modify_date = datetime.fromtimestamp(os.path.getmtime(path))
                    delta = (datetime.now() - modify_date).total_seconds()
                    if delta <= 30.00:  # 30s
                        response["persistent_storage"] = True

                except OSError:
                    # No worries... continue with below testing
                    pass

            if not (store.set("foo", "bar") and store.flush()):
                # No persistent store
                response["details"].append("STORE_PERMISSION_ISSUE")
            else:
                # Toggle our status
                response["persistent_storage"] = True

            # Clear our test
            store.clear("foo")

    if not response["details"]:
        response["details"].append("OK")

    return response
