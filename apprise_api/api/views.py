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
from django.views import View
from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.gzip import gzip_page
from django.utils.translation import ugettext_lazy as _
from .utils import ConfigCache
from .forms import AddByUrlForm
from .forms import AddByConfigForm
from .forms import NotifyForm
from .forms import NotifyByUrlForm

from tempfile import NamedTemporaryFile
import apprise
import json
import re

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


class ResponseCode(object):
    """
    These codes are based on those provided by the requests object
    """
    okay = 200
    no_content = 204
    bad_request = 400
    not_found = 404
    method_not_allowed = 405
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
        # Our default response type
        content_type = 'text/plain; charset=utf-8'

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

        elif MIME_IS_JSON.match(request.content_type):
            # Prepare our default response
            try:
                # load our JSON content
                content = json.loads(request.body)

            except (AttributeError, ValueError):
                # could not parse JSON response...
                return HttpResponse(
                    _('Invalid JSON specified.'),
                    content_type=content_type,
                    status=ResponseCode.bad_request)

        if not content:
            return HttpResponse(
                _('The message format is not supported.'),
                content_type=content_type,
                status=ResponseCode.bad_request)

        # Create ourselves an apprise object to work with
        a_obj = apprise.Apprise()
        if 'urls' in content:
            # Load our content
            a_obj.add(content['urls'])
            if not len(a_obj):
                # No URLs were loaded
                return HttpResponse(
                    _('No valid URLs were found.'),
                    content_type=content_type,
                    status=ResponseCode.bad_request)

            if not ConfigCache.put(
                    key, '\r\n'.join([s.url() for s in a_obj]),
                    apprise.ConfigFormat.TEXT):

                return HttpResponse(
                    _('The configuration could not be saved.'),
                    content_type=content_type,
                    status=ResponseCode.internal_server_error,
                )

        elif 'config' in content:
            fmt = content.get('format', '').lower()
            if fmt not in apprise.CONFIG_FORMATS:
                # Format must be one supported by apprise
                return HttpResponse(
                    _('The format specified is invalid.'),
                    content_type=content_type,
                    status=ResponseCode.bad_request)

            # prepare our apprise config object
            ac_obj = apprise.AppriseConfig()

            try:
                # Write our file to a temporary file
                with NamedTemporaryFile() as f:
                    # Write our content to disk
                    f.write(content['config'].encode())
                    f.flush()

                    if not ac_obj.add(
                            'file://{}?format={}'.format(f.name, fmt)):
                        # Bad Configuration
                        return HttpResponse(
                            _('The configuration specified is invalid.'),
                            content_type=content_type,
                            status=ResponseCode.bad_request)

                    # Add our configuration
                    a_obj.add(ac_obj)

                    if not len(a_obj):
                        # No specified URL(s) were loaded due to
                        # mis-configuration on the caller's part
                        return HttpResponse(
                            _('No valid URL(s) were specified.'),
                            content_type=content_type,
                            status=ResponseCode.bad_request)

            except OSError:
                # We could not write the temporary file to disk
                return HttpResponse(
                    _('The configuration could not be loaded.'),
                    content_type=content_type,
                    status=ResponseCode.internal_server_error)

            if not ConfigCache.put(key, content['config'], fmt=fmt):
                # Something went very wrong; return 500
                return HttpResponse(
                    _('An error occured saving configuration.'),
                    content_type=content_type,
                    status=ResponseCode.internal_server_error,
                )
        else:
            # No configuration specified; we're done
            return HttpResponse(
                _('No configuration specified.'),
                content_type=content_type, status=ResponseCode.bad_request)

        # If we reach here; we successfully loaded the configuration so we can
        # go ahead and write it to disk and alert our caller of the success.
        return HttpResponse(
            _('Successfully saved configuration.'),
            content_type=content_type, status=ResponseCode.okay)


@method_decorator(never_cache, name='dispatch')
class DelView(View):
    """
    A Django view for removing content associated with a key
    """
    def post(self, request, key):
        """
        Handle a POST request
        """
        # Our default response type
        content_type = 'text/plain; charset=utf-8'

        # Clear the key
        result = ConfigCache.clear(key)
        if result is None:
            return HttpResponse(
                _('There was no configuration to remove.'),
                content_type=content_type,
                status=ResponseCode.no_content,
            )

        elif result is False:
            # There was a failure at the os level
            return HttpResponse(
                _('The configuration could not be removed.'),
                content_type=content_type,
                status=ResponseCode.internal_server_error,
            )

        # Removed content
        return HttpResponse(
            _('Successfully removed configuration.'),
            content_type=content_type,
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
        # Our default response type
        content_type = 'text/plain; charset=utf-8'

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
                    content_type=content_type,
                    status=ResponseCode.no_content,
                )

            # Something went very wrong; return 500
            return HttpResponse(
                _('An error occured accessing configuration.'),
                content_type=content_type,
                status=ResponseCode.internal_server_error,
            )

        # Our configuration was retrieved; now our response varies on whether
        # we are a YAML configuration or a TEXT based one.  This allows us to
        # be compatible with those using the AppriseConfig() library or the
        # reference to it through the --config (-c) option in the CLI
        if format == apprise.ConfigFormat.YAML:
            # update our return content type from the default text
            content_type = 'text/yaml; charset=utf-8'

        # Return our retrieved content
        return HttpResponse(
            config, content_type=content_type, status=ResponseCode.okay)


