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

from .utils import parse_attachments
from .utils import ConfigCache
from .utils import apply_global_filters
from .utils import send_webhook
from .forms import AddByUrlForm
from .forms import AddByConfigForm
from .forms import NotifyForm
from .forms import NotifyByUrlForm
from .forms import CONFIG_FORMATS
from .forms import AUTO_DETECT_CONFIG_KEYWORD

import logging
import apprise
import json
import re

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

# Tags separated by space , &, or + are and'ed together
# Tags separated by commas (even commas wrapped in spaces) are "or'ed" together
# We start with a regular expression used to clean up provided tags
TAG_VALIDATION_RE = re.compile(r'^\s*[a-z0-9\s| ,_-]+\s*$', re.IGNORECASE)

# In order to separate our tags only by comma's or '|' entries found
TAG_DETECT_RE = re.compile(
    r'\s*([a-z0-9\s_&+-]+)(?=$|\s*[|,]\s*[a-z0-9\s&+_-|,])', re.I)

# Break apart our objects anded together
TAG_AND_DELIM_RE = re.compile(r'[\s&+]+')

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

        elif isinstance(obj, apprise.AppriseLocale.LazyTranslation):
            return str(obj)

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
        default_key = 'KEY'
        key = request.GET.get('key', default_key).strip()
        return render(request, self.template_name, {
            'secure': request.scheme[-1].lower() == 's',
            'key': key if key else default_key,
        })


@method_decorator((gzip_page, never_cache), name='dispatch')
class DetailsView(View):
    """
    A Django view used to list all supported endpoints
    """

    template_name = 'details.html'

    def get(self, request):
        """
        Handle a GET request
        """

        # Detect the format our response should be in
        json_response = \
            MIME_IS_JSON.match(
                request.content_type
                if request.content_type
                else request.headers.get(
                    'accept', request.headers.get(
                        'content-type', ''))) is not None

        # Show All flag
        # Support 'yes', '1', 'true', 'enable', 'active', and +
        show_all = request.GET.get('all', 'no')[0].lower() in (
            'a', 'y', '1', 't', 'e', '+')

        # Our status
        status = ResponseCode.okay

        #
        # Apply Any Global Filters (if identified)
        #
        apply_global_filters()

        # Create an Apprise Object
        a_obj = apprise.Apprise()

        # Load our details
        details = a_obj.details(show_disabled=show_all)

        # Sort our result set
        details['schemas'] = sorted(
            details['schemas'], key=lambda i: str(i['service_name']).upper())

        # Return our content
        return render(request, self.template_name, {
            'show_all': show_all,
            'details': details,
        }, status=status) if not json_response else \
            JsonResponse(
                details,
                encoder=JSONEncoder,
                safe=False,
                status=status)


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
        json_response = \
            MIME_IS_JSON.match(
                request.content_type
                if request.content_type
                else request.headers.get(
                    'accept', request.headers.get(
                        'content-type', ''))) is not None

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
        json_response = \
            MIME_IS_JSON.match(
                request.content_type
                if request.content_type
                else request.headers.get(
                    'accept', request.headers.get(
                        'content-type', ''))) is not None

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
        json_response = \
            MIME_IS_JSON.match(
                request.content_type
                if request.content_type
                else request.headers.get(
                    'accept', request.headers.get(
                        'content-type', ''))) is not None

        if settings.APPRISE_CONFIG_LOCK:
            # General Access Control
            msg = _('The site has been configured to deny this request.')
            status = ResponseCode.no_access
            return HttpResponse(msg, status=status) \
                if not json_response else JsonResponse(
                    {'error': msg},
                    encoder=JSONEncoder,
                    safe=False,
                    status=status)

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
                    if not json_response else JsonResponse(
                        {'error': msg},
                        encoder=JSONEncoder,
                        safe=False,
                        status=status)

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
            'config': config},
            encoder=JSONEncoder,
            safe=False,
            status=ResponseCode.okay,
        )


