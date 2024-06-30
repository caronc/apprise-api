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
from datetime import datetime

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


class AttachmentPayload(object):
    """
    Defines the supported Attachment Payload Types
    """
    # BASE64
    BASE64 = 'base64'

    # URL request
    URL = 'url'


STORE_MODES = (
    AppriseStoreMode.HASH,
    AppriseStoreMode.SIMPLE,
    AppriseStoreMode.DISABLED,
)

# Access our Attachment Manager Singleton
A_MGR = apprise.manager_attachment.AttachmentManager()

# Access our Notification Manager Singleton
N_MGR = apprise.manager_plugins.NotificationManager()


class Attachment(A_MGR['file']):
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
            try:
                os.remove(self._path)
            except FileNotFoundError:
                # no problem
                pass


class HTTPAttachment(A_MGR['http']):
    """
    A Light Weight Attachment Object for Auto-cleanup that wraps the Apprise
    Web Attachments
    """

    def __init__(self, filename, delete=True, **kwargs):
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
            raise ValueError('Could not create directory {}'.format(
                settings.APPRISE_ATTACH_DIR))

        try:
            d, self._path = tempfile.mkstemp(dir=settings.APPRISE_ATTACH_DIR)
            # Close our file descriptor
            os.close(d)

        except FileNotFoundError:
            raise ValueError(
                'Could not prepare {} attachment in {}'.format(
                    filename, settings.APPRISE_ATTACH_DIR))

        # Prepare our item
        super().__init__(name=filename, **kwargs)

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
            try:
                os.remove(self._path)
            except FileNotFoundError:
                # no problem
                pass


def touch(fname, mode=0o666, dir_fd=None, **kwargs):
    """
    Acts like a Linux touch and updates a file with a current timestamp
    """
    flags = os.O_CREAT | os.O_APPEND
    try:
        with os.fdopen(os.open(fname, flags=flags, mode=mode, dir_fd=dir_fd)) as f:
            os.utime(f.fileno() if os.utime in os.supports_fd else fname,
                     dir_fd=None if os.supports_fd else dir_fd, **kwargs)

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

    # Attachment Count
    count = sum([
        0 if not isinstance(attachment_payload, (set, tuple, list))
        else len(attachment_payload),
        0 if not isinstance(files_request, dict) else len(files_request),
    ])

    if isinstance(attachment_payload, (dict, str, bytes)):
        # Convert and adjust counter
        attachment_payload = (attachment_payload, )
        count += 1

    if settings.APPRISE_ATTACH_SIZE <= 0:
        raise ValueError("Attachment support has been disabled")

    if settings.APPRISE_MAX_ATTACHMENTS > 0 and count > settings.APPRISE_MAX_ATTACHMENTS:
        raise ValueError(
            "There is a maximum of %d attachments" %
            settings.APPRISE_MAX_ATTACHMENTS)

    if isinstance(attachment_payload, (tuple, list, set)):
        for no, entry in enumerate(attachment_payload, start=1):
            if isinstance(entry, (str, bytes)):
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

                except AttributeError:
                    # not a string that was provided
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
            if isinstance(entry, str):
                if not entry.strip():
                    # ignore blank entries; these can come from using the
                    # api/website and submitting without an element defined.
                    # There is no need have a bad outcome; just decrement our
                    # counter and move along
                    count -= 1
                    continue

                if not re.match(r'^https?://.+', entry[:10], re.I):
                    # We failed to retrieve the product
                    raise ValueError(
                        "Failed to load attachment "
                        "%d (not web request): %s" % (no, entry))

                attachment = HTTPAttachment(
                    filename, **A_MGR['http'].parse_url(entry))
                if not attachment:
                    # We failed to retrieve the attachment
                    raise ValueError(
                        "Failed to retrieve attachment %d: %s" % (no, entry))

            else:   # web, base64 or raw
                attachment = Attachment(filename)
                try:
                    with open(attachment.path, 'wb') as f:
                        # Write our content to disk
                        if isinstance(entry, dict) and \
                                AttachmentPayload.BASE64 in entry:
                            # BASE64
                            f.write(
                                base64.b64decode(
                                    entry[AttachmentPayload.BASE64]))

                        elif isinstance(entry, dict) and \
                                AttachmentPayload.URL in entry:

                            attachment = HTTPAttachment(
                                filename, **A_MGR['http']
                                .parse_url(entry[AttachmentPayload.URL]))
                            if not attachment:
                                # We failed to retrieve the attachment
                                raise ValueError(
                                    "Failed to retrieve attachment "
                                    "%d: %s" % (no, entry))

                        elif isinstance(entry, bytes):
                            # RAW
                            f.write(entry)

                        else:
                            raise ValueError(
                                "Invalid filetype was provided for "
                                "attachment %s" % filename)

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
                if settings.APPRISE_ATTACH_SIZE > 0 and \
                        attachment.size > settings.APPRISE_ATTACH_SIZE:
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
                    "An invalid filename was provided for attachment %d" % no)

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
            if settings.APPRISE_ATTACH_SIZE > 0 and \
                    attachment.size > settings.APPRISE_ATTACH_SIZE:
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

        N_MGR.enable_only(*entries)

    elif settings.APPRISE_DENY_SERVICES:
        alphanum_re = re.compile(
            r'^(?P<name>[a-z][a-z0-9]+)', re.IGNORECASE)
        entries = \
            [alphanum_re.match(x).group('name').lower()
             for x in re.split(r'[ ,]+', settings.APPRISE_DENY_SERVICES)
             if alphanum_re.match(x)]

        N_MGR.disable(*entries)


