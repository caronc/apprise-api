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
from django.shortcuts import render
from django.http import HttpResponse
from django.http import JsonResponse
from django.views import View
from django.conf import settings
from django.utils.html import escape
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.gzip import gzip_page
from django.utils.translation import gettext_lazy as _
from django.core.serializers.json import DjangoJSONEncoder

from .utils import ConfigCache
from .forms import AddByUrlForm
from .forms import AddByConfigForm
from .forms import NotifyForm
from .forms import NotifyByUrlForm
from .forms import CONFIG_FORMATS
from .forms import AUTO_DETECT_CONFIG_KEYWORD

import apprise
import json
import re

# import the logging library
import logging

# Get an instance of a logger
logger = logging.getLogger('django')

# Content-Type Parsing
# application/x-www-form-urlencoded
# application/x-www-form-urlencoded
# multipart/form-data
MIME_IS_FORM = re.compile(
    r'(multipart|application)/(x-www-)?form-(data|urlencoded)', re.I)

# Support JSON formats
# text/json
# text/x-json
# application/json
# application/x-json
MIME_IS_JSON = re.compile(
    r'(text|application)/(x-)?json', re.I)


class JSONEncoder(DjangoJSONEncoder):
    """
    A wrapper to the DjangoJSONEncoder to support
    sets() (converting them to lists).
    """
    def default(self, obj):
        if isinstance(obj, set):
            return list(obj)
        return super().default(obj)


class ResponseCode(object):
    """
    These codes are based on those provided by the requests object
    """
    okay = 200
    no_content = 204
    bad_request = 400
    no_access = 403
    not_found = 404
    method_not_allowed = 405
    method_not_accepted = 406
    failed_dependency = 424
    internal_server_error = 500


class WelcomeView(View):
    """
    A simple welcome/index page
    """
    template_name = 'welcome.html'

    def get(self, request):
        return render(request, self.template_name, {})


@method_decorator(never_cache, name='dispatch')
class ConfigView(View):
    """
    A Django view used to manage configuration
    """
    template_name = 'config.html'

    def get(self, request, key):
        """
        Handle a GET request
        """
        return render(request, self.template_name, {
            'key': key,
            'form_url': AddByUrlForm(),
            'form_cfg': AddByConfigForm(),
            'form_notify': NotifyForm(),
        })