@method_decorator((gzip_page, never_cache), name='dispatch')
class NotifyView(View):
    """
    A Django view for sending a notification
    """
    def post(self, request, key):
        """
        Handle a POST request
        """
        # Our default response type
        content_type = 'text/plain; charset=utf-8'

        # our content
        content = {}
        if MIME_IS_FORM.match(request.content_type):
            content = {}
            form = NotifyForm(request.POST)
            if form.is_valid():
                content.update(form.cleaned_data)

        elif MIME_IS_JSON.match(request.content_type):
            # Prepare our default response
            try:
                # load our JSON content
                content = json.loads(request.body)

            except (AttributeError, ValueError):
                # could not parse JSON response...
                return HttpResponse(
                    _('Invalid JSON specified.'),
                    content_type=content_type,
                    status=ResponseCode.bad_request)

        if not content:
            # We could not handle the Content-Type
            return HttpResponse(
                _('The message format is not supported.'),
                content_type=content_type,
                status=ResponseCode.bad_request)

        # Some basic error checking
        if not content.get('body') or \
                content.get('type', apprise.NotifyType.INFO) \
                not in apprise.NOTIFY_TYPES:

            return HttpResponse(
                _('An invalid payload was specified.'),
                content_type=content_type,
                status=ResponseCode.bad_request)

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
                return HttpResponse(
                    _('There was no configuration found.'),
                    content_type=content_type,
                    status=ResponseCode.no_content,
                )

            # Something went very wrong; return 500
            return HttpResponse(
                _('An error occured accessing configuration.'),
                content_type=content_type,
                status=ResponseCode.internal_server_error,
            )

        # Prepare our apprise object
        a_obj = apprise.Apprise()

        # Create an apprise config object
        ac_obj = apprise.AppriseConfig()

        try:
            # Write our file to a temporary file containing our configuration
            # so that we can read it back.  In the future a change will be to
            # Apprise so that we can just directly write the configuration as
            # is to the AppriseConfig() object... but for now...
            with NamedTemporaryFile() as f:
                # Write our content to disk
                f.write(config.encode())
                f.flush()

                # Read our configuration back in to our configuration
                ac_obj.add('file://{}?format={}'.format(f.name, format))

                # Add our configuration
                a_obj.add(ac_obj)

                # Perform our notification at this point
                a_obj.notify(
                    content.get('body'),
                    title=content.get('title', ''),
                    notify_type=content.get('type', apprise.NotifyType.INFO),
                    tag=content.get('tag'),
                )

        except OSError:
            # We could not write the temporary file to disk
            return HttpResponse(
                _('The configuration could not be loaded.'),
                content_type=content_type,
                status=ResponseCode.internal_server_error)

        # Return our retrieved content
        return HttpResponse(
            _('Notification(s) sent.'),
            content_type=content_type, status=ResponseCode.okay)


@method_decorator((gzip_page, never_cache), name='dispatch')
class StatelessNotifyView(View):
    """
    A Django view for sending a stateless notification
    """
    def post(self, request):
        """
        Handle a POST request
        """
        # Our default response type
        content_type = 'text/plain; charset=utf-8'

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
                content = json.loads(request.body)

            except (AttributeError, ValueError):
                # could not parse JSON response...
                return HttpResponse(
                    _('Invalid JSON specified.'),
                    content_type=content_type,
                    status=ResponseCode.bad_request)

        if not content:
            # We could not handle the Content-Type
            return HttpResponse(
                _('The message format is not supported.'),
                content_type=content_type,
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
                content_type=content_type,
                status=ResponseCode.bad_request)

        # Prepare our apprise object
        a_obj = apprise.Apprise()

        # Add URLs
        a_obj.add(content.get('urls'))
        if not len(a_obj):
            return HttpResponse(
                _('There was no services to notify.'),
                content_type=content_type,
                status=ResponseCode.no_content,
            )

        # Perform our notification at this point
        a_obj.notify(
            content.get('body'),
            title=content.get('title', ''),
            notify_type=content.get('type', apprise.NotifyType.INFO),
            tag='all',
        )

        # Return our retrieved content
        return HttpResponse(
            _('Notification(s) sent.'),
            content_type=content_type, status=ResponseCode.okay)