def gen_unique_config_id():
    """
    Generates a unique configuration ID
    """
    # our key to use
    h = hashlib.sha256()
    h.update(datetime.now().strftime('%Y%m%d%H%M%S%f').encode('utf-8'))
    h.update(settings.SECRET_KEY.encode('utf-8'))
    return h.hexdigest()


def send_webhook(payload):
    """
    POST our webhook results
    """

    # Prepare HTTP Headers
    headers = {
        'User-Agent': 'Apprise-API',
        'Content-Type': 'application/json',
    }

    try:
        if not apprise.utils.VALID_URL_RE.match(settings.APPRISE_WEBHOOK_URL).group('schema'):
            raise AttributeError()

    except (AttributeError, TypeError):
        logger.warning(
            'The Apprise Webhook Result URL is not a valid web based URI')
        return

    # Parse our URL
    results = apprise.URLBase.parse_url(settings.APPRISE_WEBHOOK_URL)
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


def healthcheck(lazy=True):
    """
    Runs a status check on the data and returns the statistics
    """

    # Some status variables we can flip
    response = {
        'can_write_config': False,
        'can_write_attach': False,
        'details': [],
    }

    if not (settings.APPRISE_STATEFUL_MODE == AppriseStoreMode.DISABLED or settings.APPRISE_CONFIG_LOCK):
        # Update our Configuration Check Block
        path = os.path.join(ConfigCache.root, '.tmp_hc')
        if lazy:
            try:
                modify_date = datetime.fromtimestamp(os.path.getmtime(path))
                delta = (datetime.now() - modify_date).total_seconds()
                if delta <= 30.00:  # 30s
                    response['can_write_config'] = True

            except FileNotFoundError:
                # No worries... continue with below testing
                pass

            except OSError:
                # Permission Issue or something else likely
                # We can take an early exit
                response['details'].append('CONFIG_PERMISSION_ISSUE')

        if not (response['can_write_config'] or 'CONFIG_PERMISSION_ISSUE' in response['details']):
            try:
                os.makedirs(ConfigCache.root, exist_ok=True)
                if touch(path):
                    # Toggle our status
                    response['can_write_config'] = True

                else:
                    # We can take an early exit as there is already a permission issue detected
                    response['details'].append('CONFIG_PERMISSION_ISSUE')

            except OSError:
                # We can take an early exit as there is already a permission issue detected
                response['details'].append('CONFIG_PERMISSION_ISSUE')

    if settings.APPRISE_ATTACH_SIZE > 0:
        # Test our ability to access write attachments

        # Update our Configuration Check Block
        path = os.path.join(settings.APPRISE_ATTACH_DIR, '.tmp_hc')
        if lazy:
            try:
                modify_date = datetime.fromtimestamp(os.path.getmtime(path))
                delta = (datetime.now() - modify_date).total_seconds()
                if delta <= 30.00:  # 30s
                    response['can_write_attach'] = True

            except FileNotFoundError:
                # No worries... continue with below testing
                pass

            except OSError:
                # We can take an early exit as there is already a permission issue detected
                response['details'].append('ATTACH_PERMISSION_ISSUE')

        if not (response['can_write_attach'] or 'ATTACH_PERMISSION_ISSUE' in response['details']):
            # No lazy mode set or content require a refresh
            try:
                os.makedirs(settings.APPRISE_ATTACH_DIR, exist_ok=True)
                if touch(path):
                    # Toggle our status
                    response['can_write_attach'] = True

                else:
                    # We can take an early exit as there is already a permission issue detected
                    response['details'].append('ATTACH_PERMISSION_ISSUE')

            except OSError:
                # We can take an early exit
                response['details'].append('ATTACH_PERMISSION_ISSUE')

    if not response['details']:
        response['details'].append('OK')

    return response
