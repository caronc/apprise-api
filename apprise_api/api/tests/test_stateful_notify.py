# -*- coding: utf-8 -*-
#
# Copyright (C) 2020 Chris Caron <lead2gold@gmail.com>
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
from django.test import SimpleTestCase
from django.test.utils import override_settings
from unittest.mock import patch, Mock
from ..forms import NotifyForm
from ..utils import ConfigCache
from json import dumps
import os
import re
import apprise
import requests
import inspect

# Grant access to our Notification Manager Singleton
N_MGR = apprise.manager_plugins.NotificationManager()


class StatefulNotifyTests(SimpleTestCase):
    """
    Test stateless notifications
    """

    @override_settings(APPRISE_CONFIG_LOCK=True)
    def test_stateful_configuration_with_lock(self):
        """
        Test the retrieval of configuration when the lock is set
        """
        # our key to use
        key = 'test_stateful_with_lock'

        # It doesn't matter if there is or isn't any configuration; when this
        # flag is set. All that overhead is skipped and we're denied access
        # right off the bat
        response = self.client.post('/get/{}'.format(key))
        assert response.status_code == 403

    @patch('requests.post')
    def test_stateful_configuration_io(self, mock_post):
        """
        Test the writing, removal, writing and removal of configuration to
        verify it persists and is removed when expected
        """

        # our key to use
        key = 'test_stateful'

        request = Mock()
        request.content = b'ok'
        request.status_code = requests.codes.ok
        mock_post.return_value = request

        # Monkey Patch
        N_MGR['mailto'].enabled = True

        # Preare our list of URLs we want to save
        urls = [
            'devops=slack://TokenA/TokenB/TokenC',
            'pushbullet=pbul://tokendetails',
            'general,json=json://hostname',
        ]

        # Monkey Patch
        N_MGR['slack'].enabled = True
        N_MGR['pbul'].enabled = True
        N_MGR['json'].enabled = True

        # For 10 iterations, repeat these tests to verify that don't change
        # and our saved content is not different on subsequent calls.
        for _ in range(10):
            # No content saved to the location yet
            response = self.client.post('/get/{}'.format(key))
            assert response.status_code == 204

            # Add our content
            response = self.client.post(
                '/add/{}'.format(key),
                {'config': '\r\n'.join(urls)})
            assert response.status_code == 200

            # Now we should be able to see our content
            response = self.client.post('/get/{}'.format(key))
            assert response.status_code == 200

            entries = re.split(r'[\r*\n]+', response.content.decode('utf-8'))
            assert len(entries) == 3

            form_data = {
                'body': '## test notification',
                'format': apprise.NotifyFormat.MARKDOWN,
                'tag': 'general',
            }

            form = NotifyForm(data=form_data)
            assert form.is_valid()

            # Required to prevent None from being passed into
            # self.client.post()
            del form.cleaned_data['attachment']

            # We sent the notification successfully
            response = self.client.post(
                '/notify/{}'.format(key), form.cleaned_data)
            assert response.status_code == 200
            assert mock_post.call_count == 1

            mock_post.reset_mock()

            form_data = {
                'payload': '## test notification',
                'fmt': apprise.NotifyFormat.MARKDOWN,
                'extra': 'general',
            }

            # We sent the notification successfully (use our rule mapping)
            # FORM
            response = self.client.post(
                f'/notify/{key}/?:payload=body&:fmt=format&:extra=tag',
                form_data)
            assert response.status_code == 200
            assert mock_post.call_count == 1

            mock_post.reset_mock()

            form_data = {
                'payload': '## test notification',
                'fmt': apprise.NotifyFormat.MARKDOWN,
                'extra': 'general',
            }

            # We sent the notification successfully (use our rule mapping)
            # JSON
            response = self.client.post(
                f'/notify/{key}/?:payload=body&:fmt=format&:extra=tag',
                dumps(form_data),
                content_type="application/json")
            assert response.status_code == 200
            assert mock_post.call_count == 1

            mock_post.reset_mock()

            form_data = {
                'body': '## test notification',
                'format': apprise.NotifyFormat.MARKDOWN,
                'tag': 'no-on-with-this-tag',
            }

            form = NotifyForm(data=form_data)
            assert form.is_valid()

            # Required to prevent None from being passed into
            # self.client.post()
            del form.cleaned_data['attachment']

            # No one to notify
            response = self.client.post(
                '/notify/{}'.format(key), form.cleaned_data)
            assert response.status_code == 424
            assert mock_post.call_count == 0

            mock_post.reset_mock()

            # Now empty our data
            response = self.client.post('/del/{}'.format(key))
            assert response.status_code == 200

            # A second call; but there is nothing to remove
            response = self.client.post('/del/{}'.format(key))
            assert response.status_code == 204

            # Reset our count
            mock_post.reset_mock()

        # Now we do a similar approach as the above except we remove the
        # configuration from under the application
        for _ in range(10):
            # No content saved to the location yet
            response = self.client.post('/get/{}'.format(key))
            assert response.status_code == 204

            # Add our content
            response = self.client.post(
                '/add/{}'.format(key),
                {'config': '\r\n'.join(urls)})
            assert response.status_code == 200

            # Now we should be able to see our content
            response = self.client.post('/get/{}'.format(key))
            assert response.status_code == 200

            entries = re.split(r'[\r*\n]+', response.content.decode('utf-8'))
            assert len(entries) == 3

            form_data = {
                'body': '## test notification',
                'format': apprise.NotifyFormat.MARKDOWN,
            }

            form = NotifyForm(data=form_data)
            assert form.is_valid()

            # Required to prevent None from being passed into
            # self.client.post()
            del form.cleaned_data['attachment']

            # No one to notify (no tag specified)
            response = self.client.post(
                '/notify/{}'.format(key), form.cleaned_data)
            assert response.status_code == 424
            assert mock_post.call_count == 0

            # Reset our configuration
            mock_post.reset_mock()

            #
            # Test tagging now
            #
            form_data = {
                'body': '## test notification',
                'format': apprise.NotifyFormat.MARKDOWN,
                'tag': 'general+json',
            }

            form = NotifyForm(data=form_data)
            assert form.is_valid()

            # Required to prevent None from being passed into
            # self.client.post()
            del form.cleaned_data['attachment']

            response = self.client.post(
                '/notify/{}'.format(key), form.cleaned_data)
            # + (plus) not supported at this time
            assert response.status_code == 400
            assert mock_post.call_count == 0

            # Reset our configuration
            mock_post.reset_mock()

            form_data = {
                'body': '## test notification',
                'format': apprise.NotifyFormat.MARKDOWN,
                # Plus with space inbetween
                'tag': 'general + json',
            }

            form = NotifyForm(data=form_data)
            assert form.is_valid()

            # Required to prevent None from being passed into
            # self.client.post()
            del form.cleaned_data['attachment']

            response = self.client.post(
                '/notify/{}'.format(key), form.cleaned_data)
            # + (plus) not supported at this time
            assert response.status_code == 400
            assert mock_post.call_count == 0

            mock_post.reset_mock()

            form_data = {
                'body': '## test notification',
                'format': apprise.NotifyFormat.MARKDOWN,
                # Space (AND)
                'tag': 'general json',
            }

            form = NotifyForm(data=form_data)
            assert form.is_valid()

            # Required to prevent None from being passed into
            # self.client.post()
            del form.cleaned_data['attachment']

            response = self.client.post(
                '/notify/{}'.format(key), form.cleaned_data)
            assert response.status_code == 200
            assert mock_post.call_count == 1

            mock_post.reset_mock()

            form_data = {
                'body': '## test notification',
                'format': apprise.NotifyFormat.MARKDOWN,
                # Comma (OR)
                'tag': 'general, devops',
            }

            form = NotifyForm(data=form_data)
            assert form.is_valid()

            # Required to prevent None from being passed into
            # self.client.post()
            del form.cleaned_data['attachment']

            response = self.client.post(
                '/notify/{}'.format(key), form.cleaned_data)
            assert response.status_code == 200

            # 2 endpoints hit
            assert mock_post.call_count == 2

            # Now remove the file directly (as though one
            # removed the configuration directory)
            result = ConfigCache.path(key)
            entry = os.path.join(result[0], '{}.text'.format(result[1]))
            assert os.path.isfile(entry)
            # The removal
            os.unlink(entry)
            # Verify
            assert not os.path.isfile(entry)

            # Call /del/ but now there is nothing to remove
            response = self.client.post('/del/{}'.format(key))
            assert response.status_code == 204

            # Reset our count
            mock_post.reset_mock()

    @patch('requests.post')
    def test_stateful_group_dict_notify(self, mock_post):
        """
        Test the handling of a group defined as a dictionary
        """

        # our key to use
        key = 'test_stateful_group_notify_dict'

        request = Mock()
        request.content = b'ok'
        request.status_code = requests.codes.ok
        mock_post.return_value = request

        # Monkey Patch
        N_MGR['mailto'].enabled = True

        config = inspect.cleandoc("""
        version: 1
        groups:
          mygroup: user1, user2

        urls:
          - json:///user:pass@localhost:
            - to: user1@example.com
              tag: user1
            - to: user2@example.com
              tag: user2
        """)

        # Monkey Patch
        N_MGR['json'].enabled = True

        # Add our content
        response = self.client.post(
            '/add/{}'.format(key),
            {'config': config})
        assert response.status_code == 200

        # Now we should be able to see our content
        response = self.client.post('/get/{}'.format(key))
        assert response.status_code == 200

        for tag in ('user1', 'user2'):
            form_data = {
                'body': '## test notification',
                'format': apprise.NotifyFormat.MARKDOWN,
                'tag': tag,
            }
            form = NotifyForm(data=form_data)
            assert form.is_valid()

            # Required to prevent None from being passed into
            # self.client.post()
            del form.cleaned_data['attachment']

            # We sent the notification successfully
            response = self.client.post(
                '/notify/{}'.format(key), form.cleaned_data)
            assert response.status_code == 200

            # Our single endpoint is notified
            assert mock_post.call_count == 1

            mock_post.reset_mock()

        # Now let's notify by our group
        form_data = {
            'body': '## test notification',
            'format': apprise.NotifyFormat.MARKDOWN,
            'tag': 'mygroup',
        }

        form = NotifyForm(data=form_data)
        assert form.is_valid()

        # Required to prevent None from being passed into
        # self.client.post()
        del form.cleaned_data['attachment']

        # We sent the notification successfully
        response = self.client.post(
            '/notify/{}'.format(key), form.cleaned_data)
        assert response.status_code == 200

        # Our 2 endpoints are notified
        assert mock_post.call_count == 2

        mock_post.reset_mock()

        # Now empty our data
        response = self.client.post('/del/{}'.format(key))
        assert response.status_code == 200

        # Reset our count
        mock_post.reset_mock()

    @patch('requests.post')
    def test_stateful_group_dictlist_notify(self, mock_post):
        """
        Test the handling of a group defined as a list of dictionaries
        """

        # our key to use
        key = 'test_stateful_group_notify_list_dict'

        request = Mock()
        request.content = b'ok'
        request.status_code = requests.codes.ok
        mock_post.return_value = request

        # Monkey Patch
        N_MGR['mailto'].enabled = True

        config = inspect.cleandoc("""
        version: 1
        groups:
          - mygroup: user1, user2

        urls:
          - json:///user:pass@localhost:
            - to: user1@example.com
              tag: user1
            - to: user2@example.com
              tag: user2
        """)

        # Monkey Patch
        N_MGR['json'].enabled = True

        # Add our content
        response = self.client.post(
            '/add/{}'.format(key),
            {'config': config})
        assert response.status_code == 200

        # Now we should be able to see our content
        response = self.client.post('/get/{}'.format(key))
        assert response.status_code == 200

        for tag in ('user1', 'user2'):
            form_data = {
                'body': '## test notification',
                'format': apprise.NotifyFormat.MARKDOWN,
                'tag': tag,
            }
            form = NotifyForm(data=form_data)
            assert form.is_valid()

            # Required to prevent None from being passed into
            # self.client.post()
            del form.cleaned_data['attachment']

            # We sent the notification successfully
            response = self.client.post(
                '/notify/{}'.format(key), form.cleaned_data)
            assert response.status_code == 200

            # Our single endpoint is notified
            assert mock_post.call_count == 1

            mock_post.reset_mock()

        # Now let's notify by our group
        form_data = {
            'body': '## test notification',
            'format': apprise.NotifyFormat.MARKDOWN,
            'tag': 'mygroup',
        }

        form = NotifyForm(data=form_data)
        assert form.is_valid()

        # Required to prevent None from being passed into
        # self.client.post()
        del form.cleaned_data['attachment']

        # We sent the notification successfully
        response = self.client.post(
            '/notify/{}'.format(key), form.cleaned_data)
        assert response.status_code == 200

        # Our 2 endpoints are notified
        assert mock_post.call_count == 2

        mock_post.reset_mock()

        # Now empty our data
        response = self.client.post('/del/{}'.format(key))
        assert response.status_code == 200

        # Reset our count
        mock_post.reset_mock()
