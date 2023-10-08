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
import re
import binascii
import os
import tempfile
import shutil
import gzip
import apprise
import hashlib
import errno
import base64
import requests
from json import dumps
from django.conf import settings

# import the logging library
import logging

# Get an instance of a logger
logger = logging.getLogger('django')


class AppriseStoreMode(object):
    """
    Defines the store modes of configuration
    """
    # This is the default option. Content is cached and written by
    # it's key
    HASH = 'hash'

    # Content is written straight to disk using it's key
    # there is nothing further done
    SIMPLE = 'simple'

    # When set to disabled; stateful functionality is disabled
    DISABLED = 'disabled'


STORE_MODES = (
    AppriseStoreMode.HASH,
    AppriseStoreMode.SIMPLE,
    AppriseStoreMode.DISABLED,
)


class Attachment(apprise.attachment.AttachFile):
    """
    A Light Weight Attachment Object for Auto-cleanup that wraps the Apprise
    Attachments
    """

    def __init__(self, filename, path=None, delete=True):
        """
        Initialize our attachment
        """
        self._filename = filename
        try:
            os.makedirs(settings.APPRISE_ATTACH_DIR, exist_ok=True)

        except OSError:
            # Permission error
            raise ValueError('Could not create directory {}'.format(
                settings.APPRISE_ATTACH_DIR))

        if not path:
            try:
                d, path = tempfile.mkstemp(dir=settings.APPRISE_ATTACH_DIR)
                # Close our file descriptor
                os.close(d)

            except FileNotFoundError:
                raise ValueError(
                    'Could not prepare {} attachment in {}'.format(
                        filename, settings.APPRISE_ATTACH_DIR))

        self._path = path
        self.delete = delete

        # Prepare our item
        super().__init__(path=self._path, name=filename)

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
        if self.delete:
            try:
                os.remove(self._path)
            except FileNotFoundError:
                # no problem
                pass


def parse_attachments(attachment_payload, files_request):
    """
    Takes the payload provided in a `/notify` call and extracts the
    attachments out of it.

    Content is written to a temporary directory until the garbage
    collection kicks in.
    """
    attachments = []

    # Attachment Count
    count = sum([
        0 if not isinstance(attachment_payload, (tuple, list))
        else len(attachment_payload),
        0 if not isinstance(files_request, dict) else len(files_request),
    ])

    if settings.APPRISE_MAX_ATTACHMENTS > 0 and \
            count > settings.APPRISE_MAX_ATTACHMENTS:
        raise ValueError(
            "There is a maximum of %d attachments" %
            settings.APPRISE_MAX_ATTACHMENTS)

    if isinstance(attachment_payload, (tuple, list)):
        for no, entry in enumerate(attachment_payload, start=1):

            if isinstance(entry, str):
                filename = "attachment.%.3d" % no

            elif isinstance(entry, dict):
                try:
                    filename = entry.get("filename", "").strip()

                    # Max filename size is 250
                    if len(filename) > 250:
                        raise ValueError(
                            "The filename associated with attachment "
                            "%d is too long" % no)

                    elif not filename:
                        filename = "attachment.%.3d" % no

                except TypeError:
                    raise ValueError(
                        "An invalid filename was provided for attachment %d" %
                        no)

            else:
                # you must pass in a base64 string, or a dict containing our
                # required parameters
                raise ValueError(
                    "An invalid filename was provided for attachment %d" % no)

            #
            # Prepare our Attachment
            #
            attachment = Attachment(filename)

            try:
                with open(attachment.path, 'wb') as f:
                    # Write our content to disk
                    f.write(base64.b64decode(entry["base64"]))

            except binascii.Error:
                # The file ws not base64 encoded
                raise ValueError(
                    "Invalid filecontent was provided for attachment %s" %
                    filename)

            except OSError:
                raise ValueError(
                    "Could not write attachment %s to disk" % filename)

            #
            # Some Validation
            #
            if settings.APPRISE_MAX_ATTACHMENT_SIZE > 0 and \
                    attachment.size > settings.APPRISE_MAX_ATTACHMENT_SIZE:
                raise ValueError(
                    "attachment %s's filesize is to large" % filename)

            # Add our attachment
            attachments.append(attachment)

    #
    # Now handle the request.FILES
    #
    if isinstance(files_request, dict):
        for no, (key, meta) in enumerate(
                files_request.items(), start=len(attachments) + 1):

            try:
                # Filetype is presumed to be of base class
                # django.core.files.UploadedFile
                filename = meta.name.strip()

                # Max filename size is 250
                if len(filename) > 250:
                    raise ValueError(
                        "The filename associated with attachment "
                        "%d is too long" % no)

                elif not filename:
                    filename = "attachment.%.3d" % no

            except (AttributeError, TypeError):
                raise ValueError(
                    "An invalid filename was provided for attachment %d" %
                    no)

            #
            # Prepare our Attachment
            #
            attachment = Attachment(filename)
            try:
                with open(attachment.path, 'wb') as f:
                    # Write our content to disk
                    f.write(meta.read())

            except OSError:
                raise ValueError(
                    "Could not write attachment %s to disk" % filename)

            #
            # Some Validation
            #
            if settings.APPRISE_MAX_ATTACHMENT_SIZE > 0 and \
                    attachment.size > settings.APPRISE_MAX_ATTACHMENT_SIZE:
                raise ValueError(
                    "attachment %s's filesize is to large" % filename)

            # Add our attachment
            attachments.append(attachment)

    return attachments