@method_decorator(never_cache, name='dispatch')
class AddView(View):
    """
    A Django view used to store Apprise configuration
    """

    def post(self, request, key):
        """
        Handle a POST request
        """
        # Detect the format our response should be in
        json_response = MIME_IS_JSON.match(request.content_type) is not None

        if settings.APPRISE_CONFIG_LOCK:
            # General Access Control
            msg = _('The site has been configured to deny this request.')
            status = ResponseCode.no_access
            return HttpResponse(msg, status=status) \
                if not json_response else JsonResponse({
                    'error': msg,
                },
                encoder=JSONEncoder,
                safe=False,
                status=status,
            )

        # our content
        content = {}
        if MIME_IS_FORM.match(request.content_type):
            content = {}
            form = AddByConfigForm(request.POST)
            if form.is_valid():
                content.update(form.cleaned_data)

            form = AddByUrlForm(request.POST)
            if form.is_valid():
                content.update(form.cleaned_data)

        elif json_response:
            # Prepare our default response
            try:
                # load our JSON content
                content = json.loads(request.body.decode('utf-8'))

            except (AttributeError, ValueError):
                # could not parse JSON response...
                return JsonResponse({
                        'error': _('Invalid JSON specified.'),
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=ResponseCode.bad_request,
                )

        if not content:
            # No information was posted
            msg = _('The message format is not supported.')
            status = ResponseCode.bad_request
            return HttpResponse(msg, status=status) \
                if not json_response else JsonResponse({
                    'error': msg,
                },
                encoder=JSONEncoder,
                safe=False,
                status=status,
            )

        # Create ourselves an apprise object to work with
        a_obj = apprise.Apprise()
        if 'urls' in content:
            # Load our content
            a_obj.add(content['urls'])
            if not len(a_obj):
                # No URLs were loaded
                msg = _('No valid URLs were found.')
                status = ResponseCode.bad_request
                return HttpResponse(msg, status=status) \
                    if not json_response else JsonResponse({
                        'error': msg,
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )

            if not ConfigCache.put(
                    key, '\r\n'.join([s.url() for s in a_obj]),
                    apprise.ConfigFormat.TEXT):

                msg = _('The configuration could not be saved.')
                status = ResponseCode.internal_server_error
                return HttpResponse(msg, status=status) \
                    if not json_response else JsonResponse({
                        'error': msg,
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )

        elif 'config' in content:
            fmt = content.get('format', '').lower()
            if fmt not in [i[0] for i in CONFIG_FORMATS]:
                # Format must be one supported by apprise
                msg = _('The format specified is invalid.')
                status = ResponseCode.bad_request
                return HttpResponse(msg, status=status) \
                    if not json_response else JsonResponse({
                        'error': msg,
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )

            # prepare our apprise config object
            ac_obj = apprise.AppriseConfig()

            if fmt == AUTO_DETECT_CONFIG_KEYWORD:
                # By setting format to None, it is automatically detected from
                # within the add_config() call
                fmt = None

            # Load our configuration
            if not ac_obj.add_config(content['config'], format=fmt):
                # The format could not be detected
                msg = _('The configuration format could not be detected.')
                status = ResponseCode.bad_request
                return HttpResponse(msg, status=status) \
                    if not json_response else JsonResponse({
                        'error': msg,
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )

            # Add our configuration
            a_obj.add(ac_obj)

            if not len(a_obj):
                # No specified URL(s) were loaded due to
                # mis-configuration on the caller's part
                msg = _('No valid URL(s) were specified.')
                status = ResponseCode.bad_request
                return HttpResponse(msg, status=status) \
                    if not json_response else JsonResponse({
                        'error': msg,
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )

            if not ConfigCache.put(
                    key, content['config'], fmt=ac_obj[0].config_format):
                # Something went very wrong; return 500
                msg = _('An error occured saving configuration.')
                status = ResponseCode.internal_server_error

                return HttpResponse(msg, status=status) \
                    if not json_response else JsonResponse({
                        'error': msg,
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )

        else:
            # No configuration specified; we're done
            msg = _('No configuration specified.')
            status = ResponseCode.bad_request
            return HttpResponse(msg, status=status) \
                if not json_response else JsonResponse({
                       'error': msg,
                   },
                   encoder=JSONEncoder,
                   safe=False,
                   status=status,
               )

        # If we reach here; we successfully loaded the configuration so we can
        # go ahead and write it to disk and alert our caller of the success.
        return HttpResponse(
            _('Successfully saved configuration.'),
            status=ResponseCode.okay,
        )


@method_decorator(never_cache, name='dispatch')
class DelView(View):
    """
    A Django view for removing content associated with a key
    """
    def post(self, request, key):
        """
        Handle a POST request
        """
        # Detect the format our response should be in
        json_response = MIME_IS_JSON.match(request.content_type) is not None

        if settings.APPRISE_CONFIG_LOCK:
            # General Access Control
            msg = _('The site has been configured to deny this request.')
            status = ResponseCode.no_access
            return HttpResponse(msg, status=status) \
                if not json_response else JsonResponse({
                    'error': msg,
                },
                encoder=JSONEncoder,
                safe=False,
                status=status,
            )

        # Clear the key
        result = ConfigCache.clear(key)
        if result is None:
            msg = _('There was no configuration to remove.')
            status = ResponseCode.no_content
            return HttpResponse(msg, status=status) \
                if not json_response else JsonResponse({
                    'error': msg,
                },
                encoder=JSONEncoder,
                safe=False,
                status=status,
            )

        elif result is False:
            # There was a failure at the os level
            msg = _('The configuration could not be removed.')
            status = ResponseCode.internal_server_error
            return HttpResponse(msg, status=status) \
                if not json_response else JsonResponse({
                    'error': msg,
                },
                encoder=JSONEncoder,
                safe=False,
                status=status,
            )

        # Removed content
        return HttpResponse(
            _('Successfully removed configuration.'),
            status=ResponseCode.okay,
        )


@method_decorator((gzip_page, never_cache), name='dispatch')
class GetView(View):
    """
    A Django view used to retrieve previously stored Apprise configuration
    """
    def post(self, request, key):
        """
        Handle a POST request
        """

        # Detect the format our response should be in
        json_response = MIME_IS_JSON.match(request.content_type) is not None

        if settings.APPRISE_CONFIG_LOCK:
            # General Access Control
            return HttpResponse(
                _('The site has been configured to deny this request.'),
                status=ResponseCode.no_access,
            ) if not json_response else JsonResponse({
                    'error':
                    _('The site has been configured to deny this request.')
                },
                encoder=JSONEncoder,
                safe=False,
                status=ResponseCode.no_access,
            )

        config, format = ConfigCache.get(key)
        if config is None:
            # The returned value of config and format tell a rather cryptic
            # story; this portion could probably be updated in the future.
            # but for now it reads like this:
            #   config == None and format == None: We had an internal error
            #   config == None and format != None: we simply have no data
            #   config != None: we simply have no data
            if format is not None:
                # no content to return
                return HttpResponse(
                    _('There was no configuration found.'),
                    status=ResponseCode.no_content,
                ) if not json_response else JsonResponse({
                        'error': _('There was no configuration found.')
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=ResponseCode.no_content,
                )

            # Something went very wrong; return 500
            msg = _('An error occured accessing configuration.')
            status = ResponseCode.internal_server_error
            return HttpResponse(msg, status=status) \
                if not json_response else JsonResponse({
                    'error': msg,
                },
                encoder=JSONEncoder,
                safe=False,
                status=status,
            )

        # Our configuration was retrieved; now our response varies on whether
        # we are a YAML configuration or a TEXT based one.  This allows us to
        # be compatible with those using the AppriseConfig() library or the
        # reference to it through the --config (-c) option in the CLI.
        content_type = 'text/yaml; charset=utf-8' \
            if format == apprise.ConfigFormat.YAML \
            else 'text/html; charset=utf-8'

        # Return our retrieved content
        return HttpResponse(
            config,
            content_type=content_type,
            status=ResponseCode.okay,
        ) if not json_response else JsonResponse({
                'format': format,
                'config': config,
            },
            encoder=JSONEncoder,
            safe=False,
            status=ResponseCode.okay,
        )


@method_decorator((gzip_page, never_cache), name='dispatch')
class NotifyView(View):
    """
    A Django view for sending a notification
    """
    def post(self, request, key):
        """
        Handle a POST request
        """
        # Detect the format our response should be in
        json_response = MIME_IS_JSON.match(request.content_type) is not None

        # our content
        content = {}
        if MIME_IS_FORM.match(request.content_type):
            content = {}
            form = NotifyForm(request.POST)
            if form.is_valid():
                content.update(form.cleaned_data)

        elif json_response:
            # Prepare our default response
            try:
                # load our JSON content
                content = json.loads(request.body.decode('utf-8'))

            except (AttributeError, ValueError):
                # could not parse JSON response...
                return JsonResponse(
                    _('Invalid JSON specified.'),
                    encoder=JSONEncoder,
                    safe=False,
                    status=ResponseCode.bad_request)

        if not content:
            # We could not handle the Content-Type
            msg = _('The message format is not supported.')
            status = ResponseCode.bad_request
            return HttpResponse(msg, status=status) \
                if not json_response else JsonResponse({
                    'error': msg,
                },
                encoder=JSONEncoder,
                safe=False,
                status=status,
            )

        # Some basic error checking
        if not content.get('body') or \
                content.get('type', apprise.NotifyType.INFO) \
                not in apprise.NOTIFY_TYPES:

            msg = _('An invalid payload was specified.')
            status = ResponseCode.bad_request
            return HttpResponse(msg, status=status) \
                if not json_response else JsonResponse({
                    'error': msg,
                },
                encoder=JSONEncoder,
                safe=False,
                status=status,
            )

        # Acquire our body format (if identified)
        body_format = content.get('format', apprise.NotifyFormat.TEXT)
        if body_format and body_format not in apprise.NOTIFY_FORMATS:
            msg = _('An invalid body input format was specified.')
            status = ResponseCode.bad_request
            return HttpResponse(msg, status=status) \
                if not json_response else JsonResponse({
                    'error': msg,
                },
                encoder=JSONEncoder,
                safe=False,
                status=status,
            )

        # If we get here, we have enough information to generate a notification
        # with.
        config, format = ConfigCache.get(key)
        if config is None:
            # The returned value of config and format tell a rather cryptic
            # story; this portion could probably be updated in the future.
            # but for now it reads like this:
            #   config == None and format == None: We had an internal error
            #   config == None and format != None: we simply have no data
            #   config != None: we simply have no data
            if format is not None:
                # no content to return
                msg = _('There was no configuration found.')
                status = ResponseCode.no_content
                return HttpResponse(msg, status=status) \
                    if not json_response else JsonResponse({
                        'error': msg,
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )

            # Something went very wrong; return 500
            msg = _('An error occured accessing configuration.')
            status = ResponseCode.internal_server_error
            return HttpResponse(msg, status=status) \
                if not json_response else JsonResponse({
                    'error': msg,
                },
                encoder=JSONEncoder,
                safe=False,
                status=status,
            )

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

            for plugin in set(apprise.plugins.SCHEMA_MAP.values()):
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
                    apprise.plugins.SCHEMA_MAP[name].enabled = False

                except KeyError:
                    logger.warning(
                        'APPRISE_DENY_SERVICES plugin %s:// was not found -'
                        ' ignoring.', name)

        # Prepare our keyword arguments (to be passed into an AppriseAsset
        # object)
        kwargs = {}

        if body_format:
            # Store our defined body format
            kwargs['body_format'] = body_format

        # Acquire our recursion count (if defined)
        try:
            recursion = \
                int(request.headers.get('X-Apprise-Recursion-Count', 0))

            if recursion < 0:
                # We do not accept negative numbers
                raise TypeError("Invalid Recursion Value")

            if recursion > settings.APPRISE_RECURSION_MAX:
                return HttpResponse(
                    _('The recursion limit has been reached.'),
                    status=ResponseCode.method_not_accepted)

            # Store our recursion value for our AppriseAsset() initialization
            kwargs['_recursion'] = recursion

        except (TypeError, ValueError):
            return HttpResponse(
                _('An invalid recursion value was specified.'),
                status=ResponseCode.bad_request)

        # Acquire our unique identifier (if defined)
        uid = request.headers.get('X-Apprise-ID', '').strip()
        if uid:
            kwargs['_uid'] = uid

        # Prepare ourselves a default Asset
        asset = None if not body_format else \
            apprise.AppriseAsset(body_format=body_format)

        # Prepare our apprise object
        a_obj = apprise.Apprise(asset=asset)

        # Create an apprise config object
        ac_obj = apprise.AppriseConfig()

        # Load our configuration
        ac_obj.add_config(config, format=format)

        # Add our configuration
        a_obj.add(ac_obj)

        # Our return content type can be controlled by the Accept keyword
        # If it includes /* or /html somewhere then we return html, otherwise
        # we return the logs as they're processed in their text format.
        # The HTML response type has a bit of overhead where as it's not
        # the case with text/plain
        content_type = \
            'text/html' if re.search(r'text\/(\*|html)',
                                     request.headers.get('Accept', ''),
                                     re.IGNORECASE) \
            else 'text/plain'

        # Acquire our log level from headers if defined, otherwise use
        # the global one set in the settings
        level = request.headers.get(
            'X-Apprise-Log-Level',
            settings.LOGGING['loggers']['apprise']['level']).upper()

        # Initialize our response object
        response = None

        if level in ('CRITICAL', 'ERROR' 'WARNING', 'INFO', 'DEBUG'):
            level = getattr(apprise.logging, level)

            esc = '<!!-!ESC!-!!>'
            fmt = '<li class="log_%(levelname)s">' \
                '<div class="log_time">%(asctime)s</div>' \
                '<div class="log_level">%(levelname)s</div>' \
                f'<div class="log_msg">{esc}%(message)s{esc}</div></li>' \
                if content_type == 'text/html' else \
                settings.LOGGING['formatters']['standard']['format']

            # Now specify our format (and over-ride the default):
            with apprise.LogCapture(level=level, fmt=fmt) as logs:
                # Perform our notification at this point
                result = a_obj.notify(
                    content.get('body'),
                    title=content.get('title', ''),
                    notify_type=content.get('type', apprise.NotifyType.INFO),
                    tag=content.get('tag'),
                )

            if content_type == 'text/html':
                # Iterate over our entries so that we can prepare to escape
                # things to be presented as HTML
                esc = re.escape(esc)
                entries = re.findall(
                    r'(?P<head><li .+?){}(?P<to_escape>.*?)'
                    r'{}(?P<tail>.+li>$)(?=$|<li .+{})'.format(
                        esc, esc, esc), logs.getvalue(),
                    re.DOTALL)

                # Wrap logs in `<ul>` tag and escape our message body:
                response = '<ul class="logs">{}</ul>'.format(
                    ''.join([e[0] + escape(e[1]) + e[2] for e in entries]))

            else:  # content_type == 'text/plain'
                response = logs.getvalue()

        else:
            # Perform our notification at this point without logging
            result = a_obj.notify(
                content.get('body'),
                title=content.get('title', ''),
                notify_type=content.get('type', apprise.NotifyType.INFO),
                tag=content.get('tag'),
            )

        if not result:
            # If at least one notification couldn't be sent; change up
            # the response to a 424 error code
            msg = _('One or more notification could not be sent.')
            status = ResponseCode.failed_dependency
            return HttpResponse(response if response else msg, status=status) \
                if not json_response else JsonResponse({
                    'error': msg,
                },
                encoder=JSONEncoder,
                safe=False,
                status=status,
            )

        # Return our retrieved content
        return HttpResponse(
            response if response is not None else
            _('Notification(s) sent.'),
            content_type=content_type,
            status=ResponseCode.okay,
        )


@method_decorator((gzip_page, never_cache), name='dispatch')
class StatelessNotifyView(View):
    """
    A Django view for sending a stateless notification
    """
    def post(self, request):
        """
        Handle a POST request
        """
        # our content
        content = {}
        if MIME_IS_FORM.match(request.content_type):
            content = {}
            form = NotifyByUrlForm(request.POST)
            if form.is_valid():
                content.update(form.cleaned_data)

        elif MIME_IS_JSON.match(request.content_type):
            # Prepare our default response
            try:
                # load our JSON content
                content = json.loads(request.body.decode('utf-8'))

            except (AttributeError, ValueError):
                # could not parse JSON response...
                return HttpResponse(
                    _('Invalid JSON specified.'),
                    status=ResponseCode.bad_request)

        if not content:
            # We could not handle the Content-Type
            return HttpResponse(
                _('The message format is not supported.'),
                status=ResponseCode.bad_request)

        if not content.get('urls') and settings.APPRISE_STATELESS_URLS:
            # fallback to settings.APPRISE_STATELESS_URLS if no urls were
            # defined
            content['urls'] = settings.APPRISE_STATELESS_URLS

        # Some basic error checking
        if not content.get('body') or \
                content.get('type', apprise.NotifyType.INFO) \
                not in apprise.NOTIFY_TYPES:

            return HttpResponse(
                _('An invalid payload was specified.'),
                status=ResponseCode.bad_request)

        # Acquire our body format (if identified)
        body_format = content.get('format', apprise.NotifyFormat.TEXT)
        if body_format and body_format not in apprise.NOTIFY_FORMATS:
            return HttpResponse(
                _('An invalid (body) format was specified.'),
                status=ResponseCode.bad_request)

        # Prepare our keyword arguments (to be passed into an AppriseAsset
        # object)
        kwargs = {}

        if body_format:
            # Store our defined body format
            kwargs['body_format'] = body_format

        # Acquire our recursion count (if defined)
        try:
            recursion = \
                int(request.headers.get('X-Apprise-Recursion-Count', 0))

            if recursion < 0:
                # We do not accept negative numbers
                raise TypeError("Invalid Recursion Value")

            if recursion > settings.APPRISE_RECURSION_MAX:
                return HttpResponse(
                    _('The recursion limit has been reached.'),
                    status=ResponseCode.method_not_accepted)

            # Store our recursion value for our AppriseAsset() initialization
            kwargs['_recursion'] = recursion

        except (TypeError, ValueError):
            return HttpResponse(
                _('An invalid recursion value was specified.'),
                status=ResponseCode.bad_request)

        # Acquire our unique identifier (if defined)
        uid = request.headers.get('X-Apprise-ID', '').strip()
        if uid:
            kwargs['_uid'] = uid

        # Prepare ourselves a default Asset
        asset = None if not body_format else \
            apprise.AppriseAsset(body_format=body_format)

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

            for plugin in set(apprise.plugins.SCHEMA_MAP.values()):
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
                    apprise.plugins.SCHEMA_MAP[name].enabled = False

                except KeyError:
                    logger.warning(
                        'APPRISE_DENY_SERVICES plugin %s:// was not found -'
                        ' ignoring.', name)

        # Prepare our apprise object
        a_obj = apprise.Apprise(asset=asset)

        # Add URLs
        a_obj.add(content.get('urls'))
        if not len(a_obj):
            return HttpResponse(
                _('There was no services to notify.'),
                status=ResponseCode.no_content,
            )

        # Perform our notification at this point
        result = a_obj.notify(
            content.get('body'),
            title=content.get('title', ''),
            notify_type=content.get('type', apprise.NotifyType.INFO),
            tag='all',
        )

        if not result:
            # If at least one notification couldn't be sent; change up the
            # response to a 424 error code
            return HttpResponse(
                _('One or more notification could not be sent.'),
                status=ResponseCode.failed_dependency)

        # Return our retrieved content
        return HttpResponse(
            _('Notification(s) sent.'),
            status=ResponseCode.okay,
        )


@method_decorator((gzip_page, never_cache), name='dispatch')
class JsonUrlView(View):
    """
    A Django view that lists all loaded tags and URLs for a given key
    """
    def get(self, request, key):
        """
        Handle a POST request
        """

        # Now build our tag response that identifies all of the tags
        # and the URL's they're associated with
        #  {
        #    "tags": ["tag1', "tag2", "tag3"],
        #    "urls": [
        #       {
        #          "url": "windows://",
        #          "tags": [],
        #       },
        #       {
        #          "url": "mailto://user:pass@gmail.com"
        #          "tags": ["tag1", "tag2", "tag3"]
        #       }
        #    ]
        #  }
        response = {
            'tags': set(),
            'urls': [],
        }

        # Privacy flag
        # Support 'yes', '1', 'true', 'enable', 'active', and +
        privacy = settings.APPRISE_CONFIG_LOCK or \
            request.GET.get('privacy', 'no')[0] in (
                'a', 'y', '1', 't', 'e', '+')

        # Optionally filter on tags. Use comma to identify more then one
        tag = request.GET.get('tag', 'all')

        config, format = ConfigCache.get(key)
        if config is None:
            # The returned value of config and format tell a rather cryptic
            # story; this portion could probably be updated in the future.
            # but for now it reads like this:
            #   config == None and format == None: We had an internal error
            #   config == None and format != None: we simply have no data
            #   config != None: we simply have no data
            if format is not None:
                # no content to return
                return JsonResponse(
                    response,
                    encoder=JSONEncoder,
                    safe=False,
                    status=ResponseCode.no_content,
                )

            # Something went very wrong; return 500
            response['error'] = _('There was no configuration found.')
            return JsonResponse(
                response,
                encoder=JSONEncoder,
                safe=False,
                status=ResponseCode.internal_server_error,
            )

        # Prepare our apprise object
        a_obj = apprise.Apprise()

        # Create an apprise config object
        ac_obj = apprise.AppriseConfig()

        # Load our configuration
        ac_obj.add_config(config, format=format)

        # Add our configuration
        a_obj.add(ac_obj)

        for notification in a_obj.find(tag):
            # Set Notification
            response['urls'].append({
                'url': notification.url(privacy=privacy),
                'tags': notification.tags,
            })

            # Store Tags
            response['tags'] |= notification.tags

        # Return our retrieved content
        return JsonResponse(
            response,
            encoder=JSONEncoder,
            safe=False,
            status=ResponseCode.okay
        )