@method_decorator((gzip_page, never_cache), name='dispatch')
class NotifyView(View):
    """
    A Django view for sending a notification in a stateful manner
    """
    def post(self, request, key):
        """
        Handle a POST request
        """
        # Detect the format our response should be in
        json_response = \
            MIME_IS_JSON.match(
                request.content_type
                if request.content_type
                else request.headers.get(
                    'accept', request.headers.get(
                        'content-type', ''))) is not None

        # our content
        content = {}
        if MIME_IS_FORM.match(request.content_type):
            form = NotifyForm(data=request.POST, files=request.FILES)
            if form.is_valid():
                content.update(form.cleaned_data)

        elif json_response:
            # Prepare our default response
            try:
                # load our JSON content
                content = json.loads(request.body.decode('utf-8'))

            except (AttributeError, ValueError):
                # could not parse JSON response...
                logger.warning(
                    'NOTIFY - %s - Invalid JSON Payload provided',
                    request.META['REMOTE_ADDR'])

                return JsonResponse(
                    _('Invalid JSON provided.'),
                    encoder=JSONEncoder,
                    safe=False,
                    status=ResponseCode.bad_request)

        if not content:
            # We could not handle the Content-Type
            logger.warning(
                'NOTIFY - %s - Invalid FORM Payload provided',
                request.META['REMOTE_ADDR'])

            msg = _('Bad FORM Payload provided.')
            status = ResponseCode.bad_request
            return HttpResponse(msg, status=status) \
                if not json_response else JsonResponse({
                    'error': msg,
                },
                encoder=JSONEncoder,
                safe=False,
                status=status
            )

        # Handle Attachments
        attach = None
        if 'attachment' in content or request.FILES:
            try:
                attach = parse_attachments(
                    content.get('attachment'), request.FILES)

            except (TypeError, ValueError):
                # Invalid entry found in list
                logger.warning(
                    'NOTIFY - %s - Bad attachment specified',
                    request.META['REMOTE_ADDR'])

                return HttpResponse(
                    _('Bad attachment'),
                    status=ResponseCode.bad_request)

        #
        # Allow 'tag' value to be specified as part of the URL parameters
        # if not found otherwise defined.
        #
        tag = content.get('tag', content.get('tags'))
        if not tag:
            # Allow GET parameter over-rides
            if 'tag' in request.GET:
                tag = request.GET['tag']

            elif 'tags' in request.GET:
                tag = request.GET['tags']

        # Validation - Tag Logic:
        # "TagA"                        : TagA
        # "TagA, TagB"                  : TagA OR TagB
        # "TagA TagB"                  : TagA AND TagB
        # "TagA TagC, TagB"             : (TagA AND TagC) OR TagB
        # ['TagA', 'TagB']              : TagA OR TagB
        # [('TagA', 'TagC'), 'TagB']    : (TagA AND TagC) OR TagB
        # [('TagB', 'TagC')]            : TagB AND TagC
        if tag:
            if isinstance(tag, (list, set, tuple)):
                # Assign our tags as they were provided
                content['tag'] = tag

            elif isinstance(tag, str):
                if not TAG_VALIDATION_RE.match(tag):
                    # Invalid entry found in list
                    logger.warning(
                        'NOTIFY - %s - Ignored invalid tag specified '
                        '(type %s): %s', request.META['REMOTE_ADDR'],
                        str(type(tag)), str(tag)[:12])

                    msg = _('Unsupported characters found in tag definition.')
                    status = ResponseCode.bad_request
                    return HttpResponse(msg, status=status) \
                        if not json_response else JsonResponse({
                            'error': msg,
                        },
                        encoder=JSONEncoder,
                        safe=False,
                        status=status,
                    )

                # If we get here, our specified tag was valid
                tags = []
                for _tag in TAG_DETECT_RE.findall(tag):
                    tag = _tag.strip()
                    if not tag:
                        continue

                    # Disect our results
                    group = TAG_AND_DELIM_RE.split(tag)
                    if len(group) > 1:
                        tags.append(tuple(group))
                    else:
                        tags.append(tag)

                # Assign our tags
                content['tag'] = tags

            else:  # Could be int, float or some other unsupported type
                logger.warning(
                    'NOTIFY - %s - Ignored invalid tag specified (type %s): '
                    '%s', request.META['REMOTE_ADDR'],
                    str(type(tag)), str(tag)[:12])

                msg = _('Unsupported characters found in tag definition.')
                status = ResponseCode.bad_request
                return HttpResponse(msg, status=status) \
                    if not json_response else JsonResponse({
                        'error': msg,
                    },
                    encoder=JSONEncoder,
                    safe=False,
                    status=status,
                )
        #
        # Allow 'format' value to be specified as part of the URL
        # parameters if not found otherwise defined.
        #
        if not content.get('format') and 'format' in request.GET:
            content['format'] = request.GET['format']

        #
        # Allow 'type' value to be specified as part of the URL parameters
        # if not found otherwise defined.
        #
        if not content.get('type') and 'type' in request.GET:
            content['type'] = request.GET['type']

        #
        # Allow 'title' value to be specified as part of the URL parameters
        # if not found otherwise defined.
        #
        if not content.get('title') and 'title' in request.GET:
            content['title'] = request.GET['title']

        # Some basic error checking
        if not content.get('body') and not attach or \
                content.get('type', apprise.NotifyType.INFO) \
                not in apprise.NOTIFY_TYPES:

            logger.warning(
                'NOTIFY - %s - Payload lacks minimum requirements',
                request.META['REMOTE_ADDR'])

            return HttpResponse(msg, status=status) \
                if not json_response else JsonResponse({
                    'error': _('Payload lacks minimum requirements.'),
                },
                encoder=JSONEncoder,
                safe=False,
                status=ResponseCode.bad_request,
            )

        # Acquire our body format (if identified)
        body_format = content.get('format', apprise.NotifyFormat.TEXT)
        if body_format and body_format not in apprise.NOTIFY_FORMATS:
            logger.warning(
                'NOTIFY - %s - Format parameter contains an unsupported '
                'value (%s)', request.META['REMOTE_ADDR'], str(body_format))

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
                logger.debug(
                    'NOTIFY - %s - Empty configuration found using KEY: %s',
                    request.META['REMOTE_ADDR'], key)
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
            logger.error(
                'NOTIFY - %s - I/O error accessing configuration '
                'using KEY: %s', request.META['REMOTE_ADDR'], key)
            return HttpResponse(msg, status=status) \
                if not json_response else JsonResponse({
                    'error': msg,
                },
                encoder=JSONEncoder,
                safe=False,
                status=status,
            )

        # Prepare our keyword arguments (to be passed into an AppriseAsset
        # object)
        kwargs = {
            'plugin_paths': settings.APPRISE_PLUGIN_PATHS,
        }

        if body_format:
            # Store our defined body format
            kwargs['body_format'] = body_format

        # Acquire our recursion count (if defined)
        recursion = request.headers.get('X-Apprise-Recursion-Count', 0)
        try:
            recursion = int(recursion)

            if recursion < 0:
                # We do not accept negative numbers
                raise TypeError("Invalid Recursion Value")

            if recursion > settings.APPRISE_RECURSION_MAX:
                logger.warning(
                    'NOTIFY - %s - Recursion limit reached (%d > %d)',
                    request.META['REMOTE_ADDR'], recursion,
                    settings.APPRISE_RECURSION_MAX)
                return HttpResponse(
                    _('The recursion limit has been reached.'),
                    status=ResponseCode.method_not_accepted)

            # Store our recursion value for our AppriseAsset() initialization
            kwargs['_recursion'] = recursion

        except (TypeError, ValueError):
            logger.warning(
                'NOTIFY - %s - Invalid recursion value (%s) provided',
                request.META['REMOTE_ADDR'], str(recursion))
            return HttpResponse(
                _('An invalid recursion value was specified.'),
                status=ResponseCode.bad_request)

        # Acquire our unique identifier (if defined)
        uid = request.headers.get('X-Apprise-ID', '').strip()
        if uid:
            kwargs['_uid'] = uid

        #
        # Apply Any Global Filters (if identified)
        #
        apply_global_filters()

        # Prepare ourselves a default Asset
        asset = apprise.AppriseAsset(**kwargs)

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
        if level not in (
                'CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG', 'TRACE'):
            level = settings.LOGGING['loggers']['apprise']['level'].upper()

        # Convert level to it's integer value
        if level == 'CRITICAL':
            level = logging.CRITICAL

        elif level == 'ERROR':
            level = logging.ERROR

        elif level == 'WARNING':
            level = logging.WARNING

        elif level == 'INFO':
            level = logging.INFO

        elif level == 'DEBUG':
            level = logging.DEBUG

        elif level == 'TRACE':
            level = logging.DEBUG - 1

        # Initialize our response object
        response = None

        esc = '<!!-!ESC!-!!>'

        # Format is only updated if the content_type is html
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
                attach=attach,
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

        if settings.APPRISE_WEBHOOK_URL:
            webhook_payload = {
                'source': request.META['REMOTE_ADDR'],
                'status': 0 if result else 1,
                'output': response,
            }

            # Send our webhook (pass or fail)
            send_webhook(webhook_payload)

        if not result:
            # If at least one notification couldn't be sent; change up
            # the response to a 424 error code
            msg = _('One or more notification could not be sent.')
            status = ResponseCode.failed_dependency
            logger.warning(
                'NOTIFY - %s - One or more notifications not '
                'sent%s using KEY: %s', request.META['REMOTE_ADDR'],
                '' if not tag else f' (Tags: {tag})', key)
            return HttpResponse(response if response else msg, status=status) \
                if not json_response else JsonResponse({
                    'error': msg,
                },
                encoder=JSONEncoder,
                safe=False,
                status=status,
            )

        logger.info(
            'NOTIFY - %s - Proccessed%s KEY: %s', request.META['REMOTE_ADDR'],
            '' if not tag else f' (Tags: {tag}),', key)

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
            form = NotifyByUrlForm(request.POST, request.FILES)
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
        kwargs = {
            'plugin_paths': settings.APPRISE_PLUGIN_PATHS,
        }

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

        #
        # Apply Any Global Filters (if identified)
        #
        apply_global_filters()

        # Prepare ourselves a default Asset
        asset = apprise.AppriseAsset(**kwargs)

        # Prepare our apprise object
        a_obj = apprise.Apprise(asset=asset)

        # Add URLs
        a_obj.add(content.get('urls'))
        if not len(a_obj):
            return HttpResponse(
                _('There was no services to notify.'),
                status=ResponseCode.no_content,
            )

        # Handle Attachments
        attach = None
        if 'attachment' in content or request.FILES:
            try:
                attach = parse_attachments(
                    content.get('attachment'), request.FILES)

            except (TypeError, ValueError):
                return HttpResponse(
                    _('Bad attachment'),
                    status=ResponseCode.bad_request)

        # Acquire our log level from headers if defined, otherwise use
        # the global one set in the settings
        level = request.headers.get(
            'X-Apprise-Log-Level',
            settings.LOGGING['loggers']['apprise']['level']).upper()
        if level not in (
                'CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG', 'TRACE'):
            level = settings.LOGGING['loggers']['apprise']['level'].upper()

        # Convert level to it's integer value
        if level == 'CRITICAL':
            level = logging.CRITICAL

        elif level == 'ERROR':
            level = logging.ERROR

        elif level == 'WARNING':
            level = logging.WARNING

        elif level == 'INFO':
            level = logging.INFO

        elif level == 'DEBUG':
            level = logging.DEBUG

        elif level == 'TRACE':
            level = logging.DEBUG - 1

        if settings.APPRISE_WEBHOOK_URL:
            fmt = settings.LOGGING['formatters']['standard']['format']
            with apprise.LogCapture(level=level, fmt=fmt) as logs:
                # Perform our notification at this point
                result = a_obj.notify(
                    content.get('body'),
                    title=content.get('title', ''),
                    notify_type=content.get('type', apprise.NotifyType.INFO),
                    tag='all',
                    attach=attach,
                )

                response = logs.getvalue()

                webhook_payload = {
                    'source': request.META['REMOTE_ADDR'],
                    'status': 0 if result else 1,
                    'output': response,
                }

                # Send our webhook (pass or fail)
                send_webhook(webhook_payload)

        else:
            # Perform our notification at this point
            result = a_obj.notify(
                content.get('body'),
                title=content.get('title', ''),
                notify_type=content.get('type', apprise.NotifyType.INFO),
                tag='all',
                attach=attach,
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
            request.GET.get('privacy', 'no')[0].lower() in (
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