class SimpleFileExtension(object):
    """
    Defines the simple file exension lookups
    """
    # Simple Configuration file
    TEXT = 'cfg'

    # YAML Configuration file
    YAML = 'yml'


SIMPLE_FILE_EXTENSION_MAPPING = {
    apprise.ConfigFormat.TEXT: SimpleFileExtension.TEXT,
    apprise.ConfigFormat.YAML: SimpleFileExtension.YAML,
    SimpleFileExtension.TEXT: SimpleFileExtension.TEXT,
    SimpleFileExtension.YAML: SimpleFileExtension.YAML,
}

SIMPLE_FILE_EXTENSIONS = (SimpleFileExtension.TEXT, SimpleFileExtension.YAML)


class AppriseConfigCache(object):
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
            logger.error(
                'APPRISE_STATEFUL_MODE {} is not supported; '
                'reverted to {}.'.format(mode, self.mode))

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
            logger.error('Could not create directory {}'.format(path))
            return False

        # Write our file to a temporary file
        d, tmp_path = tempfile.mkstemp(suffix='.tmp', dir=path)
        # Close the file handle provided by mkstemp()
        # We're reopening it, and it can't be renamed while open on Windows
        os.close(d)

        if self.mode == AppriseStoreMode.HASH:
            try:
                with gzip.open(tmp_path, 'wb') as f:
                    # Write our content to disk
                    f.write(content.encode())

            except OSError:
                # Handle failure
                os.remove(tmp_path)
                return False

        else:  # AppriseStoreMode.SIMPLE
            # Update our file extenion based on our fmt
            fmt = SIMPLE_FILE_EXTENSION_MAPPING[fmt]
            try:
                with open(tmp_path, 'wb') as f:
                    # Write our content to disk
                    f.write(content.encode())

            except OSError:
                # Handle failure
                os.remove(tmp_path)
                return False

        # If we reach here we successfully wrote the content. We now safely
        # move our configuration into place. The following writes our content
        # to disk
        shutil.move(tmp_path, os.path.join(
            path, '{}.{}'.format(filename, fmt)))

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
            return (None, '')

        # There isn't a lot of error handling done here as it is presumed most
        # of the checking has been done higher up.

        # First two characters are reserved for cache level directory writing.
        path, filename = self.path(key)

        # prepare our format to return
        fmt = None

        # Test the only possible hashed files we expect to find
        if self.mode == AppriseStoreMode.HASH:
            text_file = os.path.join(
                path, '{}.{}'.format(filename, apprise.ConfigFormat.TEXT))
            yaml_file = os.path.join(
                path, '{}.{}'.format(filename, apprise.ConfigFormat.YAML))

        else:  # AppriseStoreMode.SIMPLE
            text_file = os.path.join(
                path, '{}.{}'.format(filename, SimpleFileExtension.TEXT))
            yaml_file = os.path.join(
                path, '{}.{}'.format(filename, SimpleFileExtension.YAML))

        if os.path.isfile(text_file):
            fmt = apprise.ConfigFormat.TEXT
            path = text_file

        elif os.path.isfile(yaml_file):
            fmt = apprise.ConfigFormat.YAML
            path = yaml_file

        else:
            # Not found; we set the fmt to something other than none as
            # an indication for the upstream handling to know that we didn't
            # fail on error
            return (None, '')

        # Initialize our content
        content = None
        if self.mode == AppriseStoreMode.HASH:
            try:
                with gzip.open(path, 'rb') as f:
                    # Write our content to disk
                    content = f.read().decode()

            except OSError:
                # all none return means to let upstream know we had a hard
                # failure
                return (None, None)

        else:  # AppriseStoreMode.SIMPLE
            try:
                with open(path, 'rb') as f:
                    # Write our content to disk
                    content = f.read().decode()

            except OSError:
                # all none return means to let upstream know we had a hard
                # failure
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
                os.remove(os.path.join(path, '{}.{}'.format(
                    filename,
                    fmt if self.mode == AppriseStoreMode.HASH
                    else SIMPLE_FILE_EXTENSION_MAPPING[fmt])))

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

        else:   # AppriseStoreMode.SIMPLE
            return (self.root, key)


# Initialize our singleton
ConfigCache = AppriseConfigCache(
    settings.APPRISE_CONFIG_DIR, salt=settings.SECRET_KEY,
    mode=settings.APPRISE_STATEFUL_MODE)


def apply_global_filters():
    #
    # Apply Any Global Filters (if identified)
    #
    if settings.APPRISE_ALLOW_SERVICES:
        alphanum_re = re.compile(
            r'^(?P<name>[a-z][a-z0-9]+)', re.IGNORECASE)
        entries = \
            [alphanum_re.match(x).group('name').lower()
             for x in re.split(r'[ ,]+', settings.APPRISE_ALLOW_SERVICES)
             if alphanum_re.match(x)]

        for plugin in set(apprise.common.NOTIFY_SCHEMA_MAP.values()):
            if entries:
                # Get a list of the current schema's associated with
                # a given plugin
                schemas = set(apprise.plugins.details(plugin)
                              ['tokens']['schema']['values'])

                # Check what was defined and see if there is a hit
                for entry in entries:
                    if entry in schemas:
                        # We had a hit; we're done
                        break

                if entry in schemas:
                    entries.remove(entry)
                    # We can keep this plugin enabled and move along to the
                    # next one...
                    continue

            # if we reach here, we have to block our plugin
            plugin.enabled = False

        for entry in entries:
            # Generate some noise for those who have bad configurations
            logger.warning(
                'APPRISE_ALLOW_SERVICES plugin %s:// was not found - '
                'ignoring.', entry)

    elif settings.APPRISE_DENY_SERVICES:
        alphanum_re = re.compile(
            r'^(?P<name>[a-z][a-z0-9]+)', re.IGNORECASE)
        entries = \
            [alphanum_re.match(x).group('name').lower()
             for x in re.split(r'[ ,]+', settings.APPRISE_DENY_SERVICES)
             if alphanum_re.match(x)]

        for name in entries:
            try:
                # Force plugin to be disabled
                apprise.common.NOTIFY_SCHEMA_MAP[name].enabled = False

            except KeyError:
                logger.warning(
                    'APPRISE_DENY_SERVICES plugin %s:// was not found -'
                    ' ignoring.', name)


def send_webhook(payload):
    """
    POST our webhook results
    """

    # Prepare HTTP Headers
    headers = {
        'User-Agent': 'Apprise-API',
        'Content-Type': 'application/json',
    }

    if not apprise.utils.VALID_URL_RE.match(
            settings.APPRISE_WEBHOOK_RESULTS_URL):
        logger.warning(
            'The Apprise Webhook Result URL is not a valid web based URI')
        return

    # Parse our URL
    results = apprise.URLBase.parse_url(settings.APPRISE_WEBHOOK_RESULTS_URL)
    if not results:
        logger.warning('The Apprise Webhook Result URL is not parseable')
        return

    if results['schema'] not in ('http', 'https'):
        logger.warning(
            'The Apprise Webhook Result URL is not using the HTTP protocol')
        return

    # Load our URL
    base = apprise.URLBase(**results)

    # Our Query String Dictionary; we use this to track arguments
    # specified that aren't otherwise part of this class
    params = {k: v for k, v in results.get('qsd', {}).items()
              if k not in base.template_args}

    try:
        requests.post(
            base.request_url,
            data=dumps(payload),
            params=params,
            headers=headers,
            auth=base.request_auth,
            verify=base.verify_certificate,
            timeout=base.request_timeout,
        )

    except requests.RequestException as e:
        logger.warning(
            'A Connection error occurred sending the Apprise Webhook '
            'results to %s.' % base.url(privacy=True))
        logger.debug('Socket Exception: %s' % str(e))

    return
